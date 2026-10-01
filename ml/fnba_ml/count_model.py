"""a count-model challenger for the rare per-game stats. nothing here is served.

MODEL.md 12.7 names the gap: the served ``E[BLK | plays] = E[MIN | plays] x
rate(BLK/min)`` treats a block rate as a per-minute constant, and for a stat that
averages under one event a game a count model is the obvious challenger nobody
has run. this module is that challenger.

the production tournament DID run count-native rates (TOURNAMENT.md M6 Poisson
with a log-minutes offset, M7 Tweedie on rates) and both lost by under 0.5%. this
is not a rerun of M6. it differs in exactly three ways:

  (a) targets. COUNT_TARGETS = STL, BLK, FG3M, TOV, the rare stats the 12.7 gap is
      about. M6/M7 were fitted only for PTS and AST, where a per-minute constant
      is least objectionable.
  (b) parameters. small trees selected on inner folds inside each origin's
      training window, over COUNT_PARAM_GRID (n_estimators {100, 200},
      num_leaves {7, 15}, min_child_samples {100, 300}). M6 ran untuned on
      config.LGBM_PARAMS (400 trees, 31 leaves), which TOURNAMENT.md section 5
      item 5 records as the reason its number is not the best the family can do.
  (c) the offset. ``log(max(E[MIN | play], 1))``, where E[MIN | play] is the
      out-of-fold minutes prediction (MIN_PRED) on validation rows and the
      strictly prior ``ewma_MIN`` on training rows. M6 used a cross-fit OOF
      minutes column on both sides. the floor of one minute keeps a near-zero
      minutes forecast from sending the log to minus infinity. the init score
      also carries the training league log rate (below), which M6 did not.

LightGBM objective ``poisson`` with ``init_score = offset + log(league rate)``,
the league rate being sum(stat) / sum(offset minutes) over the training rows, so
the booster learns a log per-minute intensity relative to the league and
``exp(raw_score + log(league rate) + offset)`` is the conditional count. the
constant does not change the offset; it only spares the trees from spending
themselves on an intercept, since LightGBM does not boost from the average
when an init_score is supplied. features are the served per-minute rate column for the stat,
a handful of usage and context columns, and the v4 opponent columns when the
frame carries them. output is a conditional expectation per appearance row; the
unconditional estimate is ``P_PLAY x`` that, exactly as in the served
composition.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import CHAMPIONS, RANDOM_STATE
from .eval_rates import INNER_FOLD_DAYS, INNER_FOLDS, inner_folds
from .models import (
    MIN_PRED,
    LeakageError,
    MinutesModel,
    PerMinuteRate,
    decomposed_estimate,
    validate_minutes_out_of_fold,
)

log = logging.getLogger(__name__)

COUNT_TARGETS: tuple[str, ...] = ("STL", "BLK", "FG3M", "TOV")

COUNT_CORE_FEATURES: tuple[str, ...] = (
    "usg_ewma",
    "roll10_MIN",
    "exp_vacated_usg",
    "p_star_out",
    "OPP_DEF_FORM",
    "IS_HOME",
    "TEAM_REST_DAYS",
)

# v4 matchup columns, absent from a v3 dataset.
COUNT_OPTIONAL_FEATURES: tuple[str, ...] = (
    "opp_pace",
    "opp_def_rating",
    "opp_fg3a_allowed_per100",
)

COUNT_PARAM_GRID: dict[str, tuple[int, ...]] = {
    "n_estimators": (100, 200),
    "num_leaves": (7, 15),
    "min_child_samples": (100, 300),
}

COUNT_BASE_PARAMS: dict[str, object] = {
    "objective": "poisson",
    "learning_rate": 0.05,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": RANDOM_STATE,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
    "n_jobs": -1,
}

OFFSET_MINUTES_FLOOR = 1.0

TRAIN_MINUTES_COL = "ewma_MIN"

COUNT_PRED_CUTOFF = "COUNT_PRED_CUTOFF"


def count_pred_column(target: str) -> str:
    return f"COUNT_PRED_{target}"


def param_grid(grid: dict[str, tuple[int, ...]] = COUNT_PARAM_GRID) -> list[dict[str, int]]:
    """every grid member, in a fixed order so a tie resolves the same way twice."""
    keys = sorted(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def count_features(frame: pd.DataFrame, target: str) -> list[str]:
    """the design columns for one stat, in a fixed order."""
    rate_col = PerMinuteRate(target).column
    core = [rate_col, *COUNT_CORE_FEATURES]
    missing = [c for c in core if c not in frame.columns]
    if missing:
        raise ValueError(f"{target}: count model needs {', '.join(missing)}")
    optional = [c for c in COUNT_OPTIONAL_FEATURES if c in frame.columns]
    skipped = [c for c in COUNT_OPTIONAL_FEATURES if c not in frame.columns]
    if skipped:
        log.info("%s count model: optional %s absent, skipped", target, ", ".join(skipped))
    return core + optional


def log_offset(minutes: np.ndarray | pd.Series, fill: float) -> np.ndarray:
    """log(max(minutes, 1)); a missing minutes value takes ``fill`` first."""
    values = pd.to_numeric(pd.Series(np.asarray(minutes, dtype=float)), errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan).fillna(fill).to_numpy(dtype=float)
    return np.log(np.clip(values, OFFSET_MINUTES_FLOOR, None))


def poisson_deviance(y_true, mu) -> float:
    """mean unit poisson deviance, with the y log(y / mu) term zero where y is zero."""
    y = np.asarray(y_true, dtype=float)
    m = np.clip(np.asarray(mu, dtype=float), 1e-9, None)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / m), 0.0)
    return float(np.mean(2.0 * (term - (y - m))))


@dataclass
class CountModel:
    """E[stat | plays] from a poisson booster with a log-minutes offset.

    ``cutoff`` is the exclusive upper bound of the training window and travels
    with every value :meth:`attach` writes, like MinutesModel.
    """

    target: str
    params: dict[str, int] = field(default_factory=lambda: dict(param_grid()[0]))
    feature_cols: list[str] = field(default_factory=list)
    cutoff: pd.Timestamp | None = None
    offset_fill: float = OFFSET_MINUTES_FLOOR
    base_log_rate: float = 0.0
    estimator: lgb.LGBMRegressor | None = None

    def fit(self, train_appearances: pd.DataFrame, cutoff: pd.Timestamp) -> "CountModel":
        cutoff = pd.Timestamp(cutoff)
        late = pd.to_datetime(train_appearances["GAME_DATE"]) >= cutoff
        if late.any():
            raise LeakageError(
                f"{int(late.sum())} training rows are on or after the cutoff "
                f"{cutoff.date()} - the training window would include the games "
                f"it is meant to predict"
            )
        rows = train_appearances[train_appearances[self.target].notna()]
        if rows.empty:
            raise ValueError(f"{self.target}: no appearance rows to fit a count model on")
        self.feature_cols = count_features(rows, self.target)
        self.cutoff = cutoff
        # training rows have no out-of-fold minutes forecast, and an in-sample one
        # (or realized MIN) would be fitted on the very games it offsets; ewma_MIN
        # is the strictly prior quantity of the same kind.
        prior_minutes = pd.to_numeric(rows[TRAIN_MINUTES_COL], errors="coerce")
        median = float(prior_minutes.median())
        self.offset_fill = median if np.isfinite(median) else OFFSET_MINUTES_FLOOR
        offset = log_offset(prior_minutes, self.offset_fill)
        y = rows[self.target].to_numpy(dtype=float)
        # lightgbm skips boost_from_average when init_score is given, so without
        # the league log rate a 100-tree booster spends itself climbing to it.
        total = float(y.sum())
        self.base_log_rate = (
            float(np.log(total / np.exp(offset).sum())) if total > 0 else float(np.log(1e-6))
        )
        self.estimator = lgb.LGBMRegressor(**{**COUNT_BASE_PARAMS, **self.params})
        self.estimator.fit(
            rows[self.feature_cols], y, init_score=offset + self.base_log_rate
        )
        return self

    def predict_conditional(
        self, frame: pd.DataFrame, minutes: np.ndarray | pd.Series
    ) -> np.ndarray:
        """exp(raw score + log offset) for an explicit minutes forecast."""
        if self.estimator is None:
            raise RuntimeError(f"{self.target} count model is not fitted")
        # raw_score excludes the fit-time init_score, so the offset is added back
        raw = self.estimator.predict(frame[self.feature_cols], raw_score=True)
        return np.exp(
            np.asarray(raw, dtype=float) + self.base_log_rate
            + log_offset(minutes, self.offset_fill)
        )

    def attach(self, frame: pd.DataFrame) -> pd.DataFrame:
        """frame + COUNT_PRED_<stat> + its cutoff, offset by the stamped MIN_PRED."""
        validate_minutes_out_of_fold(frame)
        minutes_cutoff = pd.to_datetime(frame["MIN_PRED_CUTOFF"])
        if (minutes_cutoff != self.cutoff).any():
            raise LeakageError(
                f"{self.target}: the count model was trained through "
                f"{pd.Timestamp(self.cutoff).date()} but the minutes forecast it is "
                f"offset by carries a different cutoff"
            )
        out = frame.copy()
        out[count_pred_column(self.target)] = self.predict_conditional(frame, frame[MIN_PRED])
        out[COUNT_PRED_CUTOFF] = self.cutoff
        return out


def count_unconditional(scored: pd.DataFrame, conditional: np.ndarray) -> np.ndarray:
    """P_PLAY x the conditional count, behind the same out-of-fold guard."""
    return decomposed_estimate(scored, conditional)


def select_count_params(
    train: pd.DataFrame,
    target: str,
    grid: dict[str, tuple[int, ...]] = COUNT_PARAM_GRID,
    minutes_kind: str = CHAMPIONS["minutes"],
    minutes_features: list[str] | None = None,
    n_folds: int = INNER_FOLDS,
    days: int = INNER_FOLD_DAYS,
) -> tuple[dict[str, int], pd.DataFrame]:
    """pick grid parameters on inner folds carved off the end of ``train``.

    each inner fold's offset is an out-of-fold minutes forecast from a model fit
    strictly before the fold, mirroring the validation side. the score is mean
    poisson deviance; ties go to the earlier grid member. returns the chosen
    parameters and the per-(fold, member) evidence.
    """
    from .features import available_features  # noqa: PLC0415

    frame = train.copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    members = param_grid(grid)
    feats = minutes_features or available_features(frame)
    rows: list[dict[str, object]] = []
    for fold, start, stop in inner_folds(frame, n_folds, days):
        inner_train = frame[(frame["GAME_DATE"] < start) & (frame["PLAYED"] == 1)]
        inner_valid = frame[
            (frame["GAME_DATE"] >= start) & (frame["GAME_DATE"] < stop)
            & (frame["PLAYED"] == 1)
        ]
        inner_valid = inner_valid[inner_valid[target].notna()]
        if inner_train.empty or inner_valid.empty:
            log.warning("%s count selection: %s has an empty side; skipped", target, fold)
            continue
        minutes = MinutesModel(kind=minutes_kind).fit(inner_train, feats, start)
        minutes_pred = minutes.predict(inner_valid)
        y = inner_valid[target].to_numpy(dtype=float)
        for index, params in enumerate(members):
            model = CountModel(target, params=params).fit(inner_train, start)
            mu = model.predict_conditional(inner_valid, minutes_pred)
            rows.append({
                "fold": fold, "member": index, **params,
                "deviance": poisson_deviance(y, mu), "n": int(len(y)),
            })
    evidence = pd.DataFrame(rows)
    if evidence.empty:
        log.warning("%s count selection: no usable inner fold; first grid member used",
                    target)
        return dict(members[0]), evidence
    pooled = evidence.groupby("member")["deviance"].mean()
    best = int(pooled.sort_index().idxmin())
    return dict(members[best]), evidence
