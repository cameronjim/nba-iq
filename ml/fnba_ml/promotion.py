"""the P2 promotion decision: paired block bootstrap over a feature-set bracket.

WHAT THIS MODULE DECIDES. Given two passes of the evaluation ladder over identical
rows and identical origins - ``v3-honest`` (the incumbent, and the frozen served
contract) and ``v4`` (the candidate) - it answers one question with one number per
endpoint: does the candidate clear the bar pre-registered in ``config``'s P2 block?

THE BAR, restated here so a reader of this file does not have to hold two files at
once (``config.P2_PROMOTION_FLOOR`` and friends are the authority):

  * a paired 7-day moving-block bootstrap over game dates, 95% percentile CI
    EXCLUDING ZERO, on the pooled deltas;
  * AND at least ``config.P2_PROMOTION_FLOOR`` (1%) relative improvement on
    minutes MAE **or** availability Brier;
  * AND no reported cohort regressing by more than
    ``config.P2_COHORT_REGRESSION_TOLERANCE`` (1%).

WHY THE BOOTSTRAP IS IMPORTED RATHER THAN REWRITTEN. The 7-day moving-block
bootstrap is a FROZEN CONVENTION of this repo: it is the instrument the production
tournament pre-registered and closed PTS/AST under, and MODEL.md 13.6's "one look per
feature version" rule explicitly points at that tournament as the precedent this
phase follows. A second implementation would be a second set of edge cases in the
block-sum arithmetic, the within-origin resampling and the ratio-of-sums estimator,
and the first time the two disagreed nobody would know which was right. So
``ml/experiments/production_tournament/bootstrap.py`` is loaded BY PATH:

  * ``ml/experiments/`` is read-only in this phase, so the module cannot be moved
    into ``fnba_ml`` without editing a directory this phase does not own;
  * it is pure - numpy, pandas, dataclasses, and no relative imports - so loading it
    standalone is exact rather than approximately exact;
  * ``importlib`` by path is used rather than a ``sys.path`` insertion because
    ``fnba_ml`` is imported from several working directories and a path-dependent
    import would work in the test suite and fail in a CLI.

If the file ever moves, this raises at import time with a message naming it, which
is the correct failure: a promotion decision computed with a silently different
instrument is worse than no decision.

THE PAIRING, and why it is by ROW rather than by cohort mean. Both passes score the
SAME validation rows with the SAME estimator class, differing only in the feature
list, so row *i*'s two absolute errors are a matched pair. Differencing them first
removes all the between-row variance - which is enormous, since a star and a
two-way player are in the same pool - and leaves only the variance of the effect.
A bootstrap over unpaired per-cohort means would be testing whether two noisy
averages differ and would need several times the data to see a 1% effect.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from types import ModuleType

import numpy as np
import pandas as pd

from .config import (
    ML_ROOT,
    P2_COHORT_REGRESSION_TOLERANCE,
    P2_PROMOTION_ENDPOINTS,
    P2_PROMOTION_FLOOR,
    RANDOM_STATE,
)

log = logging.getLogger(__name__)

TOURNAMENT_BOOTSTRAP_PATH = (
    ML_ROOT / "experiments" / "production_tournament" / "bootstrap.py"
)


def _load_tournament_bootstrap() -> ModuleType:
    """load the frozen bootstrap implementation by path. see the module docstring."""
    if not TOURNAMENT_BOOTSTRAP_PATH.exists():
        raise ImportError(
            f"the frozen moving-block bootstrap is not where this module expects it "
            f"({TOURNAMENT_BOOTSTRAP_PATH}). The P2 promotion rule is defined in "
            f"terms of THAT implementation; reimplementing it here would make two "
            f"instruments out of one convention. Move the file back or update this "
            f"path deliberately."
        )
    name = "_fnba_tournament_bootstrap"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOURNAMENT_BOOTSTRAP_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {TOURNAMENT_BOOTSTRAP_PATH}")
    module = importlib.util.module_from_spec(spec)
    # REGISTERED BEFORE EXECUTION, and not as tidiness. ``@dataclass`` resolves its
    # own class's annotations through ``sys.modules[cls.__module__].__dict__``, so a
    # module executed while absent from ``sys.modules`` raises AttributeError the
    # moment it declares a frozen dataclass - which ``BootstrapResult`` is.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # pragma: no cover - defensive
        del sys.modules[name]
        raise
    return module


_bootstrap = _load_tournament_bootstrap()
moving_block_bootstrap = _bootstrap.moving_block_bootstrap
BLOCK_DAYS: int = _bootstrap.BLOCK_DAYS
N_REPLICATES: int = _bootstrap.N_REPLICATES


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------
# each endpoint is (name, what the per-row loss is, which rows it is scored over).
# The two GATED ones are named in config.P2_PROMOTION_ENDPOINTS; the third is
# reported and is deliberately not a gate - unconditional PTS is downstream of both
# availability and minutes, so letting it clear the bar would be counting one win
# twice.
ENDPOINT_MINUTES = "minutes_mae"
ENDPOINT_AVAILABILITY = "availability_brier"
ENDPOINT_UNCOND_PTS = "uncond_pts_mae"

ENDPOINTS: tuple[str, ...] = (
    ENDPOINT_AVAILABILITY, ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS,
)


@dataclass(frozen=True)
class EndpointDecision:
    """one endpoint's bootstrap result plus the pre-registered verdict on it."""

    endpoint: str
    incumbent: float
    candidate: float
    relative: float      # positive = the candidate is BETTER (error went down)
    lo: float
    hi: float
    p_value: float
    n_rows: int
    n_dates: int
    is_gate: bool
    # the P3 bracket passes its own floor; the default is P2's, unchanged.
    floor: float = P2_PROMOTION_FLOOR

    @property
    def ci_excludes_zero(self) -> bool:
        return (self.lo > 0.0) or (self.hi < 0.0)

    @property
    def clears(self) -> bool:
        """the two halves of the bar, on a GATED endpoint only.

        a non-gate endpoint never "clears" anything, because clearing is a
        promotion concept and this one was pre-registered as report-only. Returning
        False for it rather than raising keeps the table uniform.
        """
        if not self.is_gate:
            return False
        return self.ci_excludes_zero and self.relative >= self.floor


