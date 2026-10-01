"""serving-time injury-report overrides on P(play).

THE PROBLEM THIS FIXES. the availability model is good (Brier 0.0734, -34% on the
shifted appearance rate) and it is good at the wrong thing for a player who has
been ruled out. it reads appearance history, rest, and schedule; it does not read
the injury report, because the report history only starts accumulating on
2026-08-16 and training on it before then would be leakage-by-imputation
(MODEL.md section 4). so a star ruled OUT an hour before tipoff still scored 0.93
to play, and the projections page showed him at 28 points. no amount of model
quality fixes that: the information is not in the features.

THE SHAPE OF THE FIX. a post-hoc layer, applied at serving time, after the model
has scored and before the rows are built. it is deliberately NOT a feature:

  - a feature would need history to train on, and there is none yet;
  - a feature would let the report's signal diffuse into the model's other
    coefficients, where it could not be audited or turned off;
  - an override is inspectable. every overridden row carries what the model said,
    what the override said, why, and the timestamp of the report it used, so a
    backtest can measure the layer separately from the model and eventually
    replace these hand-set numbers with learned ones.

THE NUMBERS ARE HAND-SET. every constant below is a judgement call from published
league-wide play-through rates and the asymmetry of the two mistakes, not an
estimate from our own data - we have none yet. they are module constants rather
than literals precisely so that the day the report history is deep enough, each
one becomes a measured quantity with a diff that shows exactly what changed.

AS-OF DISCIPLINE APPLIES HERE TOO. a report captured at or after the run's
information boundary must not be used, or a backtest of the T-24h horizon quietly
reads the T-60m report and looks prescient. :func:`apply_status_overrides` takes
``as_of`` and drops every report at or after it. this is the same rule the
training cutoff enforces, one layer further out.

REPORTS EXPIRE, AND CLEARANCES ARE ROWS. a recovered player simply drops off the
source page, so his last 'out' would otherwise stand indefinitely. two guards: the
scraper appends an explicit 'cleared' row for every player who left the page, and
a report older than ``REPORT_MAX_AGE_HOURS`` before ``as_of`` is ignored. 'cleared'
is a passthrough status, so a newer clearance supersedes an older 'out' and leaves
the model alone.

PURE. a frame in, a frame out. no database, no clock, no environment - predict.py
supplies both frames and the boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EXPIRE_UNAVAILABLE_STATUSES, GAME_SCOPED_STATUS_RESOLUTION
from .models import P_PLAY

log = logging.getLogger(__name__)

# ---- column contract ----
# the model's own probability, preserved on every row whether or not it was
# overridden. kept for two reasons: the override layer has to be measurable
# against the model it corrects, and a serving bug that drops the layer is
# invisible unless both numbers are on the record.
P_PLAY_MODEL = "P_PLAY_MODEL"
OVERRIDE_REASON = "OVERRIDE_REASON"
# captured_at of the report that produced the override. NOT the run timestamp: a
# 3-day-old "out" and a 20-minute-old "out" are different claims, and only this
# column can tell them apart after the fact.
STATUS_CAPTURED_AT = "STATUS_CAPTURED_AT"
STATUS_NORMALIZED = "STATUS_NORMALIZED"

STATUS_COLUMNS: tuple[str, ...] = ("nba_player_id", "status_normalized", "captured_at")
# optional: an absent column reads as a general report from an unknown source.
OPTIONAL_STATUS_COLUMNS: tuple[str, ...] = ("nba_game_id", "source")

# which report decided an overridden row: its scope (a report for this game, or a
# general designation) and the feed it came from.
STATUS_SCOPE = "STATUS_SCOPE"
STATUS_SOURCE = "STATUS_SOURCE"
SCOPE_GAME = "game"
SCOPE_GENERAL = "general"
SOURCE_OFFICIAL = "nba_official"
SOURCE_UNKNOWN = "unknown"

# ---- the policy constants ----
# OUT / SUSPENDED / G_LEAGUE. not 0.0, deliberately. an official "out" is
# occasionally reversed (a warmup goes well, a report is stale by an hour, a
# two-way player is recalled), and a hard zero makes the whole downstream product
# assert a certainty the report does not have - it also makes every calibration
# statistic on this bucket degenerate. 0.02 is "essentially no" while keeping the
# arithmetic and the Brier decomposition well behaved.
OUT_PROBABILITY: float = 0.02

# DOUBTFUL. the published league-wide play-through rate for doubtful sits in the
# 5-15% range depending on season and source; 0.10 is the middle of it. no blend
# with the model here: "doubtful" is a team's own statement about a specific game,
# and it dominates anything appearance history can say.
DOUBTFUL_PROBABILITY: float = 0.10

# QUESTIONABLE is the only bucket where the model still carries real information,
# because "questionable" is where teams put everyone they have not decided about -
# including stars who play 80% of the time and fringe players who play 30%. so it
# BLENDS rather than replaces: 0.6 x model + 0.4 x prior.
QUESTIONABLE_MODEL_WEIGHT: float = 0.6
# PLACEHOLDER. the league-wide empirical share of QUESTIONABLE designations that
# end in an appearance. 0.60 is the commonly cited figure across public
# analyses; it is NOT measured on our data and it is the single number in this
# module most likely to be wrong. it is also the first one that becomes learnable:
# one season of player_injury_reports joined to player_game_status measures it
# directly, and per-player and per-team versions after that.
LEAGUE_QUESTIONABLE_PLAY_RATE: float = 0.60

# PROBABLE. `0.85 x model + 0.15`, and it is a floor everywhere on [0, 1] rather
# than needing to be wrapped in a max() to become one.
#
# CORRECTED 2026-08-17 (P1b). The previous code and the previous policy table both
# read `max(model, 0.85 x model + 0.15)` and justified the max with "above ~0.99 the
# shifted form dips below the model's own number". That claim is false, and one line
# of algebra settles it:
#
#     0.85p + 0.15 >= p   <=>   0.15 >= 0.15p   <=>   p <= 1
#
# which holds for every probability. The two forms coincide at exactly p = 1 and the
# shifted form is strictly larger everywhere below it, so the max never once
# selected its first argument. It was dead code defended by a wrong reason, which is
# worse than dead code: a reader who trusted the comment would have believed the
# layer had a guard it did not need and would have looked for the guard's effect in
# the wrong place.
#
# The general statement, for whoever tunes these next: `w*p + s` is a floor on
# [0, 1] iff s >= 1 - w. Here 0.15 >= 0.15 with equality, which is the tight case -
# any smaller shift at this weight WOULD need the max, and any change to either
# constant has to re-check the inequality rather than assume it.
#
# what the numbers do: a bench player the model has at 0.40 is lifted to 0.49,
# because "probable" is a team saying he is expected to play; a star at 0.95 moves to
# 0.9575, a deliberate near-no-op. A probable designation must never be able to
# lower a projection, and now that property is a consequence of the arithmetic
# instead of a branch.
PROBABLE_MODEL_WEIGHT: float = 0.85
PROBABLE_SHIFT: float = 0.15

# a designation older than this says nothing about tonight; the CBS feed re-lists
# active injuries every scrape, so a report that has not been refreshed in three
# days is one the source has dropped.
REPORT_MAX_AGE_HOURS: float = 72.0

# under game-scoped resolution an official report beats a disagreeing cbs report
# unless the cbs one is newer by more than this. the official report is filed per
# game several times a day, so a gap this wide means it has stopped updating.
OFFICIAL_PRECEDENCE_HOURS: float = 6.0

# ---- the status vocabulary ----
# migration 013 normalises to: out, doubtful, questionable, probable, day_to_day,
# available, unknown. the extra names here are the ones a widened scraper will
# start emitting (suspensions and G-League assignments are not injuries but have
# the same serving consequence) plus the wordings that reliably mean one of the
# above.
STATUS_OUT = "out"
STATUS_SUSPENDED = "suspended"
STATUS_G_LEAGUE = "g_league"
STATUS_DOUBTFUL = "doubtful"
STATUS_QUESTIONABLE = "questionable"
STATUS_PROBABLE = "probable"
STATUS_CLEARED = "cleared"

# aliases collapse source wording onto the vocabulary above, applied AFTER case,
# whitespace and hyphen normalisation - so "G-League", "g league" and "g_league"
# all arrive here as "g_league" and only genuinely different words need an entry.
# anything not listed and not a vocabulary member is treated as unlisted, i.e. the
# model stands. that is the safe default: a status nobody anticipated should not
# silently pick up whichever rule happens to be nearest.
STATUS_ALIASES: dict[str, str] = {
    "out_for_season": STATUS_OUT,
    "inactive": STATUS_OUT,
    "suspension": STATUS_SUSPENDED,
    "gleague": STATUS_G_LEAGUE,
    "g_league_assignment": STATUS_G_LEAGUE,
    "gtd": STATUS_QUESTIONABLE,
    "game_time_decision": STATUS_QUESTIONABLE,
}

# statuses that mean "he is not playing tonight" for three different reasons that
# all reduce to the same number.
UNAVAILABLE_STATUSES: frozenset[str] = frozenset(
    {STATUS_OUT, STATUS_SUSPENDED, STATUS_G_LEAGUE}
)

# DELIBERATELY NOT OVERRIDDEN: 'available', 'day_to_day' (a roster note, not a
# statement about tonight's game - the CBS-style feeds attach it to players who then
# play), 'cleared' (the scraper's row for a player who left the report), 'unknown',
# and anything unlisted.
#
# [PLANNED] 'AVAILABLE' IS NOT INFORMATIONALLY NULL, and the previous justification
# for passing it through - "the model is already answering that question with more
# information than the label carries" - is only true of a player who was never on the
# report. A player who appears on the report AS 'available' is a different animal: he
# was listed questionable or doubtful at some earlier capture and has since been
# CLEARED. That transition carries information the model provably does not have,
# because the model does not read the report at all, and it points the opposite way
# from the appearance history a recent absence has just depressed.
#
# The honest fix needs the report HISTORY, not the latest row: 'available' after a
# 'questionable' is a clearance, 'available' with no prior designation this cycle is
# noise, and only a query over the player's report sequence can tell them apart.
# player_injury_reports is append-only and started accumulating on 2026-08-16, so the
# sequence exists going forward and does not exist for any backtest. Logged as future
# work rather than guessed at: an 'available' rule invented from nothing would be a
# third hand-set constant with no measurement behind it, and this module already has
# five.
PASSTHROUGH_STATUSES: frozenset[str] = frozenset(
    {"available", "day_to_day", STATUS_CLEARED, "unknown"}
)

# the game-specific designations that go stale within days. with
# expire_unavailable=False only these expire; an out stands until a clearance row
# or a newer designation replaces it.
FAST_DECAY_STATUSES: frozenset[str] = frozenset(
    {STATUS_DOUBTFUL, STATUS_QUESTIONABLE, STATUS_PROBABLE}
)


def normalise_status(value: object) -> str:
    """source wording -> the vocabulary above. unrecognised text passes through.

    passing unrecognised text through rather than mapping it to 'unknown' keeps the
    distinction between "the source said something we do not handle" and "the
    source said it does not know", which is the difference between a scraper bug
    and a real designation.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return STATUS_ALIASES.get(text, text)


