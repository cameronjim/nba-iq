"""the served v7-preseason-role columns: serving builds what the dataset build builds."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from predict import rebuild_context
from fnba_ml import config
from fnba_ml.box_context import attach_v6_features, teammate_start_features
from fnba_ml.data.schema import STAT_COLS
from fnba_ml.matchup import attach_serving_stakes, attach_v4_features
from fnba_ml.preseason_role import (
    attach_preseason_role_features,
    attach_serving_preseason_role,
)
from fnba_ml.prospective import (
    SOURCE_PROSPECTIVE,
    attach_serving_v7_columns,
    build_prospective_features,
)
from fnba_ml.universe import TEAM_TOTAL_COLS

KEY = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
SERVED_ONLY_COLS: list[str] = [
    *config.V5_STAKES_FEATURE_COLS,
    *config.V6_CONTEXT_FEATURE_COLS,
    *config.PRESEASON_ROLE_FEATURE_COLS,
]


class _FixedBaseModel:
    """a stage-1 model that returns the dataset's own p_j for each row."""

    def __init__(self, p: dict[tuple[str, str], float], cutoff: pd.Timestamp) -> None:
        self.p = p
        self.cutoff = cutoff

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        keys = zip(frame["PLAYER_ID"].astype(str), frame["GAME_ID"].astype(str))
        return np.array([self.p[k] for k in keys], dtype=float)


@pytest.fixture(scope="module")
def dataset(
    features_status: pd.DataFrame, team_logs: pd.DataFrame, box_details: pd.DataFrame,
    preseason_logs: pd.DataFrame,
) -> pd.DataFrame:
    """the frame build_dataset.py writes: base, v4, v6 and v7 families attached."""
    frame = attach_v6_features(attach_v4_features(features_status, team_logs), box_details)
    return attach_preseason_role_features(frame, preseason_logs)


def _as_unplayed(day: pd.DataFrame) -> pd.DataFrame:
    """a played date rewritten the way prospective_universe writes an unplayed one."""
    out = day.copy()
    out["PLAYED"] = 0
    for column in STAT_COLS:
        out[column] = 0.0
    for column in ("TEAM_PTS", "TEAM_PTS_ALLOWED", *TEAM_TOTAL_COLS.values()):
        if column in out.columns:
            out[column] = np.nan
    out["LISTED_INACTIVE"] = pd.array([pd.NA] * len(out), dtype="boolean")
    out["UNIVERSE_SOURCE"] = SOURCE_PROSPECTIVE
    return out


def _served(
    universe: pd.DataFrame, dataset: pd.DataFrame, game_date: pd.Timestamp,
    team_logs: pd.DataFrame, box: pd.DataFrame, preseason: pd.DataFrame,
) -> pd.DataFrame:
    """the serving path for one date: history before it, that date unplayed."""
    dates = pd.to_datetime(universe["GAME_DATE"])
    history = universe[dates < game_date].reset_index(drop=True)
    future = _as_unplayed(universe[dates == game_date])
    built = build_prospective_features(history, future)
    # the full inputs on purpose: the builders must drop everything from game_date on.
    served = attach_serving_v7_columns(built, history, team_logs, box, preseason, game_date)
    day = dataset[pd.to_datetime(dataset["GAME_DATE"]) == game_date]
    p = {
        (str(r.PLAYER_ID), str(r.GAME_ID)): float(r.P_CONTEXT)
        for r in day[["PLAYER_ID", "GAME_ID", config.P_CONTEXT]].itertuples(index=False)
    }
    base = _FixedBaseModel(p, game_date - pd.Timedelta(days=1))
    rebuilt, _ = rebuild_context(served, base, None, game_date)
    return rebuilt


def _season_dates(dataset: pd.DataFrame, season: str) -> list[pd.Timestamp]:
    dates = pd.to_datetime(dataset.loc[dataset["SEASON"] == season, "GAME_DATE"])
    return sorted(dates.unique())


