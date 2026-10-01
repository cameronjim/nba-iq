"""the frozen `prospective_2026_27_v2` pre-registration, machine-readable half.

every value here is a hand-copied snapshot of the served configuration, never a
read of the live constant: a mirror that reads the live value can never fail.
tests/test_prospective_freeze.py compares each one against the live module.
"""

from __future__ import annotations

import pandas as pd

PROSPECTIVE_PROTOCOL_VERSION = "prospective_2026_27_v2"

PROSPECTIVE_ARTIFACT_CHECKSUMS: dict[str, str] = {
    "availability_model.joblib":
        "aa62f880f6774537ab58ba52d0aa4e641c96964ca3d26691e9ab7dd52180f06c",
    "base_availability_model.joblib":
        "3d5fcdeb180f4e8c6650b9c3542d4ef1714401f4048a3d14605de7747262adba",
    "ewma_state.parquet":
        "bcc83dec9e55375645f80165df6f490c6056d9659215bde497d31ced99943328",
    "feature_gain.csv":
        "f2765c542b452e1ff8726be27e556c0a1a4a7b323b503ca2cdd01dc191728b94",
    "metadata.json":
        "24978b3261261ad52d28cae7e7fceee6378655d20e515c26882391545adae3fe",
    "minutes_model.joblib":
        "2fd13615d64ba1d62e33bc903cfa4851ff63b602f2a2dec4e96af9343f28115a",
}

# (name, look date, minimum scheduled rows for the look to be binding). the date
# is a cutoff: a look scores every game with GAME_DATE strictly before it.
PROSPECTIVE_LOOKS: tuple[tuple[str, str, int], ...] = (
    ("dec1", "2026-12-01", 7_500),
    ("all_star", "2027-02-15", 20_000),
    ("season_end", "2027-04-20", 32_000),
)
PROSPECTIVE_LOOK_DATES: tuple[str, ...] = tuple(d for _, d, _ in PROSPECTIVE_LOOKS)

# the label stamped into prediction_runs.notes; a run without it is not part of
# the prospective test.
PROSPECTIVE_RUN_NOTE_LABEL = "prospective_2026_27_v2"

# a secondary reporting axis, not a filter: flagged rows stay in every endpoint.
PROSPECTIVE_COLD_START_FLAG = "cold_start"
PROSPECTIVE_COLD_START_THROUGH = "2026-11-30"

PROSPECTIVE_OCTOBER_REPLAY_WINDOW: tuple[str, str] = ("2025-10-01", "2025-10-31")

PROSPECTIVE_SHADOW_FEATURE_SETS: tuple[str, ...] = ("v1",)

PROSPECTIVE_SERVING_HORIZON = "gameday"

PROSPECTIVE_FEATURE_VERSION = "v3"
PROSPECTIVE_MODEL_VERSION = "20260818"

# sha256 of "\n".join(FEATURE_COLS); any addition, removal or reordering of that
# list invalidates the pinned artifact.
PROSPECTIVE_FEATURE_COLS_SHA256 = (
    "914cdc17c25ee9cb32b072f254691a625472b43ecb37b3da0c23483f165e1b6e"
)
PROSPECTIVE_N_FEATURES = 51

PROSPECTIVE_CHAMPIONS: dict[str, str] = {
    "availability": "lightgbm",
    "minutes": "lightgbm",
    "production": "ewma",
    "composition": "decomposed_p_x_minutes_x_ppm",
}
PROSPECTIVE_RATE_TARGETS: tuple[str, ...] = (
    "PTS", "AST", "REB", "STL", "BLK", "TOV", "FG3M", "FGM", "FGA", "FTM", "FTA",
)
PROSPECTIVE_RATE_HALFLIVES: dict[str, float] = {
    "PTS": 5.0, "AST": 5.0, "REB": 20.0, "TOV": 20.0, "FG3M": 20.0, "STL": 20.0,
    "FTM": 12.0, "FTA": 12.0, "BLK": 5.0, "FGM": 5.0, "FGA": 5.0,
}
PROSPECTIVE_RATE_ESTIMATORS: dict[str, str] = {
    "PTS": "ewma", "AST": "ewma", "REB": "ewma", "STL": "expanding", "BLK": "ewma",
    "TOV": "ewma", "FG3M": "ewma", "FGM": "ewma", "FGA": "ewma", "FTM": "ewma",
    "FTA": "ewma",
}
PROSPECTIVE_HORIZON_WINDOWS: dict[str, tuple[float, float]] = {
    "lock": (0.0, 2.0),
    "gameday": (2.0, 12.0),
    "early": (12.0, 48.0),
}
PROSPECTIVE_COHERENCE_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("FGM", "FGA"), ("FG3M", "FGM"), ("FTM", "FTA"),
)

