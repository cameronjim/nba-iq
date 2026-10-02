from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import daily_run
from fnba_ml import config, registry
from fnba_ml.prospective import SOURCE_PROSPECTIVE


class TestEasternToday:
    @pytest.mark.parametrize(
        ("utc", "expected"),
        [
            ("2026-08-19T03:30:00Z", date(2026, 8, 18)),
            ("2026-08-19T04:30:00Z", date(2026, 8, 19)),
            # EST (UTC-5). the offset changes and the rule must not.
            ("2026-12-15T04:30:00Z", date(2026, 12, 14)),
            ("2026-12-15T05:30:00Z", date(2026, 12, 15)),
            ("2026-10-20T16:00:00Z", date(2026, 10, 20)),
            ("2027-01-20T16:00:00Z", date(2027, 1, 20)),
            ("2026-10-20T21:00:00Z", date(2026, 10, 20)),
            ("2027-01-20T21:00:00Z", date(2027, 1, 20)),
        ],
    )
    def test_utc_instant_maps_to_the_eastern_date(self, utc: str, expected: date) -> None:
        assert daily_run.eastern_today(pd.Timestamp(utc).to_pydatetime()) == expected

    def test_a_naive_now_is_read_as_utc(self) -> None:
        naive = datetime(2026, 8, 19, 3, 30)
        aware = datetime(2026, 8, 19, 3, 30, tzinfo=timezone.utc)
        assert daily_run.eastern_today(naive) == daily_run.eastern_today(aware)

    def test_the_utc_date_and_the_eastern_date_genuinely_differ(self) -> None:
        instant = datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)
        assert instant.date() == date(2026, 10, 21)
        assert daily_run.eastern_today(instant) == date(2026, 10, 20)


class TestPredictionWindow:
    def test_default_is_today_and_tomorrow(self) -> None:
        now = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
        assert daily_run.prediction_window(2, now=now) == (
            date(2026, 10, 20), date(2026, 10, 21),
        )

    def test_one_day_window_is_today_only(self) -> None:
        now = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
        start, end = daily_run.prediction_window(1, now=now)
        assert start == end == date(2026, 10, 20)

    def test_window_extends_forward_never_backward(self) -> None:
        for days in (1, 2, 3, 7):
            start, end = daily_run.prediction_window(
                days, now=datetime(2026, 12, 1, 21, 0, tzinfo=timezone.utc)
            )
            assert start == date(2026, 12, 1)
            assert end >= start
            assert (end - start).days == days - 1

    @pytest.mark.parametrize("days", [0, -1, -7])
    def test_a_non_positive_window_is_refused(self, days: int) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            daily_run.prediction_window(days)

    def test_window_start_accepts_a_string_or_a_date(self) -> None:
        expected = (date(2026, 10, 20), date(2026, 10, 21))
        assert daily_run.prediction_window(2, "2026-10-20") == expected
        assert daily_run.prediction_window(2, date(2026, 10, 20)) == expected

    def test_window_start_ignores_today(self) -> None:
        start, _ = daily_run.prediction_window(
            2, "2026-10-20", now=datetime(2027, 3, 1, tzinfo=timezone.utc)
        )
        assert start == date(2026, 10, 20)

    def test_a_month_boundary_does_not_wrap(self) -> None:
        assert daily_run.prediction_window(3, "2026-10-30") == (
            date(2026, 10, 30), date(2026, 11, 1),
        )


class TestStalenessWarning:
    def test_no_lag_is_not_stale(self) -> None:
        assert daily_run.staleness_warning(
            date(2026, 12, 1), date(2026, 12, 1)
        ) is None

    @pytest.mark.parametrize("lag", [1, 2, 3])
    def test_a_lag_within_tolerance_is_not_stale(self, lag: int) -> None:
        logs = date(2026, 12, 1)
        schedule = date(2026, 12, 1) + pd.Timedelta(days=lag).to_pytimedelta()
        assert daily_run.staleness_warning(logs, schedule) is None

    @pytest.mark.parametrize("lag", [4, 10, 90])
    def test_a_lag_past_tolerance_returns_a_message(self, lag: int) -> None:
        logs = date(2026, 12, 1)
        schedule = logs + pd.Timedelta(days=lag).to_pytimedelta()
        message = daily_run.staleness_warning(logs, schedule)
        assert message is not None
        assert "STALE" in message
        assert str(logs) in message
        assert str(schedule) in message
        assert f"{lag} days behind" in message

    def test_the_boundary_is_inclusive(self) -> None:
        logs = date(2026, 12, 1)
        edge = logs + pd.Timedelta(days=daily_run.STALE_AFTER_DAYS).to_pytimedelta()
        assert daily_run.staleness_warning(logs, edge) is None
        over = edge + pd.Timedelta(days=1).to_pytimedelta()
        assert daily_run.staleness_warning(logs, over) is not None

    def test_nothing_behind_the_window_is_not_stale(self) -> None:
        assert daily_run.staleness_warning(None, None) is None
        assert daily_run.staleness_warning(date(2026, 4, 12), None) is None

    def test_an_empty_truth_layer_with_games_behind_us_is_stale(self) -> None:
        message = daily_run.staleness_warning(None, date(2026, 12, 1))
        assert message is not None
        assert "empty" in message

    def test_tolerance_is_configurable_for_the_caller(self) -> None:
        logs, schedule = date(2026, 12, 1), date(2026, 12, 3)
        assert daily_run.staleness_warning(logs, schedule, max_lag_days=1) is not None
        assert daily_run.staleness_warning(logs, schedule, max_lag_days=5) is None


def _qualifying(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "seasons": [str(config.PROSPECTIVE_2026_27["season"])],
        "season_types": ["Regular Season"],
        "horizon": config.PROSPECTIVE_SERVING_HORIZON,
        "model_version": config.PROSPECTIVE_MODEL_VERSION,
        "feature_version": config.PROSPECTIVE_FEATURE_VERSION,
        "universe_source": SOURCE_PROSPECTIVE,
        "artifact_verified": True,
    }
    kwargs.update(overrides)
    return kwargs