@dataclass(frozen=True)
class StatusPolicy:
    """the override table, as substitutable data rather than branches in a loop.

    a frozen dataclass so a caller can pass a variant (a backtest sweeping the
    questionable prior, say) without touching the module, and so the values used
    by a run can be recorded verbatim.
    """

    out_probability: float = OUT_PROBABILITY
    doubtful_probability: float = DOUBTFUL_PROBABILITY
    questionable_model_weight: float = QUESTIONABLE_MODEL_WEIGHT
    questionable_prior: float = LEAGUE_QUESTIONABLE_PLAY_RATE
    probable_model_weight: float = PROBABLE_MODEL_WEIGHT
    probable_shift: float = PROBABLE_SHIFT

    def probability(self, status: str, model_probability: float) -> float | None:
        """the overridden P(play), or None to mean "leave the model alone"."""
        if status in UNAVAILABLE_STATUSES:
            return self.out_probability
        if status == STATUS_DOUBTFUL:
            return self.doubtful_probability
        if status == STATUS_QUESTIONABLE:
            return (
                self.questionable_model_weight * model_probability
                + (1.0 - self.questionable_model_weight) * self.questionable_prior
            )
        if status == STATUS_PROBABLE:
            # a floor by arithmetic, not by max(): w*p + s >= p for all p in [0, 1]
            # whenever s >= 1 - w, and the defaults satisfy it with equality. See the
            # PROBABLE_* constants for the algebra and for why the max that used to be
            # here was provably unreachable.
            return (
                self.probable_model_weight * model_probability + self.probable_shift
            )
        return None

    def as_dict(self) -> dict[str, float]:
        """json-serialisable, for the run's provenance."""
        return {
            "out_probability": self.out_probability,
            "doubtful_probability": self.doubtful_probability,
            "questionable_model_weight": self.questionable_model_weight,
            "questionable_prior": self.questionable_prior,
            "probable_model_weight": self.probable_model_weight,
            "probable_shift": self.probable_shift,
        }


