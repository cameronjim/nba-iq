"""publish one slate's predictions, end to end, from the database. ONE COMMAND.

    python daily_run.py                          # the scheduled run
    python daily_run.py --dry-run                # everything except the insert
    python daily_run.py --window-start 2026-10-20 --dry-run   # a named window

WHAT THIS IS. ``.github/workflows/predictions.yml`` runs this once a day and nothing
else. Every step it performs already existed as a separate script - build_dataset.py,
project_preseason.py, predict.py - and running them by hand in the right order with
the right flags was the whole operational risk: a rebuild skipped means features that
are three days stale, a forgotten ``--horizon`` means a run that cannot be pooled with
its neighbours, and a forgotten ``--notes`` means a slate that is not part of the
prospective test at all (MODEL.md 13.8.4). This file is that order, written down.

IT IS NOT A NEW MODEL, A NEW FEATURE OR A NEW POLICY. It computes nothing that the
three scripts it drives do not already compute; the only logic that is genuinely new
here is (a) which dates the run covers, (b) whether the truth layer is fresh enough to
serve from, (c) whether the run qualifies for the frozen prospective label, and (d)
which games have already tipped off. Those four are pure functions at the top of this
module and `tests/test_daily_run.py` is about them, because they are the four things
that can be wrong in a way no downstream script would notice.

TWO RUNS PER DAY, PROSPECTIVE FIRST. The app serves only the newest complete run, and
a manager needs the next seven days, preseason included. So the schedule, universe
and features are built ONCE over the extended window and scored twice from that one
frame: run A is the frozen prospective run exactly as before (``--window-days``,
Regular Season only, ``gameday``, the label); run B is every servable game in the
extended window, published AFTER A so it is the one the app shows. B is NOT
PROSPECTIVE by construction and says so in its notes, and it carries no horizon
because a seven-day run has no single bucket (MODEL.md 16.7).

THE OFFSEASON NO-OP IS THE NORMAL CASE FOR MOST OF THE YEAR. No games in the extended
window means exit 0 having written nothing: not a failure, not an empty run row, nothing. A
daily cron that goes red every morning from June to October is a cron whose red is
worth nothing by November.

FAILURE IS LOUD AND IT NAMES A PHASE. There is no retry, no partial write and no
"continue anyway" except for the one case that is explicitly a warning (a stale truth
layer, which is recorded in the run notes and served regardless, because yesterday's
form is a better projection than no projection). A non-zero exit is the alert.

TWO RULES THIS FILE ENFORCES THAT NOTHING ELSE CAN:

  1. **A prediction is never inserted after tipoff** (MODEL.md 13.8.2, the one rule in
     section 13 with no judgement in it). ``predict.py`` has no notion of a tip time,
     so the filter has to be here, and it is applied TWICE - once when the slate is
     chosen and once against the frame that is about to be scored, because the phases
     in between take minutes and a 7pm tip does not wait for them.
  2. **The prospective label is written only under the frozen conditions**
     (13.8.4). A run that is off-artifact, off-horizon, off-season or built from the
     biased universe gets a note that says so instead. The label is what makes a run
     part of the first genuinely untouched evaluation this system will ever get, and a
     debugging run wearing it would contaminate the season quietly.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_dataset  # noqa: E402
import predict as predict_script  # noqa: E402
from fnba_ml import registry  # noqa: E402
from fnba_ml.cli import add_common_args, load_dataset, setup_logging  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    DATA_DIR,
    MODELS_DIR,
    PROSPECTIVE_2026_27,
    PROSPECTIVE_ARTIFACT_CHECKSUMS,
    PROSPECTIVE_FEATURE_VERSION,
    PROSPECTIVE_MODEL_VERSION,
    PROSPECTIVE_RUN_NOTE_LABEL,
    PROSPECTIVE_SERVING_HORIZON,
    SEASON_TYPES,
    SEASONS,
    SERVED_FEATURE_SET,
)
from fnba_ml.store import PRODUCTION_CHANNEL  # noqa: E402
from fnba_ml.prospective import (  # noqa: E402
    SOURCE_PROSPECTIVE,
    build_prospective_features,
    history_from_dataset,
    prospective_universe,
)

log = logging.getLogger("daily_run")

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

# GAME DATES ARE EASTERN CALENDAR DATES and this is not a detail. A 9:30pm ET tip on
# 2026-10-20 carries ``scheduled_at = 2026-10-21 01:30Z``; the schedule row's
# ``game_date`` is 2026-10-20 and that is the date a manager setting a lineup means.
# A window computed in UTC would, for five hours every evening, ask for tomorrow.
EASTERN = ZoneInfo("America/New_York")  # US/Eastern, under its canonical name

# how far the truth layer may lag the schedule before the run says so out loud. THREE
# DAYS, because the scraper runs every six hours (``.github/workflows/scraper.yml``)
# so a one-day gap is an ordinary overnight and a three-day gap is a broken scrape.
# It is a WARNING and not a refusal: features built on Friday's game logs are a worse
# projection for Monday than Sunday's would have been, and they are a much better one
# than an empty page.
STALE_AFTER_DAYS = 3

# the season types the app may serve. Wider than ``config.SEASON_TYPES`` on purpose:
# that list feeds the training history and the freshness check and must stay Regular
# Season; this one only widens which scheduled games get a forecast.
SLATE_SEASON_TYPES: tuple[str, ...] = ("Pre Season", "Regular Season")

# the only season type the frozen prospective run may contain (13.8.4).
PROSPECTIVE_SEASON_TYPE = "Regular Season"

# the phases, in order, for the banner and for the failure message. A failure has to
# name one of these: "daily_run failed" sends an operator to read this file, "daily_run
# failed in phase 'schedule'" sends them to the database.
PHASES: tuple[str, ...] = (
    "preflight",
    "window",
    "schedule",
    "dataset",
    "prospective",
    "statuses",
    "predict",
)

# ---------------------------------------------------------------------------
# SQL. window-scoped reads; every one of them is a SELECT.
# ---------------------------------------------------------------------------

# the slate. Column aliases match the canonical schedule frame
# (``data.postgres_source.SCHEDULE_SQL``) so ``prospective`` can consume it directly.
#
# postponed_status: the NBA's own ``postponedStatus`` field, and 'N' means NOT
# postponed - it is present on every future row, so "non-null means postponed" (which
# migration 013's comment says) would drop the entire schedule. Anything that is
# neither null nor 'N' is treated as postponed and is not served.
WINDOW_SCHEDULE_SQL = """
SELECT
    s.nba_game_id          AS "GAME_ID",
    s.season               AS "SEASON",
    s.season_type          AS "SEASON_TYPE",
    s.game_date            AS "GAME_DATE",
    s.scheduled_at         AS "SCHEDULED_AT",
    s.home_team_id         AS "HOME_TEAM_ID",
    s.away_team_id         AS "AWAY_TEAM_ID",
    s.game_status          AS "GAME_STATUS",
    s.postponed_status     AS "POSTPONED_STATUS"