class TestProspectiveConditions:
    def test_a_real_gameday_run_qualifies(self) -> None:
        assert daily_run.prospective_conditions(**_qualifying()) == []

    @pytest.mark.parametrize(
        ("override", "fragment"),
        [
            ({"seasons": ["2025-26"]}, "season 2025-26"),
            ({"seasons": []}, "no season"),
            ({"season_types": ["Pre Season"]}, "season_type Pre Season"),
            ({"horizon": "early"}, "horizon early"),
            ({"horizon": "lock"}, "horizon lock"),
            ({"model_version": "20260817d"}, "model 20260817d"),
            ({"feature_version": "v4"}, "feature_version v4"),
            ({"universe_source": "approximation"}, "universe approximation"),
            ({"universe_source": "status"}, "universe status"),
            ({"artifact_verified": False}, "checksums not verified"),
        ],
    )
    def test_each_condition_disqualifies_on_its_own(
        self, override: dict[str, object], fragment: str
    ) -> None:
        reasons = daily_run.prospective_conditions(**_qualifying(**override))
        assert reasons, f"{override} should have disqualified the run"
        assert any(fragment in r for r in reasons), reasons

    def test_a_mixed_season_window_disqualifies(self) -> None:
        reasons = daily_run.prospective_conditions(
            **_qualifying(seasons=["2026-27", "2027-28"])
        )
        assert reasons and "2027-28" in reasons[0]

    def test_the_frozen_season_is_read_from_the_freeze(self) -> None:
        source = (daily_run.__file__ or "")
        text = open(source, encoding="utf-8").read()
        assert '"2026-27"' not in text and "'2026-27'" not in text

    def test_every_failure_is_reported_not_just_the_first(self) -> None:
        reasons = daily_run.prospective_conditions(
            **_qualifying(seasons=["2025-26"], horizon="lock", artifact_verified=False)
        )
        assert len(reasons) == 3


class TestRunNotes:
    def test_a_qualifying_run_carries_the_frozen_note(self) -> None:
        note = daily_run.run_notes([])
        assert note == (
            f"{config.PROSPECTIVE_RUN_NOTE_LABEL}; "
            f"feature_set={config.SERVED_FEATURE_SET}; channel=production"
        )

    def test_a_disqualified_run_never_carries_the_label(self) -> None:
        note = daily_run.run_notes(["horizon lock is not gameday"])
        assert config.PROSPECTIVE_RUN_NOTE_LABEL not in note
        assert "NOT PROSPECTIVE" in note
        assert "horizon lock is not gameday" in note

    def test_the_reason_text_cannot_smuggle_the_label_in(self) -> None:
        with pytest.raises(AssertionError, match=config.PROSPECTIVE_RUN_NOTE_LABEL):
            daily_run.run_notes([f"not {config.PROSPECTIVE_RUN_NOTE_LABEL}"])

    def test_the_feature_set_and_channel_flags_are_always_present(self) -> None:
        for reasons in ([], ["something"]):
            note = daily_run.run_notes(reasons)
            assert f"feature_set={config.SERVED_FEATURE_SET}" in note
            assert "channel=production" in note
            assert "shadow=" not in note

    def test_staleness_is_appended_to_either_form(self) -> None:
        stale = "STALE truth layer: game logs end 2026-11-20"
        assert stale in daily_run.run_notes([], stale)
        assert stale in daily_run.run_notes(["horizon lock is not gameday"], stale)

    def test_a_stale_qualifying_run_still_carries_the_label(self) -> None:
        note = daily_run.run_notes([], "STALE truth layer: 9 days behind")
        assert note.startswith(config.PROSPECTIVE_RUN_NOTE_LABEL)


def _slate() -> pd.DataFrame:
    """three real 2026-10-20 tips: 3pm, 7pm and 9:30pm ET, in UTC."""
    return pd.DataFrame(
        {
            "GAME_ID": ["0022600001", "0022600002", "0022600003"],
            "GAME_DATE": pd.to_datetime(["2026-10-20"] * 3),
            "SCHEDULED_AT": pd.to_datetime(
                [
                    "2026-10-20T19:00:00Z",
                    "2026-10-20T23:00:00Z",
                    "2026-10-21T01:30:00Z",
                ]
            ),
        }
    )