DEFAULT_POLICY = StatusPolicy()


def reason_for(status: str) -> str:
    return f"status_{status}"


# numeric codes, because player_game_predictions.value is NUMERIC NOT NULL and
# migration 014's schema is not being changed for this. the reason is recoverable
# from the stored run without a text column; the mapping is append-only, so a code
# never changes meaning.
OVERRIDE_REASON_CODES: dict[str, int] = {
    reason_for(STATUS_OUT): 1,
    reason_for(STATUS_DOUBTFUL): 2,
    reason_for(STATUS_QUESTIONABLE): 3,
    reason_for(STATUS_PROBABLE): 4,
    reason_for(STATUS_SUSPENDED): 5,
    reason_for(STATUS_G_LEAGUE): 6,
}


def _to_naive_utc(values) -> pd.Series:
    """timestamps comparable regardless of what the source attached to them.

    postgres hands back TIMESTAMPTZ, a parquet round-trip may or may not keep the
    zone, and a hand-written csv has none. comparing a tz-aware series to a naive
    boundary raises; silently coercing in one direction would shift a report
    across the cutoff. everything is normalised to UTC and then made naive, so the
    comparison is always between the same kind of thing.
    """
    parsed = pd.to_datetime(pd.Series(values), errors="coerce", utc=True)
    return parsed.dt.tz_localize(None)