# literals rather than an import: overrides imports models which imports config.
PROSPECTIVE_OVERRIDE_CONSTANTS: dict[str, float] = {
    "out_probability": 0.02,
    "doubtful_probability": 0.10,
    "questionable_model_weight": 0.6,
    "questionable_prior": 0.60,
    "probable_model_weight": 0.85,
    "probable_shift": 0.15,
}

# literals for the same reason: report expiry and the statuses the layer leaves alone.
PROSPECTIVE_REPORT_MAX_AGE_HOURS = 72.0
PROSPECTIVE_PASSTHROUGH_STATUSES: frozenset[str] = frozenset(
    {"available", "day_to_day", "cleared", "unknown"}
)

# `direction` says which way a FAILURE lies; a `None` threshold means the look is
# report-only for that endpoint because it has no power at that sample size.
PROSPECTIVE_FALSIFICATION: dict[str, dict[str, object]] = {
    "availability_brier_v3_vs_v1": {
        "direction": "lower_is_better",
        "retrospective": -1.892,
        "block_sd": 1.724,
        "thresholds": {"dec1": 1.00, "all_star": -0.40, "season_end": -0.75},
    },
    "minutes_mae_v3_vs_v1": {
        "direction": "lower_is_better",
        "retrospective": -0.807,
        "block_sd": 0.580,
        "thresholds": {"dec1": 0.50, "all_star": -0.30, "season_end": -0.40},
    },
    "pts_uncond_mae_v3_vs_v1": {
        "direction": "lower_is_better",
        "retrospective": -0.319,
        "block_sd": 0.282,
        "thresholds": {"dec1": 0.30, "all_star": 0.00, "season_end": -0.10},
    },
    "ninecat_aggregate_vs_ewma_total": {
        "direction": "higher_is_better",
        "retrospective": 1.295,
        "block_sd": 0.202,
        "thresholds": {"dec1": 0.50, "all_star": 0.50, "season_end": 0.50},
    },
    "minutes_mae_vs_ewma_baseline": {
        "direction": "higher_is_better",
        "retrospective": 2.986,
        "block_sd": 0.518,
        "thresholds": {"dec1": 2.00, "all_star": 2.00, "season_end": 2.00},
    },
    "availability_brier_skill_vs_shifted_rate": {
        "direction": "higher_is_better",
        "retrospective": 0.3516,
        "block_sd": 0.0151,
        "thresholds": {"dec1": 0.25, "all_star": 0.25, "season_end": 0.25},
    },
    "stl_expanding_vs_h20_ewma": {
        "direction": "higher_is_better",
        "retrospective": 2.082,
        "block_sd": 0.855,
        "thresholds": {"dec1": None, "all_star": 1.00, "season_end": 1.00},
    },
    "rare_event_h20_vs_expanding": {
        "direction": "higher_is_better",
        "retrospective": -0.797,
        "block_sd": 0.500,
        "thresholds": {"dec1": None, "all_star": None, "season_end": 0.00},
    },
    # calibration is two-sided, so it takes three entries: `_ceiling` and
    # `_abs_intercept` are lower_is_better because a failure lies above them.
    "availability_calibration_slope_floor": {
        "direction": "higher_is_better",
        "retrospective": None,
        "block_sd": None,
        "thresholds": {"dec1": None, "all_star": None, "season_end": 0.85},
    },
    "availability_calibration_slope_ceiling": {
        "direction": "lower_is_better",
        "retrospective": None,
        "block_sd": None,
        "thresholds": {"dec1": None, "all_star": None, "season_end": 1.20},
    },
    "availability_calibration_abs_intercept": {
        "direction": "lower_is_better",
        "retrospective": None,
        "block_sd": None,
        "thresholds": {"dec1": None, "all_star": None, "season_end": 0.25},
    },
    "override_layer_brier_increment": {
        "direction": "higher_is_better",
        "retrospective": None,
        "block_sd": None,
        "thresholds": {"dec1": None, "all_star": 0.00, "season_end": 0.00},
    },
}

