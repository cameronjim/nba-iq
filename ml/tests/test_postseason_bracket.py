from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import build_dataset  # noqa: E402
import run_postseason_bracket as bracket  # noqa: E402
from fnba_ml import config  # noqa: E402
from fnba_ml.data.schema import training_rows  # noqa: E402
from fnba_ml.features import build_features  # noqa: E402
from fnba_ml.promotion import ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS  # noqa: E402
from fnba_ml.universe import build_postseason_appearances  # noqa: E402

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


class _StatusSnapshot:
    """a source whose player_game_status lacks the given games' rows, as before a backfill."""

    def __init__(self, inner, missing_games: set[str]) -> None:
        self._inner = inner
        self._missing = missing_games

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def load_player_game_status(self) -> pd.DataFrame:
        status = self._inner.load_player_game_status()
        return status[~status["GAME_ID"].astype(str).isin(self._missing)].reset_index(drop=True)


@pytest.fixture(scope="module")
def baseline_parquet(source, tmp_path_factory) -> Path:
    games = training_rows(source.load_schedule()).sort_values("GAME_DATE")["GAME_ID"]
    late = set(games.astype(str).tail(3))
    universe = build_dataset.load_universe(_StatusSnapshot(source, late))
    path = tmp_path_factory.mktemp("baseline") / "dataset.parquet"
    build_features(universe).to_parquet(path, index=False)
    return path


def test_a_requeried_universe_gains_the_backfilled_rows(source, baseline_parquet) -> None:
    # arrange
    baseline = pd.read_parquet(baseline_parquet)

    # act
    requeried = build_dataset.load_universe(source)

    # assert
    assert len(requeried) > len(baseline)


def test_the_candidate_universe_is_the_baseline_after_the_status_source_grows(
    source, baseline_parquet,
) -> None:
    # arrange
    baseline = pd.read_parquet(baseline_parquet)
    postseason = build_postseason_appearances(source)

    # act
    universe = build_dataset.load_universe(source, baseline_parquet)
    candidate = build_features(universe, postseason=postseason, include_postseason=True)
    a, b = bracket.align(baseline, candidate)

    # assert
    assert len(universe) == len(baseline)
    assert a[bracket.KEY].astype(str).equals(b[bracket.KEY].astype(str))
    assert a["PLAYED"].equals(b["PLAYED"])


def test_build_dataset_takes_a_universe_from_option() -> None:
    # act
    args = build_dataset.parse_args(["--universe-from", "data/dataset.parquet"])

    # assert
    assert args.universe_from == Path("data/dataset.parquet")
    assert build_dataset.parse_args([]).universe_from is None


SELECTION_HOLDOUT_START = pd.Timestamp("2026-02-01")


def _season_label(date: pd.Timestamp) -> str:
    year = date.year if date.month >= 7 else date.year - 1
    return f"{year}-{str(year + 1)[2:]}"


def test_parse_args_defaults_to_the_dev_origins() -> None:
    # act
    args = bracket.parse_args([])

    # assert
    assert args.origins == "dev"
    assert bracket.ORIGIN_SETS["dev"] == config.ORIGINS


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("dev", ["dev"]), ("season-start", ["season-start"]), ("both", ["dev", "season-start"])],
)
def test_the_origin_choices_parse(choice: str, expected: list[str]) -> None:
    # act
    args = bracket.parse_args(["--origins", choice])

    # assert
    assert bracket.origin_set_names(args.origins) == expected


def test_an_unknown_origin_set_is_refused() -> None:
    # act + assert
    with pytest.raises(SystemExit):
        bracket.parse_args(["--origins", "october"])


def test_the_season_start_origins_stay_out_of_the_selection_origins() -> None:
    # arrange
    names = {name for name, *_ in config.SEASON_START_ORIGINS}

    # act
    overlap = names & {name for name, *_ in config.DEV_ORIGINS}

    # assert
    assert len(config.ORIGINS) == 5
    assert not overlap
    assert bracket.ORIGIN_SETS["season-start"] == config.SEASON_START_ORIGINS


@pytest.mark.parametrize("origin", config.SEASON_START_ORIGINS, ids=lambda o: o[0])
def test_a_season_start_window_is_before_the_holdout_with_two_seasons_behind_it(
    origin: tuple[str, str, str],
) -> None:
    # arrange
    _, start, end = origin
    start, end = pd.Timestamp(start), pd.Timestamp(end)

    # act
    season = _season_label(start)
    seasons_before = config.SEASONS.index(season)

    # assert
    assert start < end < SELECTION_HOLDOUT_START
    assert _season_label(end) == season
    assert start.month == 10 and end.month == 11
    assert seasons_before >= 2


def _opener_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "SEASON": ["2023-24", "2023-24", "2023-24", "2024-25", "2024-25"],
        "GAME_DATE": pd.to_datetime(
            ["2023-10-24", "2024-04-14", "2024-05-10", "2024-10-22", "2024-11-15"]
        ),
        "COMPETITION": ["regular", "regular", "playoffs", "regular", "regular"],
    })