class TestDropTippedOff:
    def test_before_every_tip_nothing_is_dropped(self) -> None:
        upcoming, tipped = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-20T17:00:00Z")
        )
        assert len(upcoming) == 3
        assert tipped.empty

    def test_the_cron_instant_keeps_the_whole_slate(self) -> None:
        upcoming, tipped = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-20T16:00:00Z")
        )
        assert list(upcoming["GAME_ID"]) == [
            "0022600001", "0022600002", "0022600003",
        ]
        assert tipped.empty

    def test_the_retired_cron_instant_drops_only_the_afternoon_game(self) -> None:
        upcoming, tipped = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-20T21:00:00Z")
        )
        assert list(tipped["GAME_ID"]) == ["0022600001"]
        assert list(upcoming["GAME_ID"]) == ["0022600002", "0022600003"]

    def test_after_every_tip_nothing_survives(self) -> None:
        upcoming, tipped = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-21T03:00:00Z")
        )
        assert upcoming.empty
        assert len(tipped) == 3

    def test_a_prediction_at_the_tip_is_not_before_it(self) -> None:
        exact = pd.Timestamp("2026-10-20T19:00:00Z")
        upcoming, tipped = daily_run.drop_tipped_off(_slate(), exact)
        assert "0022600001" in set(tipped["GAME_ID"])
        assert "0022600001" not in set(upcoming["GAME_ID"])

    def test_a_naive_boundary_is_read_as_utc(self) -> None:
        aware = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-20T21:00:00Z")
        )[0]
        naive = daily_run.drop_tipped_off(
            _slate(), pd.Timestamp("2026-10-20T21:00:00")
        )[0]
        assert list(aware["GAME_ID"]) == list(naive["GAME_ID"])

    def test_a_datetime_boundary_works_as_well_as_a_timestamp(self) -> None:
        upcoming, _ = daily_run.drop_tipped_off(
            _slate(), datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
        )
        assert len(upcoming) == 2

    def test_an_unknown_tip_falls_back_to_the_nominal_hour(self) -> None:
        frame = _slate()
        frame["SCHEDULED_AT"] = pd.NaT
        # an unknown tip is dropped rather than published on an optimistic guess
        upcoming, tipped = daily_run.drop_tipped_off(
            frame, pd.Timestamp("2026-10-20T12:00:00Z")
        )
        assert upcoming.empty and len(tipped) == 3
        upcoming, tipped = daily_run.drop_tipped_off(
            frame, pd.Timestamp("2026-10-19T12:00:00Z")
        )
        assert len(upcoming) == 3 and tipped.empty

    def test_a_missing_scheduled_at_column_falls_back_too(self) -> None:
        frame = _slate().drop(columns=["SCHEDULED_AT"])
        upcoming, tipped = daily_run.drop_tipped_off(
            frame, pd.Timestamp("2026-10-20T12:00:00Z")
        )
        assert upcoming.empty and len(tipped) == 3

    def test_a_partial_scheduled_at_uses_the_real_one_where_it_has_it(self) -> None:
        frame = _slate()
        frame.loc[1, "SCHEDULED_AT"] = pd.NaT
        upcoming, tipped = daily_run.drop_tipped_off(
            frame, pd.Timestamp("2026-10-20T18:00:00Z")
        )
        assert list(upcoming["GAME_ID"]) == ["0022600001", "0022600003"]
        assert list(tipped["GAME_ID"]) == ["0022600002"]

    def test_a_naive_scheduled_at_is_read_as_utc(self) -> None:
        frame = _slate()
        frame["SCHEDULED_AT"] = pd.to_datetime(
            ["2026-10-20T19:00:00", "2026-10-20T23:00:00", "2026-10-21T01:30:00"]
        )
        upcoming, _ = daily_run.drop_tipped_off(
            frame, pd.Timestamp("2026-10-20T21:00:00Z")
        )
        assert list(upcoming["GAME_ID"]) == ["0022600002", "0022600003"]

    def test_the_two_halves_partition_the_frame(self) -> None:
        for hour in range(0, 30, 3):
            now = pd.Timestamp("2026-10-20T00:00:00Z") + pd.Timedelta(hours=hour)
            upcoming, tipped = daily_run.drop_tipped_off(_slate(), now)
            assert len(upcoming) + len(tipped) == 3
            assert set(upcoming["GAME_ID"]).isdisjoint(set(tipped["GAME_ID"]))

    def test_an_empty_frame_survives(self) -> None:
        empty = _slate().iloc[:0]
        upcoming, tipped = daily_run.drop_tipped_off(
            empty, pd.Timestamp("2026-10-20T21:00:00Z")
        )
        assert upcoming.empty and tipped.empty

    def test_it_does_not_mutate_its_input(self) -> None:
        frame = _slate()
        before = frame.copy()
        daily_run.drop_tipped_off(frame, pd.Timestamp("2026-10-20T21:00:00Z"))
        pd.testing.assert_frame_equal(frame, before)

    def test_the_nominal_hour_is_predicts_own(self) -> None:
        import predict

        frame = _slate().drop(columns=["SCHEDULED_AT"])
        tip = daily_run.nominal_tip(frame)
        expected = pd.Timestamp("2026-10-20T00:00:00Z") + pd.Timedelta(
            hours=predict.NOMINAL_TIP_HOUR_UTC
        )
        assert (tip == expected).all()


class TestVerifyPinnedArtifact:
    def test_the_checked_in_artifact_verifies(self) -> None:
        assert daily_run.verify_pinned_artifact() == []

    def test_a_missing_directory_is_reported_not_silently_passed(self, tmp_path) -> None:
        bad = daily_run.verify_pinned_artifact(tmp_path)
        assert bad and "missing" in bad[0]

    def test_a_corrupted_file_is_caught(self, tmp_path) -> None:
        target = tmp_path / config.PROSPECTIVE_MODEL_VERSION
        target.mkdir()
        for name in config.PROSPECTIVE_ARTIFACT_CHECKSUMS:
            (target / name).write_bytes(b"not the frozen bytes")
        assert sorted(daily_run.verify_pinned_artifact(tmp_path)) == sorted(
            config.PROSPECTIVE_ARTIFACT_CHECKSUMS
        )

    def test_an_extra_file_in_the_served_directory_is_caught(self, tmp_path) -> None:
        real = config.MODELS_DIR / config.PROSPECTIVE_MODEL_VERSION
        target = tmp_path / config.PROSPECTIVE_MODEL_VERSION
        target.mkdir()
        for name in config.PROSPECTIVE_ARTIFACT_CHECKSUMS:
            (target / name).write_bytes((real / name).read_bytes())
        assert daily_run.verify_pinned_artifact(tmp_path) == []
        (target / "second_model.joblib").write_bytes(b"surprise")
        assert daily_run.verify_pinned_artifact(tmp_path) == ["second_model.joblib"]


class TestPhaseContract:
    def test_a_failure_names_its_phase(self) -> None:
        with pytest.raises(daily_run.PhaseFailure) as caught:
            with daily_run.phase("dataset"):
                raise RuntimeError("postgres said no")
        assert caught.value.phase == "dataset"
        assert "postgres said no" in str(caught.value)

    def test_a_driven_scripts_systemexit_is_relabelled(self) -> None:
        with pytest.raises(daily_run.PhaseFailure) as caught:
            with daily_run.phase("predict"):
                raise SystemExit("no trained model at models/20260818")
        assert caught.value.phase == "predict"
        assert "no trained model" in str(caught.value)

    def test_a_clean_no_op_passes_through_unrelabelled(self) -> None:
        with pytest.raises(daily_run.NothingToDo):
            with daily_run.phase("schedule"):
                raise daily_run.NothingToDo("no games in window")

    def test_a_nested_phase_failure_keeps_the_inner_phase(self) -> None:
        with pytest.raises(daily_run.PhaseFailure) as caught:
            with daily_run.phase("predict"):
                raise daily_run.PhaseFailure("dataset", "already labelled")
        assert caught.value.phase == "dataset"

    def test_every_phase_the_driver_enters_is_declared(self) -> None:
        text = open(daily_run.__file__, encoding="utf-8").read()
        used = {
            line.split('phase("')[1].split('"')[0]
            for line in text.splitlines()
            if 'with phase("' in line
        }
        assert used == set(daily_run.PHASES), (
            f"declared {set(daily_run.PHASES)}, entered {used}"
        )

    def test_the_phases_are_in_pipeline_order(self) -> None:
        assert daily_run.PHASES[0] == "preflight"
        assert daily_run.PHASES[-1] == "predict"
        assert daily_run.PHASES.index("dataset") < daily_run.PHASES.index("prospective")
        assert daily_run.PHASES.index("prospective") < daily_run.PHASES.index("predict")


