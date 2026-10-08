"""the v7 candidate family: a player's role in his team's last preseason games. never served.

a regular-season row reads only preseason games of its own SEASON, dated strictly
before its game, and only the last ``PRESEASON_ROLE_GAMES`` of them played by the
team it is predicted for (MODEL.md 23). preseason stays truth only for every other
purpose (MODEL.md 20.5): nothing here enters a rate history or a training row.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (
    PRESEASON_ROLE_FADE_GAMES,
    PRESEASON_ROLE_FEATURE_COLS,
    PRESEASON_ROLE_GAMES,
    PRESEASON_ROLE_PRIOR_GAMES,
    PRESEASON_ROLE_PRIOR_MIN_GAMES,
    PRESEASON_ROLE_PRIOR_WEIGHT,
    PRESEASON_ROLE_TEAM_MINUTES,
)
from .data.schema import PRESEASON_LOG_COLS

log = logging.getLogger(__name__)

TEAM_KEY: list[str] = ["SEASON", "TEAM_ID"]
WINDOW_KEY: list[str] = [*TEAM_KEY, "_n_before"]

# the evaluation-only helper columns the bracket stamps on the frame. not features.
SEASON_APPS_COL = "PRE_ROLE_SEASON_APPS"
ROSTER_COHORT_COL = "PRE_ROLE_ROSTER"
COHORT_NEW_TEAM = "season start: new team"
COHORT_NO_HISTORY = "season start: no history"
COHORT_SAME_TEAM = "season start: same team"
ROSTER_COHORTS: tuple[str, ...] = (COHORT_NEW_TEAM, COHORT_NO_HISTORY, COHORT_SAME_TEAM)


def _clean_preseason(pre: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in PRESEASON_LOG_COLS if c not in pre.columns]
    if missing:
        raise ValueError(f"preseason logs are missing {', '.join(missing)}")
    out = pre[list(PRESEASON_LOG_COLS)].copy()
    for col in ("PLAYER_ID", "GAME_ID", "TEAM_ID", "SEASON"):
        out[col] = out[col].astype(str)
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"]).astype("datetime64[ns]")
    out["MIN"] = pd.to_numeric(out["MIN"], errors="coerce").fillna(0.0).astype(float)
    out["STARTED"] = out["STARTED"].astype("boolean").astype("Float64").astype(float)
    return out


def regular_season_apps_before(frame: pd.DataFrame) -> np.ndarray:
    """per row, the player's regular-season appearances that SEASON before this game."""
    work = pd.DataFrame({
        "PLAYER_ID": frame["PLAYER_ID"].astype(str).to_numpy(),
        "SEASON": frame["SEASON"].astype(str).to_numpy(),
        "GAME_DATE": pd.to_datetime(frame["GAME_DATE"]).to_numpy(),
        "GAME_ID": frame["GAME_ID"].astype(str).to_numpy(),
        "played": pd.to_numeric(frame["PLAYED"], errors="coerce").fillna(0.0).to_numpy(),
        "_pos": np.arange(len(frame)),
    }).sort_values(["PLAYER_ID", "SEASON", "GAME_DATE", "GAME_ID"])
    before = work.groupby(["PLAYER_ID", "SEASON"])["played"].cumsum() - work["played"]
    out = np.empty(len(frame), dtype=float)
    out[work["_pos"].to_numpy()] = before.to_numpy()
    return out


def _team_games(pre: pd.DataFrame) -> pd.DataFrame:
    """one row per team-game: its date, its player-minutes sum and its ordinal."""
    games = (
        pre.groupby([*TEAM_KEY, "GAME_ID"], as_index=False)
        .agg(GAME_DATE=("GAME_DATE", "min"), _team_min=("MIN", "sum"))
        .sort_values([*TEAM_KEY, "GAME_DATE", "GAME_ID"])
        .reset_index(drop=True)
    )
    games["_ordinal"] = games.groupby(TEAM_KEY).cumcount()
    return games


def _games_before(rows: pd.DataFrame, games: pd.DataFrame) -> np.ndarray:
    """per row, how many of its team's preseason games that season are dated before it."""
    out = np.zeros(len(rows), dtype=int)
    dates_by_team = {
        key: group["GAME_DATE"].to_numpy() for key, group in games.groupby(TEAM_KEY)
    }
    row_dates = rows["GAME_DATE"].to_numpy()
    for key, index in rows.groupby(TEAM_KEY).indices.items():
        dates = dates_by_team.get(key)
        if dates is not None:
            out[index] = np.searchsorted(dates, row_dates[index], side="left")
    return out