def _boundary(as_of: pd.Timestamp | None) -> pd.Timestamp | None:
    if as_of is None:
        return None
    boundary = pd.Timestamp(as_of)
    if boundary.tzinfo is not None:
        boundary = boundary.tz_convert("UTC").tz_localize(None)
    return boundary


def _text_or_none(value: object) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def _prepare_statuses(statuses: pd.DataFrame) -> pd.DataFrame:
    """the frame normalised: text ids, vocabulary statuses, naive utc, scope, source."""
    missing = [c for c in STATUS_COLUMNS if c not in statuses.columns]
    if missing:
        raise ValueError(
            f"statuses frame is missing {', '.join(missing)}; expected columns "
            f"{', '.join(STATUS_COLUMNS)}"
        )

    frame = statuses.copy()
    frame["nba_player_id"] = frame["nba_player_id"].astype(str)
    frame[STATUS_NORMALIZED] = frame["status_normalized"].map(normalise_status)
    frame["captured_at"] = _to_naive_utc(frame["captured_at"])
    frame = frame[frame["captured_at"].notna()].copy()

    if "nba_game_id" in frame.columns:
        game = frame["nba_game_id"].map(_text_or_none)
    else:
        game = pd.Series([None] * len(frame), index=frame.index, dtype=object)
    frame["nba_game_id"] = game.astype(object)
    if "source" in frame.columns:
        source = frame["source"].map(_text_or_none).fillna(SOURCE_UNKNOWN)
    else:
        source = pd.Series(SOURCE_UNKNOWN, index=frame.index, dtype=object)
    frame["source"] = source.astype(object)
    frame[STATUS_SCOPE] = np.where(
        frame["nba_game_id"].notna(), SCOPE_GAME, SCOPE_GENERAL
    ).astype(object)
    frame[STATUS_SOURCE] = frame["source"]
    return frame


def _drop_future(frame: pd.DataFrame, boundary: pd.Timestamp | None) -> pd.DataFrame:
    if boundary is None:
        return frame
    future = frame["captured_at"] >= boundary
    if future.any():
        log.info(
            "ignoring %d injury reports captured at or after the run's "
            "information boundary %s", int(future.sum()), boundary,
        )
    return frame[~future]


