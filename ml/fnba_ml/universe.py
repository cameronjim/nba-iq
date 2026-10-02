"""the scheduled-player-game universe.

one row per (player who could have played, team-game). this is the unit of
analysis for everything downstream: a model trained only on recorded
appearances answers "how much given he plays", which is selection-biased for
fantasy (REPORT.md section 3e measures the penalty at 12.4% MAE).

two constructions, and they are not equivalent:

  1. STATUS-BASED (preferred, unbiased). one row per rostered player per
     team-game, straight out of ``player_game_status``. a player listed
     inactive for two months is present in every one of those team-games with
     PLAYED = 0, which is exactly what the availability model needs to see.

  2. APPROXIMATION (fallback, BIASED). eligibility inferred from game-log
     presence within a +/-15 day window. only for parquet-fixture mode, where
     no roster table exists. quantified in REPORT.md section 5: availability
     base rate inflated by at least +0.0192, absence streaks capped at 16
     consecutive team-games, and a model that over-predicts availability in
     every probability bin. every code path that reaches it logs a warning and
     stamps ``UNIVERSE_SOURCE = 'approximation'`` on the output.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import COMPETITION_COL, FALLBACK_ROSTER_WINDOW_DAYS, POSITION_GROUPS
from .data.schema import (
    STAT_COLS,
    normalise_dates,
    normalise_ids,
    postseason_rows,
    training_rows,
)

log = logging.getLogger(__name__)

SOURCE_STATUS = "status"
SOURCE_APPROXIMATION = "approximation"
SOURCE_POSTSEASON = "postseason"

# the player_game_status.source of a row, and the value the scraper stamps on
# the active-DNP rows the box-score pass inserts (MODEL.md 17.8).
STATUS_SOURCE_COL = "STATUS_SOURCE"
BOX_SCORE_STATUS_SOURCE = "boxscoretraditionalv3"

# the team's own game totals, carried under TEAM_-prefixed names so nothing can
# confuse a team total with the player's own line. these are the denominator of
# the usage-rate feature and are outcomes of the target game, which is why
# config.TARGET_COLS lists them: only a SHIFTED usage EWMA over prior games may
# read them.
TEAM_TOTAL_COLS: dict[str, str] = {
    "MIN": "TEAM_MIN",
    "FGA": "TEAM_FGA",
    "FTA": "TEAM_FTA",
    "TOV": "TEAM_TOV",
}

UNIVERSE_COLS: tuple[str, ...] = (
    "SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "TEAM_ABBREVIATION",
    "OPP_TEAM_ID", "IS_HOME", "TEAM_PTS", "TEAM_PTS_ALLOWED",
    *TEAM_TOTAL_COLS.values(),
    "PLAYER_ID", "PLAYER_NAME", "POSITION", "POS_GROUP",
    "PLAYED", "LISTED_INACTIVE", "UNIVERSE_SOURCE",
) + STAT_COLS


def position_group(position: pd.Series) -> pd.Series:
    """"PG,SG" -> "G". the FIRST listed position decides the bucket.

    a multi-position string is ordered primary-first by the source, so taking the
    head is taking the listed primary position rather than averaging two buckets
    into neither. an unrecognised or missing string yields ``pd.NA``, never a
    guess - the positional features are then null for that player's rows, which
    LightGBM handles as missing and which a mislabelled bucket would not.
    """
    primary = (
        position.astype("string")
        .str.strip()
        .str.upper()
        .str.split(",").str[0]
        .str.strip()
    )
    return primary.map(POSITION_GROUPS).astype("string")


def team_game_frame(schedule: pd.DataFrame, team_logs: pd.DataFrame) -> pd.DataFrame:
    """one row per (team, game) with opponent, home flag and points allowed.

    built from the schedule rather than from game logs so that a team-game
    exists before any box score does - the precondition for same-day
    predictions (REPORT.md section 6, implication 2).
    """
    sched = normalise_dates(normalise_ids(schedule))

    home = sched.rename(columns={"HOME_TEAM_ID": "TEAM_ID", "AWAY_TEAM_ID": "OPP_TEAM_ID"})
    home["IS_HOME"] = 1
    away = sched.rename(columns={"AWAY_TEAM_ID": "TEAM_ID", "HOME_TEAM_ID": "OPP_TEAM_ID"})
    away["IS_HOME"] = 0

    cols = ["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "OPP_TEAM_ID", "IS_HOME"]
    if COMPETITION_COL in sched.columns:
        cols.append(COMPETITION_COL)
    tg = pd.concat([home[cols], away[cols]], ignore_index=True)

    tl = normalise_dates(normalise_ids(team_logs))
    pts = tl[["GAME_ID", "TEAM_ID", "PTS"]].drop_duplicates(["GAME_ID", "TEAM_ID"])
    tg = tg.merge(pts.rename(columns={"PTS": "TEAM_PTS"}), on=["GAME_ID", "TEAM_ID"], how="left")
    tg = tg.merge(
        pts.rename(columns={"TEAM_ID": "OPP_TEAM_ID", "PTS": "TEAM_PTS_ALLOWED"}),
        on=["GAME_ID", "OPP_TEAM_ID"],
        how="left",
    )

    # the team's own possession totals, for the usage denominator. own team only:
    # a player's usage share is measured against his own team's possessions, and
    # an opponent copy would be four unused columns on 147k rows.
    present = [c for c in TEAM_TOTAL_COLS if c in tl.columns]
    if present:
        totals = tl[["GAME_ID", "TEAM_ID", *present]].drop_duplicates(["GAME_ID", "TEAM_ID"])
        tg = tg.merge(
            totals.rename(columns={c: TEAM_TOTAL_COLS[c] for c in present}),
            on=["GAME_ID", "TEAM_ID"],
            how="left",
        )
    missing = [c for c in TEAM_TOTAL_COLS if c not in present]
    if missing:
        log.warning(
            "team logs carry no %s; the usage-rate feature will be null",
            ", ".join(missing),
        )

    if "TEAM_ABBREVIATION" in tl.columns:
        abbr = tl[["TEAM_ID", "TEAM_ABBREVIATION"]].drop_duplicates("TEAM_ID")
        tg = tg.merge(abbr, on="TEAM_ID", how="left")
    else:
        tg["TEAM_ABBREVIATION"] = pd.NA

    return tg.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)


def _attach_outcomes(
    eligibility: pd.DataFrame,
    team_games: pd.DataFrame,
    player_logs: pd.DataFrame,
    source: str,
    positions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """join schedule context and the observed box score onto eligible rows."""
    universe = eligibility.merge(
        team_games,
        on=["GAME_ID", "TEAM_ID"],
        how="left",
        suffixes=("", "_sched"),
    )

    stats = [c for c in STAT_COLS if c in player_logs.columns]
    actual = player_logs[["PLAYER_ID", "GAME_ID", "TEAM_ID", "PLAYER_NAME"] + stats].copy()
    actual = actual.drop_duplicates(["PLAYER_ID", "GAME_ID", "TEAM_ID"])
    actual["_APPEARED"] = 1

    universe = universe.merge(actual, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left")

    if "PLAYED" in universe.columns:
        # status supplied a played flag; only fill the gaps from log presence
        universe["PLAYED"] = (
            universe["PLAYED"].astype("Float64").fillna(universe["_APPEARED"].astype("Float64"))
        )
    else:
        universe["PLAYED"] = universe["_APPEARED"]
    universe["PLAYED"] = universe["PLAYED"].fillna(0).astype(int)
    universe = universe.drop(columns=["_APPEARED"])

    for col in STAT_COLS:
        universe[col] = pd.to_numeric(universe.get(col), errors="coerce").fillna(0.0)

    names = player_logs.dropna(subset=["PLAYER_NAME"]).drop_duplicates("PLAYER_ID")
    universe["PLAYER_NAME"] = universe["PLAYER_NAME"].fillna(
        universe["PLAYER_ID"].map(names.set_index("PLAYER_ID")["PLAYER_NAME"])
    )

    # positions: reference data, left-joined so an unmatched player keeps a null
    # bucket. a source with no positions at all still gets the columns, so the
    # frame shape does not depend on which source built it.
    if positions is not None and len(positions) > 0:
        pos = normalise_ids(positions).drop_duplicates("PLAYER_ID")
        universe = universe.merge(
            pos[["PLAYER_ID", "POSITION"]], on="PLAYER_ID", how="left"
        )
    else:
        universe["POSITION"] = pd.NA
    universe["POS_GROUP"] = position_group(universe["POSITION"])

    if "LISTED_INACTIVE" not in universe.columns:
        universe["LISTED_INACTIVE"] = pd.NA
    universe["LISTED_INACTIVE"] = universe["LISTED_INACTIVE"].astype("boolean")

    universe["UNIVERSE_SOURCE"] = source
    # STATUS_SOURCE and COMPETITION ride along outside UNIVERSE_COLS so datasets
    # built before them still satisfy the prospective-path column contract.
    keep = [c for c in UNIVERSE_COLS if c in universe.columns]
    for extra in (STATUS_SOURCE_COL, COMPETITION_COL):
        if extra in universe.columns:
            keep.append(extra)
    universe = universe[keep]
    return universe.sort_values(
        ["GAME_DATE", "GAME_ID", "TEAM_ID", "PLAYER_ID"]
    ).reset_index(drop=True)


def universe_from_status(
    schedule: pd.DataFrame,
    team_logs: pd.DataFrame,
    player_logs: pd.DataFrame,
    status: pd.DataFrame,
    positions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """preferred construction: rostered players per team-game, unapproximated.

    LISTED_INACTIVE is carried through as of feature_version v2. it is the
    official-inactive-list flag and it is the ABSENCE SET the vacated-resource
    features aggregate over - for a row's TEAMMATES. it is in
    ``config.TARGET_COLS`` because for the row's own player it is the availability
    answer, not a feature (see :mod:`fnba_ml.teammates`).
    """
    # training rows only: a playoff or preseason game must never become a modelled row.
    all_games = set(normalise_ids(schedule)["GAME_ID"])
    schedule, team_logs, player_logs = (
        training_rows(schedule), training_rows(team_logs), training_rows(player_logs)
    )
    excluded_games = all_games - set(normalise_ids(schedule)["GAME_ID"])
    tg = team_game_frame(schedule, team_logs)
    st = normalise_ids(status)
    if excluded_games:
        st = st[~st["GAME_ID"].isin(excluded_games)]

    rostered = st["ROSTERED"].astype("boolean").fillna(True)
    keep = ["PLAYER_ID", "GAME_ID", "TEAM_ID", "PLAYED"]
    if "LISTED_INACTIVE" in st.columns:
        keep.append("LISTED_INACTIVE")
    if STATUS_SOURCE_COL in st.columns:
        keep.append(STATUS_SOURCE_COL)
    elig = st.loc[rostered, keep].copy()
    elig["PLAYED"] = elig["PLAYED"].astype("boolean").astype("Float64")
    if "LISTED_INACTIVE" in elig.columns:
        elig["LISTED_INACTIVE"] = elig["LISTED_INACTIVE"].astype("boolean")

    # a status row for a game that is not on the schedule is a data-truth bug,
    # not something to silently model around
    known = set(zip(tg["GAME_ID"], tg["TEAM_ID"]))
    unknown = [k for k in zip(elig["GAME_ID"], elig["TEAM_ID"]) if k not in known]
    if unknown:
        sample = sorted({f"{game}/{team}" for game, team in unknown})[:5]
        log.warning(
            "%d status rows reference team-games absent from the schedule and are "
            "DROPPED (e.g. %s) - check nba_schedule home/away team ids for those games",
            len(unknown), ", ".join(sample),
        )
        elig = elig[[k in known for k in zip(elig["GAME_ID"], elig["TEAM_ID"])]]

    log.info("status-based universe: %d rostered player-game rows", len(elig))
    return _attach_outcomes(elig, tg, player_logs, SOURCE_STATUS, positions)


def approximate_universe(
    schedule: pd.DataFrame,
    team_logs: pd.DataFrame,
    player_logs: pd.DataFrame,
    window_days: int = FALLBACK_ROSTER_WINDOW_DAYS,
    positions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """BIASED fallback for parquet-fixture mode. do not ship predictions from it.

    a player is treated as eligible for a team-game on date ``d`` if he
    recorded at least one game log for that team within ``[d-w, d+w]``. the
    forward half of the window reconstructs roster membership only and never
    reaches a model feature.

    known distortions, measured in REPORT.md section 5:
      - long injuries vanish entirely; the longest representable absence streak
        is ``2*w/interval`` team-games (16 at w=15)
      - availability base rate biased upward by at least +0.0192
      - traded players stay eligible for their old team for up to ``w`` days
      - NO INACTIVE LIST EXISTS, so LISTED_INACTIVE is null on every row and the
        teammate-absence set degenerates to "rostered and did not appear". on the
        status universe the two definitions coincide exactly; here the set is
        missing every absence the +/-15 day window already failed to represent,
        which understates vacated resources rather than inventing them
    """
    log.warning(
        "BIASED UNIVERSE: no player_game_status available, falling back to the "
        "+/-%d day game-log-presence approximation. availability is over-stated "
        "and absences longer than ~16 team-games are structurally invisible "
        "(REPORT.md section 5). fixture/backtest use only.",
        window_days,
    )

    schedule, team_logs, player_logs = (
        training_rows(schedule), training_rows(team_logs), training_rows(player_logs)
    )
    tg = team_game_frame(schedule, team_logs)
    pl = normalise_dates(normalise_ids(player_logs))
    window = np.timedelta64(window_days, "D")

    appearances = (
        pl.groupby(["SEASON", "TEAM_ID", "PLAYER_ID"])["GAME_DATE"]
        .apply(lambda s: np.sort(s.to_numpy()))
        .reset_index(name="APP_DATES")
    )

    rows: list[pd.DataFrame] = []
    for (season, team_id), team_games in tg.groupby(["SEASON", "TEAM_ID"], sort=False):
        game_dates = team_games["GAME_DATE"].to_numpy()
        game_ids = team_games["GAME_ID"].to_numpy()
        lo = game_dates - window
        hi = game_dates + window

        roster = appearances[
            (appearances["SEASON"] == season) & (appearances["TEAM_ID"] == team_id)
        ]
        for player_id, app_dates in zip(roster["PLAYER_ID"], roster["APP_DATES"]):
            left = np.searchsorted(app_dates, lo, side="left")
            right = np.searchsorted(app_dates, hi, side="right")
            eligible = right > left
            if not eligible.any():
                continue
            rows.append(
                pd.DataFrame({
                    "TEAM_ID": team_id,
                    "PLAYER_ID": player_id,
                    "GAME_ID": game_ids[eligible],
                })
            )

    if not rows:
        raise ValueError("approximation produced no eligible player-games")
    elig = pd.concat(rows, ignore_index=True)
    log.info("approximated universe: %d eligible player-game rows", len(elig))
    return _attach_outcomes(elig, tg, player_logs, SOURCE_APPROXIMATION, positions)


def postseason_appearances(
    schedule: pd.DataFrame,
    team_logs: pd.DataFrame,
    player_logs: pd.DataFrame,
    positions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """play-in and playoff APPEARANCES in the universe shape, never modelled rows.

    the career-scoped history features may read them (config
    RATE_HISTORY_INCLUDES_POSTSEASON); nothing is trained or scored on them. a log
    row with a dnp reason is a dressed DNP, exactly as the status universe treats it.
    """
    logs = normalise_dates(normalise_ids(postseason_rows(player_logs)))
    games = postseason_rows(schedule)
    if logs.empty or games.empty:
        empty = pd.DataFrame(columns=[*UNIVERSE_COLS, COMPETITION_COL])
        empty["GAME_DATE"] = pd.to_datetime(empty["GAME_DATE"])
        return empty
    tg = team_game_frame(games, postseason_rows(team_logs))
    elig = logs[["PLAYER_ID", "GAME_ID", "TEAM_ID"]].copy()
    dnp = logs["DNP_REASON"] if "DNP_REASON" in logs.columns else pd.Series(pd.NA, index=logs.index)
    elig["PLAYED"] = dnp.isna().astype(float).to_numpy()
    elig = elig.drop_duplicates(["PLAYER_ID", "GAME_ID", "TEAM_ID"])
    out = _attach_outcomes(elig, tg, logs, SOURCE_POSTSEASON, positions)
    out = out[out["PLAYED"] == 1].reset_index(drop=True)
    log.info("postseason appearances: %d rows", len(out))
    return out


def build_postseason_appearances(source) -> pd.DataFrame:
    """:func:`postseason_appearances` straight from a source's history frames."""
    positions = (
        source.load_player_positions()
        if hasattr(source, "load_player_positions") else None
    )
    return postseason_appearances(
        source.load_schedule(),
        source.load_team_game_logs(),
        source.load_player_game_logs(),
        positions,
    )