FROM nba_schedule s
WHERE s.game_date >= %(start)s
  AND s.game_date <= %(end)s
  AND s.season_type = ANY(%(season_types)s)
ORDER BY s.game_date, s.nba_game_id
"""

# the freshness pair. ``logs_through`` is what the truth layer holds; ``schedule_through``
# is the last game the schedule says should already have been played. Their difference
# is the lag, and neither number alone is interpretable: a truth layer that stops on
# 2026-04-12 is perfectly fresh in July and four months stale in December.
FRESHNESS_SQL = """
SELECT
    (SELECT MAX(game_date) FROM player_game_logs)                    AS logs_through,
    (SELECT MAX(s.game_date) FROM nba_schedule s
      WHERE s.game_date < %(start)s
        AND s.season_type = ANY(%(season_types)s))                   AS schedule_through
"""

# roster assignments, primary source: the open stints the scraper's roster snapshot
# writes. This is what puts a traded player on his new team.
OPEN_STINTS_SQL = """
SELECT nba_player_id, team_id
  FROM player_team_stints
 WHERE valid_to IS NULL
"""

# roster assignments, fallback: the most recent CLOSED stint per player. Used only when
# no open stint exists at all - a roster snapshot that has never been taken, or one
# whose rows were all closed by a later sync. Last-known-team is a worse answer than
# today's roster and a much better one than no slate; the run notes say which was used.
LAST_KNOWN_STINTS_SQL = """
SELECT DISTINCT ON (nba_player_id)
       nba_player_id, team_id
  FROM player_team_stints
 ORDER BY nba_player_id, valid_to DESC NULLS FIRST
"""

POSITIONS_SQL = """
SELECT p.nba_id AS "PLAYER_ID", p.position AS "POSITION"
  FROM players p
 WHERE p.nba_id IS NOT NULL
"""


# ---------------------------------------------------------------------------
# control flow
# ---------------------------------------------------------------------------


class PhaseFailure(RuntimeError):
    """a phase failed. carries the phase name so the exit message can print it."""

    def __init__(self, phase: str, cause: BaseException | str) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(f"phase {phase!r}: {cause}")


class NothingToDo(Exception):
    """a clean, successful, wrote-nothing exit. the offseason, mostly."""


@contextmanager
def phase(name: str) -> Iterator[None]:
    """banner in, timing out, and any exception relabelled with the phase name."""
    index = PHASES.index(name) + 1
    log.info("%s", "=" * 78)
    log.info("PHASE %d/%d  %s", index, len(PHASES), name)
    log.info("%s", "=" * 78)
    started = time.monotonic()
    try:
        yield
    except (NothingToDo, PhaseFailure):
        raise
    except SystemExit as exc:
        # the three scripts this drives signal failure with SystemExit(str). that is
        # the right behaviour for a script and the wrong one here, where the exit
        # message has to name the phase it came from.
        raise PhaseFailure(name, exc.code if exc.code else "exited") from exc
    except BaseException as exc:  # noqa: BLE001 - relabelled and re-raised immediately
        raise PhaseFailure(name, exc) from exc
    log.info("PHASE %d/%d  %s  ok  (%.1fs)", index, len(PHASES), name,
             time.monotonic() - started)


# ---------------------------------------------------------------------------
# the four pure functions. everything testable without a database lives here.
# ---------------------------------------------------------------------------


def eastern_today(now: datetime | None = None) -> date:
    """today's Eastern calendar date. ``now`` is for tests and takes any tz."""
    stamp = datetime.now(tz=timezone.utc) if now is None else now
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(EASTERN).date()


