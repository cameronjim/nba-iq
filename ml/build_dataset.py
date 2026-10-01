"""build the scheduled-player-game feature dataset."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import add_common_args, add_source_args, build_source, setup_logging  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    DATA_DIR,
    FEATURE_COLS,
    FEATURE_SETS,
    FEATURE_VERSION,
    MAGNITUDE_SHRINK_K,
    MAGNITUDE_WINDOW,
    P_CONTEXT,
    V4_FEATURE_COLS,
)
from fnba_ml.features import attach_cross_fit_context, build_features  # noqa: E402
from fnba_ml.matchup import attach_v4_features  # noqa: E402
from fnba_ml.teammates import (  # noqa: E402
    position_group_counts,
    teammate_feature_summary,
)
from fnba_ml.universe import (  # noqa: E402
    build_universe,
    coverage_report,
    universe_composition,
)

log = logging.getLogger("build_dataset")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_source_args(parser)
    add_common_args(parser)
    parser.add_argument("--out", type=Path, default=DATA_DIR / "dataset.parquet")
    parser.add_argument(
        "--no-v4-candidate", action="store_true",
        help=f"skip the {len(V4_FEATURE_COLS)} P2 candidate columns",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    source = build_source(args)
    universe = build_universe(source)
    coverage = coverage_report(universe, source.load_player_game_logs())
    composition = universe_composition(universe)
    features = build_features(universe)
    features = attach_cross_fit_context(features)
    if not args.no_v4_candidate:
        features = attach_v4_features(features, source.load_team_game_logs())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(args.out, index=False)

    universe_source = str(universe["UNIVERSE_SOURCE"].iloc[0])
    print("--- DATASET ---")
    print(f"source           : {source.name}")
    print(f"universe         : {universe_source}"
          + ("   <-- BIASED, fixture/backtest only" if universe_source == "approximation" else ""))
    for key, value in coverage.items():
        print(f"{key:17s}: {value:,.4f}")
    print("universe composition (played, listed_inactive):")
    for (played, inactive), count in composition["by_played_listed_inactive"].items():
        label = "unknown" if inactive is None else str(inactive).lower()
        print(f"  played={played} listed_inactive={label:7s} {count:>10,}")
    box_rows = composition["box_score_rows"]
    print("  active-DNP rows from boxscoretraditionalv3: "
          + ("n/a (source not carried)" if box_rows is None else f"{box_rows:,}"))
    print(f"feature version  : {FEATURE_VERSION}")
    print(f"feature columns  : {len(FEATURE_COLS)}")
    print(f"rows             : {len(features):,}")
    print(f"saved            -> {args.out}")

    if "P_CONTEXT_SOURCE" in features.columns:
        shares = features["P_CONTEXT_SOURCE"].value_counts(normalize=True)
        print("\ncontext probability p_j (stage 2):")
        for label, share in shares.items():
            print(f"  {str(label):14s} {share:7.2%}")
        print(f"  mean p          {features[P_CONTEXT].mean():7.4f}")
    print(f"\nmagnitude window : last {MAGNITUDE_WINDOW} appearances, career-scoped, "
          f"shrunk with k={MAGNITUDE_SHRINK_K:g}")
    print("feature sets     : "
          + ", ".join(f"{name}={len([c for c in cols if c in features.columns])}"
                      for name, cols in FEATURE_SETS.items()))

    positions = position_group_counts(features)
    if not positions.empty:
        print("\nrows per position bucket:")
        for bucket, count in positions.items():
            print(f"  {str(bucket):12s} {count:>8,}")
    summary = teammate_feature_summary(features)
    if not summary.empty:
        print("\nteammate-context features:")
        print(summary.to_string(float_format=lambda v: f"{v:8.3f}"))

    present = [c for c in FEATURE_COLS if c in features.columns]
    nulls = features[present].isna().mean().sort_values(ascending=False)
    print("\nnull rate per feature (top 10):")
    for name, rate in nulls.head(10).items():
        print(f"  {name:26s} {rate:7.4f}")

    print("\nplayed rate by minutes tier:")
    print(features.groupby("MIN_TIER")["PLAYED"].agg(["mean", "size"]).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
