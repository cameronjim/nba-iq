from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from fnba_ml import config, frozen, overrides

ML_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ML_ROOT / "models" / config.PROSPECTIVE_MODEL_VERSION
SHADOW = config.PROSPECTIVE_SHADOW_ARTIFACTS["v3"]
SHADOW_DIR = ML_ROOT / "models" / str(SHADOW["model_version"])
REGISTRY = ML_ROOT / "models" / "registry.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_no_pinned_checksum_is_a_placeholder() -> None:
    # arrange
    placeholder = config.PROSPECTIVE_CHECKSUM_PLACEHOLDER

    # act
    unfilled = sorted(
        name for name, digest in config.PROSPECTIVE_ARTIFACT_CHECKSUMS.items()
        if digest == placeholder or len(digest) != 64
    )

    # assert
    assert not unfilled, (
        f"{', '.join(unfilled)} still carry the {placeholder!r} placeholder. train "
        f"models/{config.PROSPECTIVE_MODEL_VERSION}/ (ML Evaluate, train_feature_set "
        f"{config.PROSPECTIVE_FEATURE_SET}), commit it, and copy each file's sha256 "
        "into frozen.PROSPECTIVE_ARTIFACT_CHECKSUMS (MODEL.md 24)"
    )


def test_frozen_artifact_directory_exists() -> None:
    assert ARTIFACT_DIR.is_dir(), (
        f"the frozen 2026-27 serving artifact {ARTIFACT_DIR} is missing. the "
        "protocol pins a specific set of bytes; without them there is nothing to "
        "run the prospective test with"
    )


@pytest.mark.parametrize(
    "filename", sorted(config.PROSPECTIVE_ARTIFACT_CHECKSUMS)
)
def test_pinned_checksum_matches_disk(filename: str) -> None:
    path = ARTIFACT_DIR / filename
    assert path.is_file(), f"{filename} is pinned by the protocol and is not on disk"
    assert _sha256(path) == config.PROSPECTIVE_ARTIFACT_CHECKSUMS[filename], (
        f"{filename} does not match the checksum frozen in "
        "config.PROSPECTIVE_ARTIFACT_CHECKSUMS. the serving artifact changed after "
        "the freeze - bump PROSPECTIVE_PROTOCOL_VERSION and re-freeze MODEL.md "
        "section 13, or revert"
    )


def test_checksum_set_covers_the_whole_artifact_directory() -> None:
    on_disk = {p.name for p in ARTIFACT_DIR.iterdir() if p.is_file()}
    assert on_disk == set(config.PROSPECTIVE_ARTIFACT_CHECKSUMS)


def test_pinned_checksums_agree_with_the_registry() -> None:
    entries = json.loads(REGISTRY.read_text())["entries"]
    entry = next(
        (e for e in entries if e["model_version"] == config.PROSPECTIVE_MODEL_VERSION),
        None,
    )
    assert entry is not None, (
        f"registry.json has no entry for {config.PROSPECTIVE_MODEL_VERSION}; merge the "
        "one the training run uploaded"
    )
    recorded = {a["path"]: a["sha256"] for a in entry["artifacts"]}
    assert recorded == config.PROSPECTIVE_ARTIFACT_CHECKSUMS


def test_frozen_metadata_matches_the_protocol() -> None:
    meta = json.loads((ARTIFACT_DIR / "metadata.json").read_text())
    assert meta["model_version"] == config.PROSPECTIVE_MODEL_VERSION
    assert meta["feature_version"] == config.PROSPECTIVE_FEATURE_VERSION
    assert meta["feature_set"] == config.PROSPECTIVE_FEATURE_SET
    assert meta["training_window"]["cutoff"] == "2026-04-13"
    assert "base_artifact_checksum" in meta
    assert meta["champions"] == config.PROSPECTIVE_CHAMPIONS
    assert meta["production"]["rate_halflives"] == config.PROSPECTIVE_RATE_HALFLIVES
    assert meta["production"]["rate_estimators"] == config.PROSPECTIVE_RATE_ESTIMATORS
    assert tuple(meta["production"]["rate_targets"]) == config.PROSPECTIVE_RATE_TARGETS
    assert tuple(meta["feature_cols"]) == tuple(
        config.FEATURE_SETS[config.PROSPECTIVE_FEATURE_SET]
    )