class TestArgs:
    def test_the_scheduled_invocation_needs_no_arguments(self) -> None:
        args = daily_run.parse_args([])
        assert args.window_days == 2
        assert args.extended_days == 7
        assert args.window_start is None
        assert args.dry_run is False

    def test_dry_run_is_the_only_flag_the_workflow_passes(self) -> None:
        assert daily_run.parse_args(["--dry-run"]).dry_run is True

    def test_the_out_dir_is_not_the_hand_built_dataset(self) -> None:
        args = daily_run.parse_args([])
        assert args.out_dir != config.DATA_DIR
        assert args.out_dir.parent == config.DATA_DIR

    def test_the_extended_window_defaults_to_seven_days(self) -> None:
        assert daily_run.parse_args([]).extended_days == 7

    def test_the_extended_window_may_equal_the_prospective_one(self) -> None:
        assert daily_run.parse_args(["--extended-days", "2"]).extended_days == 2

    def test_the_extended_window_may_not_be_shorter(self) -> None:
        with pytest.raises(SystemExit):
            daily_run.parse_args(["--extended-days", "1"])
        with pytest.raises(SystemExit):
            daily_run.parse_args(["--window-days", "3", "--extended-days", "2"])


def _extended_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GAME_ID": ["0012600001", "0022600001", "0022600002", "0022600003"],
            "PLAYER_ID": ["1", "2", "3", "4"],
            "GAME_DATE": pd.to_datetime(
                ["2026-10-20", "2026-10-20", "2026-10-21", "2026-10-22"]
            ),
        }
    )


def _extended_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GAME_ID": ["0012600001", "0022600001", "0022600002", "0022600003"],
            "SEASON_TYPE": ["Pre Season", "Regular Season", "Regular Season",
                            "Regular Season"],
        }
    )


class TestSplitProspective:
    def test_keeps_regular_season_games_inside_the_window(self) -> None:
        kept = daily_run.split_prospective(
            _extended_frame(), _extended_schedule(),
            date(2026, 10, 20), date(2026, 10, 21),
        )
        assert list(kept["GAME_ID"]) == ["0022600001", "0022600002"]

    def test_preseason_is_excluded_even_inside_the_window(self) -> None:
        kept = daily_run.split_prospective(
            _extended_frame(), _extended_schedule(),
            date(2026, 10, 20), date(2026, 10, 20),
        )
        assert "0012600001" not in set(kept["GAME_ID"])

    def test_games_after_the_window_are_excluded(self) -> None:
        kept = daily_run.split_prospective(
            _extended_frame(), _extended_schedule(),
            date(2026, 10, 20), date(2026, 10, 21),
        )
        assert "0022600003" not in set(kept["GAME_ID"])

    def test_an_all_preseason_slate_yields_an_empty_frame(self) -> None:
        schedule = _extended_schedule().assign(SEASON_TYPE="Pre Season")
        kept = daily_run.split_prospective(
            _extended_frame(), schedule, date(2026, 10, 20), date(2026, 10, 21)
        )
        assert kept.empty
        assert list(kept.columns) == list(_extended_frame().columns)

    def test_it_does_not_mutate_its_input(self) -> None:
        frame = _extended_frame()
        before = frame.copy()
        daily_run.split_prospective(
            frame, _extended_schedule(), date(2026, 10, 20), date(2026, 10, 21)
        )
        pd.testing.assert_frame_equal(frame, before)


class TestExtendedNotes:
    def test_never_carries_the_prospective_label(self) -> None:
        note = daily_run.extended_notes(7, [])
        assert config.PROSPECTIVE_RUN_NOTE_LABEL not in note
        assert note.startswith("NOT PROSPECTIVE (extended 7-day serving window")

    def test_carries_no_horizon_token_and_keeps_the_tail(self) -> None:
        note = daily_run.extended_notes(
            7, ["season_type Pre Season is not Regular Season"]
        )
        assert "horizon=" not in note
        assert "Pre Season" in note
        assert f"feature_set={config.SERVED_FEATURE_SET}; channel=production" in note

    def test_staleness_is_appended(self) -> None:
        note = daily_run.extended_notes(7, [], "STALE truth layer")
        assert "STALE truth layer" in note

    def test_the_label_cannot_be_smuggled_in_through_a_condition(self) -> None:
        with pytest.raises(AssertionError):
            daily_run.extended_notes(7, [config.PROSPECTIVE_RUN_NOTE_LABEL])

    def test_the_slate_widens_only_the_slate_query(self) -> None:
        assert "Pre Season" in daily_run.SLATE_SEASON_TYPES
        assert config.SEASON_TYPES == ["Regular Season"]


class TestPredictArgv:
    def _argv(self, **overrides) -> list[str]:
        kwargs = dict(
            dataset_path=Path("prospective.parquet"),
            models_dir=Path("models"),
            out_path=Path("predictions.parquet"),
            notes="note",
            horizon="gameday",
            window_start=date(2026, 10, 20),
            statuses_as_of=pd.Timestamp("2026-10-20T15:30:00Z"),
            statuses_path=None,
            history_through=date(2026, 10, 19),
            write_db=True,
        )
        kwargs.update(overrides)
        return daily_run.predict_argv(**kwargs)

    @staticmethod
    def _value(argv: list[str], flag: str) -> str:
        return argv[argv.index(flag) + 1]

    def test_every_daily_run_is_on_the_production_channel(self) -> None:
        for horizon in ("gameday", "none"):
            argv = self._argv(horizon=horizon)
            assert self._value(argv, "--channel") == "production"

    def test_the_two_boundaries_are_passed_separately(self) -> None:
        argv = self._argv()
        assert self._value(argv, "--run-at") == "2026-10-20"
        assert self._value(argv, "--statuses-as-of") == "2026-10-20T15:30:00+00:00"
        assert self._value(argv, "--history-through") == "2026-10-19"

    def test_unknown_history_is_left_for_predict_to_derive(self) -> None:
        assert "--history-through" not in self._argv(history_through=None)

    def test_a_dry_run_never_writes(self) -> None:
        assert "--write-db" not in self._argv(write_db=False)
        assert "--write-db" in self._argv(write_db=True)

    def test_the_argv_parses_in_predict(self) -> None:
        import predict  # noqa: PLC0415

        args = predict.parse_args(self._argv(statuses_path=Path("s.parquet")))
        assert args.channel == "production"
        assert args.history_through == "2026-10-19"
        assert args.write_db is True