def paired_endpoint_bootstrap(
    incumbent: pd.DataFrame,
    candidate: pd.DataFrame,
    endpoint: str,
    loss_col: str = "loss",
    gates: tuple[str, ...] = P2_PROMOTION_ENDPOINTS,
    floor: float = P2_PROMOTION_FLOOR,
) -> EndpointDecision:
    """paired 7-day moving-block bootstrap on one endpoint's per-row losses.

    both frames must carry ``origin``, ``GAME_DATE`` and ``loss_col``, one row per
    scored player-game, IN THE SAME ORDER. The order requirement is checked rather
    than trusted: a silent misalignment between two 30,000-row frames produces a
    perfectly plausible near-zero effect and there is nothing in the output to show
    it happened.

    the sign convention is the tournament's: ``delta = incumbent_loss -
    candidate_loss``, so POSITIVE MEANS THE CANDIDATE IS BETTER, and
    ``theta = sum(delta) / sum(incumbent_loss)`` is the relative improvement.
    """
    for name, frame in (("incumbent", incumbent), ("candidate", candidate)):
        for col in ("origin", "GAME_DATE", loss_col):
            if col not in frame.columns:
                raise ValueError(f"the {name} frame is missing {col!r}")
    if len(incumbent) != len(candidate):
        raise ValueError(
            f"the two passes scored different row counts ({len(incumbent)} vs "
            f"{len(candidate)}); a paired test needs identical rows"
        )
    if "row_key" in incumbent.columns and "row_key" in candidate.columns:
        if not incumbent["row_key"].equals(candidate["row_key"]):
            raise ValueError(
                "the two passes' row keys are not aligned; pairing them positionally "
                "would silently compare different player-games"
            )

    base = incumbent[loss_col].to_numpy(dtype=float)
    cand = candidate[loss_col].to_numpy(dtype=float)
    delta = base - cand
    result = moving_block_bootstrap(
        delta=delta,
        base_abs=base,
        dates=incumbent["GAME_DATE"],
        origins=incumbent["origin"],
        seed=RANDOM_STATE,
    )
    return EndpointDecision(
        endpoint=endpoint,
        incumbent=float(np.mean(base)),
        candidate=float(np.mean(cand)),
        relative=float(result.theta),
        lo=float(result.lo),
        hi=float(result.hi),
        p_value=float(result.p_value),
        n_rows=int(result.n_rows),
        n_dates=int(result.n_dates),
        is_gate=endpoint in gates,
        floor=float(floor),
    )