@pytest.mark.parametrize("filename", sorted(config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS))
def test_the_v3_shadow_artifact_is_still_byte_for_byte_20260818(filename: str) -> None:
    # arrange
    path = SHADOW_DIR / filename

    # act
    digest = _sha256(path)

    # assert
    assert SHADOW["checksums"] is config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS
    assert digest == config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS[filename]


def test_the_v3_shadow_checksums_agree_with_the_registry() -> None:
    # arrange
    entries = json.loads(REGISTRY.read_text())["entries"]

    # act
    entry = next(e for e in entries if e["model_version"] == SHADOW["model_version"])

    # assert
    recorded = {a["path"]: a["sha256"] for a in entry["artifacts"]}
    assert recorded == config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS


def test_the_v3_shadow_directory_holds_nothing_else() -> None:
    # act
    on_disk = {p.name for p in SHADOW_DIR.iterdir() if p.is_file()}

    # assert
    assert on_disk == set(config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS)


def test_the_v3_shadow_metadata_names_the_v3_contract() -> None:
    # arrange
    meta = json.loads((SHADOW_DIR / "metadata.json").read_text())

    # act
    feature_cols = tuple(meta["feature_cols"])

    # assert
    assert meta["model_version"] == SHADOW["model_version"] == "20260818"
    assert meta["feature_version"] == SHADOW["feature_version"] == "v3"
    assert SHADOW["feature_set"] == "v3-honest" == config.SERVED_FEATURE_SET
    assert feature_cols == tuple(config.FEATURE_COLS)
    assert meta["training_window"]["cutoff"] == "2026-04-13"


def test_champions_have_not_drifted() -> None:
    assert config.CHAMPIONS == config.PROSPECTIVE_CHAMPIONS, (
        "a CHAMPIONS entry changed after the 2026-27 freeze. this is one of the "
        "named re-freeze triggers in MODEL.md 13.2"
    )


def test_rate_targets_have_not_drifted() -> None:
    assert config.RATE_TARGETS == config.PROSPECTIVE_RATE_TARGETS
    assert config.PRODUCTION_TARGETS == config.PROSPECTIVE_RATE_TARGETS


@pytest.mark.parametrize("stat", sorted(config.PROSPECTIVE_RATE_HALFLIVES))
def test_rate_halflife_has_not_drifted(stat: str) -> None:
    assert config.rate_halflife(stat) == config.PROSPECTIVE_RATE_HALFLIVES[stat]
    assert config.RATE_HALFLIVES[stat] == config.PROSPECTIVE_RATE_HALFLIVES[stat]


@pytest.mark.parametrize("stat", sorted(config.PROSPECTIVE_RATE_ESTIMATORS))
def test_rate_estimator_has_not_drifted(stat: str) -> None:
    assert config.rate_estimator(stat) == config.PROSPECTIVE_RATE_ESTIMATORS[stat]


def test_steals_still_ships_the_expanding_baseline() -> None:
    """the one stat in the vocabulary that does not ship an EWMA."""
    assert config.rate_estimator("STL") == "expanding"
    assert all(
        config.rate_estimator(s) == "ewma"
        for s in config.PROSPECTIVE_RATE_TARGETS
        if s != "STL"
    )


def test_feature_contract_has_not_drifted() -> None:
    served = config.FEATURE_SETS[config.PROSPECTIVE_FEATURE_SET]
    digest = hashlib.sha256("\n".join(served).encode()).hexdigest()
    assert config.PROSPECTIVE_FEATURE_SET == "v7-preseason-role"
    assert len(served) == config.PROSPECTIVE_N_FEATURES == 73
    assert digest == config.PROSPECTIVE_FEATURE_COLS_SHA256, (
        "the served feature set changed after the freeze. any change to the feature "
        "list - an addition, a removal, a reordering - invalidates the pinned "
        "artifact, which was fitted against this exact contract"
    )
    assert config.CANDIDATE_FEATURE_VERSION_V7 == config.PROSPECTIVE_FEATURE_VERSION


def test_the_v3_contract_the_shadow_reads_has_not_drifted() -> None:
    # act
    digest = hashlib.sha256("\n".join(config.FEATURE_COLS).encode()).hexdigest()

    # assert
    assert len(config.FEATURE_COLS) == config.PROSPECTIVE_V3_N_FEATURES == 51
    assert digest == config.PROSPECTIVE_V3_FEATURE_COLS_SHA256
    assert config.FEATURE_VERSION == "v3"
    assert config.SERVED_FEATURE_SET == "v3-honest"