def prediction_window(
    window_days: int,
    window_start: date | str | None = None,
    now: datetime | None = None,
) -> tuple[date, date]:
    """the inclusive [first, last] game dates this run covers.

    TODAY IS ALWAYS IN IT and the window always extends FORWARD. A backward window
    would be a backfill, and 13.8.2 forbids backfilling a missed slate - so the
    arithmetic simply cannot express one.
    """
    if window_days < 1:
        raise ValueError(f"--window-days must be at least 1, got {window_days}")
    if window_start is None:
        first = eastern_today(now)
    elif isinstance(window_start, date):
        first = window_start
    else:
        first = pd.Timestamp(window_start).date()
    return first, first + timedelta(days=window_days - 1)


def staleness_warning(
    logs_through: date | None,
    schedule_through: date | None,
    max_lag_days: int = STALE_AFTER_DAYS,
) -> str | None:
    """the message when the truth layer lags the schedule, else None.

    Returns a STRING rather than a bool because the string is the thing that goes
    into ``prediction_runs.notes``: a stale run has to be identifiable as stale from
    the store alone, months later, by someone who was not watching the logs that
    morning.

    A missing ``schedule_through`` (nothing before the window - opening night) is not
    stale. A missing ``logs_through`` with games behind us IS: an empty truth layer
    is the most stale state there is, and returning "fine" for it would make the
    check silent in exactly the case it exists for.
    """
    if schedule_through is None:
        return None
    if logs_through is None:
        return (
            f"STALE truth layer: player_game_logs is empty and the schedule has "
            f"games through {schedule_through}"
        )
    lag = (schedule_through - logs_through).days
    if lag <= max_lag_days:
        return None
    return (
        f"STALE truth layer: game logs end {logs_through}, schedule has games "
        f"through {schedule_through} ({lag} days behind, tolerance {max_lag_days})"
    )


def prospective_conditions(
    seasons: Iterable[str],
    season_types: Iterable[str],
    horizon: str,
    model_version: str,
    feature_version: str,
    universe_source: str,
    artifact_verified: bool,
) -> list[str]:
    """the reasons this run does NOT qualify for the frozen label. empty means it does.

    MODEL.md 13.8.4: a run that carries the frozen protocol label in its notes is
    part of the prospective test and a run that does not is not, "whatever else it
    did". That makes the label a claim about SEVEN things at once, and this returns
    the ones that are false so the note can say which.

    Every expected value is read from the freeze (``config.PROSPECTIVE_*``); nothing
    here is a literal, because a second copy of the frozen configuration is exactly
    what section 13's own preamble forbids.
    """
    frozen_season = str(PROSPECTIVE_2026_27["season"])
    reasons: list[str] = []

    seen_seasons = sorted({str(s) for s in seasons})
    off_season = [s for s in seen_seasons if s != frozen_season]
    if off_season:
        reasons.append(f"season {','.join(off_season)} is not {frozen_season}")
    if not seen_seasons:
        reasons.append("no season on the slate")

    seen_types = sorted({str(t) for t in season_types})
    off_type = [t for t in seen_types if t != "Regular Season"]
    if off_type:
        reasons.append(f"season_type {','.join(off_type)} is not Regular Season")

    if horizon != PROSPECTIVE_SERVING_HORIZON:
        reasons.append(f"horizon {horizon} is not {PROSPECTIVE_SERVING_HORIZON}")
    if str(model_version) != PROSPECTIVE_MODEL_VERSION:
        reasons.append(f"model {model_version} is not {PROSPECTIVE_MODEL_VERSION}")
    if str(feature_version) != PROSPECTIVE_FEATURE_VERSION:
        reasons.append(
            f"feature_version {feature_version} is not {PROSPECTIVE_FEATURE_VERSION}"
        )
    if universe_source != SOURCE_PROSPECTIVE:
        reasons.append(f"universe {universe_source} is not {SOURCE_PROSPECTIVE}")
    if not artifact_verified:
        reasons.append("pinned artifact checksums not verified")
    return reasons


def run_notes(reasons: list[str], stale: str | None = None) -> str:
    """``prediction_runs.notes`` for this run.

    The qualifying form follows 13.4: the label, then ``feature_set=v3-honest``,
    then ``channel=production``. The channel token is derived from the same
    constant written to ``prediction_runs.channel``, so notes and column cannot
    disagree; it replaces 13.4's ``shadow=false``. The non-qualifying form must
    NOT contain the label anywhere - a substring match is how a look report will
    select the season's runs, and "not <label>" would be selected by
    it. The assertion below is not decoration; it is the only thing standing between
    a reworded reason string and a contaminated season.
    """
    tail = f"feature_set={SERVED_FEATURE_SET}; channel={PRODUCTION_CHANNEL}"
    if reasons:
        note = f"NOT PROSPECTIVE ({'; '.join(reasons)}); {tail}"
        if PROSPECTIVE_RUN_NOTE_LABEL in note:
            raise AssertionError(
                f"a non-prospective run note must not contain "
                f"{PROSPECTIVE_RUN_NOTE_LABEL!r}; got {note!r}"
            )
    else:
        note = f"{PROSPECTIVE_RUN_NOTE_LABEL}; {tail}"
    if stale:
        note = f"{note}; {stale}"
    return note


def extended_notes(
    extended_days: int, conditions: list[str], stale: str | None = None
) -> str:
    """``prediction_runs.notes`` for the extended run: never the prospective label.

    Goes through ``run_notes`` with a non-empty reason list, so the assertion that a
    non-qualifying note cannot contain the label applies to this run too.
    """
    return run_notes([f"extended {extended_days}-day serving window", *conditions], stale)


