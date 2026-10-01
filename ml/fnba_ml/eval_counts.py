"""evaluation hooks for the two phase-2 challengers: count models and tiered intervals.

both endpoint functions return the same tidy frame,
``[variant, endpoint, stat, cohort, n, value]``, so the bracket script can stack
them across origins without knowing which challenger produced a row. nothing in
this module reads the dataset at import time.

column contract on a scored frame (one row per scheduled player-game):

  PLAYED, MIN_TIER, the realized stat columns
  COND_<variant>_<stat>    E[stat | plays] from that variant
  UNCOND_<variant>_<stat>  E[stat], P_PLAY x the conditional
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import CHAMPIONS, TIER_ORDER
from .count_model import (
    COUNT_PARAM_GRID,
    COUNT_TARGETS,
    CountModel,
    count_pred_column,
    count_unconditional,
    poisson_deviance,
    select_count_params,
)
from .intervals import (
    POOLED_KEY,
    QUANTILE_TARGETS,
    QuantileOffsets,
    apply_quantiles,
    apply_quantiles_by_tier,
    fit_residual_quantiles,
    fit_residual_quantiles_by_tier,
    tier_labels,
)
from .models import (
    AvailabilityModel,
    MinutesModel,
    PerMinuteRate,
    mae,
    minutes_propagated_estimate,
)

log = logging.getLogger(__name__)

VARIANT_CHAMPION = "champion_rate"
VARIANT_COUNT = "count_poisson"
COUNT_VARIANTS: tuple[str, ...] = (VARIANT_CHAMPION, VARIANT_COUNT)

VARIANT_POOLED = "pooled"
VARIANT_TIERED = "tiered"

ENDPOINT_COND_MAE = "cond_MAE"
ENDPOINT_UNCOND_MAE = "uncond_MAE"
ENDPOINT_DEVIANCE = "cond_poisson_deviance"
ENDPOINT_COVERAGE = "coverage"
ENDPOINT_WIDTH = "mean_width"

INTERVAL_NOMINAL = 0.80

ENDPOINT_COLUMNS: list[str] = ["variant", "endpoint", "stat", "cohort", "n", "value"]

HOLDOUT_DAYS = 28


def cond_column(variant: str, stat: str) -> str:
    return f"COND_{variant}_{stat}"


def uncond_column(variant: str, stat: str) -> str:
    return f"UNCOND_{variant}_{stat}"


def _cohorts(frame: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """ALL, then each minutes tier present, in TIER_ORDER."""
    out = [(POOLED_KEY, np.ones(len(frame), dtype=bool))]
    tiers = tier_labels(frame)
    out.extend((tier, tiers == tier) for tier in TIER_ORDER if (tiers == tier).any())
    return out


def _tidy(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=ENDPOINT_COLUMNS)


def count_endpoints(
    scored: pd.DataFrame,
    stats: tuple[str, ...] = COUNT_TARGETS,
    variants: tuple[str, ...] = COUNT_VARIANTS,
) -> pd.DataFrame:
    """conditional MAE, unconditional MAE and conditional poisson deviance per cohort.

    conditional endpoints are read on appearances (PLAYED == 1), the unconditional
    one on every scheduled row. a variant or stat whose columns are missing is
    skipped rather than reported as zero.
    """
    played = scored["PLAYED"].to_numpy() == 1
    cohorts = _cohorts(scored)
    rows: list[dict[str, object]] = []
    for variant in variants:
        for stat in stats:
            cond_col, uncond_col = cond_column(variant, stat), uncond_column(variant, stat)
            if stat not in scored.columns or cond_col not in scored.columns:
                continue
            y = scored[stat].to_numpy(dtype=float)
            cond = scored[cond_col].to_numpy(dtype=float)
            uncond = (
                scored[uncond_col].to_numpy(dtype=float)
                if uncond_col in scored.columns else None
            )
            for cohort, mask in cohorts:
                app = mask & played & np.isfinite(y)
                if app.any():
                    n = int(app.sum())
                    rows.append({"variant": variant, "endpoint": ENDPOINT_COND_MAE,
                                 "stat": stat, "cohort": cohort, "n": n,
                                 "value": mae(y[app], cond[app])})
                    rows.append({"variant": variant, "endpoint": ENDPOINT_DEVIANCE,
                                 "stat": stat, "cohort": cohort, "n": n,
                                 "value": poisson_deviance(y[app], cond[app])})
                sched = mask & np.isfinite(y)
                if uncond is not None and sched.any():
                    rows.append({"variant": variant, "endpoint": ENDPOINT_UNCOND_MAE,
                                 "stat": stat, "cohort": cohort, "n": int(sched.sum()),
                                 "value": mae(y[sched], uncond[sched])})
    return _tidy(rows)


def _interval_bounds(
    levels: tuple[float, ...], nominal: float
) -> tuple[float, float]:
    tail = round((1.0 - nominal) / 2.0, 6)
    lo, hi = tail, round(1.0 - tail, 6)
    rounded = [round(float(x), 6) for x in levels]
    if lo not in rounded or hi not in rounded:
        raise ValueError(
            f"offsets carry levels {levels}; a {nominal:.0%} interval needs {lo} and {hi}"
        )
    return levels[rounded.index(lo)], levels[rounded.index(hi)]


def interval_endpoints(
    frame: pd.DataFrame,
    point_cols: dict[str, str],
    pooled: dict[str, QuantileOffsets],
    tiered: dict[str, dict[str, QuantileOffsets]],
    nominal: float = INTERVAL_NOMINAL,
) -> pd.DataFrame:
    """coverage and mean width of the ``nominal`` central interval, pooled vs tiered.

    ``frame`` is the appearance rows being scored, ``point_cols`` maps a stat to
    the column holding the point estimate the offsets decorate. coverage counts
    an outcome on either bound as inside, matching models.quantile_coverage; width
    is measured after the value floor, so it is the width a reader would see.
    """
    cohorts = _cohorts(frame)
    tiers = tier_labels(frame)
    rows: list[dict[str, object]] = []
    for stat, point_col in point_cols.items():
        if stat not in frame.columns or stat not in pooled or stat not in tiered:
            continue
        y = frame[stat].to_numpy(dtype=float)
        point = frame[point_col].to_numpy(dtype=float)
        lo_level, hi_level = _interval_bounds(pooled[stat].levels, nominal)
        bands = {
            VARIANT_POOLED: apply_quantiles(point, pooled[stat]),
            VARIANT_TIERED: apply_quantiles_by_tier(point, tiers, tiered[stat]),
        }
        for variant, band in bands.items():
            lo, hi = band[lo_level], band[hi_level]
            inside = (y >= lo) & (y <= hi)
            for cohort, mask in cohorts:
                keep = mask & np.isfinite(y) & np.isfinite(point)
                if not keep.any():
                    continue
                n = int(keep.sum())
                rows.append({"variant": variant, "endpoint": ENDPOINT_COVERAGE,
                             "stat": stat, "cohort": cohort, "n": n,
                             "value": float(np.mean(inside[keep]))})
                rows.append({"variant": variant, "endpoint": ENDPOINT_WIDTH,
                             "stat": stat, "cohort": cohort, "n": n,
                             "value": float(np.mean((hi - lo)[keep]))})
    return _tidy(rows)


def score_count_origin(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    feature_cols: list[str],
    cutoff: pd.Timestamp,
    stats: tuple[str, ...] = COUNT_TARGETS,
    grid: dict[str, tuple[int, ...]] = COUNT_PARAM_GRID,
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """one origin's scored validation frame, champion rate vs count model per stat.

    one availability model and one minutes model, both cut at the validation
    start, feed every variant, so a difference is the production estimate and
    nothing else. returns the scored frame and the inner-fold parameter choice
    per stat.
    """
    cutoff = pd.Timestamp(cutoff)
    train_app = train[train["PLAYED"] == 1]
    availability = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(
        train, feature_cols, cutoff
    )
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feature_cols, cutoff)
    scored = minutes.attach(availability.attach(valid))

    chosen: dict[str, dict[str, int]] = {}
    for stat in stats:
        if stat not in scored.columns:
            log.warning("no %s column; it is excluded from the count comparison", stat)
            continue
        rate = PerMinuteRate(stat).fit(train_app)
        cond, uncond = minutes_propagated_estimate(scored, rate.predict(scored))
        scored[cond_column(VARIANT_CHAMPION, stat)] = cond
        scored[uncond_column(VARIANT_CHAMPION, stat)] = uncond

        params, _ = select_count_params(train, stat, grid, minutes_features=feature_cols)
        chosen[stat] = params
        model = CountModel(stat, params=params).fit(train_app, cutoff)
        attached = model.attach(scored)
        count_cond = attached[count_pred_column(stat)].to_numpy(dtype=float)
        scored[cond_column(VARIANT_COUNT, stat)] = count_cond
        scored[uncond_column(VARIANT_COUNT, stat)] = count_unconditional(scored, count_cond)
    return scored, chosen


def holdout_offsets(
    train: pd.DataFrame,
    feature_cols: list[str],
    stats: tuple[str, ...] = QUANTILE_TARGETS,
    holdout_days: int = HOLDOUT_DAYS,
) -> tuple[dict[str, QuantileOffsets], dict[str, dict[str, QuantileOffsets]]]:
    """pooled and tiered offsets from the last ``holdout_days`` of a training window.

    the same construction train.holdout_quantiles ships, fitted strictly inside
    the origin's training window so the validation rows score offsets that never
    saw them.
    """
    end = pd.Timestamp(train["GAME_DATE"].max()).normalize() + pd.Timedelta(days=1)
    split_at = end - pd.Timedelta(days=holdout_days)
    fit_rows = train[(train["GAME_DATE"] < split_at) & (train["PLAYED"] == 1)]
    held = train[(train["GAME_DATE"] >= split_at) & (train["PLAYED"] == 1)]
    if fit_rows.empty or held.empty:
        return {}, {}
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(fit_rows, feature_cols, split_at)
    minutes_pred = minutes.predict(held)
    pooled: dict[str, QuantileOffsets] = {}
    tiered: dict[str, dict[str, QuantileOffsets]] = {}
    for stat in stats:
        if stat not in held.columns:
            continue
        if stat == "MIN":
            point = minutes_pred
        else:
            point = minutes_pred * PerMinuteRate(stat).fit(fit_rows).predict(held)
        frame = held.assign(_point=np.clip(point, 0.0, None))
        pooled[stat] = fit_residual_quantiles(frame[stat], frame["_point"], stat)
        tiered[stat] = fit_residual_quantiles_by_tier(frame, stat, "_point")
    return pooled, tiered


def score_interval_origin(
    train: pd.DataFrame,
    scored: pd.DataFrame,
    feature_cols: list[str],
    stats: tuple[str, ...] = QUANTILE_TARGETS,
) -> pd.DataFrame:
    """interval endpoints for one origin around the champion conditional estimate."""
    pooled, tiered = holdout_offsets(train, feature_cols, stats)
    if not pooled:
        return _tidy([])
    app = scored[scored["PLAYED"] == 1].copy()
    point_cols: dict[str, str] = {}
    for stat in pooled:
        if stat == "MIN":
            app["_point_MIN"] = app["MIN_PRED"].to_numpy(dtype=float)
            point_cols[stat] = "_point_MIN"
            continue
        column = cond_column(VARIANT_CHAMPION, stat)
        if column not in app.columns:
            rate = PerMinuteRate(stat).fit(train[train["PLAYED"] == 1])
            app[column] = app["MIN_PRED"].to_numpy(dtype=float) * rate.predict(app)
        point_cols[stat] = column
    return interval_endpoints(app, point_cols, pooled, tiered)
