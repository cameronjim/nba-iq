"""preseason games: graded as truth, never a training row or a rate input (MODEL.md 20.5)."""

from __future__ import annotations

import pandas as pd
import pytest

from fnba_ml import config
from fnba_ml.data.postgres_source import PostgresSource
from fnba_ml.data.schema import competition_of
from fnba_ml.features import build_features, career_history
from fnba_ml.scoring import (
    build_baselines,
    excluded_season_type_rows,
    pool_label,
    score_runs,
    split_endpoint_rows,
)
from fnba_ml.universe import (
    approximate_universe,
    postseason_appearances,
    universe_from_status,
)
from test_scoring import long_rows, result, truth_row
from test_scoring_baselines import paired_predictions, shadow_runs

PRESEASON_PREFIX = "001"
PRESEASON_MINUTES = 12.0
# the first regular-season date of 2024-25 in the fixture; preseason copies sit before it.
SEASON_OPENER = pd.Timestamp("2024-11-01")


def _preseason_ids(frame: pd.DataFrame) -> pd.Series:
    return frame["GAME_ID"].astype(str).str.startswith(PRESEASON_PREFIX)


def _as_preseason(frame: pd.DataFrame, games: set[str]) -> pd.DataFrame:
    """copies of the rows of ``games``, relabelled as preseason games two weeks earlier."""
    rows = frame[frame["GAME_ID"].isin(games)].copy()
    rows["GAME_ID"] = PRESEASON_PREFIX + rows["GAME_ID"].str[3:]
    if "GAME_DATE" in rows.columns:
        rows["GAME_DATE"] = pd.to_datetime(rows["GAME_DATE"]) - pd.Timedelta(days=14)
    if "SCHEDULED_AT" in rows.columns:
        rows["SCHEDULED_AT"] = pd.to_datetime(rows["SCHEDULED_AT"]) - pd.Timedelta(days=14)
    if "SEASON_TYPE" in rows.columns:
        rows["SEASON_TYPE"] = config.PRESEASON_SEASON_TYPE
    if config.COMPETITION_COL in rows.columns:
        rows[config.COMPETITION_COL] = "preseason"
    if "MIN" in rows.columns:
        rows["MIN"] = PRESEASON_MINUTES
    return rows


@pytest.fixture(scope="module")
def planted(history_schedule, history_team_logs, history_logs, history_status):
    opener = history_schedule[
        (history_schedule["SEASON"] == "2024-25")
        & (pd.to_datetime(history_schedule["GAME_DATE"]) == SEASON_OPENER)
    ]
    games = set(opener["GAME_ID"])
    assert games, "the fixture must hold games on the 2024-25 opener"
    return {
        name: pd.concat([frame, _as_preseason(frame, games)], ignore_index=True)
        for name, frame in (
            ("schedule", history_schedule),
            ("team_logs", history_team_logs),
            ("player_logs", history_logs),
            ("status", history_status),
        )
    }


class TestPreseasonIsTruthOnly:
    def test_the_preseason_is_tagged_but_neither_trained_on_nor_rated_from(self):
        # act
        label = config.COMPETITION_BY_SEASON_TYPE[config.PRESEASON_SEASON_TYPE]

        # assert
        assert label == "preseason"
        assert config.PRESEASON_SEASON_TYPE not in config.HISTORY_SEASON_TYPES
        assert config.PRESEASON_SEASON_TYPE not in config.TRAINING_SEASON_TYPES
        assert label not in config.TRAINING_COMPETITIONS
        assert label not in config.POSTSEASON_COMPETITIONS

    def test_the_truth_types_are_the_history_types_plus_the_preseason(self):
        # act + assert
        assert set(config.TRUTH_SEASON_TYPES) == {
            config.PRESEASON_SEASON_TYPE, *config.HISTORY_SEASON_TYPES
        }

    def test_a_pre_season_row_is_labelled_preseason(self):
        # act
        labels = competition_of(pd.Series(["Pre Season", "Regular Season", "All Star"]))

        # assert
        assert labels.iloc[0] == "preseason"
        assert labels.iloc[1] == "regular"
        assert pd.isna(labels.iloc[2])

    def test_the_dataset_source_excludes_the_preseason_and_the_truth_reader_loads_it(self):
        # act
        dataset = PostgresSource(database_url="postgresql://unused")
        truth = PostgresSource(
            season_types=list(config.TRUTH_SEASON_TYPES), database_url="postgresql://unused"
        )

        # assert
        assert config.PRESEASON_SEASON_TYPE not in dataset.season_types
        assert config.PRESEASON_SEASON_TYPE in truth.season_types