class TestShadowNotes:
    def test_a_qualifying_shadow_carries_the_label_on_the_shadow_channel(self) -> None:
        # act
        note = daily_run.shadow_notes([], "v1")

        # assert
        assert note == (
            f"{config.PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v1; channel=shadow"
        )

    def test_a_disqualified_shadow_never_carries_the_label(self) -> None:
        # act
        note = daily_run.shadow_notes(["horizon lock is not gameday"], "v1")

        # assert
        assert config.PROSPECTIVE_RUN_NOTE_LABEL not in note
        assert note.startswith("NOT PROSPECTIVE")
        assert note.endswith("feature_set=v1; channel=shadow")

    def test_the_served_note_is_unchanged_by_the_new_parameters(self) -> None:
        # act + assert
        assert daily_run.run_notes([]) == daily_run.run_notes(
            [], None, config.SERVED_FEATURE_SET, "production"
        )

    def test_the_shadow_version_pairs_with_the_pinned_artifact(self) -> None:
        # act + assert
        assert daily_run.shadow_version("v1") == f"{config.PROSPECTIVE_MODEL_VERSION}-v1"


def _write_metadata(directory: Path, **fields: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text(json.dumps(fields), encoding="utf-8")


def _shadow_models_dir(
    root: Path, shadow_cutoff: str = "2026-04-13", register: bool = True
) -> Path:
    """a models dir holding the served metadata and a v1 shadow next to it."""
    models_dir = root / "models"
    _write_metadata(
        models_dir / config.PROSPECTIVE_MODEL_VERSION,
        training_window={"cutoff": "2026-04-13"},
    )
    version = daily_run.shadow_version("v1")
    _write_metadata(
        models_dir / version, feature_set="v1", training_window={"cutoff": shadow_cutoff}
    )
    if register:
        registry.upsert(
            registry.build_entry(
                model_version=version, version_dir=models_dir / version,
                training_window={"cutoff": shadow_cutoff}, hyperparams={}, metrics={},
                champions={}, universe_source="status",
                feature_cols=list(config.BASE_FEATURE_COLS), feature_set="v1",
            ),
            models_dir / "registry.json",
        )
    return models_dir


class TestShadowArtifactConditions:
    def test_an_absent_artifact_is_none_not_a_reason(self, tmp_path: Path) -> None:
        # act + assert
        assert daily_run.shadow_artifact_conditions("v1", tmp_path) is None

    def test_a_matching_registered_artifact_qualifies(self, tmp_path: Path) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path)

        # act
        reasons = daily_run.shadow_artifact_conditions("v1", models_dir)

        # assert
        assert reasons == []

    def test_a_different_cutoff_disqualifies(self, tmp_path: Path) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path, shadow_cutoff="2026-05-01")

        # act
        reasons = daily_run.shadow_artifact_conditions("v1", models_dir) or []

        # assert
        assert any("cutoff 2026-05-01" in r for r in reasons)

    def test_an_unregistered_artifact_disqualifies(self, tmp_path: Path) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path, register=False)

        # act
        reasons = daily_run.shadow_artifact_conditions("v1", models_dir) or []

        # assert
        assert any("no registry entry" in r for r in reasons)

    def test_an_edited_artifact_disqualifies(self, tmp_path: Path) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path)
        meta = models_dir / daily_run.shadow_version("v1") / "metadata.json"
        meta.write_text(meta.read_text(encoding="utf-8") + " ", encoding="utf-8")

        # act
        reasons = daily_run.shadow_artifact_conditions("v1", models_dir) or []

        # assert
        assert any("checksums not verified" in r for r in reasons)


class TestShadowArgs:
    def test_no_shadow_is_requested_by_default(self) -> None:
        # act + assert
        assert daily_run.parse_args([]).shadow_feature_sets == []

    def test_the_flag_is_restricted_to_the_frozen_shadow_sets(self) -> None:
        # act
        args = daily_run.parse_args(["--shadow-feature-set", "v1"])

        # assert
        assert args.shadow_feature_sets == ["v1"]
        with pytest.raises(SystemExit):
            daily_run.parse_args(["--shadow-feature-set", "v3-honest"])

    def test_the_workflow_requests_the_v1_shadow(self) -> None:
        # arrange
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/predictions.yml"

        # act
        text = workflow.read_text(encoding="utf-8")

        # assert
        assert "daily_run.py --shadow-feature-set v1" in text


def _without(argv: list[str], *flags: str) -> list[str]:
    """argv minus the named flags and their values."""
    out: list[str] = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in flags:
            skip = True
            continue
        out.append(token)
    return out