def _expired(
    frame: pd.DataFrame,
    boundary: pd.Timestamp | None,
    max_age_hours: float | None,
    expire_unavailable: bool,
) -> pd.Series:
    """true where a report is past its age limit and its status is one that expires."""
    if boundary is None or max_age_hours is None:
        return pd.Series(False, index=frame.index)
    stale = frame["captured_at"] < boundary - pd.Timedelta(hours=max_age_hours)
    if not expire_unavailable:
        stale = stale & frame[STATUS_NORMALIZED].isin(FAST_DECAY_STATUSES)
    if stale.any():
        log.info(
            "ignoring %d injury reports captured more than %s hours before %s",
            int(stale.sum()), max_age_hours, boundary,
        )
    return stale


def latest_statuses(
    statuses: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    max_age_hours: float | None = REPORT_MAX_AGE_HOURS,
) -> pd.DataFrame:
    """one row per player: the newest report KNOWN at ``as_of``.

    reports at or after ``as_of`` are dropped, not merely deprioritised. that is
    the whole point - the T-24h run must be scored against the report that
    existed at T-24h, and "use the newest one but pretend it is older" is the
    exact error this guard exists to prevent.

    reports captured more than ``max_age_hours`` before ``as_of`` are dropped as
    stale. ``max_age_hours=None`` disables that, and without ``as_of`` there is
    no reference point so no report expires.
    """
    return resolve_statuses(statuses, as_of, None, max_age_hours)


def resolve_statuses(
    statuses: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    game_ids: pd.Series | None = None,
    max_age_hours: float | None = REPORT_MAX_AGE_HOURS,
    *,
    expire_unavailable: bool = EXPIRE_UNAVAILABLE_STATUSES,
    game_scoped: bool = GAME_SCOPED_STATUS_RESOLUTION,
    precedence_hours: float = OFFICIAL_PRECEDENCE_HOURS,
) -> pd.DataFrame:
    """the report that decides each player, or each (player, game), at ``as_of``.

    with ``game_scoped`` off this is the newest admissible report per player and
    ``game_ids`` is ignored. with it on and ``game_ids`` supplied, the result has
    one row per (player, game) carrying ``GAME_ID``. MODEL.md 20.2 has the order.
    """
    frame = _prepare_statuses(statuses)
    boundary = _boundary(as_of)
    frame = _drop_future(frame, boundary)

    if not game_scoped:
        if expire_unavailable:
            frame = frame[~_expired(frame, boundary, max_age_hours, True)]
            return (
                frame.sort_values("captured_at")
                .drop_duplicates("nba_player_id", keep="last")
                .reset_index(drop=True)
            )
        # newest first, then expire, so an expired questionable cannot let an older
        # out resurface.
        newest = (
            frame.sort_values("captured_at", kind="mergesort")
            .drop_duplicates("nba_player_id", keep="last")
        )
        newest = newest[~_expired(newest, boundary, max_age_hours, False)]
        return newest.reset_index(drop=True)

    frame = frame.assign(
        _official=frame["source"].eq(SOURCE_OFFICIAL),
        _game_key=frame["nba_game_id"].fillna(""),
    )
    newest = (
        frame.sort_values("captured_at", kind="mergesort")
        .drop_duplicates(["nba_player_id", "_game_key", "_official"], keep="last")
    )
    newest = newest[~_expired(newest, boundary, max_age_hours, expire_unavailable)]
    gap = pd.Timedelta(hours=precedence_hours)

    candidates: dict[tuple[str, str], dict[bool, dict]] = {}
    for record in newest.to_dict("records"):
        key = (record["nba_player_id"], record["_game_key"])
        candidates.setdefault(key, {})[record["_official"]] = record
    tier = {key: _tier_winner(pair, gap) for key, pair in candidates.items()}
    players = sorted({player for player, _ in tier})

    if game_ids is None:
        # no game to scope to, so each player's newest game report stands in.
        winners: list[dict] = []
        for player in players:
            scoped = [r for (p, g), r in tier.items() if p == player and g]
            newest_game = max(scoped, key=lambda r: r["captured_at"]) if scoped else None
            winner = _cross_tier_winner(newest_game, tier.get((player, "")), gap)
            if winner is not None:
                winners.append(winner)
        return _resolved_frame(winners, newest.columns, with_game=False)

    games = sorted({g for g in pd.Series(game_ids).map(_text_or_none) if g is not None})
    rows: list[dict] = []
    for player in players:
        general = tier.get((player, ""))
        for game in games:
            winner = _cross_tier_winner(tier.get((player, game)), general, gap)
            if winner is not None:
                rows.append({**winner, "GAME_ID": game})
    return _resolved_frame(rows, newest.columns, with_game=True)


