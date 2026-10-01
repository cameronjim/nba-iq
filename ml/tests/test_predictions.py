from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import predict
from fnba_ml.intervals import (
    QUANTILE_LEVELS,
    QuantileOffsets,
    apply_quantiles,
    attach_quantiles,
    fit_residual_quantiles,
    quantile_columns,
)
from fnba_ml.config import HORIZONS, MODELS_DIR, PROSPECTIVE_MODEL_VERSION
from fnba_ml.models import P_PLAY, P_PLAY_CUTOFF
from fnba_ml.overrides import (
    OVERRIDE_REASON,
    OVERRIDE_REASON_CODES,
    P_PLAY_MODEL,
    STATUS_CAPTURED_AT,
)
from fnba_ml.store import (
    INSERT_RUN_SQL,
    PROB_ACTIVE,
    RUN_CHANNELS,
    PROB_ACTIVE_MODEL,
    STATUS_CAPTURED_AT_STAT,
    STATUS_OVERRIDE,
    UNCOND_SUFFIX,
    build_prediction_rows,
    build_run_record,
    write_predictions,
)

TARGETS = ("MIN", "PTS", "AST")


def prediction_frame(**overrides) -> pd.DataFrame:
    """two scheduled player-games, scored the way predict.py scores them."""
    frame = pd.DataFrame({
        "PLAYER_ID": ["2544", "201939"],
        "GAME_ID": ["0022500123", "0022500123"],
        "GAME_DATE": pd.to_datetime(["2026-03-01", "2026-03-01"]),
        P_PLAY: [0.82, 0.35],
        P_PLAY_CUTOFF: pd.to_datetime(["2026-02-28", "2026-02-28"]),
        "E_MIN_COND": [34.0, 22.0],
        "E_MIN": [27.88, 7.70],
        "E_PTS_COND": [25.0, 12.0],
        "E_PTS": [20.5, 4.2],
        "E_AST_COND": [8.0, 3.0],
        "E_AST": [6.56, 1.05],
        "Q10_MIN": [26.0, 14.0],
        "Q50_MIN": [34.5, 22.5],
        "Q90_MIN": [41.0, 30.0],
        "Q10_PTS": [14.0, 4.0],
        "Q50_PTS": [24.5, 11.5],
        "Q90_PTS": [37.0, 21.0],
    })
    for key, value in overrides.items():
        frame[key] = value
    return frame


def rows_for(rows: list[dict], stat: str, quantile: float | None = None) -> list[dict]:
    return [
        r for r in rows
        if r["stat"] == stat
        and (r["quantile"] is None if quantile is None else r["quantile"] == quantile)
    ]


def test_every_player_game_gets_the_full_row_set():
    frame = prediction_frame()

    rows = build_prediction_rows(frame, TARGETS)

    assert len(rows) == 2 * 13
    assert {r["nba_player_id"] for r in rows} == {"2544", "201939"}
    assert {r["nba_game_id"] for r in rows} == {"0022500123"}
    assert {r["game_date"] for r in rows} == {pd.Timestamp("2026-03-01").date()}


def test_rows_carry_exactly_the_schema_columns():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    assert all(
        set(r) == {"nba_player_id", "nba_game_id", "game_date", "stat",
                   "quantile", "value", "conditional"}
        for r in rows
    )


def test_the_uniqueness_key_has_no_duplicates():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    keys = [(r["nba_player_id"], r["nba_game_id"], r["stat"], r["quantile"]) for r in rows]
    assert len(set(keys)) == len(keys)


def test_an_empty_frame_produces_no_rows():
    assert build_prediction_rows(prediction_frame().iloc[0:0], TARGETS) == []


def test_a_frame_without_probabilities_is_refused():
    frame = prediction_frame().drop(columns=[P_PLAY])

    with pytest.raises(ValueError, match="P_PLAY"):
        build_prediction_rows(frame, TARGETS)


def test_conditional_and_unconditional_are_separate_stats_flagged_correctly():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    conditional = rows_for(rows, "pts")
    unconditional = rows_for(rows, f"pts{UNCOND_SUFFIX}")
    assert [r["conditional"] for r in conditional] == [True, True]
    assert [r["conditional"] for r in unconditional] == [False, False]
    assert [r["value"] for r in conditional] == [25.0, 12.0]
    assert [r["value"] for r in unconditional] == [20.5, 4.2]


def test_the_unconditional_estimate_is_never_the_larger_one():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    for stat in ("minutes", "pts", "ast"):
        conditional = [r["value"] for r in rows_for(rows, stat)]
        unconditional = [r["value"] for r in rows_for(rows, f"{stat}{UNCOND_SUFFIX}")]
        assert all(u <= c + 1e-9 for c, u in zip(conditional, unconditional))


