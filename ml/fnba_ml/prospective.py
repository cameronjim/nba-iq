"""the scheduled-player-game universe for a season that has not been played.

WHY THIS EXISTS. :mod:`fnba_ml.universe` builds its rows from
``player_game_status``, which is derived from box scores — so it has one row per
game that has ALREADY happened and exactly zero rows for a game that has not. That
is correct for training and useless for a preseason projection: on 2026-08-17 the
2026-27 schedule is fully published (1,271 games) and no player-game exists for
any of it.

This module supplies the missing half. A future team-game plus a roster
assignment is enough to say "this player could play in this game", which is the
same claim ``player_game_status`` makes retrospectively, and it is the only claim
the universe row is required to carry. Everything else on the row — the outcome
columns — is unknown and is written as the same zero a non-appearance gets.

THE ROSTER IS AN INPUT, NOT AN INFERENCE. Team membership for a season with no
games cannot be derived from game logs; it has to come from a roster observation
(``player_team_stints`` with ``source='roster_snapshot'``, or the csv the
scraper's ``--roster-snapshot --snapshot-out`` emits before the table has it).
This is what puts a traded player on his new team in an October projection, and
without it the whole exercise projects last season's rosters with new dates on
them.

ONE FUTURE DATE AT A TIME, and this is the load-bearing rule of the module.
Almost every feature is built by an ``allow_exact_matches=False`` as-of join over
APPEARANCE rows, and a future row is never an appearance, so future rows cannot
reach each other through any of those. ``features._availability_history`` is the
exception: it computes shifted rolling windows over the player's whole SCHEDULED
series, so a Tuesday future row carrying ``PLAYED = 0`` would enter a Thursday
future row's ``avail_rate_10`` as a fabricated absence — and by the end of an
opening week a healthy starter would look like he had missed three games.
:func:`build_prospective_features` therefore builds features for exactly one
future date at a time against the full played history, which makes that leak
structurally impossible rather than merely unlikely. It costs one feature build
per date and buys a projection whose Thursday number does not depend on a Tuesday
that has not happened.

THE SCHEDULE IS THE EXCEPTION TO THAT RULE. Rest days, back-to-backs and
defensive form are read from the calendar, not from outcomes, and the calendar is
fully known: a Thursday game after a Wednesday game is a back-to-back whether or
not Wednesday has been played. Each per-date build is therefore handed the whole
known schedule (:func:`known_schedule`) for those columns, while every
outcome-derived column still sees exactly one future date.

WHAT IS DELIBERATELY NOT SOLVED HERE. The oracle teammate family
(``vacated_*``, ``star_out``, …) is computed by ``build_features`` and is
MEANINGLESS on future rows: its absence set is the target game's, and every future
row's target is unknown-written-as-zero, so it reads as "everybody is out". That is
harmless because the oracle family is not in ``config.FEATURE_COLS`` and is never
served (:mod:`fnba_ml.teammates`), and it is stated here so nobody reads those
columns off a prospective frame and believes them.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .data.schema import STAT_COLS, normalise_dates, normalise_ids
from .universe import TEAM_TOTAL_COLS, UNIVERSE_COLS, position_group

log = logging.getLogger(__name__)

# stamped on ``UNIVERSE_SOURCE`` for every future row. A THIRD value beside
# 'status' and 'approximation', not a reuse of either: 'status' would claim these
# rows came from an official inactive list, and 'approximation' would attract the
# BIASED-universe refusal in predict.py, which is about a different defect
# entirely (game-log-presence inference on games that were played).
SOURCE_PROSPECTIVE = "prospective"

ROSTER_COLS: tuple[str, ...] = ("PLAYER_ID", "TEAM_ID")


def roster_assignments(frame: pd.DataFrame) -> pd.DataFrame:
    """normalise a roster observation to one (PLAYER_ID, TEAM_ID) row per player.

    Accepts either the package's own column names or the database's
    (``nba_player_id`` / ``team_id``), because the two producers of this frame are
    a postgres read and the scraper's snapshot csv and neither should have to know
    about the other's convention.

    A player listed on two teams keeps the LAST one and the collision is logged:
    a duplicated player would multiply his team-games, and a silently-deduplicated
    one would hide a broken roster source.
    """
    out = frame.rename(columns={"nba_player_id": "PLAYER_ID", "team_id": "TEAM_ID"})
    missing = [c for c in ROSTER_COLS if c not in out.columns]
    if missing:
        raise ValueError(
            f"roster frame is missing {missing}; it needs PLAYER_ID and TEAM_ID "
            f"(or nba_player_id and team_id)"
        )
    out = normalise_ids(out[list(ROSTER_COLS)]).dropna(subset=list(ROSTER_COLS))
    duplicated = int(out["PLAYER_ID"].duplicated().sum())
    if duplicated:
        log.warning(
            "%d player(s) appear on more than one roster; keeping the last", duplicated
        )
    return out.drop_duplicates("PLAYER_ID", keep="last").reset_index(drop=True)


def future_team_games(
    schedule: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """one row per (team, game) in the window, from the schedule alone.

    The same home/away expansion :func:`universe.team_game_frame` does, minus the
    team-log join: a game that has not been played has no points, no possessions
    and no opponent totals. Those columns exist and are null, which is what the
    downstream ``DEF_FORM`` and usage features expect to see for a game whose box
    score does not exist yet.
    """
    sched = normalise_dates(normalise_ids(schedule))
    sched = sched[
        (sched["GAME_DATE"] >= pd.Timestamp(start))
        & (sched["GAME_DATE"] <= pd.Timestamp(end))
    ]

    home = sched.rename(columns={"HOME_TEAM_ID": "TEAM_ID", "AWAY_TEAM_ID": "OPP_TEAM_ID"})
    home["IS_HOME"] = 1
    away = sched.rename(columns={"AWAY_TEAM_ID": "TEAM_ID", "HOME_TEAM_ID": "OPP_TEAM_ID"})
    away["IS_HOME"] = 0

    cols = ["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "OPP_TEAM_ID", "IS_HOME"]
    if "SCHEDULED_AT" in sched.columns:
        cols.append("SCHEDULED_AT")
    tg = pd.concat([home[cols], away[cols]], ignore_index=True)

    for column in ("TEAM_PTS", "TEAM_PTS_ALLOWED", *TEAM_TOTAL_COLS.values()):
        tg[column] = np.nan
    tg["TEAM_ABBREVIATION"] = pd.NA
    return tg.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)


def prospective_universe(
    schedule: pd.DataFrame,
    rosters: pd.DataFrame,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    positions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """future scheduled player-games, in the universe schema.

    THE OUTCOME COLUMNS ARE ZERO AND THEY ARE NOT PREDICTIONS. ``PLAYED``, ``MIN``
    and every stat are written as the value a non-appearance carries, because the
    universe schema has no third state and inventing one would mean auditing every
    consumer for it. What protects this from becoming a lie is that no feature
    reads a future row's outcome: the appearance-scoped joins skip it (``PLAYED``
    is not 1), and :func:`build_prospective_features` builds one date at a time so
    the one schedule-scoped family that WOULD read it never sees a second future
    row. ``config.TARGET_COLS`` is the list of columns this paragraph is about.

    ``LISTED_INACTIVE`` is left null rather than False: nobody has been ruled out
    of a game in October 2026, and False would assert that they have been ruled IN.
    """
    tg = future_team_games(schedule, pd.Timestamp(start), pd.Timestamp(end))
    roster = roster_assignments(rosters)
    if tg.empty:
        raise ValueError(
            f"no scheduled games between {pd.Timestamp(start).date()} and "
            f"{pd.Timestamp(end).date()}"
        )

    universe = tg.merge(roster, on="TEAM_ID", how="inner")
    if universe.empty:
        raise ValueError(
            "no roster assignment matched any scheduled team; check that the "
            "roster frame's TEAM_ID values are nba team ids as strings"
        )

    universe["PLAYED"] = 0
    universe["LISTED_INACTIVE"] = pd.NA
    universe["LISTED_INACTIVE"] = universe["LISTED_INACTIVE"].astype("boolean")
    for column in STAT_COLS:
        universe[column] = 0.0
    universe["PLAYER_NAME"] = pd.NA

    if positions is not None and len(positions) > 0:
        pos = normalise_ids(positions).drop_duplicates("PLAYER_ID")
        universe = universe.merge(
            pos[["PLAYER_ID", "POSITION"]], on="PLAYER_ID", how="left"
        )
    else:
        universe["POSITION"] = pd.NA
    universe["POS_GROUP"] = position_group(universe["POSITION"])

    universe["UNIVERSE_SOURCE"] = SOURCE_PROSPECTIVE
    keep = [c for c in (*UNIVERSE_COLS, "SCHEDULED_AT") if c in universe.columns]
    universe = universe[keep]
    log.info(
        "prospective universe: %d player-game rows over %d team-games, %d players",
        len(universe), universe.groupby(["GAME_ID", "TEAM_ID"]).ngroups,
        universe["PLAYER_ID"].nunique(),
    )
    return universe.sort_values(
        ["GAME_DATE", "GAME_ID", "TEAM_ID", "PLAYER_ID"]
    ).reset_index(drop=True)


def known_schedule(history: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
    """every known team-game: the played history plus every future team-game.

    Future games are taken from both sides of each row, so an opponent with no
    rostered player still has its schedule and therefore its rest days. Their
    ``TEAM_PTS_ALLOWED`` is null, which ``features.schedule_features`` reads as
    unplayed.
    """
    from .features import SCHEDULE_COLS  # noqa: PLC0415 - avoids an import cycle

    cols = list(SCHEDULE_COLS)
    mirrored = future[["SEASON", "OPP_TEAM_ID", "GAME_ID", "GAME_DATE"]].rename(
        columns={"OPP_TEAM_ID": "TEAM_ID"}
    )
    mirrored["TEAM_PTS_ALLOWED"] = np.nan
    frame = pd.concat(
        [history[cols], future[cols], mirrored[cols]], ignore_index=True
    )
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    # keep="first" so a played row wins over a mirrored null for the same team-game
    return frame.drop_duplicates(
        ["SEASON", "TEAM_ID", "GAME_ID"], keep="first"
    ).reset_index(drop=True)


def build_prospective_features(
    history: pd.DataFrame, future: pd.DataFrame
) -> pd.DataFrame:
    """features for every future row, built ONE GAME DATE AT A TIME.

    See the module docstring for why the loop is not an optimisation waiting to
    happen. Each iteration builds features over ``history`` plus exactly one future
    date and keeps that date's rows, so no future row is ever in the frame that
    produces another future row's outcome-derived features. The schedule-derived
    columns are the exception: every iteration reads them from the whole known
    schedule, so ``future`` should be the full window rather than one date.

    ``history`` is the played universe — the same frame ``build_dataset.py``
    feeds to ``build_features`` — and it must span far enough back for the career
    EWMAs, which in practice means all of it.

    The context probability is left at the stage-0 baseline. ``predict.py`` rebuilds
    it from the base availability model (and the injury report) before scoring,
    which is the serving path; running the offline cross-fit here would fit models
    on a frame whose future rows have no labels to be out of fold about.
    """
    from .features import build_features  # noqa: PLC0415 - avoids an import cycle

    history = history.copy()
    history["GAME_DATE"] = pd.to_datetime(history["GAME_DATE"])
    future = future.copy()
    future["GAME_DATE"] = pd.to_datetime(future["GAME_DATE"])

    dates = sorted(future["GAME_DATE"].unique())
    log.info(
        "building prospective features for %d date(s), one feature build each",
        len(dates),
    )

    schedule = known_schedule(history, future)
    out: list[pd.DataFrame] = []
    for index, game_date in enumerate(dates, start=1):
        day = future[future["GAME_DATE"] == game_date]
        combined = pd.concat([history, day], ignore_index=True)
        built = build_features(combined, schedule=schedule)
        out.append(built[built["UNIVERSE_SOURCE"] == SOURCE_PROSPECTIVE])
        log.info(
            "  %d/%d  %s  %d rows", index, len(dates),
            pd.Timestamp(game_date).date(), len(day),
        )

    frame = pd.concat(out, ignore_index=True)
    return frame.sort_values(
        ["GAME_DATE", "GAME_ID", "TEAM_ID", "PLAYER_ID"]
    ).reset_index(drop=True)


def history_from_dataset(dataset: pd.DataFrame) -> pd.DataFrame:
    """recover the played universe from a built dataset parquet.

    The dataset carries every column in ``universe.UNIVERSE_COLS``, so the
    universe it was built from can be sliced back out of it. That matters
    operationally: rebuilding four seasons of universe needs a database holding
    four seasons of schedule and status, and the shipped parquet is the same rows
    with more columns.
    """
    missing = [c for c in UNIVERSE_COLS if c not in dataset.columns]
    if missing:
        raise ValueError(
            f"this dataset cannot supply a universe; it is missing {missing}"
        )
    out = dataset[list(UNIVERSE_COLS)].copy()
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"])
    return out.reset_index(drop=True)