def build_universe(source) -> pd.DataFrame:
    """status-based when the source has a roster table, approximation otherwise."""
    schedule = source.load_schedule()
    team_logs = source.load_team_game_logs()
    player_logs = source.load_player_game_logs()
    status = source.load_player_game_status()
    positions = (
        source.load_player_positions()
        if hasattr(source, "load_player_positions") else None
    )

    if status is not None and len(status) > 0:
        return universe_from_status(schedule, team_logs, player_logs, status, positions)
    return approximate_universe(
        schedule, team_logs, player_logs, positions=positions
    )


def coverage_report(universe: pd.DataFrame, player_logs: pd.DataFrame) -> dict[str, float]:
    """sanity numbers a caller can print or assert on."""
    player_logs = training_rows(player_logs)
    played = universe[universe["PLAYED"] == 1]
    universe_keys = set(zip(played["PLAYER_ID"], played["GAME_ID"]))
    log_keys = set(zip(player_logs["PLAYER_ID"], player_logs["GAME_ID"]))
    team_games = universe.groupby(["GAME_ID", "TEAM_ID"]).ngroups
    return {
        "rows": float(len(universe)),
        "players": float(universe["PLAYER_ID"].nunique()),
        "team_games": float(team_games),
        "mean_roster": len(universe) / max(team_games, 1),
        "played_rate": float(universe["PLAYED"].mean()),
        "appearance_coverage": len(log_keys & universe_keys) / max(len(log_keys), 1),
    }


