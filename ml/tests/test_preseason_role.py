from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import build_dataset  # noqa: E402
import run_p3_bracket as p3  # noqa: E402
from fixtures.generate import (  # noqa: E402
    PRESEASON_LATE_STARTER_SLOT,
    RETURNING_PER_TEAM,
    _rosters,
    _team_ids,
)
from fnba_ml import config  # noqa: E402
from fnba_ml.box_context import attach_v6_features  # noqa: E402
from fnba_ml.matchup import attach_v4_features  # noqa: E402
from fnba_ml.preseason_role import (  # noqa: E402
    COHORT_NEW_TEAM,
    COHORT_NO_HISTORY,
    COHORT_SAME_TEAM,
    attach_preseason_role_features,
    preseason_role_features,
    preseason_role_minutes_prior,
    prior_weights,
    regular_season_apps_before,
    roster_cohorts,
)

SEASON = "2024-25"
TEAM = "T1"
OTHER = "T2"
FIXTURE_SEASON_START = [("FS valid=2024-11", "2024-10-20", "2024-11-30")]


def _rows(
    dates: list[str], player: str = "p1", team: str = TEAM, season: str = SEASON,
    played: list[int] | None = None, ewma_min: float = 30.0,
) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame({
        "PLAYER_ID": player,
        "TEAM_ID": team,
        "SEASON": season,
        "GAME_ID": [f"g{player}{i}" for i in range(n)],
        "GAME_DATE": pd.to_datetime(dates),
        "PLAYED": played if played is not None else [1] * n,
        "ewma_MIN": ewma_min,
    })


def _line(player: str, game: str, date: str, minutes: float, started: bool | None,
          team: str = TEAM, season: str = SEASON) -> dict[str, object]:
    return {"PLAYER_ID": player, "GAME_ID": game, "TEAM_ID": team, "SEASON": season,
            "GAME_DATE": pd.Timestamp(date), "MIN": minutes, "STARTED": started}


def _team_game(game: str, date: str, lines: dict[str, tuple[float, bool | None]],
               team: str = TEAM, season: str = SEASON) -> list[dict[str, object]]:
    return [_line(p, game, date, m, s, team, season) for p, (m, s) in lines.items()]


