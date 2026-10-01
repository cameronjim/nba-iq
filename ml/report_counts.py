"""phase-2 challengers over the rolling origins: count models and tiered intervals.

writes two tidy csvs (count endpoints, interval endpoints) with an origin column
and prints the origin-mean of each. a report, not a decision: nothing here
touches the served configuration.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import add_common_args, default_dataset_path, load_dataset, setup_logging  # noqa: E402
from fnba_ml.config import ORIGINS, REPORTS_DIR  # noqa: E402
from fnba_ml.count_model import COUNT_TARGETS  # noqa: E402
from fnba_ml.eval_core import split  # noqa: E402
from fnba_ml.eval_counts import (  # noqa: E402
    count_endpoints,
    score_count_origin,
    score_interval_origin,
)
from fnba_ml.features import available_features  # noqa: E402
from fnba_ml.intervals import QUANTILE_TARGETS  # noqa: E402

log = logging.getLogger("report_counts")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--version", default="unversioned")
    parser.add_argument("--stats", nargs="+", default=list(COUNT_TARGETS))
    parser.add_argument("--interval-stats", nargs="+", default=list(QUANTILE_TARGETS))
    return parser.parse_args(argv)


def origin_mean(endpoints: pd.DataFrame) -> pd.DataFrame:
    """unweighted mean over origins, one row per (variant, endpoint, stat, cohort)."""
    if endpoints.empty:
        return endpoints
    return (
        endpoints.groupby(["endpoint", "stat", "cohort", "variant"], sort=False)
        .agg(value=("value", "mean"), n=("n", "sum"), origins=("origin", "nunique"))
        .reset_index()
    )


def run(features: pd.DataFrame, stats: tuple[str, ...],
        interval_stats: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    feature_cols = available_features(features)
    counts, intervals, choices = [], [], []
    for name, vstart, vend in ORIGINS:
        train, valid = split(features, vstart, vend)
        if train.empty or valid.empty:
            log.warning("origin %s has no rows on one side; skipped", name)
            continue
        log.info("origin %s: %d train, %d valid rows", name, len(train), len(valid))
        scored, chosen = score_count_origin(
            train, valid, feature_cols, pd.Timestamp(vstart), stats
        )
        counts.append(count_endpoints(scored, stats).assign(origin=name))
        intervals.append(
            score_interval_origin(train, scored, feature_cols, interval_stats)
            .assign(origin=name)
        )
        choices.extend({"origin": name, "stat": s, **p} for s, p in chosen.items())
    if not counts:
        raise SystemExit("no origin produced a scored frame")
    return (pd.concat(counts, ignore_index=True), pd.concat(intervals, ignore_index=True),
            pd.DataFrame(choices))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    features = load_dataset(args.dataset)
    counts, intervals, choices = run(features, tuple(args.stats), tuple(args.interval_stats))

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"counts_{args.version}"
    counts.to_csv(args.reports_dir / f"{stem}_count_endpoints.csv", index=False)
    intervals.to_csv(args.reports_dir / f"{stem}_interval_endpoints.csv", index=False)
    choices.to_csv(args.reports_dir / f"{stem}_count_params.csv", index=False)

    with pd.option_context("display.width", 160, "display.max_rows", 400):
        print("--- COUNT MODEL vs CHAMPION RATE (mean over origins) ---")
        print(origin_mean(counts).to_string(index=False))
        print("\n--- INTERVALS, 80% nominal, pooled vs tiered (mean over origins) ---")
        print(origin_mean(intervals).to_string(index=False))
        print("\n--- count model parameters chosen on inner folds ---")
        print(choices.to_string(index=False))
    print(f"\nreports -> {args.reports_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
