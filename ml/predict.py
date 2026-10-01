"""next-games predictions from a trained model version."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402

from fnba_ml.cli import (  # noqa: E402
    add_common_args,
    default_dataset_path,
    load_dataset,
    setup_logging,
    version_dir,
)
from fnba_ml import registry  # noqa: E402
from fnba_ml.coherence import (  # noqa: E402
    COHERENCE_NONE,
    COHERENCE_VARIANTS,
    apply_coherence,
)
from fnba_ml.config import (  # noqa: E402
    DATA_DIR,
    DEFAULT_HORIZON,
    HORIZONS,
    INITIAL_REPORT_DEADLINE_HOUR,
    MINUTES_TARGET,
    MODELS_DIR,
    P_CONTEXT,
    P_CONTEXT_CUTOFF,
    PRODUCTION_TARGETS,
    PROSPECTIVE_COLD_START_FLAG,
    PROSPECTIVE_COLD_START_THROUGH,
    horizon_for_offset,
    horizon_label,
    is_cold_start,
)
from fnba_ml.features import attach_expected_context  # noqa: E402
from fnba_ml.prospective import SOURCE_PROSPECTIVE  # noqa: E402
from fnba_ml.intervals import (  # noqa: E402
    QUANTILE_LEVELS,
    QuantileOffsets,
    attach_quantiles,
)
from fnba_ml.models import (  # noqa: E402
    MIN_PRED,
    MIN_PRED_CUTOFF,
    P_PLAY,
    P_PLAY_CUTOFF,
    LeakageError,
    PerMinuteRate,
    assert_same_cutoff,
    coherence_clip_frame,
    minutes_propagated_estimate,
    validate_minutes_out_of_fold,
    validate_out_of_fold,
)
from fnba_ml.overrides import (  # noqa: E402
    DEFAULT_POLICY,
    OVERRIDE_REASON,
    P_PLAY_MODEL,
    apply_status_overrides,
    latest_statuses,
    override_summary,
    resolve_overrides,
)
from fnba_ml.scenarios import scenario_summary, score_with_scenarios  # noqa: E402
from fnba_ml.store import (  # noqa: E402
    PRODUCTION_CHANNEL,
    RUN_CHANNELS,
    build_prediction_rows,
    build_run_record,
    utc_now,
    write_predictions,
)

log = logging.getLogger("predict")

TARGETS = (MINUTES_TARGET, *PRODUCTION_TARGETS)

BIASED_UNIVERSE = "approximation"

# a multi-day run has no single horizon bucket; this records none rather than a false one
NO_HORIZON = "none"

KEY_COLS = ["PLAYER_ID", "PLAYER_NAME", "GAME_ID", "TEAM_ID", "OPP_TEAM_ID",
            "GAME_DATE", "SEASON", "IS_HOME", "MIN_TIER"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--version", required=True)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--out", type=Path, default=DATA_DIR / "predictions.parquet")
    parser.add_argument("--run-at", default=None,
                        help="only score games on or after this date (a game-date "
                             "selector, not an information boundary; defaults to the "
                             "model cutoff)")
    parser.add_argument("--write-db", action="store_true",
                        help="also insert the run into prediction_runs / "
                             "player_game_predictions")
    parser.add_argument("--allow-biased-universe", action="store_true",
                        help="permit --write-db from the approximation universe")
    parser.add_argument("--notes", default=None, help="free text stored on the run row")
    parser.add_argument("--statuses", type=Path, default=None,
                        help="parquet or csv of latest injury designations")
    parser.add_argument("--statuses-as-of", default=None,
                        help="information boundary for the injury reports; stored "
                             "as prediction_runs.information_as_of (default: now)")
    parser.add_argument("--history-through", default=None,
                        help="last game date whose outcomes fed the features, for a "
                             "frame that holds no played rows (default: derived from "
                             "the dataset)")
    parser.add_argument("--channel", choices=RUN_CHANNELS, default=PRODUCTION_CHANNEL,
                        help="prediction_runs.channel; only 'production' is served")
    parser.add_argument("--horizon", choices=(*HORIZONS, NO_HORIZON),
                        default=DEFAULT_HORIZON,
                        help="when this run is being made relative to tipoff; 'none' "
                             "writes no horizon label (multi-day runs)")
    parser.add_argument("--coherence", choices=COHERENCE_VARIANTS, default=COHERENCE_NONE,
                        help="serving-time coherence correction: team minutes to 240, "
                             "the points identity, both, or none (frozen serving)")
    parser.add_argument("--scenarios", action="store_true",
                        help="score team-games with a questionable or doubtful star "
                             "once per play/sit world and mix the outputs (off by "
                             "default); writes a <out>_scenarios.parquet audit")
    return parser.parse_args(argv)


def load_version(version: str, models_dir: Path):
    """the three models and their shared metadata."""
    dir_ = version_dir(version, models_dir)
    model_path = dir_ / "availability_model.joblib"
    minutes_path = dir_ / "minutes_model.joblib"
    base_path = dir_ / "base_availability_model.joblib"
    meta_path = dir_ / "metadata.json"
    if not model_path.exists():
        raise SystemExit(f"no trained model at {model_path}. run train.py first.")
    if not minutes_path.exists():
        raise SystemExit(
            f"no minutes model at {minutes_path}. this version predates the "
            f"minutes-propagating composition; retrain with train.py to score it."
        )
    if not base_path.exists():
        raise SystemExit(
            f"no base availability model at {base_path}. this version predates the "
            f"two-stage probabilistic teammate context (feature_version v3); the "
            f"served context features cannot be built for an unplayed slate without "
            f"it. retrain with train.py."
        )
    with open(meta_path, encoding="utf-8") as fh:
        metadata = json.load(fh)
    return (
        joblib.load(model_path),
        joblib.load(minutes_path),
        joblib.load(base_path),
        metadata,
    )


def scenario_audit_path(out: Path) -> Path:
    """where --scenarios writes its per-pivotal-player audit, beside --out."""
    return out.with_name(f"{out.stem}_scenarios.parquet")


def load_statuses(path: Path | None) -> pd.DataFrame | None:
    """the injury-report frame from parquet or csv."""
    if path is None:
        return None
    if not path.exists():
        raise SystemExit(f"statuses file not found: {path}")
    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)
    log.info("loaded %d injury status rows from %s", len(frame), path)
    return frame


def load_quantile_offsets(metadata: dict) -> dict[str, QuantileOffsets]:
    """the P10/P50/P90 offsets train.py measured on its holdout window."""
    stored = metadata.get("production", {}).get("quantiles", {}) or {}
    offsets: dict[str, QuantileOffsets] = {}
    for target, payload in stored.items():
        try:
            offsets[target] = QuantileOffsets.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("ignoring unreadable quantile offsets for %s: %s", target, exc)
    if not offsets:
        log.warning(
            "model %s carries no quantile offsets; predictions will be point "
            "estimates only. retrain to populate them.", metadata.get("model_version"),
        )
    return offsets


def rebuild_context(
    features: pd.DataFrame,
    base_model,
    statuses: pd.DataFrame | None,
    as_of: pd.Timestamp,
    policy=DEFAULT_POLICY,
    forced_probabilities: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """returns (features with the expected context rebuilt, a per-row audit frame).

    ``forced_probabilities`` maps a player id to the p_j his teammates' sums read,
    applied after the injury override; the scenario path uses it to pin one world.
    """
    # the override must land on base p BEFORE the teammate sums are taken, or a
    # ruled-out star's minutes never move to his teammates.
    base_p = base_model.predict_proba(features)
    resolved = resolve_overrides(
        features["PLAYER_ID"], base_p, statuses, policy, as_of=as_of
    )
    probability = resolved.probability
    forced = None
    if forced_probabilities:
        pinned = features["PLAYER_ID"].astype(str).map(
            {str(k): float(v) for k, v in forced_probabilities.items()}
        )
        forced = pinned.notna().to_numpy()
        probability = np.where(forced, pinned.to_numpy(dtype=float), probability)
    rebuilt = attach_expected_context(
        features, probability, pd.Timestamp(base_model.cutoff)
    )
    validate_out_of_fold(rebuilt, P_CONTEXT, P_CONTEXT_CUTOFF, "p_context")

    audit = pd.DataFrame({
        "PLAYER_ID": features["PLAYER_ID"].to_numpy(),
        "GAME_ID": features["GAME_ID"].to_numpy(),
        "P_CONTEXT_BASE": base_p,
        "P_CONTEXT": probability,
        "CONTEXT_OVERRIDDEN": resolved.applies,
    })
    if forced is not None:
        audit["CONTEXT_FORCED"] = forced
    log.info(
        "context rebuilt from base p on %d rows; %d of them corrected by the injury "
        "report before the teammate sums were taken",
        len(features), resolved.n_applied,
    )
    return rebuilt, audit


def build_predictions(
    features: pd.DataFrame, model, minutes_model, metadata: dict
) -> pd.DataFrame:
    """score every row: P(play), E[minutes|plays], and the composed production."""
    # all three guards run before anything is multiplied.
    scored = minutes_model.attach(model.attach(features))
    validate_out_of_fold(scored)
    validate_minutes_out_of_fold(scored)
    assert_same_cutoff(scored)

    rate_fallbacks = metadata.get("production", {}).get("rate_fallbacks", {})
    quantiles = load_quantile_offsets(metadata)
    out = scored[
        [c for c in KEY_COLS if c in scored.columns]
        + [P_PLAY, P_PLAY_CUTOFF, MIN_PRED, MIN_PRED_CUTOFF]
    ].copy()

    minutes = scored[MIN_PRED].to_numpy(dtype=float)
    out[f"E_{MINUTES_TARGET}_COND"] = minutes
    out[f"E_{MINUTES_TARGET}"] = (
        scored[P_PLAY].to_numpy(dtype=float) * minutes
    ).clip(0.0)
    if MINUTES_TARGET in quantiles:
        out = attach_quantiles(out, minutes, quantiles[MINUTES_TARGET])

    # read from metadata, not config: an old artifact must keep scoring with the
    # halflives it was trained under.
    halflives = metadata.get("production", {}).get("rate_halflives", {}) or {}
    estimators = metadata.get("production", {}).get("rate_estimators", {}) or {}

    emitted: list[str] = []
    for target in PRODUCTION_TARGETS:
        estimator = estimators.get(target)
        rate = PerMinuteRate.from_fallback(
            target,
            float(rate_fallbacks.get(target, 0.0)),
            halflife=halflives.get(target),
            estimator=estimator,
        )
        has_column = rate.column in features.columns
        if target not in rate_fallbacks and not has_column:
            log.warning("no per-minute rate available for %s; skipping it", target)
            continue
        if target not in rate_fallbacks:
            log.warning(
                "model %s carries no league rate fallback for %s; players with no "
                "per-minute history will get 0. retrain to populate it.",
                metadata.get("model_version"), target,
            )
        rate_values = rate.predict(features)
        conditional, unconditional = minutes_propagated_estimate(scored, rate_values)
        out[f"E_{target}_COND"] = conditional
        out[f"E_{target}"] = unconditional
        out[f"RATE_{target}"] = rate_values
        # quantiles wrap the CONDITIONAL estimate: the offsets were measured on
        # appearances.
        if target in quantiles:
            out = attach_quantiles(out, conditional, quantiles[target])
        emitted.append(target)

    # every template is clipped against its own kind, because independently
    # smoothed rates do not preserve FGM <= FGA on their own.
    templates = ["E_{target}_COND", "E_{target}"]
    templates += [
        f"Q{int(round(level * 100)):02d}_{{target}}" for level in QUANTILE_LEVELS
    ]
    clip_counts: dict[str, int] = {}
    for template in templates:
        out, counts = coherence_clip_frame(out, template)
        for constraint, n in counts.items():
            clip_counts[f"{template.format(target='*')} {constraint}"] = n

    out["MODEL_VERSION"] = metadata.get("model_version")
    out["FEATURE_VERSION"] = metadata.get("feature_version", "unknown")

    # a reporting split, never a filter and never a feature: nothing reads it back.
    out[PROSPECTIVE_COLD_START_FLAG] = is_cold_start(out["GAME_DATE"]).to_numpy()

    log.info("emitted %d production stats: %s", len(emitted), ", ".join(emitted))
    out = out.reset_index(drop=True)
    # attached AFTER the reset: attrs propagation through pandas is version-dependent.
    out.attrs["coherence_clips"] = clip_counts
    return out


# an APPROXIMATION used only when the frame carries no real tipoff timestamp;
# horizon_metadata's tip_source labels every number derived from it.
NOMINAL_TIP_HOUR_UTC: int = 0


def horizon_metadata(
    features: pd.DataFrame,
    statuses: pd.DataFrame | None,
    as_of: pd.Timestamp,
    requested: str | None,
) -> dict[str, object]:
    """the per-run horizon facts config.HORIZON_RUN_METADATA names."""
    boundary = pd.Timestamp(as_of)
    if boundary.tzinfo is not None:
        boundary = boundary.tz_convert("UTC").tz_localize(None)

    if "SCHEDULED_AT" in features.columns and features["SCHEDULED_AT"].notna().any():
        tip = pd.to_datetime(features["SCHEDULED_AT"], errors="coerce", utc=True)
        tip = tip.dt.tz_localize(None)
        tip_source = "nba_schedule.scheduled_at"
    else:
        tip = pd.to_datetime(features["GAME_DATE"]).dt.normalize() + pd.Timedelta(
            hours=NOMINAL_TIP_HOUR_UTC
        )
        tip_source = f"approximated: GAME_DATE + {NOMINAL_TIP_HOUR_UTC:02d}:00 UTC"

    hours = (tip - boundary).dt.total_seconds() / 3600.0
    hours = hours.replace([float("inf"), float("-inf")], pd.NA).dropna()
    median = float(hours.median()) if len(hours) else float("nan")

    latest_report_at = None
    report_age = None
    report_count = 0
    if statuses is not None and len(statuses) > 0:
        admissible = latest_statuses(statuses, boundary)
        report_count = int(len(admissible))
        if report_count:
            newest = pd.Timestamp(admissible["captured_at"].max())
            latest_report_at = newest.isoformat(timespec="seconds")
            report_age = round((boundary - newest).total_seconds() / 3600.0, 3)

    # the league's initial-report deadline for the EARLIEST game on the slate: 5pm
    # local the day before. local time is not on the frame, so the deadline is
    # evaluated in UTC against the nominal tip hour - which is the same
    # approximation ``tip_source`` already declares, not a second one.
    first_deadline_passed = None
    if len(hours):
        earliest_tip = pd.Timestamp(tip.min())
        deadline = (earliest_tip.normalize() - pd.Timedelta(days=1)) + pd.Timedelta(
            hours=INITIAL_REPORT_DEADLINE_HOUR
        )
        first_deadline_passed = bool(boundary >= deadline)

    return {
        "horizon_requested": horizon_label(requested) if requested else NO_HORIZON,
        "horizon_measured": horizon_for_offset(median) or "outside every window",
        "hours_to_tip_min": round(float(hours.min()), 3) if len(hours) else None,
        "hours_to_tip_median": round(median, 3) if len(hours) else None,
        "hours_to_tip_max": round(float(hours.max()), 3) if len(hours) else None,
        "tip_source": tip_source,
        "latest_report_at": latest_report_at,
        "report_age_hours": report_age,
        "report_count": report_count,
        "first_deadline_passed": first_deadline_passed,
    }


def history_through(features: pd.DataFrame) -> date | None:
    """the last GAME_DATE with a played outcome in the frame, or None."""
    if "GAME_DATE" not in features.columns or features.empty:
        return None
    if "PLAYED" in features.columns:
        played = features[pd.to_numeric(features["PLAYED"], errors="coerce") == 1]
    elif "UNIVERSE_SOURCE" in features.columns:
        played = features[features["UNIVERSE_SOURCE"].astype(str) != SOURCE_PROSPECTIVE]
    else:
        return None
    if played.empty:
        return None
    newest = pd.to_datetime(played["GAME_DATE"], errors="coerce").max()
    return None if pd.isna(newest) else pd.Timestamp(newest).date()


def as_utc(value: object) -> pd.Timestamp:
    """an aware UTC timestamp; a naive one is taken to already be UTC."""
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def forecast_cutoff(run_at: pd.Timestamp, information_as_of: pd.Timestamp) -> pd.Timestamp:
    """the latest instant any input was allowed to see: max(start of run_at day, info)."""
    return max(as_utc(pd.Timestamp(run_at).normalize()), as_utc(information_as_of))


def run_notes(
    cold_rows: int, n_rows: int, notes: str | None, coherence: str = COHERENCE_NONE
) -> str:
    """the run-level notes: cold-start count, user text, then the coherence choice.

    the token is written only for a non-default choice, so the frozen serving run's
    note text is byte-identical to what section 13 pinned.
    """
    return "; ".join(filter(None, [
        f"{PROSPECTIVE_COLD_START_FLAG}={cold_rows}/{n_rows} rows "
        f"(GAME_DATE <= {PROSPECTIVE_COLD_START_THROUGH})",
        notes,
        f"coherence={coherence}" if coherence != COHERENCE_NONE else None,
    ]))


def universe_source(features: pd.DataFrame, metadata: dict) -> str:
    if "UNIVERSE_SOURCE" in features.columns and len(features) > 0:
        return str(features["UNIVERSE_SOURCE"].iloc[0])
    return str(metadata.get("universe_source", "unknown"))


def write_run(
    predictions: pd.DataFrame,
    metadata: dict,
    run_at: pd.Timestamp,
    notes: str | None,
    horizon: str | None,
    horizon_facts: dict[str, object] | None = None,
    channel: str = PRODUCTION_CHANNEL,
    information_as_of: pd.Timestamp | None = None,
    history_through_date: date | None = None,
    coherence: str = COHERENCE_NONE,
) -> tuple[int, int]:
    """build the rows, insert them in one transaction, link the run back."""
    rows = build_prediction_rows(predictions, TARGETS, QUANTILE_LEVELS)
    predicted_at = utc_now()
    information = (
        as_utc(information_as_of).to_pydatetime() if information_as_of is not None else None
    )
    run_record = build_run_record(
        metadata,
        predicted_at=predicted_at,
        # the start of the first scored day; build_run_record stores the later of
        # it and information_as_of, so the column is a true boundary.
        forecast_cutoff_at=as_utc(pd.Timestamp(run_at).normalize()).to_pydatetime(),
        # the commit that made the PREDICTION. the commit that trained the model
        # is a different fact and lives in the registry entry.
        code_sha=registry.git_commit(),
        status="complete",
        notes=notes,
        horizon=horizon,
        channel=channel,
        information_as_of=information,
        history_through=history_through_date,
    )
    run_id = write_predictions(rows, run_record)
    overridden = (
        int(predictions[OVERRIDE_REASON].notna().sum())
        if OVERRIDE_REASON in predictions.columns else 0
    )
    registry.record_prediction_run(
        str(metadata.get("model_version")),
        {
            "run_id": run_id,
            "predicted_at": predicted_at.isoformat(timespec="seconds"),
            "forecast_cutoff_at": pd.Timestamp(run_record["forecast_cutoff_at"]).isoformat(),
            "channel": channel,
            "information_as_of": information.isoformat() if information else None,
            "history_through": str(history_through_date) if history_through_date else None,
            # the horizon lands in both places on purpose: prediction_runs.notes is
            # what a database consumer reads, the registry entry is what an audit of
            # the artifact reads, and neither should have to join to the other to
            # answer "how far before tipoff was this claim made".
            "horizon": horizon_label(horizon) if horizon else NO_HORIZON,
            # the measured facts behind the label. a horizon comparison that has only
            # labels to work with cannot separate "the later run had better
            # information" from "the later run was made three hours later"; these are
            # what make the separation possible, and they are recorded whether or not
            # anyone is looking at them yet.
            "horizon_facts": horizon_facts,
            "rows": len(rows),
            "player_games": int(len(predictions)),
            "status_overrides": overridden,
            "coherence": coherence,
            "override_policy": DEFAULT_POLICY.as_dict() if overridden else None,
        },
    )
    return run_id, len(rows)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    model, minutes_model, base_model, metadata = load_version(
        args.version, args.models_dir
    )
    features = load_dataset(args.dataset)

    run_at = pd.Timestamp(args.run_at) if args.run_at else pd.Timestamp(model.cutoff)
    upcoming = features[features["GAME_DATE"] >= run_at]
    if upcoming.empty:
        raise SystemExit(
            f"no scheduled rows on or after {run_at.date()} in {args.dataset}. "
            f"the model's cutoff is {pd.Timestamp(model.cutoff).date()}."
        )

    statuses = load_statuses(args.statuses)
    statuses_as_of = (
        pd.Timestamp(args.statuses_as_of) if args.statuses_as_of
        else pd.Timestamp(utc_now())
    )
    run_history_through = (
        pd.Timestamp(args.history_through).date() if args.history_through
        else history_through(features)
    )
    run_cutoff = forecast_cutoff(run_at, statuses_as_of)

    # STAGES 1-3 BEFORE ANY FINAL SCORING. the report has to reach the teammate
    # context, not only the row's own probability, or the projections page would
    # correctly show a ruled-out star at ~0 and still show his backup the minutes of
    # a night the star plays.
    slate = upcoming
    try:
        upcoming, context_audit = rebuild_context(
            upcoming, base_model, statuses, statuses_as_of, DEFAULT_POLICY
        )
    except LeakageError as exc:
        raise SystemExit(
            f"refusing to build context features: {exc}. the base availability "
            f"model was trained past the games it is being asked about."
        ) from exc

    try:
        predictions = build_predictions(upcoming, model, minutes_model, metadata)
    except LeakageError as exc:
        raise SystemExit(
            f"refusing to emit predictions: {exc}. the model was trained past "
            f"{run_at.date()}; score only games at or after its cutoff "
            f"{pd.Timestamp(model.cutoff).date()}, or retrain."
        ) from exc

    # AFTER scoring, BEFORE rows are built. the layer needs the model's number to
    # blend with, and the row builder needs the final one. This is the SECOND
    # application of the same policy in one run: the first corrected the
    # probabilities the context features were built from, this one corrects the
    # player's own served P(play).
    predictions = apply_status_overrides(
        predictions, statuses, DEFAULT_POLICY, as_of=statuses_as_of
    )
    scenario_audit: pd.DataFrame | None = None
    if args.scenarios:
        try:
            predictions, scenario_audit = score_with_scenarios(
                slate, base_model, model, minutes_model, metadata, statuses,
                statuses_as_of, DEFAULT_POLICY,
                baseline=predictions, rebuild=rebuild_context, score=build_predictions,
            )
        except LeakageError as exc:
            raise SystemExit(f"refusing to emit scenario predictions: {exc}") from exc

    # last: after the overrides so the minute sums use the served P(play), and
    # after the scenario mix so a rescored team-game is corrected too.
    predictions = apply_coherence(predictions, args.coherence)

    source = universe_source(upcoming, metadata)
    notes = args.notes
    if scenario_audit is not None:
        scenario_facts = scenario_summary(scenario_audit)
        notes = "; ".join(filter(None, [
            f"scenarios=on; scenario_team_games={scenario_facts['team_games']}",
            notes,
        ]))
    horizon = None if args.horizon == NO_HORIZON else args.horizon

    # the run-level half of the cold-start flag. Prepended to notes on the same
    # pattern ``store.build_run_record`` uses for the horizon, and for the same
    # reason: prediction_runs.notes is free text on an APPEND-ONLY row, so a label
    # written there is as immutable as a column would be and needs no migration.
    # Recorded whether or not any row is flagged, because "this run was made
    # entirely outside the cold-start window" is a fact a look report needs to be
    # able to read, and an absent note cannot say it.
    cold_rows = int(predictions[PROSPECTIVE_COLD_START_FLAG].sum())
    notes = run_notes(cold_rows, len(predictions), notes, args.coherence)

    if args.write_db and source == BIASED_UNIVERSE:
        if not args.allow_biased_universe:
            print(
                "refusing --write-db: this dataset was built from the BIASED "
                "approximation universe, which over-predicts availability in every "
                "calibration bin and cannot represent an absence longer than ~16 "
                "team-games. rebuild from player_game_status, or pass "
                "--allow-biased-universe if you understand these numbers are not "
                "servable."
            )
            return 2
        notes = "; ".join(filter(None, [notes, "BIASED UNIVERSE: approximation, not servable"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(args.out, index=False)
    scenario_path = scenario_audit_path(args.out)
    if scenario_audit is not None:
        scenario_audit.to_parquet(scenario_path, index=False)

    horizon_facts = horizon_metadata(upcoming, statuses, statuses_as_of, horizon)
    if horizon and horizon_facts["horizon_measured"] != horizon:
        log.warning(
            "this run was labelled %r but its measured median offset (%s h) puts it "
            "in %r. the label is what gets stored; the measurement is what should be "
            "believed.",
            horizon, horizon_facts["hours_to_tip_median"],
            horizon_facts["horizon_measured"],
        )

    written: tuple[int, int] | None = None
    if args.write_db:
        written = write_run(
            predictions, metadata, run_at, notes, horizon, horizon_facts,
            channel=args.channel,
            information_as_of=statuses_as_of,
            history_through_date=run_history_through,
            coherence=args.coherence,
        )

    summary = override_summary(predictions)

    print("--- PREDICT ---")
    print(f"version   : {args.version}")
    print(f"run at    : {run_at.date()} (model cutoff {pd.Timestamp(model.cutoff).date()})")
    print(f"channel   : {args.channel}")
    print(f"cutoff    : {run_cutoff.isoformat()} (latest instant any input could see)")
    print(f"info as of: {as_utc(statuses_as_of).isoformat()} (injury reports)")
    print(f"history   : outcomes through {run_history_through or 'unknown'}")
    print(f"horizon   : {horizon_label(horizon) if horizon else NO_HORIZON}")
    for key, value in horizon_facts.items():
        if key != "horizon_requested":
            print(f"  {key:22s} {value}")
    print(f"universe  : {source}")
    print(f"coherence : {args.coherence}")
    print(f"context p : mean {upcoming[P_CONTEXT].mean():.4f} "
          f"(base {context_audit['P_CONTEXT_BASE'].mean():.4f}, "
          f"{int(context_audit['CONTEXT_OVERRIDDEN'].sum()):,} rows corrected by the "
          f"report before the teammate sums)")
    print(f"rows      : {len(predictions):,}")
    print(f"games     : {predictions['GAME_ID'].nunique():,}")
    print(f"cold start: {cold_rows:,} of {len(predictions):,} rows "
          f"({cold_rows / max(len(predictions), 1):.1%}) have GAME_DATE <= "
          f"{PROSPECTIVE_COLD_START_THROUGH}")
    print(f"mean P(play) : {predictions[P_PLAY].mean():.4f}  "
          f"(model {predictions[P_PLAY_MODEL].mean():.4f})")
    for target in TARGETS:
        col = f"E_{target}"
        if col in predictions.columns:
            print(f"mean E[{target}] : {predictions[col].mean():.4f}  "
                  f"(conditional {predictions[f'E_{target}_COND'].mean():.4f})")
    if summary.empty:
        print("overrides : none applied "
              f"({'no --statuses supplied' if args.statuses is None else 'no reportable status'})")
    else:
        print(f"overrides : {int(summary['rows'].sum()):,} rows, as of {statuses_as_of}")
        print(summary.to_string(index=False))
    if scenario_audit is None:
        print("scenarios : off")
    else:
        facts = scenario_summary(scenario_audit)
        print(f"scenarios : {facts['team_games']:,} team-games, "
              f"{facts['pivotal_players']:,} pivotal players, "
              f"{facts['scenarios']:,} worlds scored; backup E[MIN|plays] delta "
              f"mean {facts['mean_backup_min_delta']}, max {facts['max_backup_min_delta']}")
        if not scenario_audit.empty:
            print(scenario_audit[[
                "GAME_ID", "TEAM_ID", "PLAYER_ID", "STATUS", "P_PLAY",
                "BACKUP_PLAYER_ID", "BACKUP_MIN_DELTA",
            ]].to_string(index=False))
        print(f"            audit -> {scenario_path}")
    print(f"saved     -> {args.out}")
    if written is not None:
        print(f"database  -> prediction_runs id {written[0]}, {written[1]:,} prediction rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