def _flag(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class TestShadowPredictArgv:
    def test_the_shadow_argv_is_run_a_with_version_and_channel_swapped(self) -> None:
        # arrange
        common: dict[str, object] = dict(
            dataset_path=Path("prospective.parquet"), models_dir=Path("models"),
            out_path=Path("p.parquet"), notes="n", horizon="gameday",
            window_start=date(2026, 10, 20),
            statuses_as_of=pd.Timestamp("2026-10-20T15:30:00Z"),
            statuses_path=Path("s.parquet"), history_through=date(2026, 10, 19),
            write_db=True,
        )

        # act
        served = daily_run.predict_argv(**common)
        shadow = daily_run.predict_argv(**common, version="20260818-v1", channel="shadow")

        # assert
        assert _flag(served, "--channel") == "production"
        assert _flag(served, "--version") == config.PROSPECTIVE_MODEL_VERSION
        assert _flag(shadow, "--channel") == "shadow"
        assert _flag(shadow, "--version") == "20260818-v1"
        assert _without(served, "--channel", "--version") == _without(
            shadow, "--channel", "--version"
        )


def _drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    models_dir: Path,
    extra: list[str],
    failing_version: str | None = None,
    overrides: dict[str, object] | None = None,
) -> tuple[int, list[list[str]]]:
    """daily_run.main end to end, with every database read and predict.py faked."""
    games = ["0022600501", "0022600502"]
    tips = pd.to_datetime(["2027-01-11T00:30Z", "2027-01-12T00:30Z"])
    schedule = pd.DataFrame({
        "GAME_ID": games,
        "SEASON": ["2026-27", "2026-27"],
        "SEASON_TYPE": ["Regular Season", "Regular Season"],
        "GAME_DATE": [date(2027, 1, 10), date(2027, 1, 11)],
        "SCHEDULED_AT": tips,
        "HOME_TEAM_ID": ["1", "2"],
        "AWAY_TEAM_ID": ["3", "4"],
        "GAME_STATUS": ["Scheduled", "Scheduled"],
    })
    features = pd.DataFrame({
        "GAME_ID": games,
        "PLAYER_ID": ["10", "20"],
        "GAME_DATE": pd.to_datetime(["2027-01-10", "2027-01-11"]),
        "SCHEDULED_AT": tips,
        "UNIVERSE_SOURCE": [SOURCE_PROSPECTIVE, SOURCE_PROSPECTIVE],
    })
    history = pd.DataFrame({"GAME_DATE": pd.to_datetime(["2027-01-09"]), "PLAYER_ID": ["10"]})
    fakes = {
        "verify_pinned_artifact": lambda *_: [],
        "load_window_schedule": lambda *_: (schedule, 2),
        "load_freshness": lambda *_: (date(2027, 1, 9), date(2027, 1, 9)),
        "load_rosters": lambda *_: (pd.DataFrame(), "fake rosters"),
        "load_positions": lambda: None,
        "load_dataset": lambda *_: history,
        "history_from_dataset": lambda frame: frame,
        "prospective_universe": lambda *_, **__: None,
        "build_prospective_features": lambda *_: features,
        "load_statuses": lambda *_: pd.DataFrame(),
        "_rows_per_player_game": lambda *_: 1,
        **(overrides or {}),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(daily_run, name, fake)

    calls: list[list[str]] = []

    def fake_predict(argv: list[str]) -> int:
        calls.append(list(argv))
        if failing_version and _flag(argv, "--version") == failing_version:
            return 1
        scored = pd.read_parquet(_flag(argv, "--dataset"))
        scored[["GAME_ID", "PLAYER_ID"]].assign(P_PLAY=0.5).to_parquet(
            _flag(argv, "--out"), index=False
        )
        return 0

    monkeypatch.setattr(daily_run.predict_script, "main", fake_predict)
    code = daily_run.main([
        "--dry-run", "--window-start", "2027-01-10",
        "--dataset", str(tmp_path / "unused.parquet"),
        "--out-dir", str(tmp_path / "out"),
        "--models-dir", str(models_dir),
        *extra,
    ])
    return code, calls


class TestShadowRun:
    def test_the_shadow_follows_run_a_at_the_same_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path)

        # act
        code, calls = _drive(tmp_path, monkeypatch, models_dir, ["--shadow-feature-set", "v1"])

        # assert
        assert code == 0
        run_a, shadow, run_b = calls
        assert _flag(shadow, "--channel") == "shadow"
        assert _flag(shadow, "--version") == daily_run.shadow_version("v1")
        notes = _flag(shadow, "--notes")
        assert notes.startswith(config.PROSPECTIVE_RUN_NOTE_LABEL)
        assert "feature_set=v1; channel=shadow" in notes
        for flag in ("--dataset", "--statuses-as-of", "--run-at", "--horizon",
                     "--history-through"):
            assert _flag(shadow, flag) == _flag(run_a, flag)
        assert _flag(run_a, "--channel") == _flag(run_b, "--channel") == "production"

    def test_run_b_never_gets_a_shadow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path)

        # act
        _, calls = _drive(tmp_path, monkeypatch, models_dir, ["--shadow-feature-set", "v1"])

        # assert
        shadows = [c for c in calls if _flag(c, "--channel") == "shadow"]
        assert len(shadows) == 1
        assert Path(_flag(shadows[0], "--dataset")).name == "prospective.parquet"
        assert Path(_flag(calls[-1], "--dataset")).name == "prospective_extended.parquet"
        assert _flag(calls[-1], "--channel") == "production"

    def test_a_missing_shadow_is_skipped_and_run_a_is_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # arrange
        empty_models = tmp_path / "empty_models"
        empty_models.mkdir()
        _, baseline = _drive(tmp_path, monkeypatch, empty_models, [])
        caplog.set_level("WARNING", logger="daily_run")

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, empty_models, ["--shadow-feature-set", "v1"]
        )

        # assert
        assert code == 0
        assert len(calls) == 2
        assert "shadow shadow-v1 skipped: no artifact" in caplog.text
        for got, expected in zip(calls, baseline):
            assert _without(got, "--statuses-as-of") == _without(
                expected, "--statuses-as-of"
            )

    def test_a_failed_shadow_still_publishes_run_b_and_goes_red(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        models_dir = _shadow_models_dir(tmp_path)

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, models_dir, ["--shadow-feature-set", "v1"],
            failing_version=daily_run.shadow_version("v1"),
        )

        # assert
        assert len(calls) == 3
        assert _flag(calls[-1], "--channel") == "production"
        assert code == 1


class TestPreseasonPriorFlag:
    def test_only_run_b_asks_for_the_preseason_prior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        empty_models = tmp_path / "empty_models"
        empty_models.mkdir()

        # act
        code, calls = _drive(tmp_path, monkeypatch, empty_models, [])

        # assert
        assert code == 0
        run_a, run_b = calls
        assert "--preseason-prior" not in run_a
        assert _flag(run_b, "--preseason-prior") == "on"

    def test_run_b_frame_carries_the_season_type_and_run_a_frame_does_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        empty_models = tmp_path / "empty_models"
        empty_models.mkdir()

        # act
        _, calls = _drive(tmp_path, monkeypatch, empty_models, [])

        # assert
        run_a, run_b = calls
        assert "SEASON_TYPE" not in pd.read_parquet(_flag(run_a, "--dataset")).columns
        extended = pd.read_parquet(_flag(run_b, "--dataset"))
        assert extended["SEASON_TYPE"].tolist() == ["Regular Season", "Regular Season"]


