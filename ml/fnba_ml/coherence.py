"""serving-time coherence corrections: team minutes and the points identity.

both are model-free and off by default. the frozen serving path runs with
``none``, which returns the frame it was given.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import MINUTES_TARGET, PRODUCTION_TARGETS
from .intervals import VALUE_FLOOR, quantile_columns
from .models import P_PLAY

log = logging.getLogger(__name__)

COHERENCE_NONE = "none"
COHERENCE_TEAM_MINUTES = "team_minutes"
COHERENCE_POINTS_IDENTITY = "points_identity"
COHERENCE_ALL = "all"
COHERENCE_VARIANTS: tuple[str, ...] = (
    COHERENCE_NONE, COHERENCE_TEAM_MINUTES, COHERENCE_POINTS_IDENTITY, COHERENCE_ALL,
)

# overtime is unmodelled: a regulation game is 48 minutes x 5 players.
TEAM_REGULATION_MINUTES = 240.0
TEAM_MIN_FACTOR_BOUNDS: tuple[float, float] = (0.8, 1.25)
TEAM_MIN_FACTOR = "TEAM_MIN_FACTOR"
TEAM_MIN_SUM = "TEAM_MIN_SUM_RAW"
PTS_IDENTITY_DELTA = "PTS_IDENTITY_DELTA"

TEAM_GAME_KEYS = ["GAME_ID", "TEAM_ID"]


def _cond(target: str) -> str:
    return f"E_{target}_COND"


def _uncond(target: str) -> str:
    return f"E_{target}"


def _shift_quantiles(frame: pd.DataFrame, target: str, delta: np.ndarray) -> None:
    """add the conditional delta to every quantile level of ``target`` in place."""
    for column in quantile_columns(target).values():
        if column in frame.columns:
            shifted = frame[column].to_numpy(dtype=float) + delta
            # a uniform shift keeps the levels ordered and the floor is monotone too.
            frame[column] = np.clip(shifted, VALUE_FLOOR, None)


def team_minute_sums(predictions: pd.DataFrame) -> pd.Series:
    """per-row expected team minutes, sum(P_PLAY * E_MIN_COND) over the team-game."""
    expected = predictions[P_PLAY].to_numpy(dtype=float) * predictions[
        _cond(MINUTES_TARGET)
    ].to_numpy(dtype=float)
    keyed = predictions[TEAM_GAME_KEYS].copy()
    keyed["_expected"] = np.nan_to_num(expected, nan=0.0)
    return keyed.groupby(TEAM_GAME_KEYS, sort=False)["_expected"].transform("sum")


def renormalise_team_minutes(
    predictions: pd.DataFrame,
    factor_bounds: tuple[float, float] = TEAM_MIN_FACTOR_BOUNDS,
) -> pd.DataFrame:
    """scale conditional minutes so each team-game's expected minutes sum to 240.

    production stats are rate x minutes, so their conditional and unconditional
    expectations take the same factor; quantiles move by the conditional delta.
    P_PLAY is never touched.
    """
    out = predictions.copy()
    low, high = factor_bounds
    sums = team_minute_sums(out).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = TEAM_REGULATION_MINUTES / sums
    # a team-game with no expected minutes has nothing to rescale.
    raw = np.where(sums > 0, raw, 1.0)
    factor = np.clip(raw, low, high)

    p_play = out[P_PLAY].to_numpy(dtype=float)
    for target in (MINUTES_TARGET, *PRODUCTION_TARGETS):
        cond_col = _cond(target)
        if cond_col not in out.columns:
            continue
        old = out[cond_col].to_numpy(dtype=float)
        new = old * factor
        out[cond_col] = new
        uncond_col = _uncond(target)
        if target == MINUTES_TARGET:
            out[uncond_col] = np.clip(p_play * new, 0.0, None)
        elif uncond_col in out.columns:
            out[uncond_col] = out[uncond_col].to_numpy(dtype=float) * factor
        _shift_quantiles(out, target, new - old)

    out[TEAM_MIN_SUM] = sums
    out[TEAM_MIN_FACTOR] = factor
    _log_factors(out, raw, low, high)
    return out


def _log_factors(out: pd.DataFrame, raw: np.ndarray, low: float, high: float) -> None:
    per_game = out.assign(_raw=raw).drop_duplicates(TEAM_GAME_KEYS)
    if per_game.empty:
        return
    factors = per_game[TEAM_MIN_FACTOR].to_numpy(dtype=float)
    raw_games = per_game["_raw"].to_numpy(dtype=float)
    clipped_low = int((raw_games < low).sum())
    clipped_high = int((raw_games > high).sum())
    p10, p50, p90 = np.quantile(factors, [0.1, 0.5, 0.9])
    log.info(
        "team minutes: %d team-games, factor p10 %.3f p50 %.3f p90 %.3f, "
        "%d clipped at %.2f, %d clipped at %.2f",
        len(per_game), p10, p50, p90, clipped_low, low, clipped_high, high,
    )
    if clipped_low or clipped_high:
        log.warning(
            "team minutes: %d team-game(s) needed a factor outside [%.2f, %.2f] and "
            "were clipped; their expected minutes still do not sum to %.0f",
            clipped_low + clipped_high, low, high, TEAM_REGULATION_MINUTES,
        )


def enforce_points_identity(predictions: pd.DataFrame) -> pd.DataFrame:
    """set E_PTS_COND = 2*FGM + FG3M + FTM and carry the change to E_PTS and quantiles."""
    needed = [_cond(t) for t in ("PTS", "FGM", "FG3M", "FTM")]
    missing = [c for c in needed if c not in predictions.columns]
    if missing:
        log.info("points identity skipped: frame has no %s", ", ".join(missing))
        return predictions.copy()

    out = predictions.copy()
    old = out[_cond("PTS")].to_numpy(dtype=float)
    new = (
        2.0 * out[_cond("FGM")].to_numpy(dtype=float)
        + out[_cond("FG3M")].to_numpy(dtype=float)
        + out[_cond("FTM")].to_numpy(dtype=float)
    )
    delta = new - old
    out[_cond("PTS")] = new
    out[_uncond("PTS")] = np.clip(out[P_PLAY].to_numpy(dtype=float) * new, 0.0, None)
    _shift_quantiles(out, "PTS", delta)
    out[PTS_IDENTITY_DELTA] = delta
    finite = delta[np.isfinite(delta)]
    if finite.size:
        log.info(
            "points identity: mean delta %+.3f, mean |delta| %.3f over %d rows",
            float(finite.mean()), float(np.abs(finite).mean()), finite.size,
        )
    return out


def apply_coherence(predictions: pd.DataFrame, variant: str) -> pd.DataFrame:
    """one of COHERENCE_VARIANTS; ``none`` returns the input frame itself."""
    if variant not in COHERENCE_VARIANTS:
        raise ValueError(
            f"unknown coherence variant {variant!r}; expected one of "
            f"{', '.join(COHERENCE_VARIANTS)}"
        )
    if variant == COHERENCE_NONE:
        return predictions
    out = predictions
    # team minutes first: the identity must see the rescaled makes.
    if variant in (COHERENCE_TEAM_MINUTES, COHERENCE_ALL):
        out = renormalise_team_minutes(out)
    if variant in (COHERENCE_POINTS_IDENTITY, COHERENCE_ALL):
        out = enforce_points_identity(out)
    return out