def split_prospective(
    frame: pd.DataFrame,
    schedule: pd.DataFrame,
    window_start: date | pd.Timestamp,
    window_end: date | pd.Timestamp,
) -> pd.DataFrame:
    """the rows of run A: Regular Season games dated inside the prospective window.

    Season type lives on the schedule, not on the feature frame, so it is looked up by
    ``GAME_ID``; the feature frame itself is passed through untouched so run A scores
    exactly the rows it would have scored when it was the only run.
    """
    regular = set(
        schedule.loc[schedule["SEASON_TYPE"] == PROSPECTIVE_SEASON_TYPE, "GAME_ID"]
    )
    dates = pd.to_datetime(frame["GAME_DATE"])
    in_window = (dates >= pd.Timestamp(window_start)) & (
        dates <= pd.Timestamp(window_end)
    )
    return frame[in_window & frame["GAME_ID"].isin(regular)].copy()


def nominal_tip(frame: pd.DataFrame) -> pd.Series:
    """UTC tipoff per row: the real timestamp where we have one, else an approximation.

    Reuses ``predict.NOMINAL_TIP_HOUR_UTC`` rather than picking a second nominal hour,
    so the tip this module filters on and the tip ``predict.horizon_metadata`` measures
    the horizon against are the same instant. A row whose tip is approximated is
    filtered on an approximation, and that is stated rather than hidden: the
    approximation is 00:00 UTC, which is EARLIER than most tips, so it drops games it
    is unsure about instead of publishing them.
    """
    if "SCHEDULED_AT" in frame.columns:
        tip = pd.to_datetime(frame["SCHEDULED_AT"], errors="coerce", utc=True)
    else:
        tip = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    fallback = pd.to_datetime(
        frame["GAME_DATE"], errors="coerce", utc=True
    ).dt.normalize() + pd.Timedelta(hours=predict_script.NOMINAL_TIP_HOUR_UTC)
    return tip.fillna(fallback)


