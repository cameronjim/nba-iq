"""the contextual residual production rate. a P3 candidate, never served.

    challenger rate = max(0, served rate + f(context))

``f`` is a small LightGBM regressor fitted on appearance rows to the residual
``stat / max(MIN, floor) - served rate``, so it can only move the champion's
per-minute rate, never replace it. the composition is unchanged: the challenger
rate goes through :func:`models.minutes_propagated_estimate` like the champion.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import (
    POS_GROUP_ORDER,
    RATE_CONTEXT_COLS,
    RATE_MINUTES_FLOOR,
    RATE_MODEL_CUTOFF,
    RATE_MODEL_PARAMS,
    RATE_MODEL_TARGETS,
    RATE_TARGETS,
)
from .models import (
    MIN_PRED,
    MIN_PRED_CUTOFF,
    LeakageError,
    PerMinuteRate,
    minutes_propagated_estimate,
    validate_out_of_fold,
)

log = logging.getLogger(__name__)

SERVED_RATE_FEATURE = "rate_ctx_served_rate"
MINUTES_FEATURE = "rate_ctx_minutes"
POS_FEATURES: tuple[str, ...] = tuple(f"rate_ctx_pos_{g}" for g in POS_GROUP_ORDER)

# the strictly prior minutes column that stands in for MIN_PRED on training rows.
TRAINING_MINUTES_COL = "ewma_MIN"

DESIGN_COLUMNS: tuple[str, ...] = (
    SERVED_RATE_FEATURE, *RATE_CONTEXT_COLS, MINUTES_FEATURE, *POS_FEATURES,
)


def challenger_rate_column(target: str) -> str:
    return f"RATE_RESID_{target}"


def residual_target(
    frame: pd.DataFrame,
    target: str,
    served_rate: np.ndarray,
    minutes_floor: float = RATE_MINUTES_FLOOR,
) -> np.ndarray:
    """stat / max(MIN, floor) minus the champion's per-minute rate, row by row."""
    minutes = frame["MIN"].to_numpy(dtype=float)
    stat = frame[target].to_numpy(dtype=float)
    return stat / np.maximum(minutes, minutes_floor) - np.asarray(served_rate, dtype=float)


def design_matrix(
    frame: pd.DataFrame, served_rate: np.ndarray, minutes: np.ndarray
) -> pd.DataFrame:
    """the model's inputs in a fixed column order. reads no outcome column."""
    missing = [c for c in RATE_CONTEXT_COLS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"the frame is missing {len(missing)} rate context column(s): "
            f"{', '.join(missing)}. run build_v4_dataset.py first."
        )
    out = pd.DataFrame(index=range(len(frame)))
    out[SERVED_RATE_FEATURE] = np.asarray(served_rate, dtype=float)
    for col in RATE_CONTEXT_COLS:
        out[col] = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
    out[MINUTES_FEATURE] = np.asarray(minutes, dtype=float)
    if "POS_GROUP" in frame.columns:
        # pd.NA does not compare to a string, so unknowns become "" first
        groups = frame["POS_GROUP"].astype(object).fillna("").to_numpy()
    else:
        log.warning("no POS_GROUP on the frame; every position indicator is 0")
        groups = np.full(len(frame), "", dtype=object)
    for group, col in zip(POS_GROUP_ORDER, POS_FEATURES):
        out[col] = (groups == group).astype(float)
    return out[list(DESIGN_COLUMNS)]


