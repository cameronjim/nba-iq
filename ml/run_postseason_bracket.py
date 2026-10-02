"""the MODEL.md 20.1 measurement: the served incumbent with and without postseason rate history.

    python build_dataset.py --source postgres --out data/dataset.parquet --no-v4-candidate
    python build_dataset.py --source postgres --out data/dataset_postseason.parquet \\
        --no-v4-candidate --postseason-history --universe-from data/dataset.parquet
    python run_postseason_bracket.py --origins dev|season-start|both

both datasets hold the same modelled rows; only the career-scoped history columns
differ. every origin fits the served feature set on each and scores identical
validation rows, so the paired moving-block bootstrap of run_p3_bracket.py applies
unchanged. cohorts are taken from the baseline dataset for both passes, because a
tier is assigned from roll10_MIN, which the switch itself moves.

each origin set is its own look under its own report stem: dev is the five
config.ORIGINS, season-start is config.SEASON_START_ORIGINS.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import add_common_args, load_dataset, setup_logging  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    COMPETITION_COL,
    DATA_DIR,
    ORIGINS,
    P3_COHORT_REGRESSION_TOLERANCE,
    P3_PROMOTION_FLOOR,
    RATE_TARGETS,
    REPORTS_DIR,
    SEASON_START_ORIGINS,
    SERVED_FEATURE_SET,
    TRAINING_COMPETITIONS,
)
from fnba_ml.eval_core import cohort_masks, split  # noqa: E402
from fnba_ml.features import feature_set_columns  # noqa: E402
from fnba_ml.models import PerMinuteRate, minutes_propagated_estimate  # noqa: E402
from fnba_ml.promotion import (  # noqa: E402
    BLOCK_DAYS,
    ENDPOINT_MINUTES,
    ENDPOINT_UNCOND_PTS,
    N_REPLICATES,
    decision_table,
)
from fnba_ml.registry import git_commit  # noqa: E402
from run_p3_bracket import (  # noqa: E402
    Losses,
    _md,
    availability_minutes_losses,
    decide_comparison,
    endpoints_of,
    fit_and_score,
    per_origin_table,
    pooled,
    rate_endpoint,
    rate_losses,
)

log = logging.getLogger("run_postseason_bracket")

KEY: list[str] = ["GAME_DATE", "GAME_ID", "TEAM_ID", "PLAYER_ID"]

# written before any run: the switch is a rate-history change, so it is gated on
# what a rate moves, minutes (through the minutes model's ewma inputs) and points.
POSTSEASON_GATED_ENDPOINTS: tuple[str, ...] = (
    ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS, rate_endpoint("PTS", True),
)

ORIGIN_SETS: dict[str, list[tuple[str, str, str]]] = {
    "dev": ORIGINS,
    "season-start": SEASON_START_ORIGINS,
}
ORIGIN_CHOICES: tuple[str, ...] = (*ORIGIN_SETS, "both")
SEASON_START_SET = "season-start"

# a 5-game-halflife rate is mostly new-season games within about two weeks, so
# the effect should sit in the first phase and decay in the second.
EARLY_PHASE_DAYS: int = 14
EARLY_PHASE = "weeks 1-2"
LATE_PHASE = "week 3+"
POOLED_ORIGIN = "pooled"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--baseline", type=Path, default=DATA_DIR / "dataset.parquet",
                        help="built with the switch off")
    parser.add_argument("--candidate", type=Path,
                        default=DATA_DIR / "dataset_postseason.parquet",
                        help="built with --postseason-history")
    parser.add_argument("--origins", choices=ORIGIN_CHOICES, default="dev",
                        help="dev = the five config.ORIGINS; season-start = "
                             "config.SEASON_START_ORIGINS; both = one look at each")
    parser.add_argument("--version", default="postseason")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--allow-overwrite", action="store_true")
    return parser.parse_args(argv)


def origin_set_names(choice: str) -> list[str]:
    """'both' -> every origin set; any other choice is itself."""
    if choice == "both":
        return list(ORIGIN_SETS)
    if choice not in ORIGIN_SETS:
        raise ValueError(f"unknown origin set {choice!r}; expected one of {ORIGIN_CHOICES}")
    return [choice]


def report_stem(version: str, origin_set: str) -> str:
    return f"{version}_postseason_{origin_set}"


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


def phase_of(dates: pd.Series, start: pd.Timestamp) -> np.ndarray:
    """EARLY_PHASE for the first EARLY_PHASE_DAYS days from ``start``, else LATE_PHASE."""
    days = (pd.to_datetime(pd.Series(dates)) - pd.Timestamp(start)).dt.days.to_numpy()
    return np.where(days < EARLY_PHASE_DAYS, EARLY_PHASE, LATE_PHASE)


def phase_table(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, endpoint: str,
    starts: dict[str, pd.Timestamp],
) -> pd.DataFrame:
    """the first two weeks of each window against the rest, per origin and pooled."""
    a, b = pooled(incumbent, endpoint), pooled(candidate, endpoint)
    if not a["row_key"].equals(b["row_key"]):
        raise SystemExit(f"the two passes' {endpoint} rows are not aligned")
    joint = pd.DataFrame({
        "origin": a["origin"].to_numpy(),
        "phase": "",
        "base": a["loss"].to_numpy(dtype=float),
        "cand": b["loss"].to_numpy(dtype=float),
    })
    for origin, idx in joint.groupby("origin").groups.items():
        joint.loc[idx, "phase"] = phase_of(a.loc[idx, "GAME_DATE"], starts[origin])
    groups = [
        *joint.groupby(["origin", "phase"], sort=True),
        *(((POOLED_ORIGIN, phase), g) for phase, g in joint.groupby("phase", sort=True)),
    ]
    rows = []
    for (origin, phase), group in groups:
        base, cand = float(group["base"].mean()), float(group["cand"].mean())
        rows.append({
            "endpoint": endpoint, "origin": origin, "phase": phase,
            "n": int(len(group)), "incumbent": base, "candidate": cand,
            "delta_pct": (cand - base) / base if base > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def align(baseline: pd.DataFrame, candidate: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """both frames in one row order; refuses frames that do not hold the same rows."""
    a = baseline.sort_values(KEY, kind="stable").reset_index(drop=True)
    b = candidate.sort_values(KEY, kind="stable").reset_index(drop=True)
    if len(a) != len(b) or not a[KEY].astype(str).equals(b[KEY].astype(str)):
        raise SystemExit(
            "the two datasets do not hold the same modelled rows; the switch must "
            "change history columns only, so build the candidate with --universe-from "
            "the baseline"
        )
    return a, b


def emit(
    origin: str, valid: pd.DataFrame, losses: Losses,
    masks: list[tuple[str, np.ndarray]],
) -> list[pd.DataFrame]:
    """run_p3_bracket.emit with the cohort masks supplied rather than derived."""
    key = (
        valid["PLAYER_ID"].astype(str) + "|" + valid["GAME_ID"].astype(str) + "|"
        + valid["TEAM_ID"].astype(str)
    ).to_numpy()
    dates = valid["GAME_DATE"].to_numpy()
    every_row = np.ones(len(valid), dtype=bool)
    out: list[pd.DataFrame] = []
    for label, mask in (("ALL", every_row), *masks):
        if not mask.any():
            continue
        for endpoint, (loss, selector) in losses.items():
            sel = mask & selector
            if sel.any():
                out.append(pd.DataFrame({
                    "origin": origin, "endpoint": endpoint, "row_key": key[sel],
                    "GAME_DATE": dates[sel], "cohort": label, "loss": loss[sel],
                }))
    return out


def _losses(train: pd.DataFrame, valid: pd.DataFrame, cutoff: pd.Timestamp) -> Losses:
    train_app = train[(train["PLAYED"] == 1) & (train["MIN"] > 0)]
    scored = fit_and_score(train, valid, feature_set_columns(train, SERVED_FEATURE_SET), cutoff)
    out = availability_minutes_losses(valid, scored, PerMinuteRate("PTS").fit(train_app))
    estimates = {
        target: minutes_propagated_estimate(
            scored, PerMinuteRate(target).fit(train_app).predict(valid)
        )
        for target in RATE_TARGETS if target in valid.columns
    }
    out.update(rate_losses(valid, estimates))
    return out


def score_switch(
    baseline: pd.DataFrame, candidate: pd.DataFrame,
    origins: list[tuple[str, str, str]] = ORIGINS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(baseline losses, candidate losses) over identical rows, every origin."""
    baseline, candidate = align(baseline, candidate)
    blocks: tuple[list[pd.DataFrame], list[pd.DataFrame]] = ([], [])
    for origin, vstart, vend in origins:
        base_train, base_valid = split(baseline, vstart, vend)
        cand_train, cand_valid = split(candidate, vstart, vend)
        if base_train.empty or base_valid.empty:
            log.warning("origin %s has an empty side; skipped", origin)
            continue
        base_valid = base_valid.reset_index(drop=True)
        cand_valid = cand_valid.reset_index(drop=True)
        cutoff = pd.Timestamp(base_valid["GAME_DATE"].min())
        masks = cohort_masks(base_valid)
        log.info("origin %s: %d train / %d valid rows", origin, len(base_train), len(base_valid))
        blocks[0].extend(emit(origin, base_valid, _losses(base_train, base_valid, cutoff), masks))
        blocks[1].extend(emit(origin, cand_valid, _losses(cand_train, cand_valid, cutoff), masks))
    if not blocks[0]:
        raise SystemExit("no origin produced results; check the datasets' date range")
    return pd.concat(blocks[0], ignore_index=True), pd.concat(blocks[1], ignore_index=True)