def _tier_winner(pair: dict[bool, dict], gap: pd.Timedelta) -> dict:
    """the newer of an official and a non-official report in one scope, except that
    a disagreeing non-official report needs to be newer by more than ``gap``."""
    official, other = pair.get(True), pair.get(False)
    if official is None or other is None:
        return official if other is None else other
    if other["captured_at"] <= official["captured_at"]:
        return official
    disagree = other[STATUS_NORMALIZED] != official[STATUS_NORMALIZED]
    if disagree and other["captured_at"] - official["captured_at"] <= gap:
        return official
    return other


def _cross_tier_winner(
    game: dict | None, general: dict | None, gap: pd.Timedelta
) -> dict | None:
    """the game report, unless it is official and a disagreeing non-official
    general report is newer by more than ``gap``."""
    if game is None or general is None:
        return general if game is None else game
    if (
        game["_official"]
        and not general["_official"]
        and general[STATUS_NORMALIZED] != game[STATUS_NORMALIZED]
        and general["captured_at"] - game["captured_at"] > gap
    ):
        return general
    return game


def _resolved_frame(rows: list[dict], columns: pd.Index, with_game: bool) -> pd.DataFrame:
    keep = [c for c in columns if not str(c).startswith("_")]
    if with_game:
        keep = [*keep, "GAME_ID"]
    frame = pd.DataFrame(rows, columns=keep)
    frame["captured_at"] = pd.to_datetime(frame["captured_at"])
    return frame.reset_index(drop=True)


@dataclass(frozen=True)
class OverrideResult:
    """the policy applied to one probability vector, with its provenance.

    factored out of :func:`apply_status_overrides` for feature_version v3, because
    the override now has to run TWICE in a serving run and on two different
    quantities:

      1. on the BASE probabilities p_j, before the expected teammate-context features
         are rebuilt. A star ruled OUT must lower his own p_j so that his TEAMMATES'
         expected vacated minutes rise - otherwise the serving path knows he is out
         and declines to act on it, which was the whole defect the override layer was
         built to fix, displaced one column to the left.
      2. on the FINAL P(play), which is what :func:`apply_status_overrides` has always
         done and still does.

    the same policy, the same as-of discipline and the same report both times; only
    the vector being corrected differs.
    """

    probability: np.ndarray
    status: np.ndarray
    captured_at: np.ndarray
    applies: np.ndarray
    scope: np.ndarray
    source: np.ndarray

    @property
    def n_applied(self) -> int:
        return int(self.applies.sum())