# ---------------------------------------------------------------------------
# the cohort side condition
# ---------------------------------------------------------------------------
def cohort_regressions(
    incumbent: pd.DataFrame,
    candidate: pd.DataFrame,
    cohort_col: str = "cohort",
    loss_col: str = "loss",
    tolerance: float = P2_COHORT_REGRESSION_TOLERANCE,
) -> pd.DataFrame:
    """per-cohort relative change, and which cohorts regress past the tolerance.

    a candidate that wins on average by hurting a segment has not won, and the
    segments this phase is about are small: the top decile of blowout probability is
    10% of rows and the stakes-flagged cohort is smaller still. The check is ONE-SIDED
    - a cohort improving by 40% is not a problem - and it is a SIDE CONDITION rather
    than a bootstrapped test, deliberately: bootstrapping nine cohorts would mean nine
    more chances to fail by luck on samples chosen for being small, and the
    pre-registered rule asks whether the point estimate regressed, not whether the
    regression is significant.
    """
    rows: list[dict] = []
    joint = pd.DataFrame({
        "cohort": incumbent[cohort_col].to_numpy(),
        "base": incumbent[loss_col].to_numpy(dtype=float),
        "cand": candidate[loss_col].to_numpy(dtype=float),
    })
    for cohort, group in joint.groupby("cohort", sort=True):
        base = float(group["base"].mean())
        cand = float(group["cand"].mean())
        relative = (cand - base) / base if base > 0 else float("nan")
        rows.append({
            "cohort": str(cohort),
            "n": int(len(group)),
            "incumbent": base,
            "candidate": cand,
            # positive = WORSE, matching evaluate.feature_set_comparison's delta_pct
            "delta_pct": relative,
            "regresses": bool(np.isfinite(relative) and relative > tolerance),
        })
    return pd.DataFrame(rows).sort_values("delta_pct", ascending=False).reset_index(
        drop=True
    )


@dataclass(frozen=True)
class PromotionVerdict:
    """the whole decision, as one object, with the reason it came out that way."""

    decisions: tuple[EndpointDecision, ...]
    regressions: pd.DataFrame
    promoted: bool
    reason: str


def decide(
    decisions: list[EndpointDecision] | tuple[EndpointDecision, ...],
    regressions: pd.DataFrame,
    floor: float = P2_PROMOTION_FLOOR,
    tolerance: float = P2_COHORT_REGRESSION_TOLERANCE,
) -> PromotionVerdict:
    """apply the pre-registered rule. NO JUDGEMENT IS EXERCISED HERE.

    the whole point of writing the rule into ``config`` before the bracket ran is
    that this function is arithmetic. It reports the reason in words so that a
    reader of the report does not have to re-derive which clause failed.
    """
    gates = [d for d in decisions if d.is_gate]
    cleared = [d for d in gates if d.clears]
    regressed = (
        regressions[regressions["regresses"]] if not regressions.empty
        else pd.DataFrame()
    )

    if not cleared:
        detail = "; ".join(
            f"{d.endpoint} {d.relative:+.2%} CI [{d.lo:+.2%}, {d.hi:+.2%}]"
            for d in gates
        )
        return PromotionVerdict(
            decisions=tuple(decisions), regressions=regressions, promoted=False,
            reason=(
                f"NOT PROMOTED: no gated endpoint cleared both halves of the bar "
                f"(95% CI excluding zero AND >= {floor:.0%} relative "
                f"improvement). {detail}"
            ),
        )
    if len(regressed):
        worst = regressed.iloc[0]
        return PromotionVerdict(
            decisions=tuple(decisions), regressions=regressions, promoted=False,
            reason=(
                f"NOT PROMOTED: {', '.join(d.endpoint for d in cleared)} cleared the "
                f"bar, but {len(regressed)} cohort(s) regress past the "
                f"{tolerance:.0%} tolerance - worst is "
                f"'{worst['cohort']}' at {worst['delta_pct']:+.2%} on "
                f"{int(worst['n']):,} rows"
            ),
        )
    return PromotionVerdict(
        decisions=tuple(decisions), regressions=regressions, promoted=True,
        reason=(
            f"PROMOTED: {', '.join(d.endpoint for d in cleared)} cleared the bar "
            f"(95% CI excluding zero and >= {floor:.0%}) with no cohort "
            f"regressing past {tolerance:.0%}"
        ),
    )


def decision_table(verdict: PromotionVerdict) -> pd.DataFrame:
    """the endpoint half of the verdict, as a frame for the report and the csv."""
    return pd.DataFrame([
        {
            "endpoint": d.endpoint,
            "gate": d.is_gate,
            "incumbent": d.incumbent,
            "candidate": d.candidate,
            "relative_improvement": d.relative,
            "ci_lo": d.lo,
            "ci_hi": d.hi,
            "ci_excludes_zero": d.ci_excludes_zero,
            "p_value": d.p_value,
            "clears_bar": d.clears,
            "n_rows": d.n_rows,
            "n_dates": d.n_dates,
        }
        for d in verdict.decisions
    ])
