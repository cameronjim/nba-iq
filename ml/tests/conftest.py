from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (str(ML_ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures.generate import generate  # noqa: E402

from fnba_ml.data import ParquetSource  # noqa: E402
from fnba_ml.data.schema import postseason_rows, training_rows  # noqa: E402
from fnba_ml.features import build_features  # noqa: E402
from fnba_ml.universe import (  # noqa: E402
    approximate_universe,
    postseason_appearances,
    universe_from_status,
)


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return generate()


@pytest.fixture(scope="session")
def source(fixture_dir: Path) -> ParquetSource:
    return ParquetSource(fixture_dir, seasons=["2023-24", "2024-25"])


# the history_* frames are what a source loads, playoffs included; the plain ones
# are the training rows every other fixture and test is built from.
@pytest.fixture(scope="session")
def history_logs(source: ParquetSource) -> pd.DataFrame:
    logs = source.load_player_game_logs()
    return logs.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)


@pytest.fixture(scope="session")
def raw_logs(history_logs: pd.DataFrame) -> pd.DataFrame:
    return training_rows(history_logs)


@pytest.fixture(scope="session")
def history_schedule(source: ParquetSource) -> pd.DataFrame:
    return source.load_schedule()


@pytest.fixture(scope="session")
def schedule(history_schedule: pd.DataFrame) -> pd.DataFrame:
    return training_rows(history_schedule)


@pytest.fixture(scope="session")
def history_team_logs(source: ParquetSource) -> pd.DataFrame:
    return source.load_team_game_logs()


@pytest.fixture(scope="session")
def team_logs(history_team_logs: pd.DataFrame) -> pd.DataFrame:
    return training_rows(history_team_logs)


@pytest.fixture(scope="session")
def history_status(source: ParquetSource) -> pd.DataFrame:
    frame = source.load_player_game_status()
    assert frame is not None, "the fixture set must carry a player_game_status file"
    return frame


@pytest.fixture(scope="session")
def status(history_status: pd.DataFrame, history_schedule: pd.DataFrame) -> pd.DataFrame:
    playoff_games = set(postseason_rows(history_schedule)["GAME_ID"])
    return history_status[~history_status["GAME_ID"].isin(playoff_games)].reset_index(drop=True)


@pytest.fixture(scope="session")
def postseason(history_schedule, history_team_logs, history_logs, positions) -> pd.DataFrame:
    return postseason_appearances(history_schedule, history_team_logs, history_logs, positions)


@pytest.fixture(scope="session")
def positions(source: ParquetSource) -> pd.DataFrame:
    frame = source.load_player_positions()
    assert frame is not None, "the fixture set must carry a player_positions file"
    return frame


@pytest.fixture(scope="session")
def universe_status(schedule, team_logs, raw_logs, status, positions) -> pd.DataFrame:
    return universe_from_status(schedule, team_logs, raw_logs, status, positions)


@pytest.fixture(scope="session")
def universe_approx(schedule, team_logs, raw_logs, positions) -> pd.DataFrame:
    return approximate_universe(schedule, team_logs, raw_logs, positions=positions)


@pytest.fixture(scope="session")
def features_status(universe_status) -> pd.DataFrame:
    return build_features(universe_status)


@pytest.fixture(scope="session")
def features_approx(universe_approx) -> pd.DataFrame:
    return build_features(universe_approx)


@pytest.fixture(scope="session", params=["status", "approximation"])
def feats(request, features_status, features_approx) -> pd.DataFrame:
    return features_status if request.param == "status" else features_approx