def drop_tipped_off(
    frame: pd.DataFrame, now: datetime | pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """split a frame into (still to tip, already tipped). NEVER publish the second.

    MODEL.md 13.8.2, verbatim: "a prediction made after tipoff is never inserted into
    the store, under any circumstance, for any reason. This is the one rule in this
    section with no judgement in it: a post-tipoff row is not a late forecast, it is a
    different kind of object, and one of them in the store makes every aggregate over
    the season unauditable."

    The comparison is ``tip > now``, strictly: a prediction made AT the tip is not
    before it. A row whose tip cannot be computed at all is dropped, on the same
    principle - "I cannot tell whether this game has started" is not a licence to
    publish.
    """
    boundary = pd.Timestamp(now)
    if boundary.tzinfo is None:
        boundary = boundary.tz_localize("UTC")
    else:
        boundary = boundary.tz_convert("UTC")
    tip = nominal_tip(frame)
    upcoming = tip.notna() & (tip > boundary)
    return frame[upcoming].copy(), frame[~upcoming].copy()


def verify_pinned_artifact(models_dir: Path = MODELS_DIR) -> list[str]:
    """re-hash the frozen serving artifact. returns the mismatching filenames.

    THE SAME ASSERTION ``tests/test_prospective_freeze.py`` MAKES, at run time. The
    test proves the bytes on THIS checkout match the freeze; this proves the bytes on
    the RUNNER do, which is a different machine, a different checkout and the one that
    actually serves. It re-uses ``registry.sha256_file`` and reads the expected digests
    out of ``config.PROSPECTIVE_ARTIFACT_CHECKSUMS`` - the freeze - rather than out of
    ``registry.json``, because the registry is written by training runs and the freeze
    is not written by anything.

    Set equality is part of it: an EXTRA file in the served directory passes every
    per-file digest while changing what "the artifact" means.
    """
    directory = models_dir / PROSPECTIVE_MODEL_VERSION
    if not directory.is_dir():
        return [f"{directory} (missing)"]
    on_disk = {p.name for p in directory.iterdir() if p.is_file()}
    bad = sorted(on_disk.symmetric_difference(PROSPECTIVE_ARTIFACT_CHECKSUMS))
    for name, expected in sorted(PROSPECTIVE_ARTIFACT_CHECKSUMS.items()):
        path = directory / name
        if path.is_file() and registry.sha256_file(path) != expected:
            bad.append(name)
    return sorted(set(bad))


# ---------------------------------------------------------------------------
# database reads
# ---------------------------------------------------------------------------


def _read_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    import psycopg2  # noqa: PLC0415 - only needed on the database path

    from fnba_ml.data.postgres_source import load_database_url  # noqa: PLC0415

    with psycopg2.connect(load_database_url()) as conn:
        return pd.read_sql_query(sql, conn, params=params or {})


def load_window_schedule(start: date, end: date) -> tuple[pd.DataFrame, int]:
    """the servable slate, plus how many rows the window held before filtering.

    The second value exists so the no-op message can tell the two empty cases apart.
    "no game is scheduled" (the offseason, every day from June to October) and "every
    scheduled game has already been played" (a cron that fired a day late, or a
    ``--window-start`` in the past) are the same empty frame and completely different
    operational facts, and an operator reading one message must not have to guess.
    """
    frame = _read_sql(
        WINDOW_SCHEDULE_SQL,
        {"start": start, "end": end, "season_types": list(SLATE_SEASON_TYPES)},
    )
    queried = len(frame)
    log.info("schedule rows in window: %d", queried)
    if frame.empty:
        return frame, queried

    postponed = frame["POSTPONED_STATUS"].notna() & ~frame[
        "POSTPONED_STATUS"
    ].astype(str).str.strip().str.upper().isin({"", "N"})
    if int(postponed.sum()):
        log.warning(
            "dropping %d postponed game(s): %s", int(postponed.sum()),
            ", ".join(sorted(frame.loc[postponed, "GAME_ID"].astype(str))),
        )
        frame = frame[~postponed]

    final = frame["GAME_STATUS"].astype(str).str.strip().str.lower().eq("final")
    if int(final.sum()):
        log.warning(
            "dropping %d already-final game(s) in the window; a completed game is "
            "not a forecast (13.8.2)", int(final.sum()),
        )
        frame = frame[~final]
    return frame.drop(columns=["POSTPONED_STATUS"]).reset_index(drop=True), queried


def load_freshness(start: date) -> tuple[date | None, date | None]:
    row = _read_sql(
        FRESHNESS_SQL, {"start": start, "season_types": list(SEASON_TYPES)}
    ).iloc[0]

    def _as_date(value: object) -> date | None:
        if value is None or pd.isna(value):  # type: ignore[arg-type]
            return None
        return pd.Timestamp(value).date()  # type: ignore[arg-type]

    return _as_date(row["logs_through"]), _as_date(row["schedule_through"])


def load_rosters(snapshot: Path | None = None) -> tuple[pd.DataFrame, str]:
    """(roster frame, provenance string). open stints, then last-known, then csv."""
    if snapshot is not None:
        frame = pd.read_csv(snapshot, dtype=str)
        return frame, f"csv snapshot {snapshot.name} ({len(frame)} assignments)"
    frame = _read_sql(OPEN_STINTS_SQL)
    if len(frame):
        return frame, f"open player_team_stints ({len(frame)} assignments)"
    log.warning(
        "player_team_stints has no OPEN stint; falling back to each player's "
        "last known team. this is a scraper problem - run --roster-snapshot - and "
        "the fallback will put a traded player on his old team"
    )
    frame = _read_sql(LAST_KNOWN_STINTS_SQL)
    if frame.empty:
        raise RuntimeError(
            "player_team_stints is empty, so nothing says which players are on "
            "which team. run the scraper's --roster-snapshot first."
        )
    return frame, f"LAST-KNOWN player_team_stints ({len(frame)} assignments, degraded)"


def load_positions() -> pd.DataFrame | None:
    try:
        frame = _read_sql(POSITIONS_SQL)
    except Exception as exc:  # noqa: BLE001 - positions are optional reference data
        log.warning("could not read positions (%s); POS_GROUP will be null", exc)
        return None
    return frame if len(frame) else None


def load_statuses(as_of: pd.Timestamp) -> pd.DataFrame:
    """the newest injury designation per player, as known at ``as_of``.

    An EMPTY frame is a supported outcome and not an error. ``player_injury_reports``
    held zero rows when the override layer shipped (MODEL.md 13.9, F10) and may still;
    a run with no report is model-only, which is today's reality and is recorded as
    ``report_count = 0`` in the horizon facts rather than being treated as a fault.
    """
    from fnba_ml.data.postgres_source import PostgresSource  # noqa: PLC0415

    return PostgresSource(seasons=list(SEASONS)).load_latest_injury_statuses(as_of)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--window-days", type=int, default=2,
        help="how many game dates the PROSPECTIVE run covers, starting today "
             "Eastern (default 2: tonight's slate, plus tomorrow's so a manager can "
             "plan and so one missed cron does not lose a night)",
    )
    parser.add_argument(
        "--extended-days", type=int, default=7,
        help="how many game dates the extended NOT PROSPECTIVE run covers, "
             "preseason included (default 7; must be at least --window-days)",
    )
    parser.add_argument(
        "--window-start", default=None,
        help="TESTING ESCAPE HATCH: first game date instead of today Eastern. it "
             "cannot cause a post-tipoff write - drop_tipped_off is applied to "
             "whatever window this produces - but it can produce a run whose games "
             "are all in the past, which will publish nothing",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="do everything except the insert. reads the database, rebuilds the "
             "dataset, scores the slate, writes the parquets, and reports what it "
             "WOULD have written",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=DATA_DIR / "daily",
        help="where the run's parquets go. deliberately NOT data/dataset.parquet: "
             "a daily run must not clobber a hand-built dataset",
    )
    parser.add_argument(
        "--dataset", type=Path, default=None,
        help="skip the rebuild and use this prebuilt dataset as the played history. "
             "for iteration only - a scheduled run must rebuild, or it serves "
             "features as of whenever that file was made",
    )
    parser.add_argument(
        "--models-dir", type=Path, default=MODELS_DIR,
        help="where the pinned artifact lives",
    )
    parser.add_argument(
        "--rosters", type=Path, default=None,
        help="csv of roster assignments (nba_player_id, team_id) instead of "
             "player_team_stints",
    )
    args = parser.parse_args(argv)
    if args.extended_days < args.window_days:
        parser.error(
            f"--extended-days ({args.extended_days}) must be at least --window-days "
            f"({args.window_days})"
        )
    return args


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915 - it is a pipeline
    args = parse_args(argv)
    setup_logging(args.verbose)
    started = time.monotonic()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _run(args, started)
    except NothingToDo as exc:
        log.info("%s", "=" * 78)
        log.info("NOTHING TO DO: %s", exc)
        log.info("%s", "=" * 78)
        print("--- DAILY RUN ---")
        print(f"result    : no-op ({exc})")
        print("wrote     : nothing")
        print(f"elapsed   : {time.monotonic() - started:.1f}s")
        return 0
    except PhaseFailure as exc:
        log.error("%s", "=" * 78)
        log.error("DAILY RUN FAILED in phase %r: %s", exc.phase, exc.cause)
        log.error("%s", "=" * 78)
        print("--- DAILY RUN ---")
        print(f"result    : FAILED in phase {exc.phase!r}")
        print(f"reason    : {exc.cause}")
        print(f"elapsed   : {time.monotonic() - started:.1f}s")
        return 1