def test_quantile_rows_are_conditional_and_expected_values_carry_no_quantile():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    quantile_rows = [r for r in rows if r["quantile"] is not None]
    assert quantile_rows, "the fixture frame carries quantile columns"
    assert all(r["conditional"] for r in quantile_rows)
    assert {r["quantile"] for r in quantile_rows} == {0.1, 0.5, 0.9}
    assert {r["stat"] for r in quantile_rows} == {"minutes", "pts"}
    # an expected value is a mean, not a median: it never claims to be P50.
    assert all(r["quantile"] is None for r in rows_for(rows, "ast"))


def test_prob_active_is_unconditional_and_carries_no_quantile():
    rows = rows_for(build_prediction_rows(prediction_frame(), TARGETS), PROB_ACTIVE)

    assert len(rows) == 2
    assert all(r["conditional"] is False for r in rows)
    assert all(r["quantile"] is None for r in rows)
    assert [r["value"] for r in rows] == [0.82, 0.35]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([1.0000000002, -1e-9], [1.0, 0.0]),
        ([2.0, -3.0], [1.0, 0.0]),
        ([1.0, 0.0], [1.0, 0.0]),
    ],
)
def test_prob_active_is_clamped_into_the_unit_interval(raw, expected):
    frame = prediction_frame(**{P_PLAY: raw})

    rows = rows_for(build_prediction_rows(frame, TARGETS), PROB_ACTIVE)

    assert [r["value"] for r in rows] == expected
    assert all(0.0 <= r["value"] <= 1.0 for r in rows)


def test_a_null_probability_writes_no_probability_row():
    frame = prediction_frame(**{P_PLAY: [0.5, np.nan]})

    rows = rows_for(build_prediction_rows(frame, TARGETS), PROB_ACTIVE)

    assert [r["nba_player_id"] for r in rows] == ["2544"]


def test_a_null_estimate_writes_no_row_for_that_stat():
    frame = prediction_frame(E_PTS_COND=[np.nan, 12.0])

    rows = rows_for(build_prediction_rows(frame, TARGETS), "pts")

    assert [r["nba_player_id"] for r in rows] == ["201939"]
    assert len(rows_for(build_prediction_rows(frame, TARGETS), "minutes")) == 2


def test_quantiles_do_not_cross_in_the_emitted_rows():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    for player in ("2544", "201939"):
        for stat in ("minutes", "pts"):
            by_level = {
                r["quantile"]: r["value"]
                for r in rows
                if r["nba_player_id"] == player and r["stat"] == stat and r["quantile"] is not None
            }
            assert by_level[0.1] <= by_level[0.5] <= by_level[0.9]


def test_a_crossed_input_frame_is_sorted_rather_than_written_crossed():
    frame = prediction_frame(Q10_PTS=[40.0, 30.0], Q50_PTS=[24.5, 11.5], Q90_PTS=[9.0, 2.0])

    rows = [r for r in build_prediction_rows(frame, TARGETS)
            if r["stat"] == "pts" and r["quantile"] is not None]

    first = {r["quantile"]: r["value"] for r in rows if r["nba_player_id"] == "2544"}
    assert first == {0.1: 9.0, 0.5: 24.5, 0.9: 40.0}
    assert first[0.1] <= first[0.5] <= first[0.9]


def test_a_frame_without_quantile_columns_still_writes_expected_values():
    frame = prediction_frame().drop(
        columns=["Q10_MIN", "Q50_MIN", "Q90_MIN", "Q10_PTS", "Q50_PTS", "Q90_PTS"]
    )

    rows = build_prediction_rows(frame, TARGETS)

    assert all(r["quantile"] is None for r in rows)
    assert len(rows) == 2 * 7


def overridden_frame() -> pd.DataFrame:
    """the same two player-games, after the override layer ran on the first one."""
    frame = prediction_frame(**{P_PLAY: [0.02, 0.35]})
    frame[P_PLAY_MODEL] = [0.93, 0.35]
    frame[OVERRIDE_REASON] = ["status_out", None]
    frame[STATUS_CAPTURED_AT] = pd.to_datetime(
        ["2026-03-01T12:34:00", None]
    )
    return frame


def test_both_probabilities_are_stored_so_the_layer_stays_measurable():
    rows = build_prediction_rows(overridden_frame(), TARGETS)

    served = rows_for(rows, PROB_ACTIVE)
    model = rows_for(rows, PROB_ACTIVE_MODEL)
    assert [r["value"] for r in served] == [0.02, 0.35]
    assert [r["value"] for r in model] == [0.93, 0.35]
    assert all(r["conditional"] is False for r in served + model)
    assert all(r["quantile"] is None for r in served + model)


