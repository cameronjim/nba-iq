"""retrospective measurement of the serving-time coherence corrections.

input is a scored validation frame: prediction columns (P_PLAY, E_MIN_COND,
E_<stat>_COND, E_<stat>) on scheduled rows, plus realized PLAYED, MIN and PTS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .coherence import (
    COHERENCE_NONE,
    COHERENCE_VARIANTS,
    TEAM_GAME_KEYS,
    apply_coherence,
    team_minute_sums,
)
from .eval_core import cohort_masks

ENDPOINT_UNCOND_PTS = "uncond_pts_mae"
ENDPOINT_UNCOND_MIN = "uncond_min_mae"
ENDPOINT_COND_MIN = "cond_min_mae"

# (endpoint, prediction column, realized column, appearances only)
ENDPOINTS: tuple[tuple[str, str, str, bool], ...] = (
    (ENDPOINT_UNCOND_PTS, "E_PTS", "PTS", False),
    (ENDPOINT_UNCOND_MIN, "E_MIN", "MIN", False),
    (ENDPOINT_COND_MIN, "E_MIN_COND", "MIN", True),
)

MINUTE_SUM_BAND: tuple[float, float] = (220.0, 260.0)

ENDPOINT_COLUMNS = ["variant", "endpoint", "cohort", "n", "mae"]


def _realized(frame: pd.DataFrame, column: str) -> np.ndarray:
    # a scheduled row the player missed realized zero of everything.
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def _played(frame: pd.DataFrame) -> np.ndarray:
    return (pd.to_numeric(frame["PLAYED"], errors="coerce") == 1).to_numpy()


def paired_errors(scored: pd.DataFrame, variant: str) -> pd.DataFrame:
    """per-row absolute errors under ``none`` and ``variant``, aligned on the same rows."""
    corrected = apply_coherence(scored, variant)
    played = _played(scored)
    pieces: list[pd.DataFrame] = []
    for endpoint, pred_col, real_col, appearances_only in ENDPOINTS:
        if pred_col not in scored.columns or real_col not in scored.columns:
            continue
        actual = _realized(scored, real_col)
        base = np.abs(scored[pred_col].to_numpy(dtype=float) - actual)
        cand = np.abs(corrected[pred_col].to_numpy(dtype=float) - actual)
        keep = played if appearances_only else np.ones(len(scored), dtype=bool)
        pieces.append(pd.DataFrame({
            "endpoint": endpoint,
            "row": np.flatnonzero(keep),
            "none": base[keep],
            variant: cand[keep],
        }))
    if not pieces:
        return pd.DataFrame(columns=["endpoint", "row", "none", variant])
    return pd.concat(pieces, ignore_index=True)


def coherence_endpoints(scored_frame: pd.DataFrame) -> pd.DataFrame:
    """tidy [variant, endpoint, cohort, n, mae] for every variant, ALL plus cohorts."""
    scored = scored_frame.reset_index(drop=True)
    every_row = np.ones(len(scored), dtype=bool)
    cohorts = [("ALL", every_row), *cohort_masks(scored)]
    played = _played(scored)
    records: list[dict[str, object]] = []
    for variant in COHERENCE_VARIANTS:
        corrected = apply_coherence(scored, variant)
        for endpoint, pred_col, real_col, appearances_only in ENDPOINTS:
            if pred_col not in scored.columns or real_col not in scored.columns:
                continue
            err = np.abs(
                corrected[pred_col].to_numpy(dtype=float) - _realized(scored, real_col)
            )
            selector = played if appearances_only else every_row
            for label, mask in cohorts:
                sel = mask & selector & np.isfinite(err)
                if not sel.any():
                    continue
                records.append({
                    "variant": variant, "endpoint": endpoint, "cohort": label,
                    "n": int(sel.sum()), "mae": float(err[sel].mean()),
                })
    return pd.DataFrame(records, columns=ENDPOINT_COLUMNS)


def relative_to_none(endpoints: pd.DataFrame) -> pd.DataFrame:
    """adds delta_pct against the ``none`` row of the same endpoint and cohort."""
    base = endpoints[endpoints["variant"] == COHERENCE_NONE][
        ["endpoint", "cohort", "mae"]
    ].rename(columns={"mae": "mae_none"})
    out = endpoints.merge(base, on=["endpoint", "cohort"], how="left")
    out["delta_pct"] = (out["mae"] - out["mae_none"]) / out["mae_none"]
    return out.drop(columns=["mae_none"])


def minute_sum_diagnostic(
    scored_frame: pd.DataFrame, band: tuple[float, float] = MINUTE_SUM_BAND
) -> dict[str, float]:
    """the raw expected minute sum per team-game: mean, p10, p90, share outside band."""
    sums = team_minute_sums(scored_frame)
    per_game = scored_frame[TEAM_GAME_KEYS].assign(_sum=sums.to_numpy()).drop_duplicates(
        TEAM_GAME_KEYS
    )["_sum"].to_numpy(dtype=float)
    if per_game.size == 0:
        return {"team_games": 0, "mean": float("nan"), "p10": float("nan"),
                "p90": float("nan"), "share_outside": float("nan")}
    low, high = band
    return {
        "team_games": int(per_game.size),
        "mean": float(per_game.mean()),
        "p10": float(np.quantile(per_game, 0.1)),
        "p90": float(np.quantile(per_game, 0.9)),
        "share_outside": float(((per_game < low) | (per_game > high)).mean()),
    }
