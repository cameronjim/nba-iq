"""postseason appearances: loaded as history, never modelled, behind one switch (MODEL.md 20.1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fixtures.generate import POSTSEASON_MINUTES, POSTSEASON_PTS_PER_MIN, generate
from fnba_ml import config
from fnba_ml.data import ParquetSource
from fnba_ml.data.postgres_source import TEAM_LOGS_SQL, PostgresSource
from fnba_ml.data.schema import postseason_rows, training_rows
from fnba_ml.features import (
    build_features,
    career_history,
    player_appearance_features,
    rate_column,
)
from fnba_ml.prospective import load_postseason_sidecar, postseason_sidecar_path
from fnba_ml.universe import (
    approximate_universe,
    build_universe,
    universe_from_status,
)

PLAYOFF_PREFIX = "004"
# slot 0 of the first team: in both seasons' rosters and in every fixture playoff game.
PLAYOFF_PLAYER = "2000"

SEASON_SCOPED_COLUMNS = (
    *(f"std_{stat}" for stat in config.ROLL_STATS),
    "season_appearances",
    "avail_rate_std",
    *(f"uncond_std_{stat}" for stat in config.UNCOND_STATS),
    *(f"avail_rate_{w}" for w in config.AVAIL_WINDOWS),
    "games_since_last_app",
)


def _hand_ewma_rate(appearances: pd.DataFrame, stat: str) -> float:
    rows = appearances.sort_values("GAME_DATE")
    ratio = rows[stat].to_numpy(float) / np.maximum(
        rows["MIN"].to_numpy(float), config.RATE_MINUTES_FLOOR
    )
    decay = 0.5 ** (1.0 / config.rate_halflife(stat))
    weights = decay ** np.arange(len(ratio) - 1, -1, -1, dtype=float)
    return float((weights * ratio).sum() / weights.sum())


def _first_row(features: pd.DataFrame, player: str, season: str) -> pd.Series:
    rows = features[(features["PLAYER_ID"] == player) & (features["SEASON"] == season)]
    return rows.sort_values("GAME_DATE").iloc[0]


@pytest.fixture(scope="module")
def features_postseason_on(universe_status, postseason) -> pd.DataFrame:
    return build_features(universe_status, postseason=postseason, include_postseason=True)


class TestSourcesLoadHistory:
    def test_the_parquet_fixture_carries_playoff_rows_labelled_as_such(self, history_logs):
        # arrange
        playoffs = history_logs[history_logs["GAME_ID"].str.startswith(PLAYOFF_PREFIX)]

        # act + assert
        assert len(playoffs) > 0
        assert set(playoffs["COMPETITION"]) == {"playoffs"}
        assert set(playoffs["SEASON_TYPE"]) == {"Playoffs"}
        assert set(training_rows(history_logs)["SEASON_TYPE"]) == {"Regular Season"}

    def test_the_parquet_schedule_keeps_the_playoff_games(self, history_schedule):
        # act
        games = postseason_rows(history_schedule)

        # assert
        assert len(games) == 4
        assert set(games["SEASON_TYPE"]) == {"Playoffs"}

    def test_postgres_loads_every_history_season_type_by_default(self):
        # act
        source = PostgresSource(database_url="postgresql://unused")

        # assert
        assert source.season_types == list(config.HISTORY_SEASON_TYPES)
        assert "tgl.season_type" in TEAM_LOGS_SQL

    def test_training_stays_regular_season(self):
        # act + assert
        assert config.TRAINING_SEASON_TYPES == ("Regular Season",)
        assert config.SEASON_TYPES == ["Regular Season"]
        assert config.HISTORY_SEASON_TYPES == ("Regular Season", "PlayIn", "Playoffs")
        assert config.TRAINING_COMPETITIONS == ("regular",)


class TestUniverseNeverHoldsPlayoffRows:
    def test_build_universe_drops_the_playoff_games(self, source):
        # act
        universe = build_universe(source)

        # assert
        assert not universe["GAME_ID"].str.startswith(PLAYOFF_PREFIX).any()
        assert set(universe["COMPETITION"]) == {"regular"}

    def test_the_status_universe_drops_them_from_unfiltered_frames(
        self, history_schedule, history_team_logs, history_logs, history_status, positions
    ):
        # act
        universe = universe_from_status(
            history_schedule, history_team_logs, history_logs, history_status, positions
        )

        # assert
        assert not universe["GAME_ID"].str.startswith(PLAYOFF_PREFIX).any()

    def test_the_approximate_universe_drops_them_too(
        self, history_schedule, history_team_logs, history_logs, positions
    ):
        # act
        universe = approximate_universe(
            history_schedule, history_team_logs, history_logs, positions=positions
        )

        # assert
        assert not universe["GAME_ID"].str.startswith(PLAYOFF_PREFIX).any()

    def test_a_playoff_row_handed_in_as_a_modelled_row_is_refused(
        self, universe_status, postseason
    ):
        # arrange
        polluted = pd.concat([universe_status, postseason], ignore_index=True)

        # act + assert
        with pytest.raises(ValueError, match="postseason"):
            build_features(polluted)

    def test_postseason_appearances_are_played_playoff_rows_only(self, postseason):
        # act + assert
        assert len(postseason) == 2 * 2 * 2 * 5
        assert set(postseason["COMPETITION"]) == {"playoffs"}
        assert (postseason["PLAYED"] == 1).all()
        assert postseason["TEAM_MIN"].notna().all()


class TestSwitchOffChangesNothing:
    def test_the_switch_defaults_off(self):
        # act + assert
        assert config.RATE_HISTORY_INCLUDES_POSTSEASON is False

    def test_passing_postseason_with_the_switch_off_is_a_no_op(
        self, universe_status, postseason, features_status
    ):
        # act
        built = build_features(universe_status, postseason=postseason)

        # assert
        pd.testing.assert_frame_equal(built, features_status, check_exact=True)

    def test_the_fixture_without_playoffs_builds_byte_identical_features(
        self, tmp_path: Path, features_status
    ):
        # arrange
        legacy_dir = generate(tmp_path, with_postseason=False)
        legacy = ParquetSource(legacy_dir, seasons=["2023-24", "2024-25"])
        universe = universe_from_status(
            legacy.load_schedule(), legacy.load_team_game_logs(),
            legacy.load_player_game_logs(), legacy.load_player_game_status(),
            legacy.load_player_positions(),
        )

        # act
        built = build_features(universe)

        # assert
        pd.testing.assert_frame_equal(
            features_status[list(built.columns)], built, check_exact=True
        )


class TestSwitchOn:
    def test_the_ewma_rate_reads_the_playoff_games_and_matches_a_hand_computation(
        self, universe_status, postseason, features_status, features_postseason_on
    ):
        # arrange
        target = _first_row(features_postseason_on, PLAYOFF_PLAYER, "2024-25")
        before = universe_status[
            (universe_status["PLAYER_ID"] == PLAYOFF_PLAYER)
            & (universe_status["PLAYED"] == 1)
            & (universe_status["MIN"] > 0)
            & (universe_status["GAME_DATE"] < target["GAME_DATE"])
        ]
        playoffs = postseason[
            (postseason["PLAYER_ID"] == PLAYOFF_PLAYER)
            & (postseason["GAME_DATE"] < target["GAME_DATE"])
        ]
        expected = _hand_ewma_rate(pd.concat([before, playoffs]), "PTS")

        # act
        on = target[rate_column("PTS")]
        off = _first_row(features_status, PLAYOFF_PLAYER, "2024-25")[rate_column("PTS")]

        # assert
        assert len(playoffs) == 2
        assert on == pytest.approx(expected, rel=1e-12)
        assert off == pytest.approx(_hand_ewma_rate(before, "PTS"), rel=1e-12)
        assert on > off  # the fixture's playoff games run at 0.9 points a minute

    def test_career_counts_include_the_playoff_games(
        self, features_status, features_postseason_on
    ):
        # act
        on = _first_row(features_postseason_on, PLAYOFF_PLAYER, "2024-25")
        off = _first_row(features_status, PLAYOFF_PLAYER, "2024-25")

        # assert
        assert on["n_appearances"] == off["n_appearances"] + 2

    def test_no_row_is_added_or_dropped(self, features_status, features_postseason_on):
        # act + assert
        assert len(features_postseason_on) == len(features_status)
        pd.testing.assert_frame_equal(
            features_postseason_on[["GAME_ID", "TEAM_ID", "PLAYER_ID"]],
            features_status[["GAME_ID", "TEAM_ID", "PLAYER_ID"]],
        )

    def test_the_fixture_playoff_line_is_what_the_rate_reads(self, postseason):
        # act
        rows = postseason[postseason["PLAYER_ID"] == PLAYOFF_PLAYER]

        # assert
        assert (rows["MIN"] == POSTSEASON_MINUTES).all()
        assert (rows["PTS"] / rows["MIN"]).to_numpy() == pytest.approx(
            [POSTSEASON_PTS_PER_MIN] * len(rows)
        )


class TestSeasonScopedColumnsStayRegularSeason:
    def test_the_season_frame_never_holds_a_playoff_appearance(self, universe_status, postseason):
        # arrange
        history = career_history(universe_status, postseason, include_postseason=True)

        # act
        career, season = player_appearance_features(universe_status, history)

        # assert
        playoff_dates = set(postseason["GAME_DATE"])
        assert career["GAME_DATE"].isin(playoff_dates).sum() == len(postseason)
        assert season["GAME_DATE"].isin(playoff_dates).sum() == 0

    def test_mid_season_playoff_rows_move_career_columns_but_no_season_column(
        self, universe_status, postseason, features_status
    ):
        # arrange: the same playoff lines, relabelled into the middle of 2024-25, so
        # a season-to-date mean would read them if it could
        planted = postseason.copy()
        planted["SEASON"] = "2024-25"
        start = pd.Timestamp("2024-11-02")
        planted["GAME_DATE"] = planted["GAME_ID"].map(
            {g: start + pd.Timedelta(days=i) for i, g in enumerate(sorted(planted["GAME_ID"].unique()))}
        )

        # act
        on = build_features(universe_status, postseason=planted, include_postseason=True)

        # assert
        for column in SEASON_SCOPED_COLUMNS:
            pd.testing.assert_series_equal(on[column], features_status[column], check_exact=True)
        later = (on["PLAYER_ID"] == PLAYOFF_PLAYER) & (on["GAME_DATE"] > pd.Timestamp("2024-11-10"))
        assert not np.allclose(
            on.loc[later, rate_column("PTS")], features_status.loc[later, rate_column("PTS")]
        )


class TestPostseasonSidecar:
    def test_the_sidecar_sits_beside_the_dataset(self, tmp_path: Path):
        # act
        path = postseason_sidecar_path(tmp_path / "dataset.parquet")

        # assert
        assert path == tmp_path / "dataset_postseason.parquet"

    def test_a_dataset_without_a_sidecar_has_no_postseason(self, tmp_path: Path):
        # act + assert
        assert load_postseason_sidecar(tmp_path / "dataset.parquet") is None

    def test_the_sidecar_round_trips(self, tmp_path: Path, postseason):
        # arrange
        dataset = tmp_path / "dataset.parquet"
        postseason.to_parquet(postseason_sidecar_path(dataset), index=False)

        # act
        loaded = load_postseason_sidecar(dataset)

        # assert
        assert loaded is not None
        assert len(loaded) == len(postseason)
        assert loaded["GAME_DATE"].dtype.kind == "M"
