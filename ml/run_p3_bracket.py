"""the P3 decision run: v5-stakes and the residual rate, each against the incumbent, one look."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import add_common_args, load_dataset, setup_logging  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    CANDIDATE_FEATURE_SET_V5,
    CANDIDATE_FEATURE_VERSION_V5,
    CHAMPIONS,
    DATA_DIR,
    DEV_ORIGINS,
    FEATURE_COLS,
    FEATURE_VERSION,
    P3_COHORT_REGRESSION_TOLERANCE,
    P3_PROMOTION_FLOOR,
    P3_RATE_GATED_ENDPOINTS,
    P3_V5_GATED_ENDPOINTS,
    RATE_CONTEXT_COLS,
    RATE_MODEL_TARGETS,
    RATE_TARGETS,
    REPORTS_DIR,
    SERVED_FEATURE_SET,
    V5_STAKES_FEATURE_COLS,
)
from fnba_ml.eval_core import cohort_masks, split  # noqa: E402
from fnba_ml.features import feature_set_columns  # noqa: E402
from fnba_ml.models import (  # noqa: E402
    MIN_PRED,
    P_PLAY,
    AvailabilityModel,
    MinutesModel,
    PerMinuteRate,
    coherence_clip,
    minutes_propagated_estimate,
)
from fnba_ml.promotion import (  # noqa: E402
    BLOCK_DAYS,
    ENDPOINT_AVAILABILITY,
    ENDPOINT_MINUTES,
    ENDPOINT_UNCOND_PTS,
    N_REPLICATES,
    PromotionVerdict,
    cohort_regressions,
    decide,
    decision_table,
    paired_endpoint_bootstrap,
)
from fnba_ml.rate_model import ResidualRateModel, residual_rate_estimates  # noqa: E402
from fnba_ml.registry import git_commit  # noqa: E402

log = logging.getLogger("run_p3_bracket")

COMPARISON_V5 = CANDIDATE_FEATURE_SET_V5
COMPARISON_RATE = "residual-rate"
COMPARISON_GATES: dict[str, tuple[str, ...]] = {
    COMPARISON_V5: P3_V5_GATED_ENDPOINTS,
    COMPARISON_RATE: P3_RATE_GATED_ENDPOINTS,
}
INCUMBENT_LABEL = {COMPARISON_V5: SERVED_FEATURE_SET, COMPARISON_RATE: "champion rate"}

Losses = dict[str, tuple[np.ndarray, np.ndarray]]


def rate_endpoint(target: str, conditional: bool) -> str:
    return f"{'cond' if conditional else 'uncond'}_{target.lower()}_mae"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path,
                        default=DATA_DIR / "dataset_v4.parquet")
    parser.add_argument("--version", default="p3")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--allow-overwrite", action="store_true",
        help="replace an existing decision csv. one look per version: use a new "
             "--version instead unless the previous run crashed",
    )
    return parser.parse_args(argv)


def fit_and_score(
    train_all: pd.DataFrame, valid_all: pd.DataFrame, feats: list[str],
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """the validation frame with out-of-fold P_PLAY and MIN_PRED, one cutoff."""
    train_app = train_all[train_all["PLAYED"] == 1]
    availability = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(
        train_all, feats, cutoff
    )
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feats, cutoff)
    return minutes.attach(availability.attach(valid_all))


def availability_minutes_losses(
    valid_all: pd.DataFrame, scored: pd.DataFrame, rate: PerMinuteRate
) -> Losses:
    """(per-row loss, rows it is scored over) for the three feature-set endpoints."""
    every_row = np.ones(len(valid_all), dtype=bool)
    appearances = (valid_all["PLAYED"] == 1).to_numpy()
    p = scored[P_PLAY].to_numpy(dtype=float)
    _, uncond = minutes_propagated_estimate(scored, rate.predict(valid_all))
    return {
        ENDPOINT_AVAILABILITY: (
            (p - valid_all["PLAYED"].to_numpy(dtype=float)) ** 2, every_row
        ),
        ENDPOINT_MINUTES: (
            np.abs(scored[MIN_PRED].to_numpy(dtype=float)
                   - valid_all["MIN"].to_numpy(dtype=float)),
            appearances,
        ),
        ENDPOINT_UNCOND_PTS: (
            np.abs(uncond - valid_all["PTS"].to_numpy(dtype=float)), every_row
        ),
    }


def rate_losses(
    valid_all: pd.DataFrame,
    estimates: dict[str, tuple[np.ndarray, np.ndarray]],
) -> Losses:
    """conditional MAE over appearances and unconditional MAE over every row."""
    every_row = np.ones(len(valid_all), dtype=bool)
    appearances = (valid_all["PLAYED"] == 1).to_numpy()
    out: Losses = {}
    for target, (cond, uncond) in estimates.items():
        y = valid_all[target].to_numpy(dtype=float)
        out[rate_endpoint(target, True)] = (np.abs(cond - y), appearances)
        out[rate_endpoint(target, False)] = (np.abs(uncond - y), every_row)
    return out


def emit(origin: str, valid_all: pd.DataFrame, losses: Losses) -> list[pd.DataFrame]:
    """long per-row loss frames: one block per (cohort, endpoint).

    a row belongs to several cohorts, so cohorts are extra rows rather than a column.
    """
    key = (
        valid_all["PLAYER_ID"].astype(str) + "|"
        + valid_all["GAME_ID"].astype(str) + "|"
        + valid_all["TEAM_ID"].astype(str)
    ).to_numpy()
    dates = valid_all["GAME_DATE"].to_numpy()
    every_row = np.ones(len(valid_all), dtype=bool)
    out: list[pd.DataFrame] = []
    for label, mask in (("ALL", every_row), *cohort_masks(valid_all)):
        if not mask.any():
            continue
        for endpoint, (loss, selector) in losses.items():
            sel = mask & selector
            if not sel.any():
                continue
            out.append(pd.DataFrame({
                "origin": origin,
                "endpoint": endpoint,
                "row_key": key[sel],
                "GAME_DATE": dates[sel],
                "cohort": label,
                "loss": loss[sel],
            }))
    return out


@dataclass
class BracketScores:
    """per-row losses for both comparisons, plus the coherence side outputs."""

    losses: dict[str, tuple[pd.DataFrame, pd.DataFrame]]
    clip_rates: pd.DataFrame
    coherence_inputs: list[tuple[str, pd.DataFrame, dict[str, tuple[np.ndarray, np.ndarray]],
                                 dict[str, tuple[np.ndarray, np.ndarray]]]] = field(default_factory=list)


def score_brackets(
    frame: pd.DataFrame,
    origins: list[tuple[str, str, str]],
    targets: tuple[str, ...] = RATE_MODEL_TARGETS,
    rate_params: dict[str, object] | None = None,
) -> BracketScores:
    """both comparisons over identical rows, every origin.

    the residual rate is fitted on the incumbent's pieces, so its comparison
    differs from the champion's in the per-minute rate and in nothing else.
    """
    incumbent_feats = feature_set_columns(frame, SERVED_FEATURE_SET)
    candidate_feats = feature_set_columns(frame, CANDIDATE_FEATURE_SET_V5)
    blocks: dict[str, tuple[list[pd.DataFrame], list[pd.DataFrame]]] = {
        COMPARISON_V5: ([], []), COMPARISON_RATE: ([], []),
    }
    clip_rows: list[dict] = []
    coherence_inputs = []

    for origin, vstart, vend in origins:
        train_all, valid_all = split(frame, vstart, vend)
        train_app = train_all[(train_all["PLAYED"] == 1) & (train_all["MIN"] > 0)]
        if train_all.empty or valid_all.empty or train_app.empty:
            log.warning("origin %s has an empty side; skipped", origin)
            continue
        valid_all = valid_all.reset_index(drop=True)
        cutoff = pd.Timestamp(valid_all["GAME_DATE"].min())
        log.info("origin %s: %d train / %d valid rows", origin, len(train_all),
                 len(valid_all))

        incumbent = fit_and_score(train_all, valid_all, incumbent_feats, cutoff)
        candidate = fit_and_score(train_all, valid_all, candidate_feats, cutoff)
        pts_rate = PerMinuteRate("PTS").fit(train_app)
        blocks[COMPARISON_V5][0].extend(emit(
            origin, valid_all, availability_minutes_losses(valid_all, incumbent, pts_rate)
        ))
        blocks[COMPARISON_V5][1].extend(emit(
            origin, valid_all, availability_minutes_losses(valid_all, candidate, pts_rate)
        ))

        champion = {
            target: minutes_propagated_estimate(
                incumbent, PerMinuteRate(target).fit(train_app).predict(valid_all)
            )
            for target in RATE_TARGETS if target in valid_all.columns
        }
        models = {
            target: ResidualRateModel(
                cutoff=cutoff, **({"params": dict(rate_params)} if rate_params else {})
            ).fit(train_app, target)
            for target in targets
        }
        challenger = residual_rate_estimates(incumbent, models)
        blocks[COMPARISON_RATE][0].extend(emit(
            origin, valid_all, rate_losses(valid_all, {t: champion[t] for t in targets})
        ))
        blocks[COMPARISON_RATE][1].extend(emit(
            origin, valid_all, rate_losses(valid_all, challenger)
        ))

        # the challenger family is the champion with only its own stats replaced,
        # which is what a served swap would look like downstream.
        champion_uncond = {t: pair[1] for t, pair in champion.items()}
        challenger_uncond = {**champion_uncond,
                             **{t: pair[1] for t, pair in challenger.items()}}
        for family, values in (("champion", champion_uncond),
                               (COMPARISON_RATE, challenger_uncond)):
            _, counts = coherence_clip(values)
            for constraint, n_bound in counts.items():
                clip_rows.append({
                    "origin": origin, "family": family, "constraint": constraint,
                    "clip_rate": n_bound / len(valid_all), "n": len(valid_all),
                })
        # the scored incumbent frame carries P_PLAY and MIN_PRED, which the
        # coherence endpoints need to rebuild team minute sums.
        coherence_inputs.append((origin, incumbent, champion, {**champion, **challenger}))

    if not blocks[COMPARISON_V5][0]:
        raise SystemExit("no origin produced results; check the dataset's date range")
    losses = {
        name: (pd.concat(inc, ignore_index=True), pd.concat(cand, ignore_index=True))
        for name, (inc, cand) in blocks.items()
    }
    return BracketScores(losses, pd.DataFrame(clip_rows), coherence_inputs)


def pooled(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """the ALL-cohort rows for one endpoint, in a canonical order for pairing."""
    sub = frame[(frame["endpoint"] == endpoint) & (frame["cohort"] == "ALL")]
    return sub.sort_values(["origin", "GAME_DATE", "row_key"]).reset_index(drop=True)


def cohort_table(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, endpoint: str
) -> pd.DataFrame:
    """the cohort breakdown for one endpoint, pooled over origins."""
    order = ["cohort", "origin", "GAME_DATE", "row_key"]
    a = incumbent[incumbent["endpoint"] == endpoint].sort_values(order).reset_index(
        drop=True
    )
    b = candidate[candidate["endpoint"] == endpoint].sort_values(order).reset_index(
        drop=True
    )
    if not a["row_key"].equals(b["row_key"]):
        raise SystemExit(
            f"the two passes' cohort rows for {endpoint} are not aligned; a cohort "
            f"comparison over different rows is not a comparison"
        )
    return cohort_regressions(a, b, tolerance=P3_COHORT_REGRESSION_TOLERANCE)


def per_origin_table(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, endpoint: str
) -> pd.DataFrame:
    """one row per origin: the two means and the relative change."""
    a, b = pooled(incumbent, endpoint), pooled(candidate, endpoint)
    joint = pd.DataFrame({
        "origin": a["origin"].to_numpy(),
        "base": a["loss"].to_numpy(dtype=float),
        "cand": b["loss"].to_numpy(dtype=float),
    })
    rows = []
    for origin, group in joint.groupby("origin", sort=True):
        base, cand = float(group["base"].mean()), float(group["cand"].mean())
        rows.append({
            "origin": origin, "n": int(len(group)), "incumbent": base,
            "candidate": cand,
            "delta_pct": (cand - base) / base if base > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def endpoints_of(frame: pd.DataFrame) -> list[str]:
    return list(dict.fromkeys(frame["endpoint"]))


def decide_comparison(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, gates: tuple[str, ...]
) -> tuple[PromotionVerdict, pd.DataFrame]:
    """apply the P3 bar to one comparison. returns (verdict, gated cohort table)."""
    decisions = [
        paired_endpoint_bootstrap(
            pooled(incumbent, endpoint), pooled(candidate, endpoint), endpoint,
            gates=gates, floor=P3_PROMOTION_FLOOR,
        )
        for endpoint in endpoints_of(incumbent)
    ]
    gated = [
        cohort_table(incumbent, candidate, endpoint).assign(endpoint=endpoint)
        for endpoint in gates if endpoint in set(incumbent["endpoint"])
    ]
    gated_cohorts = pd.concat(gated, ignore_index=True) if gated else pd.DataFrame()
    verdict = decide(decisions, gated_cohorts, floor=P3_PROMOTION_FLOOR,
                     tolerance=P3_COHORT_REGRESSION_TOLERANCE)
    return verdict, gated_cohorts


def _coherence_frame(
    scored: pd.DataFrame, estimates: dict[str, tuple[np.ndarray, np.ndarray]]
) -> pd.DataFrame:
    """the prediction-shaped frame eval_coherence scores: E_<stat>_COND and E_<stat>."""
    frame = scored.reset_index(drop=True).copy()
    minutes = frame[MIN_PRED].to_numpy(dtype=float)
    frame["E_MIN_COND"] = minutes
    frame["E_MIN"] = np.clip(frame[P_PLAY].to_numpy(dtype=float) * minutes, 0.0, None)
    for target, (conditional, unconditional) in estimates.items():
        frame[f"E_{target}_COND"] = np.asarray(conditional, dtype=float)
        frame[f"E_{target}"] = np.asarray(unconditional, dtype=float)
    return frame


def coherence_report(scores: BracketScores) -> tuple[str, pd.DataFrame | None]:
    """the serving coherence corrections, scored on the same validation rows."""
    try:
        from fnba_ml.eval_coherence import coherence_endpoints  # noqa: PLC0415
    except ImportError:
        return "not available (fnba_ml.eval_coherence is not installed)", None
    frames: list[pd.DataFrame] = []
    for origin, scored, champion, challenger in scores.coherence_inputs:
        for family, estimates in (("champion", champion), (COMPARISON_RATE, challenger)):
            result = coherence_endpoints(_coherence_frame(scored, estimates))
            frames.append(result.assign(origin=origin, family=family))
    if not frames:
        return "available, no origins scored", None
    return "available", pd.concat(frames, ignore_index=True)


def _md(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "_(none)_"
    return frame.to_markdown(index=False, floatfmt=".4f")


def write_reports(
    scores: BracketScores, reports_dir: Path, version: str, header: list[str]
) -> dict[str, PromotionVerdict]:
    """decide both comparisons and write the csvs and the markdown."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{version}_p3"
    verdicts: dict[str, PromotionVerdict] = {}
    decision_frames, gated_frames, origin_frames, cohort_frames = [], [], [], []

    for name, (incumbent, candidate) in scores.losses.items():
        verdict, gated = decide_comparison(incumbent, candidate, COMPARISON_GATES[name])
        verdicts[name] = verdict
        decision_frames.append(decision_table(verdict).assign(
            comparison=name, incumbent_label=INCUMBENT_LABEL[name],
            promoted=verdict.promoted, verdict=verdict.reason,
        ))
        if not gated.empty:
            gated_frames.append(gated.assign(comparison=name))
        for endpoint in endpoints_of(incumbent):
            origin_frames.append(per_origin_table(incumbent, candidate, endpoint).assign(
                comparison=name, endpoint=endpoint
            ))
            cohort_frames.append(cohort_table(incumbent, candidate, endpoint).assign(
                comparison=name, endpoint=endpoint
            ))

    decisions = pd.concat(decision_frames, ignore_index=True)
    gated_cohorts = (
        pd.concat(gated_frames, ignore_index=True) if gated_frames else pd.DataFrame()
    )
    per_origin = pd.concat(origin_frames, ignore_index=True)
    all_cohorts = pd.concat(cohort_frames, ignore_index=True)
    coherence_status, coherence = coherence_report(scores)

    decisions.to_csv(reports_dir / f"{stem}_decision.csv", index=False)
    gated_cohorts.to_csv(reports_dir / f"{stem}_cohorts.csv", index=False)
    per_origin.to_csv(reports_dir / f"{stem}_per_origin.csv", index=False)
    all_cohorts.to_csv(reports_dir / f"{stem}_cohorts_all_endpoints.csv", index=False)
    scores.clip_rates.to_csv(reports_dir / f"{stem}_clip_rates.csv", index=False)
    if coherence is not None:
        coherence.to_csv(reports_dir / f"{stem}_coherence.csv", index=False)

    lines = [f"# P3 decision run ({version})", "", *header, ""]
    for name, verdict in verdicts.items():
        lines += [
            f"## {name} vs {INCUMBENT_LABEL[name]}", "",
            f"**{verdict.reason}**", "",
            "positive relative_improvement = the candidate is better.", "",
            _md(decision_table(verdict)), "",
            "### per origin (positive delta_pct = the candidate is worse)", "",
            _md(per_origin[per_origin["comparison"] == name].drop(
                columns=["comparison"])), "",
            "### cohorts, gated endpoints (positive delta_pct = worse)", "",
            _md(gated_cohorts[gated_cohorts["comparison"] == name].drop(
                columns=["comparison"]) if not gated_cohorts.empty else gated_cohorts),
            "",
        ]
    lines += [
        "## coherence", "",
        f"eval_coherence.coherence_endpoints: {coherence_status}", "",
        "clip rates of the unconditional estimates under COHERENCE_CONSTRAINTS:", "",
        _md(scores.clip_rates.groupby(["family", "constraint"], as_index=False)[
            "clip_rate"].mean() if not scores.clip_rates.empty else scores.clip_rates),
        "",
    ]
    if coherence is not None:
        lines += [_md(coherence), ""]
    (reports_dir / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    return verdicts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    decision_path = args.reports_dir / f"{args.version}_p3_decision.csv"
    if decision_path.exists() and not args.allow_overwrite:
        raise SystemExit(
            f"{decision_path} already exists. one look per version: pass a new "
            f"--version, or --allow-overwrite if the previous run did not finish"
        )

    frame = load_dataset(args.dataset)
    needed = [*V5_STAKES_FEATURE_COLS, *RATE_CONTEXT_COLS]
    missing = sorted({c for c in needed if c not in frame.columns})
    if missing:
        raise SystemExit(
            f"the dataset is missing {len(missing)} candidate column(s): "
            f"{', '.join(missing[:8])}. run build_v4_dataset.py first."
        )

    header = [
        f"- git commit: {git_commit() or 'unknown'}",
        f"- dataset: {args.dataset} ({len(frame):,} rows)",
        f"- served contract: feature_version {FEATURE_VERSION}, {len(FEATURE_COLS)} "
        f"columns, UNCHANGED BY THIS RUN",
        f"- candidate 1: {CANDIDATE_FEATURE_SET_V5} (feature_version "
        f"{CANDIDATE_FEATURE_VERSION_V5}), {len(FEATURE_COLS)} + "
        f"{len(V5_STAKES_FEATURE_COLS)} columns",
        f"- candidate 2: residual rate on the incumbent's pieces, stats "
        f"{', '.join(RATE_MODEL_TARGETS)}",
        f"- origins: {len(DEV_ORIGINS)} (config.DEV_ORIGINS)",
        f"- bar (config P3 block, written before this ran): paired {BLOCK_DAYS}-day "
        f"moving-block bootstrap, {N_REPLICATES} replicates, 95% CI excluding zero, "
        f"AND >= {P3_PROMOTION_FLOOR:.0%} relative improvement on a gated endpoint, "
        f"AND no gated-endpoint cohort regressing by more than "
        f"{P3_COHORT_REGRESSION_TOLERANCE:.0%}",
        f"- gates: {CANDIDATE_FEATURE_SET_V5} {' or '.join(P3_V5_GATED_ENDPOINTS)}; "
        f"residual rate {' or '.join(P3_RATE_GATED_ENDPOINTS)}; everything else is "
        f"reported only",
    ]
    for line in header:
        print(line)

    scores = score_brackets(frame, DEV_ORIGINS)
    verdicts = write_reports(scores, args.reports_dir, args.version, header)

    print()
    for name, verdict in verdicts.items():
        print(f"{name}: {verdict.reason}")
    print(f"reports -> {args.reports_dir / f'{args.version}_p3.md'} and csvs")
    # exit 0 either way: a null result is a valid outcome of a pre-registered test.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
