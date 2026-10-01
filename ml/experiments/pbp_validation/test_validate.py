"""tests for the pure checks in validate.py, on synthetic event and stint frames."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

EXPERIMENT_DIR = Path(__file__).resolve().parent
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from validate import (  # noqa: E402
    build_stints,
    check_durations,
    check_five_man,
    compute_verdict,
    expected_team_minutes,
    parse_box_minutes,
    reconcile_players,
    render_report,
    summarize_games,
)

GAME = "0022500001"
HOME = 1
FIVE_A = [1, 2, 3, 4, 5]
FIVE_B = [1, 2, 3, 4, 6]


def stint(team_id: int, period: int, start: float, end: float,
          players: list[int], game_id: str = GAME) -> dict[str, object]:
    return {
        "game_id": game_id, "team_id": team_id, "period": period, "stint_index": 0,
        "start_elapsed": start, "end_elapsed": end, "start_clock": "",
        "end_clock": "", "duration_seconds": end - start,
        "n_players": len(players), "players": players,
    }


def full_game_stints(team_id: int, players: list[int]) -> list[dict[str, object]]:
    return [stint(team_id, period, 0.0, 720.0, players) for period in range(1, 5)]


def event(order: int, period: int, remaining: float, players: list[int],
          team_id: int = HOME) -> dict[str, object]:
    return {
        "game_id": GAME, "period": period, "event_order": order,
        "seconds_remaining": remaining, "team_id": team_id, "players": players,
    }


def expected_frame(*team_ids: int, minutes: float = 240.0) -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": [GAME] * len(team_ids), "team_id": list(team_ids),
        "expected_minutes": [minutes] * len(team_ids),
    })


class TestBuildStints:
    def test_substitution_split_into_out_and_in_leaves_no_four_man_stint(self) -> None:
        # arrange: player 5 out and 6 in at 400s remaining, as the live feed records it
        events = pd.DataFrame([
            event(0, 1, 720.0, FIVE_A),
            event(1, 1, 500.0, FIVE_A),
            event(2, 1, 400.0, [1, 2, 3, 4]),
            event(3, 1, 400.0, FIVE_B),
            event(4, 1, 100.0, FIVE_B),
        ])

        # act
        stints = build_stints(events)

        # assert
        assert stints["n_players"].tolist() == [5, 5]
        assert stints["duration_seconds"].tolist() == [320.0, 400.0]
        assert stints["start_clock"].tolist() == ["12:00", "06:40"]
        assert stints["end_clock"].tolist() == ["06:40", "00:00"]

    def test_lineup_short_a_player_for_real_time_is_kept(self) -> None:
        # arrange: a missed sub-in leaves four on the floor for 100 seconds
        events = pd.DataFrame([
            event(0, 1, 720.0, FIVE_A),
            event(1, 1, 400.0, [1, 2, 3, 4]),
            event(2, 1, 300.0, FIVE_B),
        ])

        # act
        stints = build_stints(events)

        # assert
        assert stints["n_players"].tolist() == [5, 4, 5]
        assert stints["duration_seconds"].tolist() == [320.0, 100.0, 300.0]

    def test_clock_running_backwards_yields_a_negative_stint(self) -> None:
        # arrange: an out-of-order event jumps the clock back from 400 to 450
        events = pd.DataFrame([
            event(0, 1, 720.0, FIVE_A),
            event(1, 1, 400.0, FIVE_B),
            event(2, 1, 450.0, FIVE_A),
        ])

        # act
        stints = build_stints(events)

        # assert
        assert stints["duration_seconds"].tolist() == [320.0, -50.0, 450.0]

    def test_overtime_period_is_five_minutes(self) -> None:
        # arrange
        events = pd.DataFrame([event(0, 5, 300.0, FIVE_A), event(1, 5, 10.0, FIVE_A)])

        # act
        stints = build_stints(events)

        # assert
        assert stints["duration_seconds"].tolist() == [300.0]


class TestFiveMan:
    def test_six_man_stint_is_caught(self) -> None:
        # arrange
        rows = full_game_stints(HOME, FIVE_A)
        rows[2] = stint(HOME, 3, 0.0, 720.0, [1, 2, 3, 4, 5, 6])
        stints = pd.DataFrame(rows)

        # act
        result = check_five_man(stints)

        # assert
        assert result.loc[0, "bad_lineup_stints"] == 1
        assert not result.loc[0, "five_man_ok"]

    def test_all_five_man_stints_pass(self) -> None:
        # arrange
        stints = pd.DataFrame(full_game_stints(HOME, FIVE_A))

        # act
        result = check_five_man(stints)

        # assert
        assert result.loc[0, "bad_lineup_stints"] == 0
        assert result.loc[0, "five_man_ok"]


class TestDurations:
    def test_negative_duration_is_caught_even_when_totals_match(self) -> None:
        # arrange: -50 and +50 cancel, so only the sign check can see it
        rows = full_game_stints(HOME, FIVE_A)[:3] + [
            stint(HOME, 4, 0.0, 400.0, FIVE_A),
            stint(HOME, 4, 400.0, 350.0, FIVE_B),
            stint(HOME, 4, 350.0, 720.0, FIVE_A),
        ]
        stints = pd.DataFrame(rows)

        # act
        result = check_durations(stints, expected_frame(HOME))

        # assert
        assert result.loc[0, "negative_stints"] == 1
        assert result.loc[0, "team_minutes"] == pytest.approx(240.0)
        assert not result.loc[0, "team_total_ok"]

    def test_team_total_arithmetic_and_tolerance(self) -> None:
        # arrange: 4 x 720s regulation, minus 6s (0.5 player-min) on team 1 and
        # minus 7s (0.583 player-min) on team 2
        team1 = full_game_stints(1, FIVE_A)
        team1[3] = stint(1, 4, 0.0, 714.0, FIVE_A)
        team2 = full_game_stints(2, [11, 12, 13, 14, 15])
        team2[3] = stint(2, 4, 0.0, 713.0, [11, 12, 13, 14, 15])
        stints = pd.DataFrame(team1 + team2)

        # act
        result = check_durations(stints, expected_frame(1, 2)).set_index("team_id")

        # assert
        assert result.loc[1, "team_minutes"] == pytest.approx(2874 * 5 / 60)
        assert result.loc[1, "team_minutes_diff"] == pytest.approx(-0.5)
        assert result.loc[1, "team_total_ok"]
        assert result.loc[2, "team_minutes_diff"] == pytest.approx(-35 / 60)
        assert not result.loc[2, "team_total_ok"]

    def test_expected_minutes_for_overtime(self) -> None:
        # act + assert
        assert [expected_team_minutes(n) for n in (4, 5, 6)] == [240.0, 265.0, 290.0]


class TestReconcile:
    def test_player_seconds_reconcile_by_hand(self) -> None:
        # arrange: player 5 plays 600s, player 6 plays 120s, player 1 all 720s
        stints = pd.DataFrame([
            stint(HOME, 1, 0.0, 600.0, FIVE_A),
            stint(HOME, 1, 600.0, 720.0, FIVE_B),
        ])
        box = pd.DataFrame({
            "game_id": [GAME] * 7,
            "team_id": [HOME] * 7,
            "player_id": [1, 2, 3, 4, 5, 6, 7],
            "box_seconds": [720.0, 720.0, 720.0, 720.0, 640.0, 120.0, 25.0],
        })

        # act
        result = reconcile_players(stints, box).set_index("player_id")

        # assert
        assert result.loc[1, "pbp_seconds"] == 720.0
        assert result.loc[5, "pbp_seconds"] == 600.0
        assert result.loc[5, "diff_seconds"] == -40.0
        assert not result.loc[5, "within_tolerance"]
        assert result.loc[6, "abs_diff_seconds"] == 0.0
        assert result.loc[7, "pbp_seconds"] == 0.0
        assert result.loc[7, "within_tolerance"]
        assert result["within_tolerance"].sum() == 6
        assert result["abs_diff_seconds"].mean() == pytest.approx(65 / 7)

    def test_phantom_lineup_player_is_a_miss(self) -> None:
        # arrange: lineups put player 9 on the floor, box score never saw him
        stints = pd.DataFrame([stint(HOME, 1, 0.0, 720.0, [1, 2, 3, 4, 9])])
        box = pd.DataFrame({
            "game_id": [GAME] * 5, "team_id": [HOME] * 5,
            "player_id": [1, 2, 3, 4, 5],
            "box_seconds": [720.0, 720.0, 720.0, 720.0, 720.0],
        })

        # act
        result = reconcile_players(stints, box).set_index("player_id")

        # assert
        assert result.loc[9, "box_seconds"] == 0.0
        assert result.loc[5, "pbp_seconds"] == 0.0
        assert result["within_tolerance"].sum() == 4

    def test_did_not_play_rows_are_ignored(self) -> None:
        # arrange
        stints = pd.DataFrame([stint(HOME, 1, 0.0, 720.0, FIVE_A)])
        box = pd.DataFrame({
            "game_id": [GAME] * 6, "team_id": [HOME] * 6,
            "player_id": [1, 2, 3, 4, 5, 8],
            "box_seconds": [720.0] * 5 + [float("nan")],
        })

        # act
        result = reconcile_players(stints, box)

        # assert
        assert sorted(result["player_id"]) == FIVE_A

    @pytest.mark.parametrize("raw, seconds", [
        ("41:45", 2505.0), ("PT41M45.00S", 2505.0), ("0:07", 7.0), ("PT00M30.50S", 30.5),
    ])
    def test_parse_box_minutes(self, raw: str, seconds: float) -> None:
        # act + assert
        assert parse_box_minutes(raw) == seconds

    def test_parse_box_minutes_blank_is_nan(self) -> None:
        # act + assert
        assert pd.isna(parse_box_minutes("")) and pd.isna(parse_box_minutes(None))


class TestSummaryAndVerdict:
    def _clean_game(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        stints = pd.DataFrame(
            full_game_stints(1, FIVE_A) + full_game_stints(2, [11, 12, 13, 14, 15])
        )
        box = pd.DataFrame({
            "game_id": [GAME] * 10,
            "team_id": [1] * 5 + [2] * 5,
            "player_id": FIVE_A + [11, 12, 13, 14, 15],
            "box_seconds": [2880.0] * 10,
        })
        return stints, box

    def test_clean_game_passes_every_criterion(self) -> None:
        # arrange
        stints, box = self._clean_game()
        durations = check_durations(stints, expected_frame(1, 2))
        players = reconcile_players(stints, box)

        # act
        games = summarize_games(check_five_man(stints), durations, players)
        verdict = compute_verdict(stints, durations, players, games)

        # assert
        assert games["game_ok"].tolist() == [True]
        assert verdict.passed
        assert verdict.games_failing == 0

    def test_one_bad_stint_fails_the_game_and_the_verdict(self) -> None:
        # arrange
        stints, box = self._clean_game()
        stints.at[0, "players"] = [1, 2, 3, 4]
        stints.at[0, "n_players"] = 4
        durations = check_durations(stints, expected_frame(1, 2))
        players = reconcile_players(stints, box)

        # act
        games = summarize_games(check_five_man(stints), durations, players)
        verdict = compute_verdict(stints, durations, players, games)

        # assert
        assert games["game_ok"].tolist() == [False]
        assert verdict.games_failing == 1
        assert not verdict.five_man_pass
        assert verdict.five_man_share == pytest.approx(7 / 8)
        assert not verdict.passed

    def test_unreconstructed_game_counts_as_failing(self) -> None:
        # arrange
        stints, box = self._clean_game()
        durations = check_durations(stints, expected_frame(1, 2))
        players = reconcile_players(stints, box)
        games = summarize_games(check_five_man(stints), durations, players)

        # act
        verdict = compute_verdict(stints, durations, players, games,
                                  unreconstructed_games=1)

        # assert
        assert verdict.games_failing == 1
        assert verdict.n_games == 2
        assert not verdict.passed

    def test_report_states_criteria_before_results(self) -> None:
        # act
        text = render_report("2025-26", 1, "validate.py", None, None, None, None, [])

        # assert
        assert text.index("Pass criteria") < text.index("## Results")
        assert "Not yet run." in text
