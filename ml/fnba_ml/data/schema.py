"""canonical in-memory frame shapes, shared by both data sources.

a source's only job is to return these four frames. everything downstream
(universe, features, models) is source-agnostic and never learns whether the
rows came from postgres or from parquet.
"""

from __future__ import annotations

import pandas as pd

from ..config import ID_COLS

# stats carried from the game log onto the universe row. a scheduled row where
# the player did not appear gets 0.0 for all of them.
#
# FTA joined the list for feature_version v2: the standard box-score usage
# approximation needs a player's free-throw ATTEMPTS (0.44 x FTA is the
# possession-cost term), and FTM cannot stand in for it - a player who shoots 8
# free throws and makes 4 used eight possessions' worth of trips to the line.
#
# FGM joined for the 9-category extension (2026-08-18). It is the ONLY box column
# the four-season build was missing, and it is missing in the way that matters
# most: FG% is derived by consumers as FGM/FGA, so without FGM the whole
# shooting-efficiency half of a 9-cat league is unservable. It cannot be
# reconstructed from what was already here either - PTS = 2*FGM + FG3M + FTM is an
# identity only when no free throw was an and-one technical and no line was
# scrubbed, and inverting it would manufacture a makes column out of rounding
# error. player_game_logs.fgm (migration 013) has carried it all along.
STAT_COLS: tuple[str, ...] = (
    "MIN", "PTS", "AST", "FGA", "FTA", "REB", "FG3M", "FGM", "FTM", "TOV",
    "STL", "BLK",
)

PLAYER_LOG_COLS: tuple[str, ...] = (
    "PLAYER_ID", "PLAYER_NAME", "GAME_ID", "SEASON", "SEASON_TYPE", "GAME_DATE",
    "TEAM_ID", "TEAM_ABBREVIATION", "STARTED", "DNP_REASON", "PLUS_MINUS",
) + STAT_COLS

# team-game totals. MIN/FGA/FTA/TOV joined PTS for v2: they are the DENOMINATOR
# of the usage-rate feature (a player's share of his team's possessions), which
# cannot be computed from player rows alone. every source in the repo already
# carries them - postgres in team_game_logs, the nba_api parquet exports natively.
TEAM_LOG_COLS: tuple[str, ...] = (
    "TEAM_ID", "GAME_ID", "SEASON", "GAME_DATE", "PTS", "MIN", "FGA", "FTA", "TOV",
)

# FG3A joined the team log for the P2 matchup family (2026-08-19) and it is
# OPTIONAL rather than required, which is a deliberate asymmetry with FTA above.
# The v2 usage feature could not be computed at all without team FTA, so a source
# that lacked it was a source the package could not use. ``opp_fg3a_allowed_per100``
# is one feature of twenty-one in a candidate set, and the spike's own nba_api
# parquet exports predate it; making it required would break every offline parquet
# run to add a column to a candidate. Absent, :mod:`fnba_ml.matchup` logs a warning
# and leaves the derived column null, which LightGBM routes as missing.
#
# WHAT IS NOT HERE AND CANNOT BE. Offensive rebounds. ``team_game_logs`` carries
# ``reb`` with no offensive/defensive split and no other table has one, so the
# possession estimate uses the OREB-free fallback - see
# ``config.POSSESSION_USES_OREB`` for the formula and what the omission costs.
TEAM_LOG_OPTIONAL_COLS: tuple[str, ...] = ("FG3A",)

# per-player position strings, the one piece of reference data that is not a
# per-game fact. optional: a source that has no positions returns None and the
# positional half of the teammate-context features is null rather than wrong.
POSITION_COLS: tuple[str, ...] = ("PLAYER_ID", "POSITION")

SCHEDULE_COLS: tuple[str, ...] = (
    "GAME_ID", "SEASON", "SEASON_TYPE", "GAME_DATE", "SCHEDULED_AT",
    "HOME_TEAM_ID", "AWAY_TEAM_ID", "GAME_STATUS",
)

STATUS_COLS: tuple[str, ...] = (
    "PLAYER_ID", "GAME_ID", "TEAM_ID", "ROSTERED", "LISTED_INACTIVE",
    "STARTED", "PLAYED", "DNP_REASON", "MIN", "STATUS_SOURCE",
)

_SCHEDULE_ID_COLS = ("GAME_ID", "HOME_TEAM_ID", "AWAY_TEAM_ID")


def normalise_ids(df: pd.DataFrame) -> pd.DataFrame:
    """cast every identifier column present to str.

    postgres stores nba ids as TEXT, the spike parquet as int64. merging a str
    key against an int key silently produces zero matches, so both sources are
    forced onto str before anything joins.
    """
    out = df.copy()
    for col in set(ID_COLS) | set(_SCHEDULE_ID_COLS):
        if col in out.columns:
            out[col] = out[col].astype("string").astype(object)
    return out


def normalise_dates(df: pd.DataFrame, cols: tuple[str, ...] = ("GAME_DATE",)) -> pd.DataFrame:
    """coerce date columns to midnight-normalised datetime64."""
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col]).dt.normalize()
    return out


def require_columns(df: pd.DataFrame, cols: tuple[str, ...], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{what} frame is missing required columns: {missing}")
