"""shared argparse wiring and logging setup for the four command-line scripts."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, MODELS_DIR, SEASONS, SERVED_FEATURE_SET, resolve_cutoff


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source", choices=("postgres", "parquet"), default="parquet",
        help="postgres reads the data-truth tables via DATABASE_URL; parquet "
             "reads a directory of spike-shaped files",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="required for --source parquet",
    )
    parser.add_argument(
        "--seasons", nargs="+", default=list(SEASONS),
        help=f"seasons to load (default: {' '.join(SEASONS)})",
    )
    parser.add_argument(
        "--cutoff", default=None,
        help="prediction-run timestamp; features use only games strictly before it",
    )


def build_source(args: argparse.Namespace):
    from .data import make_source

    if args.source == "parquet":
        if args.data_dir is None:
            raise SystemExit("--source parquet requires --data-dir")
        return make_source("parquet", data_dir=args.data_dir, seasons=args.seasons)

    cutoff = resolve_cutoff(args.cutoff) if getattr(args, "cutoff", None) else None
    return make_source("postgres", seasons=args.seasons, cutoff=cutoff)


def default_dataset_path() -> Path:
    return DATA_DIR / "dataset.parquet"


def version_dir(model_version: str, models_dir: Path | None = None) -> Path:
    return (models_dir or MODELS_DIR) / model_version


def feature_set_version(model_version: str, feature_set: str) -> str:
    """the artifact name for a feature set: the served set keeps the bare version."""
    if feature_set == SERVED_FEATURE_SET:
        return model_version
    return f"{model_version}-{feature_set}"


def load_dataset(path: Path) -> pd.DataFrame:
    """read a built dataset, backfilling anything a newer composition needs.

    the per-minute rate columns landed after the four-season dataset was built and
    rebuilding it requires the database, so :func:`attach_per_minute_rates`
    recomputes them from the frame's own appearance history through the same
    as-of-safe code path. it is a no-op for a dataset built by the current
    pipeline.
    """
    if not path.exists():
        raise SystemExit(f"dataset not found: {path}. run build_dataset.py first.")
    from .features import attach_per_minute_rates

    df = pd.read_parquet(path)
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    return attach_per_minute_rates(df)