def _pre(*games: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame([line for game in games for line in game])


def _three_games() -> pd.DataFrame:
    # p1 starts and plays 30 in the first game, sits out the second, comes off the
    # bench for 20 in the third; the filler makes each team-game 240 minutes.
    return _pre(
        _team_game("e1", "2024-10-05", {"p1": (30.0, True), "f": (210.0, False)}),
        _team_game("e2", "2024-10-10", {"f": (240.0, False)}),
        _team_game("e3", "2024-10-15", {"p1": (20.0, False), "f": (220.0, True)}),
    )


def test_only_the_last_two_preseason_games_are_read() -> None:
    # arrange
    rows = _rows(["2024-10-22"])

    # act
    role = preseason_role_features(rows, _three_games()).iloc[0]

    # assert
    assert role["pre_games_played"] == 1.0
    assert role["pre_min_share"] == pytest.approx((0.0 + 20.0 / 240.0) / 2)
    assert role["pre_started_rate"] == 0.0
    assert role["pre_min_mean"] == 20.0


def test_share_is_his_minutes_over_the_team_player_minutes() -> None:
    # arrange
    pre = _pre(
        _team_game("e1", "2024-10-05", {"p1": (25.0, True), "f": (175.0, True)}),
        _team_game("e2", "2024-10-10", {"p1": (15.0, None), "f": (225.0, True)}),
    )
    rows = _rows(["2024-10-22"], ewma_min=30.0)

    # act
    role = preseason_role_features(rows, pre).iloc[0]

    # assert
    share = (25.0 / 200.0 + 15.0 / 240.0) / 2
    assert role["pre_min_share"] == pytest.approx(share)
    assert role["pre_min_mean"] == pytest.approx(20.0)
    # the null flag is unknown, not a bench game
    assert role["pre_started_rate"] == 1.0
    assert role["pre_role_delta_min"] == pytest.approx(share * 240.0 - 30.0)


def test_a_row_sees_only_its_own_seasons_preseason() -> None:
    # arrange
    last_season = _pre(
        _team_game("x1", "2023-10-05", {"p1": (30.0, True), "f": (210.0, False)},
                   season="2023-24"),
        _team_game("x2", "2023-10-10", {"p1": (30.0, True), "f": (210.0, False)},
                   season="2023-24"),
    )
    rows = _rows(["2024-10-22"])

    # act
    role = preseason_role_features(rows, last_season).iloc[0]

    # assert
    assert role["pre_games_played"] == 0.0
    assert np.isnan(role["pre_min_share"])
    assert np.isnan(role["pre_dressed_for_current_team"])
    assert np.isnan(role["pre_role_delta_min"])


def test_a_preseason_game_on_or_after_the_row_moves_nothing_before_it() -> None:
    # arrange
    pre = _three_games()
    rows = _rows(["2024-10-12", "2024-10-15", "2024-10-16"])
    flipped = pre.copy()
    flipped.loc[flipped["GAME_ID"] == "e3", "MIN"] = [40.0, 200.0]
    flipped.loc[flipped["GAME_ID"] == "e3", "STARTED"] = [True, False]

    # act
    before = preseason_role_features(rows, pre)
    after = preseason_role_features(rows, flipped)

    # assert
    pd.testing.assert_frame_equal(before.iloc[:2], after.iloc[:2])
    # the counter-assertion: the row after the game does read it
    assert after.loc[2, "pre_min_share"] != before.loc[2, "pre_min_share"]
    assert after.loc[2, "pre_started_rate"] == 0.5


def test_a_player_the_team_played_without_has_a_zero_role() -> None:
    # arrange
    rows = _rows(["2024-10-22"], player="newcomer")

    # act
    role = preseason_role_features(rows, _three_games()).iloc[0]

    # assert
    assert role["pre_games_played"] == 0.0
    assert role["pre_min_share"] == 0.0
    assert role["pre_started_rate"] == 0.0
    assert np.isnan(role["pre_min_mean"])
    assert np.isnan(role["pre_dressed_for_current_team"])


def test_a_team_with_no_preseason_games_reads_nothing() -> None:
    # arrange
    rows = _rows(["2024-10-22"], team="T9")

    # act
    role = preseason_role_features(rows, _three_games()).iloc[0]

    # assert
    assert role["pre_games_played"] == 0.0
    assert role[["pre_min_share", "pre_started_rate", "pre_min_mean"]].isna().all()


def test_dressed_for_current_team_reads_his_last_preseason_box_line() -> None:
    # arrange
    pre = _pre(
        _team_game("e1", "2024-10-05", {"p1": (20.0, False), "f": (220.0, True)}),
        _team_game("o1", "2024-10-12", {"p1": (25.0, True), "g": (215.0, True)},
                   team=OTHER),
    )
    rows = pd.concat([
        _rows(["2024-10-08"]),
        _rows(["2024-10-22"]),
        _rows(["2024-10-22"], team=OTHER),
    ], ignore_index=True)

    # act
    dressed = preseason_role_features(rows, pre)["pre_dressed_for_current_team"]

    # assert
    assert dressed.tolist() == [1.0, 0.0, 1.0]


def test_the_columns_go_neutral_at_the_fade() -> None:
    # arrange
    n = config.PRESEASON_ROLE_FADE_GAMES + 2
    dates = [str((pd.Timestamp("2024-10-22") + pd.Timedelta(days=2 * i)).date())
             for i in range(n)]
    rows = _rows(dates)

    # act
    role = preseason_role_features(rows, _three_games())

    # assert
    fade = config.PRESEASON_ROLE_FADE_GAMES
    assert (role["pre_games_played"].iloc[:fade] == 1.0).all()
    assert role["pre_min_share"].iloc[:fade].notna().all()
    assert (role["pre_games_played"].iloc[fade:] == 0.0).all()
    neutral = [c for c in config.PRESEASON_ROLE_FEATURE_COLS if c != "pre_games_played"]
    assert role[neutral].iloc[fade:].isna().all().all()


def test_the_fade_counts_appearances_not_scheduled_games() -> None:
    # arrange
    played = [1, 0, 1, 1, 0]
    rows = _rows(["2024-10-22", "2024-10-24", "2024-10-26", "2024-10-28", "2024-10-30"],
                 played=played)

    # act
    before = regular_season_apps_before(rows)

    # assert
    assert before.tolist() == [0.0, 1.0, 1.0, 2.0, 3.0]


def test_prior_weights_decay_linearly_to_zero() -> None:
    # arrange
    k = np.array([0.0, 5.0, 9.0, 10.0, 14.0, np.nan])

    # act
    w = prior_weights(k, weight=0.5, games=10)

    # assert
    np.testing.assert_allclose(w, [0.5, 0.25, 0.05, 0.0, 0.0, 0.0])


def test_the_prior_blends_toward_the_preseason_share_only_where_he_played() -> None:
    # arrange
    min_pred = np.array([30.0, 30.0, 30.0, 30.0, 30.0])
    share = np.array([0.1, 0.1, 0.1, 0.0, np.nan])
    played = np.array([2.0, 2.0, 1.0, 0.0, 0.0])
    k = np.array([0.0, 5.0, 10.0, 0.0, 0.0])

    # act
    blended = preseason_role_minutes_prior(min_pred, share, played, k)

    # assert
    np.testing.assert_allclose(blended, [27.0, 28.5, 30.0, 30.0, 30.0])


def test_roster_cohorts_compare_against_the_last_earlier_season_team() -> None:
    # arrange
    frame = pd.DataFrame({
        "PLAYER_ID": ["a", "a", "a", "b", "b", "c"],
        "SEASON": ["2023-24", "2023-24", "2024-25", "2023-24", "2024-25", "2024-25"],
        "TEAM_ID": ["T1", "T2", "T2", "T1", "T3", "T1"],
        "GAME_DATE": pd.to_datetime(["2023-11-01", "2024-03-01", "2024-10-22",
                                     "2023-11-01", "2024-10-22", "2024-10-22"]),
    })

    # act
    cohorts = roster_cohorts(frame)

    # assert
    assert cohorts.tolist() == [
        COHORT_NO_HISTORY, COHORT_NO_HISTORY, COHORT_SAME_TEAM,
        COHORT_NO_HISTORY, COHORT_NEW_TEAM, COHORT_NO_HISTORY,
    ]


def test_the_v7_contract_and_constants_are_written_down() -> None:
    # act + assert
    assert config.PRESEASON_ROLE_GAMES == 2
    assert config.PRESEASON_ROLE_FADE_GAMES == 10
    assert config.PRESEASON_ROLE_PRIOR_GAMES == 10
    assert config.PRESEASON_ROLE_PRIOR_WEIGHT == 0.5
    assert config.PRESEASON_ROLE_FEATURE_COLS == [
        "pre_started_rate", "pre_min_share", "pre_min_mean", "pre_games_played",
        "pre_dressed_for_current_team", "pre_role_delta_min",
    ]
    assert config.FEATURE_SETS["v7-preseason-role"] == (
        config.FEATURE_SETS["v6-context"] + config.PRESEASON_ROLE_FEATURE_COLS
    )
    assert not set(config.PRESEASON_ROLE_FEATURE_COLS) & set(config.FEATURE_COLS)
    assert config.P3_V7_GATED_ENDPOINTS == ("availability_brier", "minutes_mae")
    assert config.P3_PRESEASON_PRIOR_GATED_ENDPOINTS == (
        "minutes_mae_first10", "uncond_pts_mae_first10",
    )
    assert [o for o, *_ in config.PRESEASON_ROLE_ORIGINS] == [
        "S0 valid=2023-10/11", *(o for o, *_ in config.SEASON_START_ORIGINS),
    ]


def test_the_bracket_registers_both_v7_candidates_and_the_v6_reference() -> None:
    # act + assert
    assert p3.SEASON_START_COMPARISONS == (
        p3.COMPARISON_V7, p3.COMPARISON_V6_SEASON_START, p3.COMPARISON_PRIOR,
    )
    assert p3.COMPARISON_GATES[p3.COMPARISON_V7] == config.P3_V7_GATED_ENDPOINTS
    assert p3.COMPARISON_GATES[p3.COMPARISON_PRIOR] == (
        config.P3_PRESEASON_PRIOR_GATED_ENDPOINTS
    )
    assert p3.is_binding(p3.COMPARISON_V7)
    assert p3.is_binding(p3.COMPARISON_PRIOR)
    assert not p3.is_binding(p3.COMPARISON_V6_SEASON_START)
    assert not p3.is_binding(p3.COMPARISON_V6)
    assert not p3.is_binding(p3.COMPARISON_RATE_V6)


def test_build_dataset_attaches_v7_unless_told_not_to() -> None:
    # act + assert
    assert not build_dataset.parse_args([]).no_v7_candidate
    assert build_dataset.parse_args(["--no-v7-candidate"]).no_v7_candidate


@pytest.fixture(scope="module")
def v7_frame(
    features_status: pd.DataFrame, team_logs: pd.DataFrame, box_details: pd.DataFrame,
    preseason_logs: pd.DataFrame,
) -> pd.DataFrame:
    frame = attach_v6_features(attach_v4_features(features_status, team_logs), box_details)
    return attach_preseason_role_features(frame, preseason_logs)


def _opener_row(frame: pd.DataFrame, player: str, team: str) -> pd.Series:
    rows = frame[(frame["PLAYER_ID"] == player) & (frame["TEAM_ID"] == team)
                 & (frame["SEASON"] == "2024-25")]
    return rows.sort_values("GAME_DATE").iloc[0]


def test_the_fixture_reads_the_late_rotation_and_the_moved_player(
    v7_frame: pd.DataFrame,
) -> None:
    # arrange
    teams = _team_ids()
    roster = _rosters()[("2024-25", teams[0])]

    # act
    late_starter = _opener_row(v7_frame, roster[PRESEASON_LATE_STARTER_SLOT], teams[0])
    benched = _opener_row(v7_frame, roster[4], teams[0])
    moved = _opener_row(v7_frame, roster[RETURNING_PER_TEAM], teams[0])
    last_team_2023 = v7_frame[(v7_frame["TEAM_ID"] == teams[-1])
                              & (v7_frame["SEASON"] == "2023-24")]

    # assert
    assert late_starter["pre_started_rate"] == 1.0
    assert benched["pre_started_rate"] == 0.0
    assert moved["pre_dressed_for_current_team"] == 0.0
    assert moved["pre_games_played"] == 1.0
    assert (last_team_2023["pre_games_played"] == 0.0).all()
    assert last_team_2023["pre_min_share"].isna().all()


def test_attaching_v7_keeps_every_row_and_every_other_column(
    features_status: pd.DataFrame, preseason_logs: pd.DataFrame,
) -> None:
    # act
    out = attach_preseason_role_features(features_status, preseason_logs)

    # assert
    assert len(out) == len(features_status)
    pd.testing.assert_frame_equal(out[features_status.columns], features_status)
    assert attach_preseason_role_features(features_status, None) is features_status


@pytest.fixture(scope="module")
def season_start(v7_frame: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    origins = p3.clamp_to_opener(v7_frame, FIXTURE_SEASON_START)
    return p3.score_season_start(v7_frame, origins)


def test_the_season_start_comparisons_score_identical_rows(
    season_start: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
) -> None:
    # act
    for name, (incumbent, candidate) in season_start.items():
        order = ["endpoint", "cohort", "origin", "row_key"]
        a = incumbent.sort_values(order)
        b = candidate.sort_values(order)

        # assert
        assert a["row_key"].tolist() == b["row_key"].tolist(), name
        assert {COHORT_NEW_TEAM, COHORT_NO_HISTORY, COHORT_SAME_TEAM} & set(a["cohort"])
    prior_endpoints = set(season_start[p3.COMPARISON_PRIOR][0]["endpoint"])
    assert prior_endpoints == {
        "minutes_mae", "uncond_pts_mae", "minutes_mae_first10", "uncond_pts_mae_first10",
    }
    v7_endpoints = set(season_start[p3.COMPARISON_V7][0]["endpoint"])
    assert set(config.P3_V7_GATED_ENDPOINTS) <= v7_endpoints


def test_the_prior_changes_no_row_outside_the_first_games(
    v7_frame: pd.DataFrame,
) -> None:
    # arrange: a window that reaches past each player's tenth appearance
    origins = p3.clamp_to_opener(v7_frame, [("FS2", "2024-10-20", "2024-12-28")])

    # act
    incumbent, prior = p3.score_season_start(v7_frame, origins)[p3.COMPARISON_PRIOR]

    # assert
    for frame in (incumbent, prior):
        frame.sort_values(["endpoint", "cohort", "origin", "row_key"], inplace=True)
    every = (incumbent["cohort"] == "ALL") & (incumbent["endpoint"] == "minutes_mae")
    first_keys = set(incumbent.loc[
        (incumbent["cohort"] == "ALL") & (incumbent["endpoint"] == "minutes_mae_first10"),
        "row_key",
    ])
    later = every & ~incumbent["row_key"].isin(first_keys)
    assert later.any()
    np.testing.assert_allclose(prior.loc[later.to_numpy(), "loss"].to_numpy(),
                               incumbent.loc[later.to_numpy(), "loss"].to_numpy())
    moved = every & incumbent["row_key"].isin(first_keys)
    assert not np.allclose(prior.loc[moved.to_numpy(), "loss"].to_numpy(),
                           incumbent.loc[moved.to_numpy(), "loss"].to_numpy())
