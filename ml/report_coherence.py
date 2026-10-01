"""the coherence corrections measured on the five rolling origins (report only)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import (  # noqa: E402
    add_common_args,
    default_dataset_path,
    load_dataset,
    setup_logging,
)
from fnba_ml.config import (  # noqa: E402
    CHAMPIONS,
    MINUTES_TARGET,
    ORIGINS,
    REPORTS_DIR,
    SERVED_FEATURE_SET,
)
from fnba_ml.eval_coherence import (  # noqa: E402
    coherence_endpoints,
    minute_sum_diagnostic,
    relative_to_none,
)
from fnba_ml.evaluate import split  # noqa: E402
from fnba_ml.features import feature_set_columns  # noqa: E402
from fnba_ml.models import (  # noqa: E402
    MIN_PRED,
    P_PLAY,
    AvailabilityModel,
    MinutesModel,
    PerMinuteRate,
    coherence_clip_frame,
    minutes_propagated_estimate,
)
from fnba_ml.registry import git_commit  # noqa: E402

log = logging.getLogger("report_coherence")

# the stats the identity reads, plus the bounds the existing clip needs.
SCORED_STATS: tuple[str, ...] = ("PTS", "FGM", "FG3M", "FTM", "FGA", "FTA")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--version", required=True,
                        help="label for the output csv, reports/<version>_coherence.csv")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    return parser.parse_args(argv)


def score_origin(
    frame: pd.DataFrame, feats: list[str], origin: str, vstart: str, vend: str
) -> pd.DataFrame | None:
    """the promoted path fitted before one origin, scored on its validation month."""
    train_all, valid_all = split(frame, vstart, vend)
    train_app = train_all[train_all["PLAYED"] == 1]
    if train_all.empty or valid_all.empty or train_app.empty:
        log.warning("origin %s has an empty side; skipped", origin)
        return None
    cutoff = pd.Timestamp(valid_all["GAME_DATE"].min())

    availability = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(
        train_all, feats, cutoff
    )
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feats, cutoff)
    scored = minutes.attach(availability.attach(valid_all))

    out = valid_all.copy()
    out["origin"] = origin
    out[P_PLAY] = scored[P_PLAY].to_numpy(dtype=float)
    min_cond = scored[MIN_PRED].to_numpy(dtype=float)
    out[f"E_{MINUTES_TARGET}_COND"] = min_cond
    out[f"E_{MINUTES_TARGET}"] = (out[P_PLAY].to_numpy(dtype=float) * min_cond).clip(0.0)
    for target in SCORED_STATS:
        rate = PerMinuteRate(target).fit(train_app)
        conditional, unconditional = minutes_propagated_estimate(
            scored, rate.predict(valid_all)
        )
        out[f"E_{target}_COND"] = conditional
        out[f"E_{target}"] = unconditional
    for template in ("E_{target}_COND", "E_{target}"):
        out, _ = coherence_clip_frame(out, template)
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    frame = load_dataset(args.dataset)
    feats = feature_set_columns(frame, SERVED_FEATURE_SET)
    scored_frames = [
        scored for origin, vstart, vend in ORIGINS
        if (scored := score_origin(frame, feats, origin, vstart, vend)) is not None
    ]
    if not scored_frames:
        raise SystemExit("no origin produced results; check the dataset's date range")
    scored = pd.concat(scored_frames, ignore_index=True)

    table = relative_to_none(coherence_endpoints(scored))
    diagnostic = minute_sum_diagnostic(scored)

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.reports_dir / f"{args.version}_coherence.csv"
    table.to_csv(out_path, index=False)

    print("=" * 78)
    print("COHERENCE CORRECTIONS - report only, frozen serving keeps 'none'")
    print("=" * 78)
    print(f"git commit : {git_commit() or 'unknown'}")
    print(f"dataset    : {args.dataset} ({len(frame):,} rows)")
    print(f"origins    : {len(scored_frames)} of {len(ORIGINS)}")
    print(f"rows       : {len(scored):,}")
    print()
    print("-- raw expected minute sum per team-game (before any correction) --")
    for key, value in diagnostic.items():
        print(f"  {key:14s} {value:.4f}" if isinstance(value, float) else f"  {key:14s} {value}")
    print()
    print("-- endpoints (negative delta_pct = the correction helps) --")
    print(table.to_string(index=False, float_format=lambda v: f"{v:9.4f}"))
    print(f"csv -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