def test_the_served_contract_is_v6_context_plus_the_six_preseason_columns() -> None:
    # act
    served = config.FEATURE_SETS[config.PROSPECTIVE_FEATURE_SET]

    # assert
    assert served == [*config.FEATURE_COLS_V6_CONTEXT, *config.PRESEASON_ROLE_FEATURE_COLS]
    assert served[:51] == config.FEATURE_COLS
    assert not set(config.TEAMMATE_ORACLE_COLS) & set(served)
    assert not set(config.TARGET_COLS) & set(served)


def test_the_v7_construction_constants_have_not_drifted() -> None:
    # arrange
    frozen_values = config.PROSPECTIVE_V7_CONSTANTS

    # act
    live = {
        "v6_start_window": config.V6_START_WINDOW,
        "v6_usual_starter_rate": config.V6_USUAL_STARTER_RATE,
        "v6_box_ewma_halflife": config.V6_BOX_EWMA_HALFLIFE,
        "preseason_role_games": config.PRESEASON_ROLE_GAMES,
        "preseason_role_fade_games": config.PRESEASON_ROLE_FADE_GAMES,
        "preseason_role_team_minutes": config.PRESEASON_ROLE_TEAM_MINUTES,
    }

    # assert
    assert live == frozen_values


def test_shadow_comparator_feature_sets_exist() -> None:
    # act
    shadows = config.PROSPECTIVE_SHADOW_FEATURE_SETS

    # assert
    assert shadows == ("v3",)
    for name in shadows:
        artifact = config.PROSPECTIVE_SHADOW_ARTIFACTS[name]
        assert artifact["feature_set"] in config.FEATURE_SETS
    assert config.PROSPECTIVE_SHADOW_ARTIFACTS["v3"]["model_version"] != (
        config.PROSPECTIVE_MODEL_VERSION
    )


def test_override_constants_have_not_drifted() -> None:
    assert overrides.DEFAULT_POLICY.as_dict() == config.PROSPECTIVE_OVERRIDE_CONSTANTS


def test_report_max_age_has_not_drifted() -> None:
    assert overrides.REPORT_MAX_AGE_HOURS == frozen.PROSPECTIVE_REPORT_MAX_AGE_HOURS
    assert frozen.PROSPECTIVE_2026_27["report_max_age_hours"] == 72.0


def test_passthrough_statuses_have_not_drifted() -> None:
    assert overrides.PASSTHROUGH_STATUSES == frozen.PROSPECTIVE_PASSTHROUGH_STATUSES
    assert "cleared" in frozen.PROSPECTIVE_PASSTHROUGH_STATUSES
    assert not (
        frozen.PROSPECTIVE_PASSTHROUGH_STATUSES & overrides.UNAVAILABLE_STATUSES
    )


def test_probable_rule_is_still_a_floor_at_the_frozen_constants() -> None:
    """`w*p + s` is a floor on [0, 1] iff `s >= 1 - w`, with equality at the defaults."""
    w = config.PROSPECTIVE_OVERRIDE_CONSTANTS["probable_model_weight"]
    s = config.PROSPECTIVE_OVERRIDE_CONSTANTS["probable_shift"]
    # `1.0 - 0.85` is 0.15000000000000002 in binary floating point
    assert s - (1.0 - w) >= -1e-12

    for p in (0.0, 0.01, 0.4, 0.85, 0.95, 0.999, 1.0):
        assert overrides.DEFAULT_POLICY.probability("probable", p) >= p - 1e-12


def test_horizon_definitions_have_not_drifted() -> None:
    assert config.HORIZON_WINDOWS == config.PROSPECTIVE_HORIZON_WINDOWS
    assert config.PROSPECTIVE_SERVING_HORIZON in config.HORIZON_WINDOWS
    assert config.DEFAULT_HORIZON == config.PROSPECTIVE_SERVING_HORIZON
    lo, hi = config.HORIZON_WINDOWS[config.PROSPECTIVE_SERVING_HORIZON]
    assert config.horizon_for_offset((lo + hi) / 2) == config.PROSPECTIVE_SERVING_HORIZON


def test_coherence_constraints_have_not_drifted() -> None:
    assert config.COHERENCE_CONSTRAINTS == config.PROSPECTIVE_COHERENCE_CONSTRAINTS