def _window_roles(
    pre: pd.DataFrame, games: pd.DataFrame, windows: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per season, team, n_before and player: the role; per window: its game count)."""
    expanded = windows.merge(games, on=TEAM_KEY, how="left")
    in_window = (
        (expanded["_ordinal"] < expanded["_n_before"])
        & (expanded["_ordinal"] >= expanded["_n_before"] - PRESEASON_ROLE_GAMES)
    )
    window_games = expanded[in_window]
    n_games = window_games.groupby(WINDOW_KEY).size().rename("_n_games")
    lines = window_games[[*WINDOW_KEY, "GAME_ID", "_team_min"]].merge(
        pre, on=[*TEAM_KEY, "GAME_ID"], how="inner"
    )
    lines["_share"] = (lines["MIN"] / lines["_team_min"]).where(lines["_team_min"] > 0)
    lines["_played"] = (lines["MIN"] > 0).astype(float)
    lines["_played_min"] = lines["MIN"].where(lines["MIN"] > 0)
    lines["_null_start"] = lines["STARTED"].isna().astype(float)
    roles = lines.groupby([*WINDOW_KEY, "PLAYER_ID"], as_index=False).agg(
        _share_sum=("_share", "sum"),
        _starts=("STARTED", "sum"),
        _null_starts=("_null_start", "sum"),
        pre_games_played=("_played", "sum"),
        pre_min_mean=("_played_min", "mean"),
    )
    roles = roles.merge(n_games.reset_index(), on=WINDOW_KEY, how="left")
    roles["pre_min_share"] = roles["_share_sum"] / roles["_n_games"]
    # a game he is absent from is a known non-start; a null flag is unknown.
    known = roles["_n_games"] - roles["_null_starts"]
    roles["pre_started_rate"] = (roles["_starts"] / known).where(known > 0)
    return roles[[*WINDOW_KEY, "PLAYER_ID", "pre_started_rate", "pre_min_share",
                  "pre_min_mean", "pre_games_played"]], n_games.reset_index()


def _dressed_for(rows: pd.DataFrame, pre: pd.DataFrame) -> np.ndarray:
    """1 if his last same-season preseason box line before the game was for this team."""
    left = rows[["PLAYER_ID", "GAME_DATE", "SEASON", "TEAM_ID", "_pos"]].sort_values(
        "GAME_DATE"
    )
    right = (
        pre[["PLAYER_ID", "GAME_DATE", "GAME_ID", "SEASON", "TEAM_ID"]]
        .sort_values(["GAME_DATE", "GAME_ID"])
        .drop_duplicates(["PLAYER_ID", "GAME_DATE"], keep="last")
        .rename(columns={"SEASON": "_pre_season", "TEAM_ID": "_pre_team"})
        .drop(columns=["GAME_ID"])
    )
    joined = pd.merge_asof(
        left, right, on="GAME_DATE", by="PLAYER_ID", direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    same_season = joined["_pre_season"].astype(object) == joined["SEASON"].astype(object)
    flag = np.where(joined["_pre_team"].astype(object) == joined["TEAM_ID"], 1.0, 0.0)
    flag = np.where(same_season, flag, np.nan)
    out = np.full(len(rows), np.nan)
    out[joined["_pos"].to_numpy()] = flag
    return out


def preseason_role_features(features: pd.DataFrame, pre: pd.DataFrame) -> pd.DataFrame:
    """the six v7 columns for every row of ``features``, in its row order.

    reads ``SEASON``, ``TEAM_ID``, ``PLAYER_ID``, ``GAME_DATE``, ``PLAYED`` and
    ``ewma_MIN`` from the frame; the last is as-of by construction (build_features).
    """
    pre = _clean_preseason(pre)
    rows = pd.DataFrame({
        "PLAYER_ID": features["PLAYER_ID"].astype(str).to_numpy(),
        "TEAM_ID": features["TEAM_ID"].astype(str).to_numpy(),
        "SEASON": features["SEASON"].astype(str).to_numpy(),
        "GAME_DATE": pd.to_datetime(features["GAME_DATE"]).astype("datetime64[ns]").to_numpy(),
        "_pos": np.arange(len(features)),
    })
    games = _team_games(pre)
    rows["_n_before"] = _games_before(rows, games)
    windows = rows.loc[rows["_n_before"] > 0, WINDOW_KEY].drop_duplicates()
    roles, n_games = _window_roles(pre, games, windows)
    out = rows.merge(n_games, on=WINDOW_KEY, how="left").merge(
        roles, on=[*WINDOW_KEY, "PLAYER_ID"], how="left"
    ).sort_values("_pos").reset_index(drop=True)

    has_window = out["_n_games"].fillna(0).to_numpy() > 0
    # his team played preseason games without him in the box: a known zero role.
    absent = has_window & out["pre_games_played"].isna().to_numpy()
    out.loc[absent, ["pre_min_share", "pre_started_rate"]] = 0.0
    out["pre_games_played"] = out["pre_games_played"].fillna(0.0)
    out.loc[~has_window, ["pre_min_share", "pre_started_rate", "pre_min_mean"]] = np.nan

    out["pre_dressed_for_current_team"] = _dressed_for(rows, pre)
    ewma_min = pd.to_numeric(features["ewma_MIN"], errors="coerce").to_numpy(dtype=float)
    out["pre_role_delta_min"] = (
        out["pre_min_share"].to_numpy(dtype=float) * PRESEASON_ROLE_TEAM_MINUTES - ewma_min
    )

    faded = regular_season_apps_before(features) >= PRESEASON_ROLE_FADE_GAMES
    neutral = [c for c in PRESEASON_ROLE_FEATURE_COLS if c != "pre_games_played"]
    out.loc[faded, neutral] = np.nan
    out.loc[faded, "pre_games_played"] = 0.0

    result = out[PRESEASON_ROLE_FEATURE_COLS].astype(float)
    result.index = features.index
    return result


def attach_preseason_role_features(
    features: pd.DataFrame, pre: pd.DataFrame | None
) -> pd.DataFrame:
    """the frame with the v7 columns added; ``pre=None`` returns it unchanged."""
    if pre is None or pre.empty:
        log.warning("no preseason logs; the %d v7 columns are not attached",
                    len(PRESEASON_ROLE_FEATURE_COLS))
        return features
    out = features.drop(columns=[c for c in PRESEASON_ROLE_FEATURE_COLS
                                 if c in features.columns])
    role = preseason_role_features(out, pre)
    for col in PRESEASON_ROLE_FEATURE_COLS:
        out[col] = role[col].to_numpy()
    seen = int((out["pre_games_played"] > 0).sum())
    log.info("v7 preseason role: %d of %d rows read a preseason appearance", seen, len(out))
    return out


def prior_weights(
    apps_before: np.ndarray,
    weight: float = PRESEASON_ROLE_PRIOR_WEIGHT,
    games: int = PRESEASON_ROLE_PRIOR_GAMES,
) -> np.ndarray:
    """w_k = weight * max(0, 1 - k / games); an unknown k gets no prior."""
    k = np.asarray(apps_before, dtype=float)
    w = weight * np.clip(1.0 - k / float(games), 0.0, None)
    return np.where(np.isfinite(k), w, 0.0)


def preseason_role_minutes_prior(
    min_pred: np.ndarray,
    pre_min_share: np.ndarray,
    pre_games_played: np.ndarray,
    apps_before: np.ndarray,
    weight: float = PRESEASON_ROLE_PRIOR_WEIGHT,
    games: int = PRESEASON_ROLE_PRIOR_GAMES,
) -> np.ndarray:
    """E[MIN|plays] blended toward pre_min_share * 240 over each player's first games."""
    minutes = np.asarray(min_pred, dtype=float)
    share = np.asarray(pre_min_share, dtype=float)
    played = np.nan_to_num(np.asarray(pre_games_played, dtype=float), nan=0.0)
    usable = np.isfinite(share) & (played >= PRESEASON_ROLE_PRIOR_MIN_GAMES)
    w = np.where(usable, prior_weights(apps_before, weight, games), 0.0)
    target = np.where(usable, share * PRESEASON_ROLE_TEAM_MINUTES, 0.0)
    return (1.0 - w) * minutes + w * target