# ratios of the October window's metric to the same replay's non-October metric.
PROSPECTIVE_OCTOBER_GATE: dict[str, float] = {
    "max_brier_ratio": 1.42,
    "max_minutes_mae_ratio": 1.15,
    "min_prediction_coverage": 0.99,
}

PROSPECTIVE_2026_27: dict[str, object] = {
    "protocol_version": PROSPECTIVE_PROTOCOL_VERSION,
    "frozen_at": "2026-10-01",
    "refrozen_from": "prospective_2026_27_v1",
    "season": "2026-27",
    "model_version": PROSPECTIVE_MODEL_VERSION,
    "feature_version": PROSPECTIVE_FEATURE_VERSION,
    "artifact_dir": "models/20260818",
    "artifact_checksums": PROSPECTIVE_ARTIFACT_CHECKSUMS,
    "looks": PROSPECTIVE_LOOKS,
    "look_dates": PROSPECTIVE_LOOK_DATES,
    "run_note_label": PROSPECTIVE_RUN_NOTE_LABEL,
    "cold_start_flag": PROSPECTIVE_COLD_START_FLAG,
    "cold_start_through": PROSPECTIVE_COLD_START_THROUGH,
    "october_replay_window": PROSPECTIVE_OCTOBER_REPLAY_WINDOW,
    "october_gate": PROSPECTIVE_OCTOBER_GATE,
    "shadow_feature_sets": PROSPECTIVE_SHADOW_FEATURE_SETS,
    "serving_horizon": PROSPECTIVE_SERVING_HORIZON,
    "champions": PROSPECTIVE_CHAMPIONS,
    "rate_targets": PROSPECTIVE_RATE_TARGETS,
    "rate_halflives": PROSPECTIVE_RATE_HALFLIVES,
    "rate_estimators": PROSPECTIVE_RATE_ESTIMATORS,
    "coherence_constraints": PROSPECTIVE_COHERENCE_CONSTRAINTS,
    "override_constants": PROSPECTIVE_OVERRIDE_CONSTANTS,
    "report_max_age_hours": PROSPECTIVE_REPORT_MAX_AGE_HOURS,
    "passthrough_statuses": PROSPECTIVE_PASSTHROUGH_STATUSES,
    "horizon_windows": PROSPECTIVE_HORIZON_WINDOWS,
    "feature_cols_sha256": PROSPECTIVE_FEATURE_COLS_SHA256,
    "n_features": PROSPECTIVE_N_FEATURES,
    "falsification": PROSPECTIVE_FALSIFICATION,
}


def is_cold_start(
    game_date: object, through: str = PROSPECTIVE_COLD_START_THROUGH
) -> "pd.Series | bool":
    """is this game inside the cold-start window; scalar or elementwise.

    comparison is on the normalised date, so a 2026-11-30 19:30 tipoff is still
    inside a window that ends on 2026-11-30. an unreadable date is not flagged.
    """
    boundary = pd.Timestamp(through).normalize()
    if isinstance(game_date, (pd.Series, pd.Index)) or (
        hasattr(game_date, "__len__") and not isinstance(game_date, str)
    ):
        dates = pd.to_datetime(pd.Series(game_date), errors="coerce").dt.normalize()
        return dates.notna() & (dates <= boundary)
    stamp = pd.Timestamp(game_date)
    return bool(pd.notna(stamp)) and pd.Timestamp(stamp).normalize() <= boundary