class TestPreseasonNeverReachesTheModel:
    def test_the_status_universe_is_unchanged_by_preseason_rows(
        self, planted, positions, universe_status
    ):
        # act
        universe = universe_from_status(
            planted["schedule"], planted["team_logs"], planted["player_logs"],
            planted["status"], positions,
        )

        # assert
        assert not _preseason_ids(universe).any()
        pd.testing.assert_frame_equal(universe, universe_status, check_exact=True)

    def test_the_approximate_universe_is_unchanged_by_preseason_rows(
        self, planted, positions, universe_approx
    ):
        # act
        universe = approximate_universe(
            planted["schedule"], planted["team_logs"], planted["player_logs"],
            positions=positions,
        )

        # assert
        assert not _preseason_ids(universe).any()
        pd.testing.assert_frame_equal(universe, universe_approx, check_exact=True)

    def test_the_postseason_history_frame_holds_no_preseason_game(
        self, planted, positions, postseason
    ):
        # act
        frame = postseason_appearances(
            planted["schedule"], planted["team_logs"], planted["player_logs"], positions
        )

        # assert
        assert not _preseason_ids(frame).any()
        pd.testing.assert_frame_equal(frame, postseason, check_exact=True)

    def test_the_appearance_history_drops_preseason_rows_with_the_switch_on(
        self, universe_status, postseason
    ):
        # arrange
        preseason = postseason.copy()
        preseason["GAME_ID"] = PRESEASON_PREFIX + preseason["GAME_ID"].str[3:]
        preseason[config.COMPETITION_COL] = "preseason"
        polluted = pd.concat([postseason, preseason], ignore_index=True)

        # act
        history = career_history(universe_status, polluted, include_postseason=True)

        # assert
        assert not _preseason_ids(history).any()
        pd.testing.assert_frame_equal(
            history, career_history(universe_status, postseason, include_postseason=True)
        )

    def test_features_with_the_switch_on_ignore_a_preseason_row(
        self, universe_status, postseason
    ):
        # arrange
        preseason = postseason.copy()
        preseason["GAME_ID"] = PRESEASON_PREFIX + preseason["GAME_ID"].str[3:]
        preseason[config.COMPETITION_COL] = "preseason"
        polluted = pd.concat([postseason, preseason], ignore_index=True)

        # act
        built = build_features(universe_status, postseason=polluted, include_postseason=True)

        # assert
        expected = build_features(universe_status, postseason=postseason, include_postseason=True)
        pd.testing.assert_frame_equal(built, expected, check_exact=True)

    def test_a_preseason_row_handed_in_as_a_modelled_row_is_refused(self, universe_status):
        # arrange
        row = universe_status.iloc[[0]].copy()
        row["GAME_ID"] = PRESEASON_PREFIX + row["GAME_ID"].str[3:]
        row[config.COMPETITION_COL] = "preseason"
        polluted = pd.concat([universe_status, row], ignore_index=True)

        # act + assert
        with pytest.raises(ValueError, match="universe rows are not regular"):
            build_features(polluted)


def _history_row(game: str, day: str, season_type: str, minutes: float) -> dict[str, object]:
    return {"nba_player_id": "1", "nba_game_id": game, "team_id": "T", "game_date": day,
            "season_type": season_type, "played": True, "listed_inactive": False,
            "minutes": minutes, "pts": minutes / 2}


class TestScoringBaselinesSkipThePreseason:
    @pytest.mark.parametrize("include_postseason", [False, True])
    def test_a_preseason_appearance_moves_no_baseline(self, include_postseason):
        # arrange
        targets = pd.DataFrame([{"nba_player_id": "1", "nba_game_id": "0022600050",
                                 "game_date": "2026-11-20"}])
        regular = [_history_row(f"00226000{i:02d}", f"2026-11-{i + 1:02d}", "Regular Season", 30.0)
                   for i in range(10)]
        preseason = [_history_row("0012600001", "2026-10-10", "Pre Season", 12.0)]

        # act
        with_preseason = build_baselines(
            targets, pd.DataFrame(regular + preseason), include_postseason=include_postseason
        )
        without = build_baselines(
            targets, pd.DataFrame(regular), include_postseason=include_postseason
        )

        # assert
        pd.testing.assert_frame_equal(with_preseason, without)
        assert with_preseason.iloc[0]["roll10_MIN"] == pytest.approx(30.0)