def universe_composition(universe: pd.DataFrame) -> dict[str, object]:
    """row counts by (PLAYED, LISTED_INACTIVE), plus the box-score-sourced count.

    the measurement behind MODEL.md 17.8: how much the active-DNP rows move the
    universe before anyone decides to retrain on it. LISTED_INACTIVE None means
    unknown. ``box_score_rows`` is None when the frame carries no STATUS_SOURCE,
    which is not the same claim as zero.
    """
    played = pd.to_numeric(universe["PLAYED"], errors="coerce").fillna(0).astype(int)
    if "LISTED_INACTIVE" in universe.columns:
        inactive = universe["LISTED_INACTIVE"].astype("boolean")
    else:
        inactive = pd.Series(pd.NA, index=universe.index, dtype="boolean")

    counts: dict[tuple[int, bool | None], int] = {}
    for p, i in zip(played, inactive):
        key = (int(p), None if pd.isna(i) else bool(i))
        counts[key] = counts.get(key, 0) + 1

    box_rows: int | None = None
    if STATUS_SOURCE_COL in universe.columns:
        box_rows = int(
            (universe[STATUS_SOURCE_COL].astype("string") == BOX_SCORE_STATUS_SOURCE)
            .fillna(False).sum()
        )
    return {
        "rows": int(len(universe)),
        "by_played_listed_inactive": dict(
            sorted(counts.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))
        ),
        "box_score_rows": box_rows,
    }
