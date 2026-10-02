"""serving-time preseason minutes prior, applied only to Pre Season rows (MODEL.md 20.6).

off by default. run A never holds a preseason game, so the frozen prospective run is
untouched whether the switch is on or off.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .coherence import _cond, _shift_quantiles, _uncond
from .config import (
    COMPETITION_BY_SEASON_TYPE,
    COMPETITION_COL,
    MINUTES_TARGET,
    PRESEASON_MINUTES_PRIOR,
    PRESEASON_PRIOR_WEIGHT,
    PRESEASON_SEASON_TYPE,
    PRODUCTION_TARGETS,
    TIER_BASIS,
)
from .features import assign_minutes_tier
from .intervals import quantile_columns
from .models import P_PLAY

log = logging.getLogger(__name__)

PRESEASON_PRIOR_OFF = "off"
PRESEASON_PRIOR_ON = "on"
PRESEASON_PRIOR_CHOICES: tuple[str, ...] = (PRESEASON_PRIOR_OFF, PRESEASON_PRIOR_ON)

PRESEASON_PRIOR_APPLIED = "PRESEASON_PRIOR_APPLIED"
PRESEASON_PRIOR_TIER = "PRESEASON_PRIOR_TIER"

ROW_KEYS = ["PLAYER_ID", "GAME_ID"]


def preseason_rows(features: pd.DataFrame) -> pd.Series:
    """True where the row is a Pre Season game, read from SEASON_TYPE or COMPETITION."""
    if "SEASON_TYPE" in features.columns:
        return features["SEASON_TYPE"].astype(str).eq(PRESEASON_SEASON_TYPE)
    if COMPETITION_COL in features.columns:
        return features[COMPETITION_COL].astype(str).eq(
            COMPETITION_BY_SEASON_TYPE[PRESEASON_SEASON_TYPE]
        )
    # a frame that cannot say which rows are preseason must not pass as having none.
    raise ValueError(
        f"the preseason prior needs SEASON_TYPE or {COMPETITION_COL} on the feature "
        f"frame to find Pre Season rows"
    )


def _row_facts(predictions: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """per prediction row, in prediction order: is it preseason, and its minutes tier."""
    if TIER_BASIS not in features.columns:
        raise ValueError(f"the preseason prior needs {TIER_BASIS} on the feature frame")
    facts = features[ROW_KEYS].copy()
    facts["_preseason"] = preseason_rows(features).to_numpy()
    facts["_tier"] = assign_minutes_tier(features).to_numpy()
    if facts.duplicated(ROW_KEYS).any():
        raise ValueError("the feature frame has more than one row per player-game")
    joined = predictions[ROW_KEYS].merge(facts, on=ROW_KEYS, how="left", validate="m:1")
    missing = joined["_preseason"].isna()
    if missing.any():
        raise ValueError(
            f"{int(missing.sum())} prediction row(s) have no matching feature row"
        )
    return joined


def apply_preseason_minutes_prior(
    predictions: pd.DataFrame,
    features: pd.DataFrame,
    weight: float = PRESEASON_PRIOR_WEIGHT,
    prior: dict[str, float] = PRESEASON_MINUTES_PRIOR,
) -> pd.DataFrame:
    """blend Pre Season conditional minutes toward the tier prior and carry it through.

    production stats are rate x minutes, so their conditional and unconditional
    expectations take the minutes ratio; quantiles move by the conditional delta.
    P_PLAY is never touched and every other row is left exactly as it was.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"preseason prior weight must be in [0, 1], got {weight}")
    facts = _row_facts(predictions, features)
    mask = facts["_preseason"].to_numpy(dtype=bool)
    tiers = facts["_tier"].to_numpy(dtype=object)

    out = predictions.copy()
    out[PRESEASON_PRIOR_APPLIED] = mask
    out[PRESEASON_PRIOR_TIER] = pd.Series(
        np.where(mask, tiers, None), index=out.index, dtype=object
    )
    if not mask.any():
        log.info("preseason prior: no Pre Season rows, nothing adjusted")
        return out

    index = out.index[mask]
    sub = out.loc[index].copy()
    target_minutes = np.array([prior[t] for t in tiers[mask]], dtype=float)
    old_minutes = sub[_cond(MINUTES_TARGET)].to_numpy(dtype=float)
    new_minutes = weight * target_minutes + (1.0 - weight) * old_minutes
    usable = np.isfinite(old_minutes) & (old_minutes > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(usable, new_minutes / old_minutes, 1.0)
    if not usable.all():
        log.warning(
            "preseason prior: %d row(s) had no positive model minutes; their "
            "production stats are left unscaled", int((~usable).sum()),
        )

    p_play = sub[P_PLAY].to_numpy(dtype=float)
    touched: list[str] = []
    for target in (MINUTES_TARGET, *PRODUCTION_TARGETS):
        cond_col = _cond(target)
        if cond_col not in sub.columns:
            continue
        old = sub[cond_col].to_numpy(dtype=float)
        new = new_minutes if target == MINUTES_TARGET else old * ratio
        sub[cond_col] = new
        uncond_col = _uncond(target)
        if target == MINUTES_TARGET:
            sub[uncond_col] = np.clip(p_play * new, 0.0, None)
        elif uncond_col in sub.columns:
            sub[uncond_col] = sub[uncond_col].to_numpy(dtype=float) * ratio
        _shift_quantiles(sub, target, new - old)
        touched += [cond_col, uncond_col, *quantile_columns(target).values()]

    for column in dict.fromkeys(c for c in touched if c in sub.columns):
        out.loc[index, column] = sub[column].to_numpy()

    log.info(
        "preseason prior: %d Pre Season row(s) at weight %.2f, mean E[MIN|plays] "
        "%.2f -> %.2f",
        int(mask.sum()), weight, float(np.nanmean(old_minutes)),
        float(np.nanmean(new_minutes)),
    )
    return out