def _graded_window() -> tuple[pd.DataFrame, pd.DataFrame]:
    """two paired regular-season games plus one preseason game he sat out."""
    predictions, truth = paired_predictions(["2026-11-02", "2026-11-03"])
    preseason = pd.DataFrame(
        long_rows(10, "1", "0012600001", "2026-10-08", 0.9, {"pts": 20.0, "minutes": 30.0},
                  p_model=0.9)
        + long_rows(11, "1", "0012600001", "2026-10-08", 0.6, {"pts": 15.0, "minutes": 25.0},
                    p_model=0.6)
    )
    preseason_truth = pd.DataFrame([truth_row(
        "1", "0012600001", False, None, "2026-10-08", config.PRESEASON_SEASON_TYPE
    )])
    return (pd.concat([predictions, preseason], ignore_index=True),
            pd.concat([truth, preseason_truth], ignore_index=True))


class TestPreseasonIsGradedApartFromTheEndpoints:
    def test_a_preseason_game_is_matched_to_its_truth_and_graded(self):
        # arrange
        predictions, truth = _graded_window()

        # act
        results = score_runs(predictions, shadow_runs(), truth)

        # assert
        pooled = results[results["run_id"] == pool_label("production", True)]
        brier = result(pooled, "E1_brier", "prob_active", "season_type=Pre Season")
        assert brier["n"] == 1
        assert brier["value"] == pytest.approx(0.9**2)

    def test_the_endpoint_rows_drop_the_preseason_game_and_count_it(self):
        # arrange
        predictions, truth = _graded_window()
        preseason_rows = (predictions["nba_game_id"] == "0012600001").sum()

        # act
        graded, excluded = split_endpoint_rows(predictions, truth)

        # assert
        assert not graded["nba_game_id"].str.startswith(PRESEASON_PREFIX).any()
        assert excluded == {config.PRESEASON_SEASON_TYPE: 1}
        assert len(graded) == len(predictions) - preseason_rows

    def test_a_game_with_no_truth_row_is_kept_as_pending(self):
        # arrange
        predictions, truth = _graded_window()
        pending = truth[truth["nba_game_id"] != "g1"]

        # act
        graded, _ = split_endpoint_rows(predictions, pending)

        # assert
        assert (graded["nba_game_id"] == "g1").any()

    def test_only_the_non_endpoint_season_type_cohorts_are_split_out(self):
        # arrange
        predictions, truth = _graded_window()
        results = score_runs(predictions, shadow_runs(), truth)

        # act
        split = excluded_season_type_rows(results)

        # assert
        assert set(split["cohort"]) == {"season_type=Pre Season"}

    def test_the_look_report_reads_the_regular_season_only(self, monkeypatch, tmp_path):
        # arrange
        import score_runs as cli

        predictions, truth = _graded_window()
        runs = shadow_runs()
        frames = {
            "information_schema": pd.DataFrame({"column_name": list(runs.columns)}),
            "WITH involved": pd.DataFrame(columns=["nba_player_id", "nba_game_id", "team_id",
                                                   "game_date", "played", "listed_inactive",
                                                   "minutes"]),
            "FROM player_game_logs l": pd.DataFrame(columns=["nba_player_id", "game_date",
                                                             "minutes", "stl", "reb", "tov",
                                                             "fg3m"]),
            "WITH predicted": truth,
            "FROM player_game_predictions": predictions,
            "FROM prediction_runs": runs,
        }
        truth_params: list[dict[str, object]] = []

        def read(sql: str, params: dict[str, object] | None = None) -> pd.DataFrame:
            if "WITH predicted" in sql:
                truth_params.append(params or {})
            return next(frame for marker, frame in frames.items() if marker in sql)

        monkeypatch.setattr(cli, "_read_sql", read)
        md, csv = tmp_path / "look.md", tmp_path / "look.csv"

        # act
        code = cli.main(["--look", "dec1", "--md", str(md), "--csv", str(csv)])

        # assert
        text = md.read_text(encoding="utf-8")
        scored = pd.read_csv(csv)
        pooled = scored[scored["run_id"] == pool_label("production", True)]
        coverage = pooled[pooled["endpoint"] == "coverage"]
        comparison = scored[scored["endpoint"] == "v3_vs_v1_delta_pct"]
        split = pooled[pooled["cohort"] == "season_type=Pre Season"]
        assert code == 0
        assert config.PRESEASON_SEASON_TYPE in truth_params[0]["season_types"]
        assert int(coverage["n"].sum()) == 2
        assert (comparison["n"] == 2).all()
        assert "excluded player-games: Pre Season 1" in text
        assert "season types outside the endpoints" in text
        assert (split[split["endpoint"] == "E1_brier"]["n"] == 1).all()

    def test_the_truth_query_filters_on_the_season_types_it_is_given(self):
        # arrange
        import score_runs as cli

        # act + assert
        assert "s.season_type = ANY(%(season_types)s)" in cli.TRUTH_SQL