@pytest.mark.parametrize("ordinal", [0, 1, 4, 14])
def test_serving_builds_every_v7_column_exactly_as_the_dataset_does(
    universe_status: pd.DataFrame, dataset: pd.DataFrame, team_logs: pd.DataFrame,
    box_details: pd.DataFrame, preseason_logs: pd.DataFrame, ordinal: int,
) -> None:
    # arrange
    game_date = pd.Timestamp(_season_dates(dataset, "2024-25")[ordinal])
    expected = (
        dataset[pd.to_datetime(dataset["GAME_DATE"]) == game_date]
        .set_index(KEY).sort_index()
    )

    # act
    served = _served(universe_status, dataset, game_date, team_logs, box_details,
                     preseason_logs).set_index(KEY).sort_index()

    # assert
    assert served.index.equals(expected.index)
    for column in config.FEATURE_SETS[config.PROSPECTIVE_FEATURE_SET]:
        np.testing.assert_allclose(
            served[column].to_numpy(dtype=float),
            expected[column].to_numpy(dtype=float),
            rtol=1e-9, atol=1e-9, equal_nan=True,
            err_msg=f"{column} differs between serving and the dataset on {game_date.date()}",
        )


def test_the_parity_dates_exercise_live_and_faded_preseason_columns(
    dataset: pd.DataFrame,
) -> None:
    # arrange
    dates = _season_dates(dataset, "2024-25")

    # act
    opener = dataset[pd.to_datetime(dataset["GAME_DATE"]) == dates[0]]
    late = dataset[pd.to_datetime(dataset["GAME_DATE"]) == dates[14]]

    # assert
    assert (opener["pre_games_played"] > 0).any()
    assert opener["pre_min_share"].notna().any()
    assert opener["started_rate_10"].notna().any()
    assert late["team_win_pct"].notna().all()
    # some players have reached the fade by then and some have not, so the fade's
    # count of earlier appearances is what the last parity date tests.
    assert (late["pre_games_played"] == 0).any()
    assert (late["pre_games_played"] > 0).any()


def test_a_preseason_line_on_or_after_the_cutoff_is_never_read(
    universe_status: pd.DataFrame, dataset: pd.DataFrame, preseason_logs: pd.DataFrame,
) -> None:
    # arrange
    game_date = pd.Timestamp(_season_dates(dataset, "2024-25")[0])
    rows = dataset[pd.to_datetime(dataset["GAME_DATE"]) == game_date].reset_index(drop=True)
    history = universe_status[pd.to_datetime(universe_status["GAME_DATE"]) < game_date]
    late = preseason_logs[preseason_logs["SEASON"] == "2024-25"].copy()
    late["GAME_ID"] = late["GAME_ID"] + "x"
    late["GAME_DATE"] = game_date
    late["MIN"] = 48.0

    # act
    honest = attach_serving_preseason_role(rows, history, preseason_logs, game_date)
    leaky = attach_serving_preseason_role(
        rows, history, pd.concat([preseason_logs, late], ignore_index=True), game_date
    )

    # assert
    pd.testing.assert_frame_equal(honest, leaky)


def test_a_team_log_on_the_cutoff_date_moves_no_stakes_column(
    universe_status: pd.DataFrame, dataset: pd.DataFrame, team_logs: pd.DataFrame,
) -> None:
    # arrange
    dates = _season_dates(dataset, "2024-25")
    game_date = pd.Timestamp(dates[3])
    universe = universe_status
    history = universe[pd.to_datetime(universe["GAME_DATE"]) < game_date]
    built = build_prospective_features(
        history, _as_unplayed(universe[pd.to_datetime(universe["GAME_DATE"]) == game_date])
    )
    before = team_logs[pd.to_datetime(team_logs["GAME_DATE"]) < game_date]

    # act
    with_future = attach_serving_stakes(built, team_logs, game_date)
    without = attach_serving_stakes(built, before, game_date)

    # assert
    pd.testing.assert_frame_equal(
        with_future[config.V5_STAKES_FEATURE_COLS], without[config.V5_STAKES_FEATURE_COLS]
    )