def test_the_override_reason_is_stored_as_a_code_and_the_timestamp_as_epoch_seconds():
    rows = build_prediction_rows(overridden_frame(), TARGETS)

    reason = rows_for(rows, STATUS_OVERRIDE)
    assert [r["value"] for r in reason] == [float(OVERRIDE_REASON_CODES["status_out"])]
    assert [r["nba_player_id"] for r in reason] == ["2544"]

    captured = rows_for(rows, STATUS_CAPTURED_AT_STAT)
    assert len(captured) == 1
    assert pd.Timestamp(captured[0]["value"], unit="s") == pd.Timestamp("2026-03-01T12:34:00")


def test_rows_without_an_override_carry_no_override_rows():
    rows = build_prediction_rows(overridden_frame(), TARGETS)

    extra = [r for r in rows if r["stat"] in (STATUS_OVERRIDE, STATUS_CAPTURED_AT_STAT)]
    assert {r["nba_player_id"] for r in extra} == {"2544"}
    assert len(rows) == 2 * 13 + 2 + 1 + 1


def test_a_frame_that_never_saw_the_override_layer_still_builds():
    rows = build_prediction_rows(prediction_frame(), TARGETS)

    assert rows_for(rows, PROB_ACTIVE_MODEL) == []
    assert rows_for(rows, STATUS_OVERRIDE) == []
    assert len(rows) == 2 * 13


def test_an_unknown_override_reason_is_not_written_as_a_guess():
    frame = overridden_frame()
    frame[OVERRIDE_REASON] = ["status_invented", None]

    rows = build_prediction_rows(frame, TARGETS)

    assert rows_for(rows, STATUS_OVERRIDE) == []
    assert len(rows_for(rows, STATUS_CAPTURED_AT_STAT)) == 1


def test_override_reason_codes_are_unique():
    codes = list(OVERRIDE_REASON_CODES.values())
    assert len(set(codes)) == len(codes)


def test_residual_quantiles_recover_a_known_distribution():
    rng = np.random.default_rng(11)
    y_pred = np.full(50_000, 20.0)
    y_true = y_pred + rng.normal(0.0, 4.0, y_pred.size)

    offsets = fit_residual_quantiles(y_true, y_pred, "PTS")

    assert offsets.levels == QUANTILE_LEVELS
    assert offsets.offsets[0] == pytest.approx(-5.126, abs=0.1)
    assert offsets.offsets[1] == pytest.approx(0.0, abs=0.05)
    assert offsets.offsets[2] == pytest.approx(5.126, abs=0.1)
    assert offsets.n == 50_000


def test_residual_quantile_offsets_come_out_sorted():
    offsets = fit_residual_quantiles(
        [10.0, 12.0, 30.0, 8.0], [10.0] * 4, "PTS", levels=(0.9, 0.1, 0.5)
    )

    assert list(offsets.offsets) == sorted(offsets.offsets)


def test_residual_quantiles_ignore_rows_with_no_outcome():
    offsets = fit_residual_quantiles([10.0, np.nan, 30.0], [10.0, 10.0, 10.0], "PTS")

    assert offsets.n == 2


def test_residual_quantiles_refuse_an_empty_window():
    with pytest.raises(ValueError, match="no finite residuals"):
        fit_residual_quantiles([np.nan, np.nan], [1.0, 2.0], "MIN")


def test_applied_quantiles_are_floored_at_zero_and_stay_ordered():
    offsets = QuantileOffsets("MIN", QUANTILE_LEVELS, (-8.0, 0.5, 9.0), n=100)

    result = apply_quantiles(np.array([2.0, 30.0]), offsets)

    assert list(result[0.10]) == [0.0, 22.0]
    assert list(result[0.50]) == [2.5, 30.5]
    assert list(result[0.90]) == [11.0, 39.0]
    assert all(result[0.10][i] <= result[0.50][i] <= result[0.90][i] for i in range(2))


def test_hand_built_crossed_offsets_are_sorted_on_the_way_out():
    offsets = QuantileOffsets("PTS", QUANTILE_LEVELS, (5.0, 0.0, -5.0), n=10)

    result = apply_quantiles(np.array([20.0]), offsets)

    assert (result[0.10][0], result[0.50][0], result[0.90][0]) == (15.0, 20.0, 25.0)