def resolve_overrides(
    player_ids: pd.Series,
    model_probability: np.ndarray,
    statuses: pd.DataFrame | None,
    policy: StatusPolicy = DEFAULT_POLICY,
    as_of: pd.Timestamp | None = None,
    *,
    max_age_hours: float | None = REPORT_MAX_AGE_HOURS,
    game_ids: pd.Series | None = None,
    expire_unavailable: bool = EXPIRE_UNAVAILABLE_STATUSES,
    game_scoped: bool = GAME_SCOPED_STATUS_RESOLUTION,
) -> OverrideResult:
    """the policy table, applied to one probability vector. no frame surgery.

    returns the original probabilities untouched where no admissible report applies,
    so the caller can always use the returned vector and never has to reconstruct the
    "and otherwise keep the model's" branch itself. ``game_ids`` (aligned with
    ``player_ids``) is read only when ``game_scoped`` is on.
    """
    model_probability = np.asarray(model_probability, dtype=float)
    n = len(model_probability)
    nothing = np.array([None] * n, dtype=object)
    empty = OverrideResult(
        probability=model_probability.copy(),
        status=nothing.copy(),
        captured_at=np.full(n, np.datetime64("NaT", "ns"), dtype="datetime64[ns]"),
        applies=np.zeros(n, dtype=bool),
        scope=nothing.copy(),
        source=nothing.copy(),
    )
    if statuses is None or len(statuses) == 0:
        return empty

    scoped = game_scoped and game_ids is not None
    latest = resolve_statuses(
        statuses, as_of, game_ids if scoped else None, max_age_hours,
        expire_unavailable=expire_unavailable, game_scoped=game_scoped,
    )
    if latest.empty:
        return empty

    ids = player_ids.astype(str)
    if scoped:
        keys = pd.MultiIndex.from_arrays([
            ids.to_numpy(dtype=object),
            pd.Series(game_ids).map(_text_or_none).to_numpy(dtype=object),
        ])
        found = latest.set_index(["nba_player_id", "GAME_ID"]).reindex(keys)
        status = found[STATUS_NORMALIZED].to_numpy()
        captured = pd.to_datetime(found["captured_at"]).to_numpy()
        scope = found[STATUS_SCOPE].to_numpy()
        source = found[STATUS_SOURCE].to_numpy()
    else:
        by_player = latest.set_index("nba_player_id")
        status = ids.map(by_player[STATUS_NORMALIZED]).to_numpy()
        captured = pd.to_datetime(ids.map(by_player["captured_at"])).to_numpy()
        scope = ids.map(by_player[STATUS_SCOPE]).to_numpy()
        source = ids.map(by_player[STATUS_SOURCE]).to_numpy()

    overridden = np.array(
        [
            policy.probability(s, p) if isinstance(s, str) else None
            for s, p in zip(status, model_probability)
        ],
        dtype=object,
    )
    applies = np.array([value is not None for value in overridden])
    probability = model_probability.copy()
    if applies.any():
        probability[applies] = np.clip(
            np.array([v for v in overridden[applies]], dtype=float), 0.0, 1.0
        )
    return OverrideResult(
        probability=probability,
        status=np.where(applies, status, None),
        captured_at=np.where(applies, captured, np.datetime64("NaT", "ns")),
        applies=applies,
        scope=np.where(applies, scope, None),
        source=np.where(applies, source, None),
    )


def _unconditional_pairs(predictions: pd.DataFrame) -> list[tuple[str, str]]:
    """[(unconditional column, conditional column)] present on the frame."""
    pairs = []
    for column in predictions.columns:
        if not (column.startswith("E_") and column.endswith("_COND")):
            continue
        unconditional = column[: -len("_COND")]
        if unconditional in predictions.columns:
            pairs.append((unconditional, column))
    return pairs