def _statuses(**by_player: str) -> pd.DataFrame:
    """resolved designations, one row per player, as latest_statuses returns them."""
    return pd.DataFrame({
        "nba_player_id": list(by_player),
        "status_normalized": list(by_player.values()),
        "captured_at": pd.Timestamp("2026-10-20T15:30:00Z"),
    })


def _rescore_schedule() -> pd.DataFrame:
    """team 1 hosts team 2 at 7pm ET; team 3 hosts team 4 at 3pm ET."""
    return pd.DataFrame({
        "GAME_ID": ["0022600011", "0022600012"],
        "GAME_DATE": pd.to_datetime(["2026-10-20", "2026-10-20"]),
        "SCHEDULED_AT": pd.to_datetime(["2026-10-20T23:00:00Z", "2026-10-20T19:00:00Z"]),
        "HOME_TEAM_ID": ["1", "3"],
        "AWAY_TEAM_ID": ["2", "4"],
    })


def _rescore_rosters() -> pd.DataFrame:
    return pd.DataFrame({
        "nba_player_id": [10, 20, 30, 50],
        "team_id": [1, 2, 3, 5],
    })


class TestDesignationClass:
    @pytest.mark.parametrize("status, expected", [
        ("out", "out"), ("suspended", "out"), ("G-League", "out"), ("doubtful", "out"),
        ("questionable", "questionable"), ("GTD", "questionable"),
        ("probable", "questionable"),
        ("available", "available"), ("cleared", "available"),
        ("day_to_day", "available"), ("", "available"), ("something new", "available"),
    ])
    def test_the_class_follows_the_override_policy(self, status: str, expected: str) -> None:
        # act + assert
        assert daily_run.designation_class(status) == expected