def _appearances(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame
    if "PLAYED" in rows.columns:
        rows = rows[rows["PLAYED"] == 1]
    return rows[rows["MIN"] > 0]


@dataclass
class ResidualRateModel:
    """one stat's residual rate model, with the cutoff it was trained before.

    the cutoff is required up front, as for MinutesModel, so a model can never
    exist without the information boundary that makes its output auditable.
    """

    cutoff: pd.Timestamp
    params: dict[str, object] = field(default_factory=lambda: dict(RATE_MODEL_PARAMS))
    minutes_floor: float = RATE_MINUTES_FLOOR
    target: str | None = None
    rate: PerMinuteRate | None = None
    estimator: lgb.LGBMRegressor | None = None

    def fit(self, train_appearances: pd.DataFrame, target: str) -> "ResidualRateModel":
        if target not in RATE_TARGETS:
            raise ValueError(f"{target} has no per-minute rate to take a residual from")
        if target not in RATE_MODEL_TARGETS:
            log.warning("%s is outside RATE_MODEL_TARGETS; fitting it anyway", target)
        self.cutoff = pd.Timestamp(self.cutoff)
        late = pd.to_datetime(train_appearances["GAME_DATE"]) >= self.cutoff
        if late.any():
            raise LeakageError(
                f"{int(late.sum())} training rows are on or after the cutoff "
                f"{self.cutoff.date()} - the training window would include the games "
                f"it is meant to predict"
            )
        rows = _appearances(train_appearances)
        if rows.empty:
            raise ValueError("no appearance rows with minutes > 0 to fit on")
        self.target = target
        self.rate = PerMinuteRate(target, minutes_floor=self.minutes_floor).fit(rows)
        served = self.rate.predict(rows)
        y = residual_target(rows, target, served, self.minutes_floor)
        # an in-fold MIN_PRED on training rows would be fitted on their own
        # minutes, so the strictly prior EWMA stands in for it.
        minutes = pd.to_numeric(
            rows[TRAINING_MINUTES_COL], errors="coerce"
        ).to_numpy(dtype=float)
        self.estimator = lgb.LGBMRegressor(**self.params)
        self.estimator.fit(design_matrix(rows, served, minutes), y)
        return self

    def _check_fitted(self) -> None:
        if self.estimator is None or self.rate is None:
            raise RuntimeError("ResidualRateModel.fit must run before predict")

    def served_rate(self, frame: pd.DataFrame) -> np.ndarray:
        self._check_fitted()
        return self.rate.predict(frame)

    def predict_residual(self, frame: pd.DataFrame) -> np.ndarray:
        """the fitted correction to the champion rate, per row."""
        self._check_fitted()
        if MIN_PRED not in frame.columns:
            raise ValueError(
                f"scoring needs the minutes model's {MIN_PRED}; attach it first so "
                f"the rate model reads the same minutes the composition multiplies"
            )
        served = self.rate.predict(frame)
        minutes = frame[MIN_PRED].to_numpy(dtype=float)
        return self.estimator.predict(design_matrix(frame, served, minutes))

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """the challenger per-minute rate: served + residual, floored at 0."""
        return np.clip(self.served_rate(frame) + self.predict_residual(frame), 0.0, None)

    def attach(self, frame: pd.DataFrame) -> pd.DataFrame:
        """return frame + the challenger rate + the cutoff that makes it auditable."""
        out = frame.copy()
        out[challenger_rate_column(str(self.target))] = self.predict(frame)
        out[RATE_MODEL_CUTOFF] = self.cutoff
        return out

    def feature_gain(self) -> pd.Series:
        self._check_fitted()
        return pd.Series(
            self.estimator.booster_.feature_importance("gain"), index=list(DESIGN_COLUMNS)
        ).sort_values(ascending=False)


def residual_rate_estimates(
    scored_frame: pd.DataFrame, models: Mapping[str, ResidualRateModel]
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """(conditional, unconditional) per stat from the challenger rate.

    every rate must be out of fold and share the minutes model's cutoff, for the
    same reason assert_same_cutoff exists for P(play) and minutes.
    """
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for target, model in models.items():
        column = challenger_rate_column(target)
        stamped = model.attach(scored_frame)
        validate_out_of_fold(stamped, column, RATE_MODEL_CUTOFF, f"rate[{target}]")
        if MIN_PRED_CUTOFF in stamped.columns:
            rate_cutoff = pd.to_datetime(stamped[RATE_MODEL_CUTOFF])
            minutes_cutoff = pd.to_datetime(stamped[MIN_PRED_CUTOFF])
            if (rate_cutoff != minutes_cutoff).any():
                raise LeakageError(
                    f"the {target} rate model and the minutes model were trained to "
                    f"different cutoffs; the composition must share one"
                )
        out[target] = minutes_propagated_estimate(
            scored_frame, stamped[column].to_numpy(dtype=float)
        )
    return out