def write_reports(
    args: argparse.Namespace, origin_set: str, origins: list[tuple[str, str, str]],
    off: pd.DataFrame, on: pd.DataFrame,
) -> str:
    """decide one origin set's look and write its csvs and markdown. returns the verdict."""
    stem = report_stem(args.version, origin_set)
    verdict, gated = decide_comparison(off, on, POSTSEASON_GATED_ENDPOINTS)
    per_origin = pd.concat(
        [per_origin_table(off, on, e).assign(endpoint=e) for e in endpoints_of(off)],
        ignore_index=True,
    )
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    decision_table(verdict).to_csv(args.reports_dir / f"{stem}_decision.csv", index=False)
    per_origin.to_csv(args.reports_dir / f"{stem}_per_origin.csv", index=False)
    gated.to_csv(args.reports_dir / f"{stem}_cohorts.csv", index=False)
    windows = "; ".join(f"{name} {vstart}..{vend}" for name, vstart, vend in origins)
    lines = [
        f"# postseason rate history ({args.version}, origin set {origin_set})", "",
        f"- git commit: {git_commit() or 'unknown'}",
        f"- baseline: {args.baseline} (switch off); candidate: {args.candidate} (switch on)",
        f"- incumbent: {SERVED_FEATURE_SET}, champions unchanged",
        f"- origin set: {origin_set}, {len(origins)} origins: {windows}",
        f"- bar: paired {BLOCK_DAYS}-day moving-block bootstrap, {N_REPLICATES} "
        f"replicates, 95% CI excluding zero, >= {P3_PROMOTION_FLOOR:.0%} on a gated "
        f"endpoint ({', '.join(POSTSEASON_GATED_ENDPOINTS)}), no gated cohort "
        f"regressing by more than {P3_COHORT_REGRESSION_TOLERANCE:.0%}",
    ]
    if origin_set == SEASON_START_SET:
        lines.append(
            "- these origins answer the season-start question only and are not part of "
            "ORIGINS or DEV_ORIGINS; each start is the season opener found in the data"
        )
    lines += [
        "", f"**{verdict.reason}**", "",
        "positive relative_improvement = the switch is better.", "",
        _md(decision_table(verdict)), "",
        "## per origin (positive delta_pct = the switch is worse)", "", _md(per_origin), "",
    ]
    if origin_set == SEASON_START_SET:
        starts = {name: pd.Timestamp(vstart) for name, vstart, _ in origins}
        present = set(off["endpoint"])
        phases = pd.concat(
            [phase_table(off, on, e, starts) for e in POSTSEASON_GATED_ENDPOINTS
             if e in present],
            ignore_index=True,
        )
        phases.to_csv(args.reports_dir / f"{stem}_phases.csv", index=False)
        lines += [
            f"## by phase: the first {EARLY_PHASE_DAYS} days of each window against "
            "the rest (positive delta_pct = the switch is worse)", "",
            "reported only; the gate is the pooled decision above.", "", _md(phases), "",
        ]
    lines += ["## cohorts, gated endpoints", "", _md(gated), ""]
    (args.reports_dir / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[{origin_set}] {verdict.reason}")
    print(f"reports -> {args.reports_dir / f'{stem}.md'}")
    return verdict.reason


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    sets = origin_set_names(args.origins)
    # every stem is checked before any scoring, so 'both' never half-runs.
    for origin_set in sets:
        decision_path = args.reports_dir / f"{report_stem(args.version, origin_set)}_decision.csv"
        if decision_path.exists() and not args.allow_overwrite:
            raise SystemExit(f"{decision_path} already exists. one look per version.")

    baseline, candidate = load_dataset(args.baseline), load_dataset(args.candidate)
    for origin_set in sets:
        origins = ORIGIN_SETS[origin_set]
        if origin_set == SEASON_START_SET:
            origins = clamp_to_opener(baseline, origins)
        off, on = score_switch(baseline, candidate, origins)
        write_reports(args, origin_set, origins, off, on)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
