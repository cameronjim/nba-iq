from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import run_postseason_bracket as bracket  # noqa: E402
from fnba_ml.features import build_features  # noqa: E402
from fnba_ml.promotion import ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS  # noqa: E402

FIXTURE_ORIGINS = [("F2 valid=2024-12", "2024-12-01", "2024-12-28")]


@pytest.fixture(scope="module")
def switch_on(universe_status, postseason) -> pd.DataFrame:
    return build_features(universe_status, postseason=postseason, include_postseason=True)


@pytest.fixture(scope="module")
def losses(features_status, switch_on) -> tuple[pd.DataFrame, pd.DataFrame]:
    return bracket.score_switch(features_status, switch_on, FIXTURE_ORIGINS)


def test_the_gates_are_written_down() -> None:
    # act + assert
    assert bracket.POSTSEASON_GATED_ENDPOINTS == (
        ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS, "cond_pts_mae",
    )


def test_parse_args_defaults_to_the_two_datasets() -> None:
    # act
    args = bracket.parse_args([])

    # assert
    assert args.baseline.name == "dataset.parquet"
    assert args.candidate.name == "dataset_postseason.parquet"


def test_both_passes_score_identical_rows_and_cohorts(losses) -> None:
    # arrange
    off, on = losses
    order = ["endpoint", "cohort", "origin", "row_key"]

    # act
    a = off.sort_values(order).reset_index(drop=True)
    b = on.sort_values(order).reset_index(drop=True)

    # assert
    assert a[order].equals(b[order])
    assert set(bracket.POSTSEASON_GATED_ENDPOINTS) <= set(a["endpoint"])


def test_the_switch_moves_a_rate_endpoint(losses) -> None:
    # arrange
    off, on = losses
    pick = (off["endpoint"] == "cond_pts_mae") & (off["cohort"] == "ALL")

    # act
    a = off[pick].sort_values("row_key")["loss"].to_numpy()
    b = on[(on["endpoint"] == "cond_pts_mae") & (on["cohort"] == "ALL")].sort_values(
        "row_key"
    )["loss"].to_numpy()

    # assert
    assert len(a) == len(b) > 0
    assert (a != b).any()


def test_datasets_with_different_rows_are_refused(features_status, switch_on) -> None:
    # arrange
    short = switch_on.iloc[1:]

    # act + assert
    with pytest.raises(SystemExit, match="same modelled rows"):
        bracket.align(features_status, short)


def test_the_decision_runs_on_the_fixture(losses) -> None:
    # arrange
    off, on = losses

    # act
    verdict, _ = bracket.decide_comparison(off, on, bracket.POSTSEASON_GATED_ENDPOINTS)

    # assert
    assert isinstance(verdict.reason, str) and verdict.reason