def roster_cohorts(frame: pd.DataFrame) -> np.ndarray:
    """per row: new team, same team, or no history, against his last earlier-season team.

    the last earlier-season team is the team of his last scheduled row in the most
    recent earlier season the frame holds for him.
    """
    work = pd.DataFrame({
        "PLAYER_ID": frame["PLAYER_ID"].astype(str).to_numpy(),
        "SEASON": frame["SEASON"].astype(str).to_numpy(),
        "TEAM_ID": frame["TEAM_ID"].astype(str).to_numpy(),
        "GAME_DATE": pd.to_datetime(frame["GAME_DATE"]).to_numpy(),
    })
    last_team = (
        work.sort_values(["PLAYER_ID", "SEASON", "GAME_DATE"])
        .groupby(["PLAYER_ID", "SEASON"], as_index=False)
        .last()[["PLAYER_ID", "SEASON", "TEAM_ID"]]
        .sort_values(["PLAYER_ID", "SEASON"])
    )
    last_team["_prev_team"] = last_team.groupby("PLAYER_ID")["TEAM_ID"].shift(1)
    joined = work.merge(last_team[["PLAYER_ID", "SEASON", "_prev_team"]],
                        on=["PLAYER_ID", "SEASON"], how="left")
    prev = joined["_prev_team"]
    return np.where(
        prev.isna(), COHORT_NO_HISTORY,
        np.where(prev == joined["TEAM_ID"], COHORT_SAME_TEAM, COHORT_NEW_TEAM),
    ).astype(object)


def roster_cohort_masks(frame: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """(label, mask) per roster cohort, read from the stamped ROSTER_COHORT_COL."""
    labels = frame[ROSTER_COHORT_COL].to_numpy()
    return [(label, labels == label) for label in ROSTER_COHORTS]
