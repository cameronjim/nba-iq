from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import daily_run
from fnba_ml import config
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