def _run(args: argparse.Namespace, started: float) -> int:  # noqa: PLR0915
    # ---- preflight ------------------------------------------------------
    with phase("preflight"):
        log.info("frozen protocol : %s", PROSPECTIVE_2026_27["protocol_version"])
        log.info("pinned artifact : models/%s", PROSPECTIVE_MODEL_VERSION)
        bad = verify_pinned_artifact(args.models_dir)
        if bad:
            raise PhaseFailure(
                "preflight",
                f"the pinned serving artifact does not match the freeze: "
                f"{', '.join(bad)}. refusing to serve. either the artifact moved "
                f"(revert it) or the freeze was edited (see MODEL.md 13.2)",
            )
        log.info(
            "artifact ok     : %d files match config.PROSPECTIVE_ARTIFACT_CHECKSUMS",
            len(PROSPECTIVE_ARTIFACT_CHECKSUMS),
        )
        artifact_verified = True

    # ---- window ---------------------------------------------------------
    with phase("window"):
        window_start, window_end = prediction_window(
            args.window_days, args.window_start
        )
        _, extended_end = prediction_window(args.extended_days, args.window_start)
        log.info(
            "window          : %s .. %s  (%d date(s), US/Eastern)",
            window_start, window_end, args.window_days,
        )
        log.info(
            "extended window : %s .. %s  (%d date(s), %s)",
            window_start, extended_end, args.extended_days,
            ", ".join(SLATE_SEASON_TYPES),
        )
        if args.window_start:
            log.warning(
                "--window-start %s overrides today; this is the testing escape hatch",
                args.window_start,
            )

    # ---- schedule -------------------------------------------------------
    with phase("schedule"):
        schedule, queried = load_window_schedule(window_start, extended_end)
        if schedule.empty:
            if queried:
                raise NothingToDo(
                    f"all {queried} game(s) in window {window_start} .. "
                    f"{extended_end} are already final or postponed; there is nothing "
                    f"left to forecast and a played game is never backfilled (13.8.2)"
                )
            raise NothingToDo(
                f"no games in window {window_start} .. {extended_end} "
                f"(season types: {', '.join(SLATE_SEASON_TYPES)})"
            )
        publish_at = pd.Timestamp.now("UTC")
        schedule, tipped = drop_tipped_off(schedule, publish_at)
        if len(tipped):
            log.warning(
                "%d game(s) in the window have already tipped and will NOT be "
                "predicted (13.8.2, missed slate recorded not backfilled): %s",
                len(tipped), ", ".join(sorted(tipped["GAME_ID"].astype(str))),
            )
        if schedule.empty:
            raise NothingToDo(
                f"every game in {window_start} .. {extended_end} has already tipped; "
                f"a post-tipoff prediction is never inserted (13.8.2)"
            )
        log.info(
            "servable slate  : %d game(s) over %d date(s), season(s) %s",
            schedule["GAME_ID"].nunique(), schedule["GAME_DATE"].nunique(),
            ", ".join(sorted(schedule["SEASON"].astype(str).unique())),
        )

    # ---- dataset --------------------------------------------------------
    with phase("dataset"):
        logs_through, schedule_through = load_freshness(window_start)
        log.info(
            "truth layer     : game logs through %s, schedule through %s",
            logs_through, schedule_through,
        )
        stale = staleness_warning(logs_through, schedule_through)
        if stale:
            log.warning("%s", stale)
            log.warning(
                "continuing anyway: stale form is a worse projection than fresh "
                "form and a much better one than none. the message above is "
                "recorded in prediction_runs.notes"
            )
        else:
            log.info("truth layer is fresh enough to serve from")

        if args.dataset is not None:
            dataset_path = args.dataset
            log.warning(
                "--dataset %s given: SKIPPING the rebuild. features are as of "
                "whenever this file was built, not as of now", dataset_path,
            )
        else:
            dataset_path = args.out_dir / "dataset.parquet"
            log.info("rebuilding the historical dataset from postgres -> %s",
                     dataset_path)
            # --no-v4-candidate: the served feature contract is v3 (51 columns, the
            # frozen digest) and the P2 candidate family is not in it, so building it
            # would buy nothing and cost an extra LightGBM cross-fit over every
            # team-game. MODEL.md 15 is why the candidate is not served.
            code = build_dataset.main([
                "--source", "postgres",
                "--out", str(dataset_path),
                "--no-v4-candidate",
            ])
            if code != 0:
                raise PhaseFailure("dataset", f"build_dataset exited {code}")

        history = history_from_dataset(load_dataset(dataset_path))
        # the prospective frames carry no played rows, so predict.py is told this.
        history_through = history["GAME_DATE"].max().date()
        log.info(
            "played history  : %d rows, %s .. %s, %d players",
            len(history), history["GAME_DATE"].min().date(),
            history["GAME_DATE"].max().date(), history["PLAYER_ID"].nunique(),
        )

    # ---- prospective ----------------------------------------------------
    with phase("prospective"):
        rosters, roster_source = load_rosters(args.rosters)
        log.info("rosters         : %s", roster_source)
        positions = load_positions()
        future = prospective_universe(
            schedule, rosters, window_start, extended_end, positions=positions
        )
        features = build_prospective_features(history, future)
        extended_path = args.out_dir / "prospective_extended.parquet"
        features.to_parquet(extended_path, index=False)
        log.info(
            "prospective     : %d rows, %d games, %d players -> %s",
            len(features), features["GAME_ID"].nunique(),
            features["PLAYER_ID"].nunique(), extended_path,
        )

    # ---- statuses -------------------------------------------------------
    with phase("statuses"):
        statuses_as_of = pd.Timestamp.now("UTC")
        statuses = load_statuses(statuses_as_of)
        statuses_path: Path | None = None
        if len(statuses):
            statuses_path = args.out_dir / "statuses.parquet"
            statuses.to_parquet(statuses_path, index=False)
            counts = statuses["status_normalized"].value_counts(dropna=False)
            log.info("injury reports  : %d designations as of %s",
                     len(statuses), statuses_as_of)
            for label, count in counts.items():
                log.info("    %-14s %d", str(label), int(count))
        else:
            log.warning(
                "player_injury_reports has no designation as of %s; this run is "
                "MODEL-ONLY. that is today's reality (13.9 F10) and is recorded as "
                "report_count=0 in the horizon facts", statuses_as_of,
            )

    # ---- predict --------------------------------------------------------
    runs: list[dict[str, object]] = []
    with phase("predict"):
        # THE SECOND POST-TIPOFF FILTER, and the load-bearing one. The phases above
        # take minutes; a 7pm tip does not wait for them. It runs once against the
        # shared frame, immediately before either run is scored.
        publish_at = pd.Timestamp.now("UTC")
        features, late = drop_tipped_off(features, publish_at)
        if len(late):
            log.warning(
                "%d row(s) over %d game(s) tipped off DURING this run and are "
                "dropped: %s", len(late), late["GAME_ID"].nunique(),
                ", ".join(sorted(late["GAME_ID"].astype(str).unique())),
            )
            features.to_parquet(extended_path, index=False)
        if features.empty:
            raise NothingToDo(
                "every game tipped off during the run; nothing left to publish "
                "(13.8.2)"
            )

        universe_source = str(features["UNIVERSE_SOURCE"].iloc[0])

        # RUN A, the frozen prospective run. Same window, same season type, same
        # horizon, same notes logic as when it was the only run.
        prospective_features = split_prospective(
            features, schedule, window_start, window_end
        )
        if prospective_features.empty:
            log.info(
                "run A skipped   : no Regular Season game in %s .. %s (preseason "
                "or offseason); that is not a failure", window_start, window_end,
            )
            runs.append({"name": "prospective", "skipped": True})
        else:
            prospective_path = args.out_dir / "prospective.parquet"
            prospective_features.to_parquet(prospective_path, index=False)
            served = schedule[
                schedule["GAME_ID"].isin(set(prospective_features["GAME_ID"]))
            ]
            reasons = prospective_conditions(
                seasons=served["SEASON"].astype(str),
                season_types=served["SEASON_TYPE"].astype(str),
                horizon=PROSPECTIVE_SERVING_HORIZON,
                model_version=PROSPECTIVE_MODEL_VERSION,
                feature_version=PROSPECTIVE_FEATURE_VERSION,
                universe_source=universe_source,
                artifact_verified=artifact_verified,
            )
            notes = run_notes(reasons, stale)
            if reasons:
                log.warning(
                    "run A does NOT qualify for the frozen prospective label: %s",
                    "; ".join(reasons),
                )
            else:
                log.info("run A QUALIFIES for %s", PROSPECTIVE_RUN_NOTE_LABEL)
            run_a = _publish_run(
                args, "prospective", prospective_path,
                args.out_dir / "predictions.parquet", notes,
                PROSPECTIVE_SERVING_HORIZON, window_start, statuses_as_of,
                statuses_path, history_through,
            )
            runs.append({**run_a, "reasons": reasons, "notes": notes})

        # RUN B, everything servable in the extended window, AFTER A so it is the
        # newest run and the one the app serves. No horizon: its games span days.
        served_all = schedule[schedule["GAME_ID"].isin(set(features["GAME_ID"]))]
        extended_conditions = prospective_conditions(
            seasons=served_all["SEASON"].astype(str),
            season_types=served_all["SEASON_TYPE"].astype(str),
            horizon=predict_script.NO_HORIZON,
            model_version=PROSPECTIVE_MODEL_VERSION,
            feature_version=PROSPECTIVE_FEATURE_VERSION,
            universe_source=universe_source,
            artifact_verified=artifact_verified,
        )
        extended = extended_notes(args.extended_days, extended_conditions, stale)
        run_b = _publish_run(
            args, "extended", extended_path,
            args.out_dir / "predictions_extended.parquet", extended,
            predict_script.NO_HORIZON, window_start, statuses_as_of,
            statuses_path, history_through,
        )
        runs.append({**run_b, "notes": extended})

    # ---- summary --------------------------------------------------------
    print("--- DAILY RUN ---")
    print(f"window    : {window_start} .. {window_end}  (US/Eastern, prospective)")
    print(f"extended  : {window_start} .. {extended_end}  "
          f"({', '.join(SLATE_SEASON_TYPES)})")
    print(f"artifact  : {PROSPECTIVE_MODEL_VERSION}  "
          f"(feature_version {PROSPECTIVE_FEATURE_VERSION}, "
          f"horizon {PROSPECTIVE_SERVING_HORIZON})")
    print(f"rosters   : {roster_source}")
    print(f"truth     : game logs through {logs_through}"
          + ("   <-- STALE" if stale else ""))
    print(f"tipped    : {len(tipped):,} already tipped, "
          f"{late['GAME_ID'].nunique():,} tipped during the run")
    print(f"injury    : {len(statuses):,} designations as of "
          f"{statuses_as_of.isoformat(timespec='seconds')}")
    for run in runs:
        name = str(run["name"])
        if run.get("skipped"):
            print(f"[{name}] SKIPPED (no Regular Season game in the prospective window)")
            continue
        predictions = pd.read_parquet(Path(str(run["path"])))
        print(f"[{name}] games {predictions['GAME_ID'].nunique():,}, "
              f"rows {len(predictions):,} player-games, "
              f"players {predictions['PLAYER_ID'].nunique():,}")
        if name == "prospective":
            why = run.get("reasons") or []
            print(f"[{name}] prospective: "
                  f"{'YES' if not why else 'no - ' + '; '.join(why)}")
        print(f"[{name}] notes: {run['notes']}")
        for column in ("P_PLAY", "E_MIN", "E_PTS", "E_REB", "E_AST"):
            if column in predictions.columns:
                print(f"[{name}] mean {column:<9s}: {predictions[column].mean():.4f}")
        if args.dry_run:
            would = len(predictions) * _rows_per_player_game(predictions)
            print(f"[{name}] WOULD WRITE: 1 prediction_runs row + ~{would:,} "
                  f"player_game_predictions rows")
        else:
            print(f"[{name}] run id: {run['run_id']}")
    if args.dry_run:
        print("wrote     : nothing to the database (--dry-run)")
    print(f"parquets  -> {args.out_dir}")
    print(f"elapsed   : {time.monotonic() - started:.1f}s")
    return 0