def apply_status_overrides(
    predictions: pd.DataFrame,
    statuses: pd.DataFrame | None,
    policy: StatusPolicy = DEFAULT_POLICY,
    as_of: pd.Timestamp | None = None,
    *,
    max_age_hours: float | None = REPORT_MAX_AGE_HOURS,
    expire_unavailable: bool = EXPIRE_UNAVAILABLE_STATUSES,
    game_scoped: bool = GAME_SCOPED_STATUS_RESOLUTION,
) -> pd.DataFrame:
    """apply the injury-report policy to a scored prediction frame.

    ``predictions`` is the output of predict.build_predictions: one row per
    scheduled player-game with ``P_PLAY``, ``E_<stat>_COND`` and ``E_<stat>``.
    ``statuses`` carries (nba_player_id, status_normalized, captured_at), and
    optionally nba_game_id and source, which game-scoped resolution reads against
    the frame's GAME_ID. STATUS_SCOPE and STATUS_SOURCE record which report decided.

    what changes, and what does not:

      P_PLAY            replaced by the policy's number for overridden rows.
      P_PLAY_MODEL      the model's original probability, on EVERY row.
      E_<stat>          RECOMPUTED as the new P(play) x the conditional estimate,
                        for overridden rows only. leaving it would ship an
                        unconditional number that contradicts the probability
                        printed beside it.
      E_<stat>_COND     UNTOUCHED. "how good a night if he plays" does not change
                        because he is less likely to play; that is what
                        conditional means. an override that moved it would be
                        double-counting the availability question.
      quantiles         UNTOUCHED, for the same reason - they wrap the conditional
                        estimate.

    a missing or empty statuses frame is an identity on every value: the
    provenance columns are still added (so the row builder sees one shape
    regardless), but no probability and no estimate moves.
    """
    out = predictions.copy()
    if P_PLAY not in out.columns:
        raise ValueError(f"prediction frame is missing {P_PLAY}; nothing to override")

    out[P_PLAY_MODEL] = out[P_PLAY].astype(float)
    out[OVERRIDE_REASON] = pd.Series([None] * len(out), index=out.index, dtype=object)
    out[STATUS_NORMALIZED] = pd.Series([None] * len(out), index=out.index, dtype=object)
    out[STATUS_CAPTURED_AT] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    out[STATUS_SCOPE] = pd.Series([None] * len(out), index=out.index, dtype=object)
    out[STATUS_SOURCE] = pd.Series([None] * len(out), index=out.index, dtype=object)

    if statuses is None or len(statuses) == 0:
        log.info("no injury statuses supplied; P(play) is the model's throughout")
        return out

    resolved = resolve_overrides(
        out["PLAYER_ID"], out[P_PLAY_MODEL].to_numpy(dtype=float),
        statuses, policy, as_of, max_age_hours=max_age_hours,
        game_ids=out["GAME_ID"] if "GAME_ID" in out.columns else None,
        expire_unavailable=expire_unavailable, game_scoped=game_scoped,
    )
    applies = resolved.applies

    if not applies.any():
        log.info(
            "no admissible injury status as of %s is an overriding one; model "
            "P(play) stands", as_of,
        )
        return out

    out.loc[applies, P_PLAY] = resolved.probability[applies]
    out.loc[applies, STATUS_NORMALIZED] = resolved.status[applies]
    out.loc[applies, STATUS_CAPTURED_AT] = resolved.captured_at[applies]
    out.loc[applies, STATUS_SCOPE] = resolved.scope[applies]
    out.loc[applies, STATUS_SOURCE] = resolved.source[applies]
    out.loc[applies, OVERRIDE_REASON] = [
        reason_for(s) for s in resolved.status[applies]
    ]

    # the unconditional estimate is P(play) x conditional BY DEFINITION, so it is
    # recomputed rather than scaled: scaling by the ratio of probabilities would
    # give the same answer on the happy path and quietly propagate any pre-existing
    # incoherence between the two columns. only overridden rows are touched, so a
    # bug that made the two disagree upstream stays visible instead of being
    # papered over here.
    for unconditional, conditional in _unconditional_pairs(out):
        out.loc[applies, unconditional] = (
            out.loc[applies, P_PLAY].to_numpy(dtype=float)
            * out.loc[applies, conditional].to_numpy(dtype=float)
        ).clip(0.0)

    counts = (
        out.loc[applies, OVERRIDE_REASON].value_counts().to_dict()
    )
    log.info("injury overrides applied to %d rows: %s", int(applies.sum()), counts)
    return out


def override_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    """per-reason counts and mean probability shift, for the run's stdout."""
    if OVERRIDE_REASON not in predictions.columns:
        return pd.DataFrame(columns=["reason", "rows", "mean_model_p", "mean_override_p"])
    hit = predictions[predictions[OVERRIDE_REASON].notna()]
    if hit.empty:
        return pd.DataFrame(columns=["reason", "rows", "mean_model_p", "mean_override_p"])
    return (
        hit.groupby(OVERRIDE_REASON)
        .agg(
            rows=(P_PLAY, "size"),
            mean_model_p=(P_PLAY_MODEL, "mean"),
            mean_override_p=(P_PLAY, "mean"),
        )
        .reset_index()
        .rename(columns={OVERRIDE_REASON: "reason"})
    )


def override_provenance_counts(predictions: pd.DataFrame) -> dict[str, dict[str, int]]:
    """overridden rows counted by the scope and the source of the deciding report."""
    counts: dict[str, dict[str, int]] = {"scope": {}, "source": {}}
    if OVERRIDE_REASON not in predictions.columns:
        return counts
    hit = predictions[predictions[OVERRIDE_REASON].notna()]
    for key, column in (("scope", STATUS_SCOPE), ("source", STATUS_SOURCE)):
        if column in hit.columns:
            values = hit[column].fillna(SOURCE_UNKNOWN).astype(str)
            counts[key] = {k: int(v) for k, v in sorted(values.value_counts().items())}
    return counts
