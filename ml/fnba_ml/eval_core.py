"""the evaluation harness's shared vocabulary: tasks, cohorts, the recorder, the split."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (
    COMPETITION_COL,
    EVENT_COHORTS,
    TIER_ORDER,
    TRAINING_COMPETITIONS,
    V4_DESCRIPTIVE_COHORTS,
)
from .models import brier, mae

log = logging.getLogger(__name__)

TASK_AVAILABILITY = "A availability"
TASK_MINUTES = "B minutes|played"
TASK_PTS = "C1 pts|played"
TASK_AST = "C2 ast|played"
TASK_UNCONDITIONAL = "D pts UNCONDITIONAL"
# diagnostics rather than model comparisons; both carry metric "Gain" with the
# feature name in the segment column.
TASK_IMPORTANCE = "E feature importance"
TASK_NEGATIVE_CONTROL = "Z negative control"

CONDITIONAL_TASKS = {TASK_MINUTES: "MIN", TASK_PTS: "PTS", TASK_AST: "AST"}

COHORT_TASKS = (TASK_MINUTES, TASK_PTS, TASK_AST, TASK_UNCONDITIONAL)

# the permuted twin tracks the SERVED column: permuting the oracle column would
# be a control for a feature no model in the promoted path can see.
NEGATIVE_CONTROL_FEATURE = "exp_vacated_minutes"
NEGATIVE_CONTROL_COLUMN = "exp_vacated_minutes_permuted"

# the cohort-defining column, deliberately the realized one: hindsight is
# legitimate for describing the games, not for a feature.
COHORT_ORACLE_COLUMN = "vacated_minutes"

TASK_FAMILY = {
    TASK_AVAILABILITY: "availability",
    TASK_MINUTES: "minutes",
    TASK_PTS: "production",
    TASK_AST: "production",
    TASK_UNCONDITIONAL: "composition",
}

# the composition that was promoted away from, kept as the parity reference.
PREVIOUS_COMPOSITION = "decomposed_p_x_ewma"

SKILL_BASELINE = {
    TASK_AVAILABILITY: "shifted_appearance_rate",
    TASK_MINUTES: "ewma",
    TASK_PTS: "ewma",
    TASK_AST: "ewma",
    TASK_UNCONDITIONAL: "naive_unconditional_mean",
}

MODEL_LABELS = {
    "global_rate": "baseline: global rate",
    "shifted_appearance_rate": "baseline: shifted appearance rate (10)",
    "logistic": "logistic regression",
    "lightgbm": "LightGBM",
    "ridge": "ridge",
    "ewma": "baseline: EWMA (halflife 5)",
    "expanding_mean": "baseline: expanding season mean",
    "naive_conditional_mean": "naive: conditional season mean (selection-biased)",
    "naive_unconditional_mean": "naive: unconditional season mean (0 for misses)",
    "direct_lightgbm": "direct LightGBM on all scheduled rows",
    "decomposed_p_x_ewma": "decomposed (demoted): P(play) x EWMA[PTS|played]",
    "decomposed_p_x_lightgbm": "decomposed: P(play) x LightGBM[PTS|played]",
    "decomposed_p_x_minutes_x_ppm":
        "decomposed CHAMPION: P(play) x E[MIN|played] x EWMA[PTS/min]",
}

NOMINAL_COVERAGE = 0.80

# the column the single-feature ablation removes.
ABLATION_FEATURE = "exp_depth_rank"


def cohort_masks(valid: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """(label, mask) for every reporting cohort: minutes tiers, then event cohorts.

    the cohorts are defined on the dataset's own columns rather than on anything a
    model produced, so every pass partitions the same rows the same way. a row
    whose cohort column is null belongs to no event cohort rather than to the
    control.
    """
    out: list[tuple[str, np.ndarray]] = []
    if "MIN_TIER" in valid.columns:
        tiers = valid["MIN_TIER"].to_numpy()
        out.extend((tier, tiers == tier) for tier in TIER_ORDER)
    for label, column, comparison, threshold in (
        *EVENT_COHORTS, *V4_DESCRIPTIVE_COHORTS
    ):
        if column not in valid.columns:
            continue
        values = pd.to_numeric(valid[column], errors="coerce").to_numpy(dtype=float)
        known = np.isfinite(values)
        if comparison == "quantile>=":
            if not known.any():
                continue
            cut = float(np.quantile(values[known], threshold))
            mask = values >= cut
            # a near-constant column makes ">= q90" select everything, so the
            # cohort is undefined on this frame rather than a copy of ALL.
            share = float((mask & known).sum()) / float(known.sum())
            if share > 0.5:
                log.warning(
                    "cohort %r would cover %.0f%% of rows because %s is (near-)"
                    "constant on this frame; SKIPPED rather than reported as a "
                    "duplicate of ALL", label, 100.0 * share, column,
                )
                continue
        elif comparison == ">=":
            mask = values >= threshold
        else:
            mask = values < threshold
        out.append((label, mask & known))
    return out


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, task, origin, model, metric, value, n, segment="ALL") -> None:
        self.rows.append({
            "task": task, "origin": origin, "model": model, "segment": segment,
            "metric": metric, "value": float(value), "n": int(n),
        })

    def _segments(self, task, origin, model, valid, metric, fn, y_true, y_pred) -> None:
        self.record(task, origin, model, metric, fn(y_true, y_pred), len(y_true))
        for label, mask in cohort_masks(valid):
            if mask.sum() == 0:
                continue
            self.record(task, origin, model, metric, fn(y_true[mask], y_pred[mask]),
                        int(mask.sum()), segment=label)

    def seg_mae(self, task, origin, model, valid, y_true, y_pred) -> None:
        self._segments(task, origin, model, valid, "MAE", mae, y_true, y_pred)

    def seg_brier(self, task, origin, model, valid, y_true, y_pred) -> None:
        self._segments(task, origin, model, valid, "Brier", brier, y_true, y_pred)

    def gain(self, task, origin, model, gains: pd.Series, n: int) -> None:
        """one row per feature. metric 'Gain', feature name in the segment column."""
        for feature, value in gains.items():
            self.record(task, origin, model, "Gain", value, n, segment=str(feature))

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def split(df: pd.DataFrame, vstart, vend) -> tuple[pd.DataFrame, pd.DataFrame]:
    vstart, vend = pd.Timestamp(vstart), pd.Timestamp(vend)
    train = df[df["GAME_DATE"] < vstart]
    valid = df[(df["GAME_DATE"] >= vstart) & (df["GAME_DATE"] <= vend)]
    return train.copy(), valid.copy()


def season_opener(frame: pd.DataFrame, vend: str | pd.Timestamp) -> pd.Timestamp:
    """the first regular-season game date of the season that holds ``vend``."""
    rows = frame
    if COMPETITION_COL in rows.columns:
        rows = rows[rows[COMPETITION_COL].isin(TRAINING_COMPETITIONS)]
    dates = pd.to_datetime(rows["GAME_DATE"])
    upto = dates <= pd.Timestamp(vend)
    if not upto.any():
        raise SystemExit(f"no regular-season game on or before {vend}; no opener to clamp to")
    season = rows.loc[dates[upto].idxmax(), "SEASON"]
    return pd.Timestamp(dates[rows["SEASON"] == season].min()).normalize()


def clamp_to_opener(
    frame: pd.DataFrame, origins: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """each window's start replaced by its season's opener as the data records it."""
    out: list[tuple[str, str, str]] = []
    for origin, vstart, vend in origins:
        opener = season_opener(frame, vend)
        if pd.Timestamp(vstart) != opener:
            log.warning("origin %s: typed start %s is not the opener %s; using the opener",
                        origin, vstart, opener.date())
        out.append((origin, opener.strftime("%Y-%m-%d"), vend))
    return out