def test_without_preseason_logs_the_columns_take_their_no_preseason_values(
    features_status: pd.DataFrame,
) -> None:
    # arrange
    rows = features_status.head(20)

    # act
    out = attach_serving_preseason_role(rows, features_status, None, pd.Timestamp("2030-01-01"))

    # assert
    assert (out["pre_games_played"] == 0.0).all()
    others = [c for c in config.PRESEASON_ROLE_FEATURE_COLS if c != "pre_games_played"]
    assert out[others].isna().all().all()


def test_serving_refuses_a_frame_without_box_details(
    features_status: pd.DataFrame, team_logs: pd.DataFrame,
) -> None:
    # act + assert
    with pytest.raises(ValueError, match="box-score details"):
        attach_serving_v7_columns(
            features_status.head(5), features_status, team_logs, None, None,
            pd.Timestamp("2030-01-01"),
        )


class _PlayerBaseModel:
    def __init__(self, p: dict[str, float]) -> None:
        self.p = p
        self.cutoff = pd.Timestamp("2026-03-01")

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return frame["PLAYER_ID"].map(self.p).to_numpy(dtype=float)


def _team_game() -> pd.DataFrame:
    return pd.DataFrame({
        "PLAYER_ID": ["star", "starter", "bench"],
        "GAME_ID": ["g1"] * 3,
        "TEAM_ID": ["t1"] * 3,
        "GAME_DATE": pd.to_datetime(["2026-03-02"] * 3),
        "POS_GROUP": ["G", "F", "G"],
        "tm_MIN": [34.0, 28.0, 12.0],
        "tm_FGA": [20.0, 10.0, 5.0],
        "tm_USG": [32.0, 18.0, 14.0],
        "magnitude_ess": [20.0, 20.0, 20.0],
        "n_appearances": [500, 300, 200],
        "avail_rate_10": [0.9, 0.9, 0.9],
        "started_rate_10": [1.0, 0.8, 0.1],
    })


def test_rebuild_context_builds_the_teammate_start_columns_from_the_corrected_p() -> None:
    # arrange
    frame = _team_game()
    base = _PlayerBaseModel({"star": 0.95, "starter": 0.9, "bench": 0.9})
    statuses = pd.DataFrame({
        "nba_player_id": ["star"],
        "status_normalized": ["out"],
        "captured_at": ["2026-03-01T18:00:00+00:00"],
    })
    as_of = pd.Timestamp("2026-03-02T00:00:00")

    # act
    rebuilt, _ = rebuild_context(frame, base, statuses, as_of)

    # assert: the star is out at 0.02, so the bench player loses 0.98 + 0.1 starters.
    assert rebuilt.loc[2, "team_starters_out_exp"] == pytest.approx(0.98 + 0.1)
    assert rebuilt.loc[2, "exp_vacated_starts"] == pytest.approx(0.98 * 1.0 + 0.1 * 0.8)
    assert rebuilt.loc[0, "team_starters_out_exp"] == pytest.approx(0.1)
    expected = teammate_start_features(rebuilt.drop(
        columns=["team_starters_out_exp", "exp_vacated_starts"]
    ))
    pd.testing.assert_frame_equal(rebuilt, expected)


def test_rebuild_context_leaves_a_frame_without_the_start_columns_alone() -> None:
    # arrange
    frame = _team_game().drop(columns=["started_rate_10"])
    base = _PlayerBaseModel({"star": 0.95, "starter": 0.9, "bench": 0.9})

    # act
    rebuilt, _ = rebuild_context(frame, base, None, pd.Timestamp("2026-03-02"))

    # assert
    assert "team_starters_out_exp" not in rebuilt.columns
    assert "exp_vacated_starts" not in rebuilt.columns