def test_offsets_survive_a_json_round_trip():
    offsets = fit_residual_quantiles([1.0, 5.0, 9.0], [4.0] * 3, "MIN", window=("a", "b"))

    restored = QuantileOffsets.from_dict(offsets.as_dict())

    assert restored.target == "MIN"
    assert restored.levels == offsets.levels
    assert restored.offsets == pytest.approx(offsets.offsets)
    assert restored.window == ("a", "b")


def test_mismatched_levels_and_offsets_are_rejected():
    with pytest.raises(ValueError, match="levels against"):
        QuantileOffsets("PTS", (0.1, 0.5, 0.9), (1.0, 2.0), n=5)


def test_attach_quantiles_writes_the_expected_columns():
    frame = pd.DataFrame({"PLAYER_ID": ["1", "2"]})
    offsets = QuantileOffsets("PTS", QUANTILE_LEVELS, (-3.0, 0.0, 4.0), n=10)

    out = attach_quantiles(frame, np.array([10.0, 20.0]), offsets)

    assert set(quantile_columns("PTS").values()) == {"Q10_PTS", "Q50_PTS", "Q90_PTS"}
    assert list(out["Q10_PTS"]) == [7.0, 17.0]
    assert list(out["Q90_PTS"]) == [14.0, 24.0]
    assert "PLAYER_ID" in out.columns


def test_the_run_record_separates_when_it_ran_from_what_it_knew():
    metadata = {
        "model_version": "2026-08-16",
        "feature_version": "v1",
        "git_commit": "abc123",
        "artifact_checksum": "deadbeef",
        "training_window": {"end": "2025-02-27", "cutoff": "2025-02-28"},
    }
    predicted_at = pd.Timestamp("2026-08-16T12:00:00Z").to_pydatetime()
    cutoff = pd.Timestamp("2025-02-28T00:00:00Z").to_pydatetime()

    record = build_run_record(metadata, predicted_at, cutoff, code_sha="feed01")

    assert record["predicted_at"] == predicted_at
    assert record["forecast_cutoff_at"] == cutoff
    assert record["trained_through"] == "2025-02-27"
    assert record["feature_version"] == "v1"
    assert record["artifact_checksum"] == "deadbeef"
    assert record["code_sha"] == "feed01"
    assert record["status"] == "complete"


def test_the_run_record_falls_back_to_the_training_commit():
    record = build_run_record(
        {"model_version": "v", "git_commit": "abc123"},
        pd.Timestamp("2026-08-16T12:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-08-16T00:00:00Z").to_pydatetime(),
    )

    assert record["code_sha"] == "abc123"
    assert record["feature_version"] == "unknown"
    assert record["trained_through"] is None


def test_the_run_record_stamps_the_forecast_horizon_into_notes():
    record = build_run_record(
        {"model_version": "20260817b"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
        notes="slate of 6 games",
        horizon="gameday",
    )

    assert record["notes"] == "horizon=gameday (T-6h); slate of 6 games"


def test_the_horizon_label_is_recorded_even_with_no_other_notes():
    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
        horizon="lock",
    )

    assert record["notes"] == "horizon=lock (T-60m)"


def test_a_run_without_a_horizon_says_nothing_rather_than_guessing():
    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
        notes="backfill",
    )

    assert record["notes"] == "backfill"


def test_an_unknown_horizon_is_refused():
    with pytest.raises(ValueError, match="unknown forecast horizon"):
        build_run_record(
            {"model_version": "v"},
            pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
            pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
            horizon="whenever",
        )


def test_the_three_horizons_are_named_and_ordered_toward_tipoff():
    assert list(HORIZONS) == ["early", "gameday", "lock"]
    assert HORIZONS == {"early": "T-24h", "gameday": "T-6h", "lock": "T-60m"}


def test_writing_an_empty_run_is_refused_before_it_can_connect():
    with pytest.raises(ValueError, match="no prediction rows"):
        write_predictions([], {"model_version": "v"})


def test_the_run_record_defaults_to_the_production_channel():
    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
    )

    assert record["channel"] == "production"
    assert record["information_as_of"] is None
    assert record["history_through"] is None


def test_the_run_record_carries_channel_information_and_history():
    information = pd.Timestamp("2026-03-01T17:45:00Z").to_pydatetime()
    through = pd.Timestamp("2026-02-28").date()

    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
        channel="shadow",
        information_as_of=information,
        history_through=through,
    )

    assert record["channel"] == "shadow"
    assert record["information_as_of"] == information
    assert record["history_through"] == through


def test_an_unknown_channel_is_refused():
    with pytest.raises(ValueError, match="unknown prediction run channel"):
        build_run_record(
            {"model_version": "v"},
            pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
            pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
            channel="challenger",
        )