def predict_argv(
    dataset_path: Path,
    models_dir: Path,
    out_path: Path,
    notes: str,
    horizon: str,
    window_start: date,
    statuses_as_of: pd.Timestamp,
    statuses_path: Path | None,
    history_through: date | None,
    write_db: bool,
) -> list[str]:
    """the predict.py argv for one daily run, always on the production channel."""
    argv = [
        "--dataset", str(dataset_path),
        "--version", PROSPECTIVE_MODEL_VERSION,
        "--models-dir", str(models_dir),
        "--out", str(out_path),
        "--run-at", str(window_start),
        "--horizon", horizon,
        "--notes", notes,
        "--statuses-as-of", statuses_as_of.isoformat(),
        "--channel", PRODUCTION_CHANNEL,
    ]
    if history_through is not None:
        argv += ["--history-through", str(history_through)]
    if statuses_path is not None:
        argv += ["--statuses", str(statuses_path)]
    if write_db:
        argv.append("--write-db")
    return argv


def _publish_run(
    args: argparse.Namespace,
    name: str,
    dataset_path: Path,
    out_path: Path,
    notes: str,
    horizon: str,
    window_start: date,
    statuses_as_of: pd.Timestamp,
    statuses_path: Path | None,
    history_through: date | None = None,
) -> dict[str, object]:
    """score one frame through predict.py and report where it went."""
    log.info("run %s notes : %s", name, notes)
    registry_path = args.models_dir / "registry.json"
    entry = registry.find(PROSPECTIVE_MODEL_VERSION, registry_path)
    runs_before = len(entry.get("prediction_runs", []) if entry else [])

    predict_args = predict_argv(
        dataset_path, args.models_dir, out_path, notes, horizon, window_start,
        statuses_as_of, statuses_path, history_through, write_db=not args.dry_run,
    )
    if args.dry_run:
        log.warning("--dry-run: predict.py will NOT be given --write-db")

    log.info("predict.py %s", " ".join(predict_args))
    code = predict_script.main(predict_args)
    if code != 0:
        raise PhaseFailure("predict", f"predict.py ({name}) exited {code}")

    run_id: object = "(dry run, nothing written)"
    if not args.dry_run:
        entry = registry.find(PROSPECTIVE_MODEL_VERSION, registry_path)
        recorded = entry.get("prediction_runs", []) if entry else []
        if len(recorded) > runs_before:
            run_id = recorded[-1].get("run_id")
        else:
            log.warning(
                "predict.py reported success but the registry gained no "
                "prediction-run entry; the run id is unknown"
            )
            run_id = "(unknown: registry not updated)"
    return {"name": name, "path": out_path, "run_id": run_id}


def _rows_per_player_game(predictions: pd.DataFrame) -> int:
    """how many store rows one player-game becomes, for the dry run's estimate.

    Counted from ``store.build_prediction_rows``' own inputs rather than from the
    documented 62, so the estimate follows the target/quantile lists instead of
    needing to be re-derived every time one of them changes.
    """
    from fnba_ml.store import build_prediction_rows  # noqa: PLC0415

    if predictions.empty:
        return 0
    one = predictions.head(1)
    return len(
        build_prediction_rows(
            one, predict_script.TARGETS, predict_script.QUANTILE_LEVELS
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
