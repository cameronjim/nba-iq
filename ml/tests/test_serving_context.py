from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import (
    HORIZON_RUN_METADATA,
    HORIZON_WINDOWS,
    P_CONTEXT,
    P_CONTEXT_CUTOFF,
    horizon_for_offset,
)
from fnba_ml.models import LeakageError, validate_out_of_fold
from predict import horizon_metadata, rebuild_context


def test_horizon_windows_partition_the_offsets_and_assign_by_measurement():
    assert horizon_for_offset(1.0) == "lock"
    assert horizon_for_offset(2.0) == "lock"
    assert horizon_for_offset(2.5) == "gameday"
    assert horizon_for_offset(12.0) == "gameday"
    assert horizon_for_offset(24.0) == "early"
    assert horizon_for_offset(48.0) == "early"
    assert horizon_for_offset(0.0) == ""
    assert horizon_for_offset(-3.0) == ""
    assert horizon_for_offset(500.0) == ""
    # and the windows abut exactly, or an offset could land in two of them
    bounds = sorted(HORIZON_WINDOWS.values())
    for (_, hi), (lo, _) in zip(bounds, bounds[1:]):
        assert hi == lo


def test_the_run_metadata_list_names_every_fact_the_comparison_needs():
    assert set(HORIZON_RUN_METADATA) == {
        "hours_to_tip_min", "hours_to_tip_median", "hours_to_tip_max",
        "latest_report_at", "report_age_hours", "report_count",
        "first_deadline_passed", "horizon_measured",
    }


def _slate(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "PLAYER_ID": [str(i) for i in range(len(dates))],
        "GAME_ID": [f"g{i}" for i in range(len(dates))],
        "GAME_DATE": pd.to_datetime(dates),
    })


def test_horizon_metadata_measures_the_offset_and_the_reports_staleness():
    features = _slate(["2026-03-02", "2026-03-03"])
    statuses = pd.DataFrame({
        "nba_player_id": ["0"],
        "status_normalized": ["out"],
        "captured_at": ["2026-03-01T18:00:00+00:00"],
    })

    facts = horizon_metadata(
        features, statuses, pd.Timestamp("2026-03-02T00:00:00"), "early"
    )

    assert facts["hours_to_tip_min"] == pytest.approx(0.0)
    assert facts["hours_to_tip_max"] == pytest.approx(24.0)
    assert facts["report_count"] == 1
    assert facts["report_age_hours"] == pytest.approx(6.0)
    assert facts["latest_report_at"].startswith("2026-03-01T18:00")
    assert facts["horizon_requested"] == "early (T-24h)"
    # no tipoff timestamps on the frame, so the offsets are approximated and say so.
    assert "approximated" in facts["tip_source"]


def test_horizon_metadata_records_zero_reports_as_a_fact_not_a_blank():
    facts = horizon_metadata(
        _slate(["2026-03-02"]), None, pd.Timestamp("2026-03-01T18:00:00"), "gameday"
    )

    assert facts["report_count"] == 0
    assert facts["latest_report_at"] is None
    assert facts["report_age_hours"] is None


def test_horizon_metadata_flags_a_run_before_the_initial_report_deadline():
    # a midnight-UTC nominal tipoff, so the 5pm deadline is 2026-03-01T17:00
    features = _slate(["2026-03-02"])

    before = horizon_metadata(features, None, pd.Timestamp("2026-03-01T15:00:00"), "early")
    after = horizon_metadata(features, None, pd.Timestamp("2026-03-01T19:00:00"), "early")

    assert before["first_deadline_passed"] is False
    assert after["first_deadline_passed"] is True


def test_horizon_metadata_uses_real_tipoff_timestamps_when_the_frame_has_them():
    features = _slate(["2026-03-02"])
    features["SCHEDULED_AT"] = pd.to_datetime(["2026-03-02T23:30:00+00:00"])

    facts = horizon_metadata(features, None, pd.Timestamp("2026-03-02T00:00:00"), "early")

    assert facts["tip_source"] == "nba_schedule.scheduled_at"
    assert facts["hours_to_tip_median"] == pytest.approx(23.5)
    assert facts["horizon_measured"] == "early"


class _FakeBaseModel:
    def __init__(self, probabilities: dict[str, float], cutoff: pd.Timestamp) -> None:
        self.probabilities = probabilities
        self.cutoff = cutoff

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return (
            frame["PLAYER_ID"].astype(str).map(self.probabilities).to_numpy(dtype=float)
        )


def _serving_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "PLAYER_ID": ["star", "backup", "other"],
        "GAME_ID": ["g1"] * 3,
        "TEAM_ID": ["t1"] * 3,
        "GAME_DATE": pd.to_datetime(["2026-03-02"] * 3),
        "POS_GROUP": ["G", "G", "F"],
        "tm_MIN": [34.0, 12.0, 20.0],
        "tm_FGA": [20.0, 5.0, 9.0],
        "tm_USG": [32.0, 14.0, 20.0],
        "magnitude_ess": [20.0, 20.0, 20.0],
        "n_appearances": [500, 200, 300],
        "avail_rate_10": [0.9, 0.9, 0.9],
    })