def test_cohort_definitions_have_not_drifted() -> None:
    assert config.TIER_EDGES == (10.0, 20.0, 30.0)
    assert config.TIER_LABELS == (
        "fringe (<10)", "bench (10-20)", "starter (20-30)", "star (>=30)",
    )
    assert config.TIER_BASIS == "roll10_MIN"
    assert config.EVENT_COHORTS == (
        ("event: vacated_minutes >= 30", "vacated_minutes", ">=", 30.0),
        ("event: star_out = 1", "star_out", ">=", 1.0),
        ("control: vacated_minutes < 5", "vacated_minutes", "<", 5.0),
    )


def test_protocol_version_string() -> None:
    assert config.PROSPECTIVE_PROTOCOL_VERSION == "prospective_2026_27_v4"
    assert config.PROSPECTIVE_RUN_NOTE_LABEL == "prospective_2026_27_v4"
    assert config.PROSPECTIVE_2026_27["protocol_version"] == (
        config.PROSPECTIVE_PROTOCOL_VERSION
    )


def test_v4_records_what_it_was_refrozen_from() -> None:
    # arrange
    bundle = config.PROSPECTIVE_2026_27

    # act
    version = config.PROSPECTIVE_PROTOCOL_VERSION

    # assert
    assert version.endswith("_v4")
    assert bundle["refrozen_from"] == "prospective_2026_27_v3"
    assert bundle["frozen_at"] == "2026-10-08"
    assert bundle["feature_set"] == "v7-preseason-role"
    # the re-freeze must land before opening night, or it is a mid-season change
    assert bundle["frozen_at"] < "2026-10-20"


def test_the_scorer_pools_every_refreeze_by_the_bare_prefix() -> None:
    # arrange
    from fnba_ml import scoring

    # act
    prefix = scoring.PROSPECTIVE_PREFIX

    # assert
    assert prefix == "prospective_2026_27"
    for version in ("v1", "v2", "v3", "v4"):
        assert prefix in f"prospective_2026_27_{version}; channel=production"


def test_expire_unavailable_switch_has_not_drifted() -> None:
    # arrange
    frozen_value = frozen.PROSPECTIVE_EXPIRE_UNAVAILABLE_STATUSES

    # act
    served = [
        inspect.signature(fn).parameters["expire_unavailable"].default
        for fn in (
            overrides.resolve_statuses,
            overrides.resolve_overrides,
            overrides.apply_status_overrides,
        )
    ]

    # assert
    assert frozen_value is False
    assert config.EXPIRE_UNAVAILABLE_STATUSES is frozen_value
    assert served == [frozen_value] * 3


def test_game_scoped_resolution_switch_has_not_drifted() -> None:
    # arrange
    frozen_value = frozen.PROSPECTIVE_GAME_SCOPED_STATUS_RESOLUTION

    # act
    served = [
        inspect.signature(fn).parameters["game_scoped"].default
        for fn in (
            overrides.resolve_statuses,
            overrides.resolve_overrides,
            overrides.apply_status_overrides,
        )
    ]

    # assert
    assert frozen_value is True
    assert config.GAME_SCOPED_STATUS_RESOLUTION is frozen_value
    assert served == [frozen_value] * 3


def test_official_precedence_window_has_not_drifted() -> None:
    # arrange
    frozen_value = frozen.PROSPECTIVE_OFFICIAL_PRECEDENCE_HOURS

    # act
    served = inspect.signature(overrides.resolve_statuses).parameters[
        "precedence_hours"
    ].default

    # assert
    assert frozen_value == 6.0
    assert overrides.OFFICIAL_PRECEDENCE_HOURS == frozen_value
    assert served == frozen_value


def test_postseason_history_switch_stays_off() -> None:
    # arrange
    frozen_value = frozen.PROSPECTIVE_RATE_HISTORY_INCLUDES_POSTSEASON

    # act
    live = config.RATE_HISTORY_INCLUDES_POSTSEASON

    # assert
    assert frozen_value is False
    assert live is frozen_value


def test_look_dates_are_what_section_13_says() -> None:
    assert config.PROSPECTIVE_LOOKS == (
        ("dec1", "2026-12-01", 7_500),
        ("all_star", "2027-02-15", 20_000),
        ("season_end", "2027-04-20", 32_000),
    )
    assert config.PROSPECTIVE_LOOK_DATES == (
        "2026-12-01", "2027-02-15", "2027-04-20",
    )
    assert len(config.PROSPECTIVE_LOOKS) == 3, "three looks. not two, and not four"


