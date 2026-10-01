"""train the availability model and snapshot the EWMA production state."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402

from fnba_ml import registry  # noqa: E402
from fnba_ml.cli import (  # noqa: E402
    add_common_args,
    default_dataset_path,
    load_dataset,
    setup_logging,
    version_dir,
)
from fnba_ml.config import (  # noqa: E402
    BASE_FEATURE_COLS,
    CHAMPIONS,
    COHERENCE_CONSTRAINTS,
    CONTEXT_P_PRIOR,
    CROSS_FIT_FREQ,
    CROSS_FIT_MIN_TRAIN_ROWS,
    CUTOFF_POLICY,
    FEATURE_VERSION,
    LGBM_PARAMS,
    MAGNITUDE_PRIORS,
    MAGNITUDE_SHRINK_K,
    MAGNITUDE_WINDOW,
    MINUTES_TARGET,
    MODELS_DIR,
    RATE_ESTIMATORS,
    RATE_HALFLIVES,
    RATE_MINUTES_FLOOR,
    RATE_TARGETS,
    resolve_cutoff,
)
from fnba_ml.features import available_features  # noqa: E402
from fnba_ml.intervals import (  # noqa: E402
    QUANTILE_TARGETS,
    QuantileOffsets,
    fit_residual_quantiles,
    fit_residual_quantiles_by_tier,
    tiered_quantiles_as_dict,
)
from fnba_ml.models import (  # noqa: E402
    AvailabilityModel,
    EwmaProduction,
    MinutesModel,
    PerMinuteRate,
    baseline_column,
    brier,
    conditional_estimate,
    logloss,
    mae,
    skill_score,
    snapshot_ewma_state,
)

log = logging.getLogger("train")

MODEL_FILE = "availability_model.joblib"
MINUTES_FILE = "minutes_model.joblib"
BASE_MODEL_FILE = "base_availability_model.joblib"
EWMA_FILE = "ewma_state.parquet"
META_FILE = "metadata.json"
DEFAULT_HOLDOUT_DAYS = 28


def appearances(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["PLAYED"] == 1] if "PLAYED" in frame.columns else frame


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--version", default=None, help="model version, default today's date")
    parser.add_argument("--cutoff", default=None,
                        help=f"training cutoff. policy: {CUTOFF_POLICY}")
    parser.add_argument("--holdout-days", type=int, default=DEFAULT_HOLDOUT_DAYS)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument(
        "--tiered-quantiles", action="store_true",
        help="also write challenger per-minutes-tier offsets under "
             "production.quantiles_by_tier (nothing serves them)",
    )
    return parser.parse_args(argv)


def holdout_metrics(
    features: pd.DataFrame, feature_cols: list[str], cutoff: pd.Timestamp, holdout_days: int
) -> dict[str, float]:
    """honest metrics from the last window before the cutoff."""
    split_at = cutoff - pd.Timedelta(days=holdout_days)
    train = features[features["GAME_DATE"] < split_at]
    valid = features[(features["GAME_DATE"] >= split_at) & (features["GAME_DATE"] < cutoff)]
    if train.empty or valid.empty:
        log.warning("holdout window is empty; registry metrics will be omitted")
        return {}

    model = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(train, feature_cols, split_at)
    p = model.predict_proba(valid)
    y = valid["PLAYED"].to_numpy(dtype=int)
    base = baseline_column(valid, "avail_rate_10", float(train["PLAYED"].mean()))
    metrics = {
        "holdout_start": str(split_at.date()),
        "holdout_rows": int(len(valid)),
        "holdout_played_rate": round(float(valid["PLAYED"].mean()), 6),
        "brier": round(brier(y, p), 6),
        "log_loss": round(logloss(y, p), 6),
        "brier_skill_vs_shifted_rate": round(skill_score(brier(y, p), brier(y, base)), 6),
    }

    train_app, valid_app = appearances(train), appearances(valid)
    if not train_app.empty and not valid_app.empty:
        minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feature_cols, split_at)
        minutes_pred = minutes.predict(valid_app)
        metrics["minutes_mae"] = round(mae(valid_app[MINUTES_TARGET], minutes_pred), 6)
        metrics["minutes_mae_ewma_baseline"] = round(
            mae(valid_app[MINUTES_TARGET], EwmaProduction(MINUTES_TARGET).fit(train_app)
                .predict(valid_app)), 6
        )
        # the ewma_<stat> whole-game key is emitted only for the ROLL_STATS that
        # really carry the column; every stat also gets the expanding-rate baseline.
        for target in RATE_TARGETS:
            if target not in valid_app.columns:
                continue
            rate = PerMinuteRate(target).fit(train_app)
            composed = conditional_estimate(minutes_pred, rate.predict(valid_app))
            metrics[f"{target.lower()}_cond_mae"] = round(mae(valid_app[target], composed), 6)

            expanding = PerMinuteRate(target, estimator="expanding").fit(train_app)
            metrics[f"{target.lower()}_cond_mae_rate_baseline"] = round(
                mae(valid_app[target],
                    conditional_estimate(minutes_pred, expanding.predict(valid_app))), 6
            )
            if f"ewma_{target}" in valid_app.columns:
                metrics[f"{target.lower()}_cond_mae_ewma_baseline"] = round(
                    mae(valid_app[target],
                        EwmaProduction(target).fit(train_app).predict(valid_app)), 6
                )
    return metrics


def holdout_points(
    features: pd.DataFrame,
    feature_cols: list[str],
    cutoff: pd.Timestamp,
    holdout_days: int,
) -> tuple[pd.DataFrame, dict[str, object], tuple[str, str]] | None:
    """holdout appearance rows, the shipped conditional point per target, the window."""
    # residuals are measured on APPEARANCES only, and against the estimator that
    # ships: a band fitted to one point estimate does not cover another.
    split_at = cutoff - pd.Timedelta(days=holdout_days)
    train = appearances(features[features["GAME_DATE"] < split_at])
    valid = appearances(
        features[(features["GAME_DATE"] >= split_at) & (features["GAME_DATE"] < cutoff)]
    )
    if train.empty or valid.empty:
        log.warning("holdout window is empty; no quantile offsets will be written")
        return None

    window = (str(split_at.date()), str(pd.Timestamp(cutoff).date()))
    minutes_model = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train, feature_cols, split_at)
    minutes_pred = minutes_model.predict(valid)

    points: dict[str, object] = {}
    for target in QUANTILE_TARGETS:
        if target not in valid.columns:
            log.warning("no %s in the dataset; skipping its quantiles", target)
            continue
        if target == MINUTES_TARGET:
            points[target] = minutes_pred
        else:
            points[target] = conditional_estimate(
                minutes_pred, PerMinuteRate(target).fit(train).predict(valid)
            )
    return valid, points, window


def holdout_quantiles(
    features: pd.DataFrame,
    feature_cols: list[str],
    cutoff: pd.Timestamp,
    holdout_days: int,
) -> dict[str, QuantileOffsets]:
    """P10/P50/P90 offsets for the conditional champion, per target."""
    held = holdout_points(features, feature_cols, cutoff, holdout_days)
    if held is None:
        return {}
    valid, points, window = held
    return {
        target: fit_residual_quantiles(valid[target], point, target, window=window)
        for target, point in points.items()
    }


def holdout_quantiles_by_tier(
    features: pd.DataFrame,
    feature_cols: list[str],
    cutoff: pd.Timestamp,
    holdout_days: int,
) -> dict[str, dict[str, object]]:
    """the challenger per-tier offsets, json-ready, over the same holdout rows."""
    held = holdout_points(features, feature_cols, cutoff, holdout_days)
    if held is None:
        return {}
    valid, points, window = held
    out: dict[str, dict[str, object]] = {}
    for target, point in points.items():
        frame = valid.assign(_point=point)
        by_tier = fit_residual_quantiles_by_tier(frame, target, "_point", window=window)
        out[target] = tiered_quantiles_as_dict(by_tier)
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    features = load_dataset(args.dataset)
    feature_cols = available_features(features)

    cutoff = (
        resolve_cutoff(args.cutoff) if args.cutoff
        else features["GAME_DATE"].max() + pd.Timedelta(days=1)
    )
    train = features[features["GAME_DATE"] < cutoff]
    if train.empty:
        raise SystemExit(f"no training rows before cutoff {cutoff.date()}")

    version = args.version or pd.Timestamp.now("UTC").strftime("%Y%m%d")
    out_dir = version_dir(version, args.models_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = holdout_metrics(features, feature_cols, cutoff, args.holdout_days)

    # ONE cutoff, both models: predict.assert_same_cutoff refuses any other pair.
    model = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(train, feature_cols, cutoff)
    joblib.dump(model, out_dir / MODEL_FILE)

    train_app = appearances(train)
    if train_app.empty:
        raise SystemExit(f"no appearance rows before cutoff {cutoff.date()} to fit minutes on")
    minutes_model = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feature_cols, cutoff)
    joblib.dump(minutes_model, out_dir / MINUTES_FILE)

    # a third model, not the champion: a probability from a model that already saw
    # teammate context cannot be used to build teammate context.
    base_cols = [c for c in BASE_FEATURE_COLS if c in features.columns]
    base_model = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(
        train, base_cols, cutoff
    )
    joblib.dump(base_model, out_dir / BASE_MODEL_FILE)

    ewma_state = snapshot_ewma_state(features, cutoff)
    fallbacks = dict(ewma_state.attrs.get("fallbacks", {}))
    rate_fallbacks = dict(ewma_state.attrs.get("rate_fallbacks", {}))
    ewma_state.to_parquet(out_dir / EWMA_FILE, index=False)

    universe_source = (
        str(features["UNIVERSE_SOURCE"].iloc[0]) if "UNIVERSE_SOURCE" in features.columns
        else "unknown"
    )
    training_window = {
        "start": str(train["GAME_DATE"].min().date()),
        "end": str(train["GAME_DATE"].max().date()),
        "cutoff": str(pd.Timestamp(cutoff).date()),
        "cutoff_policy": CUTOFF_POLICY,
        "rows": int(len(train)),
        "players": int(train["PLAYER_ID"].nunique()),
        "played_rate": round(float(train["PLAYED"].mean()), 6),
    }
    quantiles = holdout_quantiles(features, feature_cols, cutoff, args.holdout_days)
    production = {
        "composition": CHAMPIONS["composition"],
        "estimator": PerMinuteRate("PTS").kind,
        # the PTS halflife, kept under its historical key for pre-9-cat readers.
        "halflife": PerMinuteRate("PTS").halflife,
        "rate_halflives": {t: RATE_HALFLIVES[t] for t in RATE_TARGETS},
        "rate_estimators": {t: RATE_ESTIMATORS[t] for t in RATE_TARGETS},
        "rate_targets": list(RATE_TARGETS),
        "coherence_constraints": [list(pair) for pair in COHERENCE_CONSTRAINTS],
        # league sum(stat)/sum(minutes) priors for players with no rate history.
        "rate_minutes_floor": RATE_MINUTES_FLOOR,
        "rate_fallbacks": {k: round(v, 6) for k, v in rate_fallbacks.items()},
        "fallbacks": {k: round(v, 6) for k, v in fallbacks.items()},
        "quantiles": {target: q.as_dict() for target, q in quantiles.items()},
    }
    if args.tiered_quantiles:
        production["quantiles_by_tier"] = holdout_quantiles_by_tier(
            features, feature_cols, cutoff, args.holdout_days
        )

    metadata = {
        "model_version": version,
        "feature_version": FEATURE_VERSION,
        "git_commit": registry.git_commit(),
        "artifact_checksum": registry.sha256_file(out_dir / MODEL_FILE),
        "minutes_artifact_checksum": registry.sha256_file(out_dir / MINUTES_FILE),
        "base_artifact_checksum": registry.sha256_file(out_dir / BASE_MODEL_FILE),
        "context": {
            "stage1_feature_cols": base_cols,
            "stage2_cross_fit_freq": CROSS_FIT_FREQ,
            "stage2_min_train_rows": CROSS_FIT_MIN_TRAIN_ROWS,
            "context_p_prior": CONTEXT_P_PRIOR,
            "magnitude_window": MAGNITUDE_WINDOW,
            "magnitude_shrink_k": MAGNITUDE_SHRINK_K,
            "magnitude_priors": MAGNITUDE_PRIORS,
            "iterations": 1,
        },
        "champions": CHAMPIONS,
        "availability_hyperparams": LGBM_PARAMS,
        "minutes_hyperparams": LGBM_PARAMS if CHAMPIONS["minutes"] != "ewma" else {},
        "production": production,
        "training_window": training_window,
        "universe_source": universe_source,
        "feature_cols": feature_cols,
        "metrics": metrics,
    }
    with open(out_dir / META_FILE, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
        fh.write("\n")

    gain = model.feature_gain()
    if not gain.empty:
        gain.rename("gain").to_csv(out_dir / "feature_gain.csv", header=True)

    entry = registry.build_entry(
        model_version=version,
        version_dir=out_dir,
        training_window=training_window,
        hyperparams={
            "availability": LGBM_PARAMS,
            "minutes": LGBM_PARAMS if CHAMPIONS["minutes"] != "ewma" else {},
            "production": production,
            "context": metadata["context"],
        },
        metrics=metrics,
        champions=CHAMPIONS,
        universe_source=universe_source,
        feature_cols=feature_cols,
    )
    registry.upsert(entry, args.models_dir / "registry.json")

    print("--- TRAIN ---")
    print(f"version          : {version}")
    print(f"training window  : {training_window['start']} .. {training_window['end']} "
          f"(cutoff {training_window['cutoff']})")
    print(f"universe         : {universe_source}")
    print(f"availability     : {CHAMPIONS['availability']} on {len(feature_cols)} features")
    print(f"base availability: {CHAMPIONS['availability']} on {len(base_cols)} "
          f"teammate-free features (stage 1 of the two-stage pipeline)")
    print(f"minutes|plays    : {CHAMPIONS['minutes']} on {len(train_app):,} appearance rows")
    print(f"production       : EWMA(halflife {production['halflife']}) of stat per minute "
          f"(denominator floor {RATE_MINUTES_FLOOR:g}m) - snapshot only, "
          f"{len(ewma_state):,} players")
    print(f"composition      : {CHAMPIONS['composition']}")
    print(f"feature version  : {FEATURE_VERSION}")
    print(f"artifact sha256  : {metadata['artifact_checksum'][:16]}... (availability), "
          f"{metadata['minutes_artifact_checksum'][:16]}... (minutes)")
    for target, fallback in production["rate_fallbacks"].items():
        print(f"rate fallback {target:<4s}: {fallback:.4f} {target.lower()}/min")
    for target, offsets in quantiles.items():
        levels = ", ".join(
            f"P{int(round(lv * 100)):02d} {off:+.2f}"
            for lv, off in zip(offsets.levels, offsets.offsets)
        )
        print(f"quantiles {target:<6s} : {levels}  (n={offsets.n:,})")
    for key, value in metrics.items():
        print(f"  {key:28s} {value}")
    if not gain.empty:
        print("\ntop 8 availability features by gain:")
        print(gain.head(8).to_string())
    print(f"\nartifacts -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