class TestStatusChanges:
    NOW = pd.Timestamp("2026-10-20T20:45:00Z")

    def _changes(self, previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
        roster = daily_run.slate_rosters(_rescore_rosters(), _rescore_schedule(), self.NOW)
        return daily_run.status_changes(previous, current, roster)

    def test_available_to_out_counts(self) -> None:
        # arrange
        previous, current = _statuses(), _statuses(**{"10": "out"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert list(changes["nba_player_id"]) == ["10"]
        assert changes.loc[0, "previous_class"] == "available"
        assert changes.loc[0, "current_class"] == "out"

    def test_questionable_to_out_counts(self) -> None:
        # arrange
        previous = _statuses(**{"20": "questionable"})
        current = _statuses(**{"20": "out"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert list(changes["nba_player_id"]) == ["20"]
        assert list(changes["team_id"]) == ["2"]

    def test_out_to_cleared_counts(self) -> None:
        # arrange
        previous = _statuses(**{"10": "out"})
        current = _statuses(**{"10": "cleared"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert list(changes["current_class"]) == ["available"]

    def test_an_unchanged_class_does_not_count(self) -> None:
        # arrange
        previous = _statuses(**{"10": "out", "20": "questionable"})
        current = _statuses(**{"10": "inactive", "20": "probable"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert changes.empty

    def test_a_player_whose_team_has_no_game_in_window_does_not_count(self) -> None:
        # arrange
        previous, current = _statuses(), _statuses(**{"50": "out", "99": "out"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert changes.empty

    def test_a_player_whose_game_already_tipped_does_not_count(self) -> None:
        # arrange
        previous, current = _statuses(), _statuses(**{"30": "out"})

        # act
        changes = self._changes(previous, current)

        # assert
        assert changes.empty

    def test_no_reports_at_either_boundary_is_no_change(self) -> None:
        # act
        changes = self._changes(pd.DataFrame(), pd.DataFrame())

        # assert
        assert changes.empty


class TestRescoreNotes:
    TOKEN = daily_run.rescore_note(3)

    def test_the_token_names_the_trigger_and_the_count(self) -> None:
        # act + assert
        assert self.TOKEN == "rescore=status_change; changed_players=3"

    def test_a_qualifying_rescore_keeps_the_label_and_carries_the_token(self) -> None:
        # act
        note = daily_run.run_notes([], rescore=self.TOKEN)

        # assert
        assert note.startswith(config.PROSPECTIVE_RUN_NOTE_LABEL)
        assert note.endswith(self.TOKEN)

    def test_the_extended_rescore_carries_the_token_and_never_the_label(self) -> None:
        # act
        note = daily_run.extended_notes(7, [], "STALE truth layer", self.TOKEN)

        # assert
        assert self.TOKEN in note
        assert config.PROSPECTIVE_RUN_NOTE_LABEL not in note
        assert note.endswith("STALE truth layer")

    def test_the_label_assertion_still_applies_to_a_rescore(self) -> None:
        # act + assert
        with pytest.raises(AssertionError):
            daily_run.extended_notes(7, [config.PROSPECTIVE_RUN_NOTE_LABEL], None, self.TOKEN)

    def test_a_scheduled_run_carries_no_token(self) -> None:
        # act + assert
        assert "rescore=" not in daily_run.run_notes([])


class TestTrigger:
    def test_the_flag_is_off_by_default(self) -> None:
        # act + assert
        assert daily_run.parse_args([]).if_status_changed is False
        assert daily_run.parse_args(["--if-status-changed"]).if_status_changed is True

    def test_the_trigger_reaches_predict(self) -> None:
        # arrange
        import predict  # noqa: PLC0415

        common: dict[str, object] = dict(
            dataset_path=Path("p.parquet"), models_dir=Path("models"),
            out_path=Path("o.parquet"), notes="n", horizon="gameday",
            window_start=date(2026, 10, 20),
            statuses_as_of=pd.Timestamp("2026-10-20T20:45:00Z"),
            statuses_path=None, history_through=None, write_db=True,
        )

        # act
        scheduled = predict.parse_args(daily_run.predict_argv(**common))
        rescored = predict.parse_args(
            daily_run.predict_argv(**common, trigger=predict.TRIGGER_STATUS_CHANGE)
        )

        # assert
        assert scheduled.trigger == "schedule"
        assert rescored.trigger == "status_change"

    def test_the_registry_entry_records_the_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        import predict  # noqa: PLC0415

        recorded: list[dict[str, object]] = []
        monkeypatch.setattr(predict, "build_prediction_rows", lambda *_: [{}])
        monkeypatch.setattr(predict, "write_predictions", lambda *_: 7)
        monkeypatch.setattr(predict.registry, "git_commit", lambda *_: None)
        monkeypatch.setattr(
            predict.registry, "record_prediction_run",
            lambda version, run, *_: recorded.append(run),
        )

        # act
        predict.write_run(
            pd.DataFrame({"PLAYER_ID": ["10"]}), {"model_version": "m"},
            pd.Timestamp("2026-10-20"), "n", None,
            trigger=predict.TRIGGER_STATUS_CHANGE,
        )

        # assert
        assert recorded[0]["trigger"] == "status_change"
        assert recorded[0]["run_id"] == 7


def _gate(
    boundary: pd.Timestamp | None, previous: pd.DataFrame, current: pd.DataFrame
) -> dict[str, object]:
    """fakes for the rescore phase: the boundary, rosters, and statuses by as_of."""
    rosters = pd.DataFrame({"nba_player_id": ["10", "20"], "team_id": ["1", "2"]})

    def load_statuses(as_of: pd.Timestamp) -> pd.DataFrame:
        return previous if boundary is not None and as_of == boundary else current

    return {
        "load_previous_boundary": lambda: boundary,
        "load_rosters": lambda *_: (rosters, "fake rosters"),
        "load_statuses": load_statuses,
    }


class TestRescoreRun:
    # relative to the wall clock, so the 72-hour report expiry never ages a fixture out.
    BOUNDARY = pd.Timestamp.now("UTC").floor("s") - pd.Timedelta(hours=1)

    def _status(self, status: str) -> pd.DataFrame:
        return pd.DataFrame({
            "nba_player_id": ["10"], "status_normalized": [status],
            "captured_at": [self.BOUNDARY - pd.Timedelta(hours=1)],
        })

    def test_no_status_change_exits_zero_and_never_calls_predict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # arrange
        unchanged = self._status("questionable")
        fakes = _gate(self.BOUNDARY, unchanged, unchanged)

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, tmp_path, ["--if-status-changed"], overrides=fakes
        )

        # assert
        assert code == 0
        assert calls == []
        assert (f"no status change since {self.BOUNDARY.isoformat()}; nothing to publish"
                in capsys.readouterr().out)

    def test_no_previous_production_run_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        fakes = _gate(None, pd.DataFrame(), self._status("out"))

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, tmp_path, ["--if-status-changed"], overrides=fakes
        )

        # assert
        assert code == 0
        assert calls == []

    def test_a_change_publishes_both_runs_with_the_token_and_trigger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        fakes = _gate(self.BOUNDARY, pd.DataFrame(), self._status("out"))

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, tmp_path, ["--if-status-changed"], overrides=fakes
        )

        # assert
        assert code == 0
        assert len(calls) == 2
        run_a, run_b = calls
        assert _flag(run_a, "--notes").startswith(config.PROSPECTIVE_RUN_NOTE_LABEL)
        for argv in calls:
            assert "rescore=status_change; changed_players=1" in _flag(argv, "--notes")
            assert _flag(argv, "--trigger") == "status_change"
        assert config.PROSPECTIVE_RUN_NOTE_LABEL not in _flag(run_b, "--notes")

    def test_a_scheduled_run_ignores_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # arrange
        def unreachable() -> None:
            raise AssertionError("the scheduled lane must not read the boundary")

        # act
        code, calls = _drive(
            tmp_path, monkeypatch, tmp_path, [],
            overrides={"load_previous_boundary": unreachable},
        )

        # assert
        assert code == 0
        assert len(calls) == 2
        for argv in calls:
            assert "rescore=" not in _flag(argv, "--notes")
            assert _flag(argv, "--trigger") == "schedule"


class TestRescoreWorkflow:
    WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/predictions.yml"
    CRON = "45 15,19,20,21,22,23,0,1,2 * * *"

    def test_the_rescore_lane_is_scheduled_and_dispatched(self) -> None:
        # act
        text = self.WORKFLOW.read_text(encoding="utf-8")

        # assert
        assert f'- cron: "{self.CRON}"' in text
        assert f'"{self.CRON}")' in text
        assert "daily_run.py --if-status-changed --shadow-feature-set v1" in text

    def test_the_scheduled_lane_is_unchanged(self) -> None:
        # act
        text = self.WORKFLOW.read_text(encoding="utf-8")

        # assert
        assert '- cron: "0 16 * * *"' in text
        assert '"0 16 * * *" | "")' in text
        assert "cancel-in-progress: false" in text
        assert "unknown schedule" in text

    def test_the_lane_trails_the_scrapers_injuries_lane_by_fifteen_minutes(self) -> None:
        # arrange
        scraper = self.WORKFLOW.with_name("scraper.yml").read_text(encoding="utf-8")

        # act
        injuries = scraper.split('- cron: "30 ', 1)[1].split('"', 1)[0]

        # assert
        assert self.CRON == f"45 {injuries}"


class TestStatusScopeColumns:
    def test_the_parquet_frame_always_carries_game_and_source(self, tmp_path: Path) -> None:
        # arrange
        statuses = pd.DataFrame({
            "nba_player_id": ["2544"],
            "status_normalized": ["out"],
            "captured_at": [pd.Timestamp("2026-03-01T12:00:00Z")],
        })
        path = tmp_path / "statuses.parquet"

        # act
        daily_run.with_status_scope_columns(statuses).to_parquet(path, index=False)
        reread = pd.read_parquet(path)

        # assert
        assert {"nba_game_id", "source"} <= set(reread.columns)
        assert reread["nba_game_id"].isna().all()

    def test_present_columns_pass_through_unchanged(self) -> None:
        # arrange
        statuses = pd.DataFrame({
            "nba_player_id": ["2544"],
            "status_normalized": ["out"],
            "captured_at": [pd.Timestamp("2026-03-01T12:00:00Z")],
            "nba_game_id": ["0022500123"],
            "source": ["nba_official"],
        })

        # act
        out = daily_run.with_status_scope_columns(statuses)

        # assert
        pd.testing.assert_frame_equal(out, statuses)