def test_the_report_reaches_the_teammate_sums_not_only_the_players_own_number():
    frame = _serving_frame()
    base = _FakeBaseModel(
        {"star": 0.93, "backup": 0.88, "other": 0.9}, pd.Timestamp("2026-03-01")
    )
    statuses = pd.DataFrame({
        "nba_player_id": ["star"],
        "status_normalized": ["out"],
        "captured_at": ["2026-03-01T18:00:00+00:00"],
    })
    as_of = pd.Timestamp("2026-03-02T00:00:00")

    without, _ = rebuild_context(frame, base, None, as_of)
    with_report, audit = rebuild_context(frame, base, statuses, as_of)

    # (1 - 0.02) - (1 - 0.93) = 0.91, times 34 minutes
    delta = (
        with_report.loc[1, "exp_vacated_minutes"]
        - without.loc[1, "exp_vacated_minutes"]
    )
    assert delta == pytest.approx((0.93 - 0.02) * 34.0)
    assert with_report.loc[1, "exp_depth_rank"] < without.loc[1, "exp_depth_rank"]
    assert with_report.loc[1, "p_star_out"] == pytest.approx(0.98)
    assert with_report.loc[0, "p_star_out"] == pytest.approx(0.0)
    assert audit.loc[0, "P_CONTEXT_BASE"] == pytest.approx(0.93)
    assert audit.loc[0, "P_CONTEXT"] == pytest.approx(0.02)
    assert audit["CONTEXT_OVERRIDDEN"].tolist() == [True, False, False]


def test_no_report_is_an_identity_on_the_context(monkeypatch):
    frame = _serving_frame()
    base = _FakeBaseModel(
        {"star": 0.93, "backup": 0.88, "other": 0.9}, pd.Timestamp("2026-03-01")
    )

    none_given, audit_none = rebuild_context(
        frame, base, None, pd.Timestamp("2026-03-02")
    )
    empty_given, audit_empty = rebuild_context(
        frame, base,
        pd.DataFrame(columns=["nba_player_id", "status_normalized", "captured_at"]),
        pd.Timestamp("2026-03-02"),
    )

    assert none_given["exp_vacated_minutes"].to_numpy() == pytest.approx(
        empty_given["exp_vacated_minutes"].to_numpy()
    )
    assert not audit_none["CONTEXT_OVERRIDDEN"].any()
    assert not audit_empty["CONTEXT_OVERRIDDEN"].any()


def test_a_report_captured_after_the_boundary_is_dropped_at_the_context_stage_too():
    frame = _serving_frame()
    base = _FakeBaseModel(
        {"star": 0.93, "backup": 0.88, "other": 0.9}, pd.Timestamp("2026-03-01")
    )
    statuses = pd.DataFrame({
        "nba_player_id": ["star"],
        "status_normalized": ["out"],
        "captured_at": ["2026-03-02T01:00:00+00:00"],
    })

    rebuilt, audit = rebuild_context(
        frame, base, statuses, pd.Timestamp("2026-03-02T00:00:00")
    )
    unrestricted, _ = rebuild_context(frame, base, None, pd.Timestamp("2026-03-02"))

    assert not audit["CONTEXT_OVERRIDDEN"].any()
    assert rebuilt["exp_vacated_minutes"].to_numpy() == pytest.approx(
        unrestricted["exp_vacated_minutes"].to_numpy()
    )


def test_the_rebuilt_context_carries_the_base_models_cutoff_for_the_guard():
    frame = _serving_frame()
    cutoff = pd.Timestamp("2026-03-01")
    base = _FakeBaseModel({"star": 0.9, "backup": 0.9, "other": 0.9}, cutoff)

    rebuilt, _ = rebuild_context(frame, base, None, pd.Timestamp("2026-03-02"))

    assert (rebuilt[P_CONTEXT] == 0.9).all()
    assert (rebuilt[P_CONTEXT_CUTOFF] == cutoff).all()
    validate_out_of_fold(rebuilt, P_CONTEXT, P_CONTEXT_CUTOFF, "p_context")


def test_a_base_model_trained_past_the_game_is_refused_at_the_context_stage():
    frame = _serving_frame()
    base = _FakeBaseModel(
        {"star": 0.9, "backup": 0.9, "other": 0.9}, pd.Timestamp("2026-04-01")
    )

    with pytest.raises(LeakageError, match="IN-FOLD"):
        rebuild_context(frame, base, None, pd.Timestamp("2026-03-02"))


def test_horizon_none_is_accepted_and_the_default_is_unchanged():
    import predict

    args = predict.parse_args(["--version", "v", "--horizon", predict.NO_HORIZON])
    assert args.horizon == predict.NO_HORIZON
    assert predict.parse_args(["--version", "v"]).horizon == "gameday"


def test_horizon_metadata_without_a_requested_horizon_still_measures():
    import predict

    facts = horizon_metadata(
        _slate(["2026-03-02", "2026-03-08"]), None,
        pd.Timestamp("2026-03-02T00:00:00"), None,
    )

    assert facts["horizon_requested"] == predict.NO_HORIZON
    assert facts["hours_to_tip_max"] is not None


def test_a_run_with_no_horizon_writes_no_horizon_token_into_notes():
    from fnba_ml.store import build_run_record

    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
        notes="NOT PROSPECTIVE (extended 7-day serving window)",
        horizon=None,
    )

    assert "horizon=" not in record["notes"]