def test_look_dates_are_ordered_and_inside_the_season() -> None:
    import pandas as pd

    dates = [pd.Timestamp(d) for d in config.PROSPECTIVE_LOOK_DATES]
    assert dates == sorted(dates)
    assert pd.Timestamp("2026-10-20") < dates[0]
    assert dates[-1] < pd.Timestamp("2027-07-01")


def test_look_row_minimums_increase() -> None:
    minimums = [n for _, _, n in config.PROSPECTIVE_LOOKS]
    assert minimums == sorted(minimums)
    assert all(n > 0 for n in minimums)


def test_cold_start_flag_and_window() -> None:
    assert config.PROSPECTIVE_COLD_START_FLAG == "cold_start"
    assert config.PROSPECTIVE_COLD_START_THROUGH == "2026-11-30"
    assert config.PROSPECTIVE_OCTOBER_REPLAY_WINDOW == ("2025-10-01", "2025-10-31")
    assert config.PROSPECTIVE_COLD_START_FLAG not in config.PRODUCTION_TARGETS


def test_october_gate_criteria_are_frozen_numbers() -> None:
    gate = config.PROSPECTIVE_OCTOBER_GATE
    assert gate == {
        "max_brier_ratio": 1.42,
        "max_minutes_mae_ratio": 1.15,
        "min_prediction_coverage": 0.99,
    }
    assert gate["max_brier_ratio"] > 1.0
    assert gate["max_minutes_mae_ratio"] > 1.0
    assert 0.0 < gate["min_prediction_coverage"] <= 1.0


LOOK_NAMES = ("dec1", "all_star", "season_end")


def test_every_falsification_endpoint_names_all_three_looks() -> None:
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        assert set(spec["thresholds"]) == set(LOOK_NAMES), name
        assert spec["direction"] in ("lower_is_better", "higher_is_better"), name


def test_no_falsification_threshold_is_left_unset() -> None:
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        for look, value in spec["thresholds"].items():
            assert value is None or isinstance(value, (int, float)), (
                f"{name}/{look} is neither a number nor an explicit report-only None"
            )


def test_at_least_one_binding_threshold_per_endpoint() -> None:
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        assert any(v is not None for v in spec["thresholds"].values()), (
            f"{name} is report-only at every look, so it is not a falsification "
            "criterion at all"
        )


def test_thresholds_are_monotonically_stricter_across_looks() -> None:
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        seq = [spec["thresholds"][k] for k in LOOK_NAMES]
        seq = [v for v in seq if v is not None]
        if spec["direction"] == "lower_is_better":
            assert seq == sorted(seq, reverse=True), name
        else:
            assert seq == sorted(seq), name


def test_binding_thresholds_are_no_stronger_than_the_retrospective_effect() -> None:
    """the bar may never demand MORE than the retrospective claim itself."""
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        retro = spec["retrospective"]
        if retro is None:
            continue
        supports_claim = (
            retro < 0 if spec["direction"] == "lower_is_better" else retro > 0
        )
        if not supports_claim:
            continue
        for look, value in spec["thresholds"].items():
            if value is None:
                continue
            if spec["direction"] == "lower_is_better":
                assert value >= retro, f"{name}/{look}"
            else:
                assert value <= retro, f"{name}/{look}"


def test_the_already_doubted_row_is_recorded_as_doubted() -> None:
    spec = config.PROSPECTIVE_FALSIFICATION["rare_event_h20_vs_expanding"]
    assert spec["retrospective"] < 0
    assert spec["thresholds"]["season_end"] == 0.00
    assert spec["thresholds"]["dec1"] is None
    assert spec["thresholds"]["all_star"] is None


def test_underpowered_looks_are_not_binding_downward() -> None:
    """at Dec 1 the teammate-context endpoints are wrong-sign tripwires only."""
    for name in (
        "availability_brier_v3_vs_v1",
        "minutes_mae_v3_vs_v1",
        "pts_uncond_mae_v3_vs_v1",
    ):
        spec = config.PROSPECTIVE_FALSIFICATION[name]
        assert spec["direction"] == "lower_is_better"
        assert spec["thresholds"]["dec1"] > 0.0, name