@pytest.mark.parametrize("typed", ["2024-04-01", "2024-10-01", "2024-10-22", "2024-10-29"])
def test_a_typed_start_is_clamped_to_the_season_opener(typed: str) -> None:
    # arrange
    frame = _opener_frame()

    # act
    [(name, start, end)] = bracket.clamp_to_opener(frame, [("S", typed, "2024-11-30")])

    # assert
    assert (name, start, end) == ("S", "2024-10-22", "2024-11-30")


def test_the_opener_ignores_a_postseason_row() -> None:
    # arrange
    frame = _opener_frame()
    frame.loc[3, "COMPETITION"] = "playoffs"

    # act
    opener = bracket.season_opener(frame, "2024-11-30")

    # assert
    assert opener == pd.Timestamp("2024-11-15")


def _phase_losses(offset: float) -> pd.DataFrame:
    dates = pd.date_range("2024-10-22", periods=20, freq="D")
    return pd.DataFrame({
        "origin": "S",
        "endpoint": ENDPOINT_MINUTES,
        "row_key": [f"p{i}" for i in range(20)],
        "GAME_DATE": dates,
        "cohort": "ALL",
        "loss": [2.0 + offset if i < 14 else 4.0 for i in range(20)],
    })


def test_phase_of_splits_on_day_fourteen() -> None:
    # arrange
    dates = pd.Series(pd.to_datetime(["2024-10-22", "2024-11-04", "2024-11-05"]))

    # act
    phases = bracket.phase_of(dates, pd.Timestamp("2024-10-22"))

    # assert
    assert list(phases) == [bracket.EARLY_PHASE, bracket.EARLY_PHASE, bracket.LATE_PHASE]


def test_the_phase_table_arithmetic_on_a_synthetic_frame() -> None:
    # arrange
    off, on = _phase_losses(0.0), _phase_losses(-0.5)
    starts = {"S": pd.Timestamp("2024-10-22")}

    # act
    table = bracket.phase_table(off, on, ENDPOINT_MINUTES, starts)

    # assert
    early = table[(table["origin"] == "S") & (table["phase"] == bracket.EARLY_PHASE)].iloc[0]
    late = table[(table["origin"] == "S") & (table["phase"] == bracket.LATE_PHASE)].iloc[0]
    assert (early["n"], early["incumbent"], early["candidate"]) == (14, 2.0, 1.5)
    assert early["delta_pct"] == pytest.approx(-0.25)
    assert (late["n"], late["incumbent"], late["candidate"]) == (6, 4.0, 4.0)
    assert late["delta_pct"] == pytest.approx(0.0)
    assert set(table["origin"]) == {"S", bracket.POOLED_ORIGIN}


def test_the_stem_names_the_origin_set() -> None:
    # act
    stems = {s: bracket.report_stem("v1", s) for s in bracket.ORIGIN_SETS}

    # assert
    assert stems == {"dev": "v1_postseason_dev", "season-start": "v1_postseason_season-start"}


def test_a_repeat_look_on_one_origin_set_is_refused(tmp_path) -> None:
    # arrange
    (tmp_path / "v1_postseason_season-start_decision.csv").write_text("x", encoding="utf-8")

    # act + assert
    with pytest.raises(SystemExit, match="one look"):
        bracket.main(["--origins", "both", "--version", "v1", "--reports-dir", str(tmp_path)])


def test_a_dev_look_does_not_block_the_season_start_look(tmp_path) -> None:
    # arrange
    (tmp_path / "v1_postseason_dev_decision.csv").write_text("x", encoding="utf-8")
    missing = tmp_path / "absent.parquet"

    # act + assert
    with pytest.raises(SystemExit, match="dataset not found"):
        bracket.main(["--origins", "season-start", "--version", "v1",
                      "--reports-dir", str(tmp_path),
                      "--baseline", str(missing), "--candidate", str(missing)])


def test_the_season_start_set_runs_on_the_fixture(features_status, switch_on) -> None:
    # arrange
    origins = bracket.clamp_to_opener(features_status, [config.SEASON_START_ORIGINS[0]])
    starts = {name: pd.Timestamp(vstart) for name, vstart, _ in origins}
    opener = features_status.loc[features_status["SEASON"] == "2024-25", "GAME_DATE"].min()

    # act
    off, on = bracket.score_switch(features_status, switch_on, origins)
    table = bracket.phase_table(off, on, ENDPOINT_MINUTES, starts)

    # assert
    assert starts[origins[0][0]] == pd.Timestamp(opener).normalize()
    assert pd.to_datetime(off["GAME_DATE"]).min() == starts[origins[0][0]]
    assert set(table["phase"]) == {bracket.EARLY_PHASE, bracket.LATE_PHASE}
