"""the v6 candidate family: box-score details and expected starters out. never served.

every per-appearance window here is inclusive of its own game and reaches a
scheduled row only through ``merge_asof(..., allow_exact_matches=False)``, the
guard build_features uses, so the row for game g reads appearances before g only.
the teammate columns are expectations over the same as-of P_CONTEXT p_j that
``exp_vacated_usg`` is built from, so no target-game outcome enters them.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (
    CONTEXT_P_PRIOR,
    P_CONTEXT,
    RATE_MINUTES_FLOOR,
    V6_BOX_EWMA_HALFLIFE,
    V6_BOX_FORM_COLS,
    V6_CONTEXT_FEATURE_COLS,
    V6_START_FEATURE_COLS,
    V6_START_WINDOW,
    V6_USUAL_STARTER_RATE,
)
from .data.schema import BOX_DETAIL_COLS, training_rows
from .teammates import _group_codes, _sum_excluding_self

log = logging.getLogger(__name__)

HISTORY_COLS: tuple[str, ...] = (*V6_START_FEATURE_COLS, *V6_BOX_FORM_COLS)


def _ewma(values: pd.Series, player: pd.Series) -> pd.Series:
    return values.groupby(player).transform(
        lambda s: s.ewm(halflife=V6_BOX_EWMA_HALFLIFE, adjust=True).mean()
    )


def _team_share(box: pd.DataFrame, stat: str) -> pd.Series:
    """the player's share of his team-game's total, null when the team total is 0."""
    value = pd.to_numeric(box[stat], errors="coerce")
    total = value.groupby([box["GAME_ID"], box["TEAM_ID"]]).transform("sum")
    return (value / total).where(total > 0)


def box_appearance_history(box: pd.DataFrame) -> pd.DataFrame:
    """one row per appearance, every window inclusive of it, ready to be as-of joined.

    regular season only, like the rate history. a null STARTED is missing in the
    start rate and breaks a streak.
    """
    missing = [c for c in BOX_DETAIL_COLS if c not in box.columns]
    if missing:
        raise ValueError(f"box details are missing {', '.join(missing)}")
    frame = training_rows(box).copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    # shares are taken over the whole team-game before cameo-free rows are dropped
    frame["_oreb_share"] = _team_share(frame, "OREB")
    frame["_dreb_share"] = _team_share(frame, "DREB")
    minutes = pd.to_numeric(frame["MIN"], errors="coerce")
    frame = frame[minutes > 0].copy()
    frame = frame.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    player = frame["PLAYER_ID"]

    started = frame["STARTED"].astype("boolean").astype("Float64").astype(float)
    frame["started_rate_10"] = started.groupby(player).transform(
        lambda s: s.rolling(V6_START_WINDOW, min_periods=1).mean()
    )
    frame["started_last"] = started
    is_start = started.fillna(0.0)
    block = (is_start == 0).astype(int).groupby(player).cumsum()
    frame["starts_streak"] = is_start.groupby([player, block]).cumsum()

    frame["plus_minus_ewma_10"] = _ewma(
        pd.to_numeric(frame["PLUS_MINUS"], errors="coerce"), player
    )
    per36 = (
        pd.to_numeric(frame["PF"], errors="coerce") * 36.0
        / pd.to_numeric(frame["MIN"], errors="coerce").clip(lower=RATE_MINUTES_FLOOR)
    )
    frame["pf_per36_ewma"] = _ewma(per36, player)
    frame["oreb_share_ewma"] = _ewma(frame["_oreb_share"], player)
    frame["dreb_share_ewma"] = _ewma(frame["_dreb_share"], player)

    return (
        frame[["PLAYER_ID", "GAME_DATE", *HISTORY_COLS]]
        .sort_values("GAME_DATE")
        .reset_index(drop=True)
    )


def attach_box_history(features: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """as-of join the appearance history onto scheduled rows, preserving row order."""
    out = features.drop(columns=[c for c in HISTORY_COLS if c in features.columns])
    out = out.reset_index(drop=True)
    original_dates = out["GAME_DATE"].copy()
    out["_row_order"] = np.arange(len(out))
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"]).astype("datetime64[ns]")
    right = history.copy()
    right["GAME_DATE"] = pd.to_datetime(right["GAME_DATE"]).astype("datetime64[ns]")
    out = pd.merge_asof(
        out.sort_values("GAME_DATE"),
        right.sort_values("GAME_DATE"),
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    out = out.sort_values("_row_order").drop(columns=["_row_order"]).reset_index(drop=True)
    out["GAME_DATE"] = original_dates.to_numpy()
    return out


def teammate_start_features(frame: pd.DataFrame) -> pd.DataFrame:
    """``team_starters_out_exp`` and ``exp_vacated_starts``, added in row order.

    usual starter: started_rate_10 >= V6_USUAL_STARTER_RATE. both sums exclude the
    row's own player and weight each teammate by 1 - p_j.
    """
    out = frame.copy()
    original_index = out.index
    out = out.reset_index(drop=True)
    if P_CONTEXT in out.columns:
        p = pd.to_numeric(out[P_CONTEXT], errors="coerce")
    else:
        log.warning("no %s on the frame; every p_j is the prior", P_CONTEXT)
        p = pd.Series(np.nan, index=out.index, dtype=float)
    absent = 1.0 - p.fillna(float(CONTEXT_P_PRIOR)).clip(0.0, 1.0)
    rate = pd.to_numeric(out["started_rate_10"], errors="coerce").fillna(0.0)
    usual = (rate >= V6_USUAL_STARTER_RATE).astype(float)
    team_game = _group_codes(out["GAME_ID"], out["TEAM_ID"])
    everyone = np.ones(len(out), dtype=bool)
    out["team_starters_out_exp"] = _sum_excluding_self(team_game, usual * absent, everyone)
    out["exp_vacated_starts"] = _sum_excluding_self(team_game, rate * absent, everyone)
    out.index = original_index
    return out


def attach_v6_features(features: pd.DataFrame, box: pd.DataFrame | None) -> pd.DataFrame:
    """the whole v6 family from a feature frame and the box details, row order kept.

    ``box=None`` returns the frame unchanged, so a source without box details
    builds every other column.
    """
    if box is None or box.empty:
        log.warning("no box details; the %d v6 columns are not attached",
                    len(V6_CONTEXT_FEATURE_COLS))
        return features
    out = attach_box_history(features, box_appearance_history(box))
    out = teammate_start_features(out)
    if len(out) != len(features):
        raise ValueError(
            f"attaching the v6 family changed the row count ({len(features)} -> "
            f"{len(out)})"
        )
    return out