def test_block_standard_deviations_are_present_where_a_threshold_was_derived() -> None:
    for name, spec in config.PROSPECTIVE_FALSIFICATION.items():
        if spec["retrospective"] is None:
            assert spec["block_sd"] is None, name
        else:
            assert isinstance(spec["block_sd"], float) and spec["block_sd"] > 0, name


def test_bundle_is_json_serialisable() -> None:
    text = json.dumps(config.PROSPECTIVE_2026_27, sort_keys=True, default=list)
    assert json.loads(text)["protocol_version"] == "prospective_2026_27_v4"


def test_bundle_agrees_with_its_components() -> None:
    bundle = config.PROSPECTIVE_2026_27
    assert bundle["model_version"] == config.PROSPECTIVE_MODEL_VERSION
    assert bundle["feature_version"] == config.PROSPECTIVE_FEATURE_VERSION
    assert bundle["artifact_checksums"] == config.PROSPECTIVE_ARTIFACT_CHECKSUMS
    assert bundle["looks"] == config.PROSPECTIVE_LOOKS
    assert bundle["run_note_label"] == config.PROSPECTIVE_RUN_NOTE_LABEL
    assert bundle["cold_start_flag"] == config.PROSPECTIVE_COLD_START_FLAG
    assert bundle["champions"] == config.PROSPECTIVE_CHAMPIONS
    assert bundle["rate_halflives"] == config.PROSPECTIVE_RATE_HALFLIVES
    assert bundle["rate_estimators"] == config.PROSPECTIVE_RATE_ESTIMATORS
    assert bundle["falsification"] == config.PROSPECTIVE_FALSIFICATION
    assert bundle["artifact_dir"] == f"models/{config.PROSPECTIVE_MODEL_VERSION}"
    assert bundle["feature_set"] == config.PROSPECTIVE_FEATURE_SET
    assert bundle["shadow_artifacts"] == config.PROSPECTIVE_SHADOW_ARTIFACTS
    assert bundle["v3_feature_cols_sha256"] == config.PROSPECTIVE_V3_FEATURE_COLS_SHA256
    assert bundle["v7_constants"] == config.PROSPECTIVE_V7_CONSTANTS
    assert bundle["report_max_age_hours"] == frozen.PROSPECTIVE_REPORT_MAX_AGE_HOURS
    assert bundle["passthrough_statuses"] == frozen.PROSPECTIVE_PASSTHROUGH_STATUSES
    assert bundle["expire_unavailable_statuses"] is (
        frozen.PROSPECTIVE_EXPIRE_UNAVAILABLE_STATUSES
    )
    assert bundle["game_scoped_status_resolution"] is (
        frozen.PROSPECTIVE_GAME_SCOPED_STATUS_RESOLUTION
    )
    assert bundle["official_precedence_hours"] == (
        frozen.PROSPECTIVE_OFFICIAL_PRECEDENCE_HOURS
    )
    assert bundle["rate_history_includes_postseason"] is (
        frozen.PROSPECTIVE_RATE_HISTORY_INCLUDES_POSTSEASON
    )


def test_model_md_section_13_exists_and_declares_the_same_protocol() -> None:
    text = (ML_ROOT / "MODEL.md").read_text(encoding="utf-8")
    assert "## 13. `prospective_2026_27_v1` (FROZEN)" in text
    assert "## 17. Phase 0 correctness and the `prospective_2026_27_v2` re-freeze" in text
    assert "## 21. The `prospective_2026_27_v3` re-freeze (2026-10-02)" in text
    assert "## 24. The `prospective_2026_27_v4` re-freeze (2026-10-08)" in text
    assert config.PROSPECTIVE_PROTOCOL_VERSION in text
    assert config.PROSPECTIVE_MODEL_VERSION in text
    assert config.PROSPECTIVE_COLD_START_FLAG in text
    for date in config.PROSPECTIVE_LOOK_DATES:
        assert date in text, f"look date {date} is in config and not in MODEL.md 13"
    for checksum in config.PROSPECTIVE_V3_ARTIFACT_CHECKSUMS.values():
        assert checksum[:16] in text, "section 13.1 must show each v3 checksum"
    for checksum in config.PROSPECTIVE_ARTIFACT_CHECKSUMS.values():
        assert checksum[:16] in text, "section 24 must show each pinned checksum"