def test_the_channels_are_production_and_shadow():
    assert RUN_CHANNELS == ("production", "shadow")


def test_the_cutoff_is_the_later_report_boundary_when_reports_were_read_after_midnight():
    day_start = pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime()
    information = pd.Timestamp("2026-03-01T15:30:00Z").to_pydatetime()

    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T16:00:00Z").to_pydatetime(),
        day_start,
        information_as_of=information,
    )

    assert record["forecast_cutoff_at"] == information


def test_the_cutoff_stays_at_the_day_start_when_the_reports_are_older():
    day_start = pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime()
    information = pd.Timestamp("2026-02-28T20:00:00Z").to_pydatetime()

    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T16:00:00Z").to_pydatetime(),
        day_start,
        information_as_of=information,
    )

    assert record["forecast_cutoff_at"] == day_start
    assert record["information_as_of"] == information


def test_a_naive_cutoff_compares_as_utc_against_an_aware_report_boundary():
    information = pd.Timestamp("2026-03-01T15:30:00Z").to_pydatetime()

    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T16:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01").to_pydatetime(),
        information_as_of=information,
    )

    assert record["forecast_cutoff_at"] == information


def test_the_insert_names_every_run_record_column():
    record = build_run_record(
        {"model_version": "v"},
        pd.Timestamp("2026-03-01T18:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-03-01T00:00:00Z").to_pydatetime(),
    )

    for column in ("channel", "information_as_of", "history_through"):
        assert column in INSERT_RUN_SQL
    for key in record:
        assert f"%({key})s" in INSERT_RUN_SQL


def test_history_through_is_the_last_played_game_date():
    frame = pd.DataFrame({
        "GAME_DATE": pd.to_datetime(["2026-02-26", "2026-02-27", "2026-03-01"]),
        "PLAYED": [1, 1, np.nan],
    })

    assert predict.history_through(frame) == pd.Timestamp("2026-02-27").date()


def test_history_through_falls_back_to_the_universe_source():
    frame = pd.DataFrame({
        "GAME_DATE": pd.to_datetime(["2026-02-27", "2026-03-01"]),
        "UNIVERSE_SOURCE": ["player_game_status", "prospective"],
    })

    assert predict.history_through(frame) == pd.Timestamp("2026-02-27").date()


def test_history_through_is_unknown_for_a_frame_with_no_played_rows():
    frame = pd.DataFrame({
        "GAME_DATE": pd.to_datetime(["2026-03-01"]),
        "UNIVERSE_SOURCE": ["prospective"],
    })

    assert predict.history_through(frame) is None


def test_the_forecast_cutoff_is_the_report_boundary_not_midnight():
    cutoff = predict.forecast_cutoff(
        pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-01T15:30:00Z")
    )

    assert cutoff == pd.Timestamp("2026-03-01T15:30:00Z")


def test_the_forecast_cutoff_never_precedes_the_first_scored_day():
    cutoff = predict.forecast_cutoff(
        pd.Timestamp("2026-03-01"), pd.Timestamp("2026-02-28T20:00:00Z")
    )

    assert cutoff == pd.Timestamp("2026-03-01T00:00:00Z")


def test_predict_defaults_to_the_production_channel_and_refuses_others():
    args = predict.parse_args(["--version", "v"])
    assert args.channel == "production"
    with pytest.raises(SystemExit):
        predict.parse_args(["--version", "v", "--channel", "challenger"])


class TestFeatureSetContext:
    def test_prepare_context_returns_no_audit_for_v1(self, caplog) -> None:
        # arrange
        frame = pd.DataFrame({"PLAYER_ID": ["1"], "GAME_ID": ["g"]})
        caplog.set_level("INFO", logger="predict")

        # act
        rebuilt, audit = predict.prepare_context(
            frame, None, {"feature_set": "v1"}, None, pd.Timestamp("2026-10-20")
        )

        # assert
        assert audit is None
        assert rebuilt is frame
        assert "feature_set v1 carries no teammate context" in caplog.text

    def test_an_artifact_without_the_key_is_read_as_the_served_set(self) -> None:
        # act + assert
        assert predict.uses_teammate_context({}) is True
        assert predict.uses_teammate_context({"feature_set": "v3-honest"}) is True
        assert predict.uses_teammate_context({"feature_set": "v1"}) is False

    def test_the_pinned_artifact_still_loads_its_base_model(self) -> None:
        # act
        _, _, base, metadata = predict.load_version(
            PROSPECTIVE_MODEL_VERSION, MODELS_DIR
        )

        # assert
        assert base is not None
        assert predict.uses_teammate_context(metadata) is True
