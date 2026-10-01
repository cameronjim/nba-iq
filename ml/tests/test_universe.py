from __future__ import annotations

import logging

import pandas as pd
import pytest

from fnba_ml.data import ParquetSource
from fnba_ml.universe import (
    SOURCE_APPROXIMATION,
    SOURCE_STATUS,
    approximate_universe,
    build_universe,
    coverage_report,
    team_game_frame,
    universe_composition,
    universe_from_status,
)

def longest_absence_streak(universe: pd.DataFrame) -> int:
    """longest run of consecutive scheduled team-games a player did not play."""
    v = universe.sort_values(["PLAYER_ID", "TEAM_ID", "GAME_DATE"]).copy()
    key = ["PLAYER_ID", "TEAM_ID"]
    v["_grp"] = (v["PLAYED"] != v.groupby(key)["PLAYED"].shift(1)).cumsum()
    runs = v[v["PLAYED"] == 0].groupby("_grp").size()
    return int(runs.max()) if len(runs) else 0


def test_build_universe_prefers_status_when_present(source: ParquetSource):
    universe = build_universe(source)

    assert (universe["UNIVERSE_SOURCE"] == SOURCE_STATUS).all()


def test_approximation_is_labelled_and_warns(schedule, team_logs, raw_logs, caplog):
    with caplog.at_level(logging.WARNING, logger="fnba_ml.universe"):
        universe = approximate_universe(schedule, team_logs, raw_logs)

    assert (universe["UNIVERSE_SOURCE"] == SOURCE_APPROXIMATION).all()
    assert any("BIASED" in record.message for record in caplog.records), (
        "the fallback must announce itself - a silently biased universe is the "
        "failure mode this whole check exists for"
    )


def test_parquet_source_without_status_warns(tmp_path, fixture_dir, caplog):
    for path in fixture_dir.glob("*.parquet"):
        if path.name.startswith("player_game_status"):
            continue
        (tmp_path / path.name).write_bytes(path.read_bytes())

    with caplog.at_level(logging.WARNING, logger="fnba_ml.data.parquet_source"):
        status = ParquetSource(tmp_path).load_player_game_status()

    assert status is None
    assert any("BIASED" in record.message for record in caplog.records)


def test_status_universe_has_more_rows_than_the_approximation(
    universe_status, universe_approx
):
    assert len(universe_status) > len(universe_approx), (
        "the approximation should drop the long-absence rows the status table keeps"
    )


def test_approximation_over_states_availability(universe_status, universe_approx):
    status_rate = float(universe_status["PLAYED"].mean())
    approx_rate = float(universe_approx["PLAYED"].mean())

    assert approx_rate > status_rate, (
        f"approximation played rate {approx_rate:.4f} should exceed the true "
        f"{status_rate:.4f} - dropping long absences can only inflate it"
    )


def test_approximation_truncates_long_absences(universe_status, universe_approx):
    status_max = longest_absence_streak(universe_status)
    approx_max = longest_absence_streak(universe_approx)

    assert status_max > approx_max, (
        f"longest absence streak: status {status_max}, approximation {approx_max}. "
        f"the approximation is supposed to be unable to represent the long one"
    )


def test_both_universes_cover_every_appearance(universe_status, universe_approx, raw_logs):
    for universe in (universe_status, universe_approx):
        report = coverage_report(universe, raw_logs)
        assert report["appearance_coverage"] == pytest.approx(1.0), (
            "a real game log that is not in the universe is a lost training row"
        )


def test_team_game_frame_is_symmetric(schedule, team_logs):
    tg = team_game_frame(schedule, team_logs)

    assert len(tg) == 2 * schedule["GAME_ID"].nunique()
    assert (tg["TEAM_ID"] != tg["OPP_TEAM_ID"]).all()
    assert tg.groupby("GAME_ID")["IS_HOME"].sum().eq(1).all()

    merged = tg.merge(
        tg[["GAME_ID", "TEAM_ID", "TEAM_PTS"]].rename(
            columns={"TEAM_ID": "OPP_TEAM_ID", "TEAM_PTS": "OPP_SCORED"}
        ),
        on=["GAME_ID", "OPP_TEAM_ID"],
    )
    assert (merged["TEAM_PTS_ALLOWED"] == merged["OPP_SCORED"]).all()


def test_status_universe_keeps_inactive_players_as_scheduled_rows(universe_status, status):
    inactive = status[status["LISTED_INACTIVE"].astype("boolean").fillna(False)]
    assert len(inactive) > 0

    keys = set(zip(universe_status["PLAYER_ID"], universe_status["GAME_ID"]))
    missing = {(p, g) for p, g in zip(inactive["PLAYER_ID"], inactive["GAME_ID"])} - keys

    assert not missing, (
        "a player listed inactive is still a scheduled player-game with PLAYED=0 - "
        "dropping those rows is exactly the selection bias the universe removes"
    )


def test_status_universe_rejects_games_absent_from_the_schedule(
    schedule, team_logs, raw_logs, status, caplog
):
    ghost = status.iloc[[0]].copy()
    ghost["GAME_ID"] = "9999999999"
    polluted = pd.concat([status, ghost], ignore_index=True)

    with caplog.at_level(logging.WARNING, logger="fnba_ml.universe"):
        universe = universe_from_status(schedule, team_logs, raw_logs, polluted)

    assert "9999999999" not in set(universe["GAME_ID"])
    assert any("absent from the schedule" in record.message for record in caplog.records)


def _composition_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "PLAYER_ID": ["1", "2", "3", "4", "5", "6"],
        "PLAYED": [1, 1, 0, 0, 0, 0],
        "LISTED_INACTIVE": pd.array([False, False, True, False, None, False], dtype="boolean"),
        "STATUS_SOURCE": [
            "boxscoresummaryv3+playergamelogs", "boxscoresummaryv3+playergamelogs",
            "boxscoresummaryv3+playergamelogs", "boxscoretraditionalv3",
            "boxscoretraditionalv3", None,
        ],
    })


def test_composition_counts_rows_by_played_and_listed_inactive():
    composition = universe_composition(_composition_frame())

    assert composition["rows"] == 6
    assert composition["by_played_listed_inactive"] == {
        (0, False): 2, (0, None): 1, (0, True): 1, (1, False): 2,
    }
    assert sum(composition["by_played_listed_inactive"].values()) == 6


def test_composition_counts_the_box_score_sourced_rows():
    composition = universe_composition(_composition_frame())

    assert composition["box_score_rows"] == 2


def test_composition_without_a_source_column_reports_none_not_zero():
    frame = _composition_frame().drop(columns=["STATUS_SOURCE"])

    composition = universe_composition(frame)

    assert composition["box_score_rows"] is None
    assert composition["rows"] == 6


def test_status_universe_carries_the_status_source(schedule, team_logs, raw_logs, status):
    tagged = status.assign(STATUS_SOURCE="boxscoresummaryv3+playergamelogs")

    universe = universe_from_status(schedule, team_logs, raw_logs, tagged)

    assert "STATUS_SOURCE" in universe.columns
    assert universe_composition(universe)["box_score_rows"] == 0
