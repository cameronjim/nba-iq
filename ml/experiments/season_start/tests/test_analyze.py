"""tests for the pure computations in analyze.py, on synthetic frames."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from analyze import (  # noqa: E402
    ALL,
    MIDSEASON,
    OPENER,
    PRED,
    PRIOR,
    READING_RULE,
    STAR,
    TIER,
    WINDOW,
    assign_tiers,
    build_counterfactual,
    changed_columns,
    hypothesis_verdict,
    minutes_summary,
    next_season,
    opener_contrast,
    preseason_table,
    preseason_verdict,
    prior_season_values,
    render_report,
    resolve_overrides,
    season_window,
    shift_table,
    team_game_numbers,
    usual_minutes_asof,
    with_prior_season,
)

STARTER = "starter (20-30)"


def schedule_frame(team: str, season: str, start: str, n_games: int) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n_games, freq="2D")
    return pd.DataFrame({
        "SEASON": season,
        "TEAM_ID": team,
        "GAME_ID": [f"{team}{season}{i:03d}" for i in range(n_games)],
        "GAME_DATE": dates,
    })


def history_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "PLAYER_ID": ["1", "1", "1", "2", "2", "1"],
        "SEASON": ["2023-24", "2023-24", "2023-24", "2023-24", "2023-24", "2024-25"],
        "PLAYED": [1, 1, 0, 1, 1, 1],
        "MIN": [30.0, 34.0, 0.0, 10.0, 14.0, 20.0],
        "PTS": [20.0, 24.0, 0.0, 4.0, 6.0, 10.0],
        "season_appearances": [np.nan, 1.0, 2.0, np.nan, 1.0, np.nan],
    })


class TestTiers:
    def test_tiers_follow_the_frozen_edges(self) -> None:
        # arrange
        frame = pd.DataFrame({"roll10_MIN": [5.0, 15.0, 25.0, 35.0, np.nan]})

        # act
        tiers = assign_tiers(frame)

        # assert
        assert tiers.tolist() == [
            "fringe (<10)", "bench (10-20)", STARTER, STAR, "unknown (no history)",
        ]


class TestSeasonWindow:
    def test_opener_is_games_one_to_three_and_midseason_eleven_to_twenty(self) -> None:
        # arrange: team B opens a week later, so dates alone would mislabel it
        frame = pd.concat([
            schedule_frame("A", "2024-25", "2024-10-22", 25),
            schedule_frame("B", "2024-25", "2024-10-30", 25),
        ], ignore_index=True)

        # act
        numbers = team_game_numbers(frame)
        window = season_window(numbers)

        # assert
        for team in ("A", "B"):
            mask = frame["TEAM_ID"] == team
            labels = window[mask].tolist()
            assert labels[:3] == [OPENER] * 3
            assert labels[3:10] == [None] * 7
            assert labels[10:20] == [MIDSEASON] * 10
            assert labels[20:] == [None] * 5
        assert numbers[frame["GAME_DATE"] == pd.Timestamp("2024-10-30")].tolist() == [5, 1]

    def test_game_numbers_restart_each_season_and_ignore_row_order(self) -> None:
        # arrange: two players per game, rows shuffled
        games = pd.concat([
            schedule_frame("A", "2023-24", "2023-10-24", 3),
            schedule_frame("A", "2024-25", "2024-10-22", 3),
        ], ignore_index=True)
        frame = pd.concat([games.assign(PLAYER_ID="1"), games.assign(PLAYER_ID="2")])
        frame = frame.sample(frac=1.0, random_state=0)

        # act
        numbers = team_game_numbers(frame)

        # assert
        expected = frame["GAME_ID"].str[-3:].astype(int) + 1
        assert numbers.tolist() == expected.tolist()


class TestCounterfactual:
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "PLAYER_ID": ["1", "2"],
            "SEASON": ["2024-25", "2024-25"],
            "days_since_last_app": [170.0, 182.0],
            "season_appearances": [np.nan, np.nan],
            "TEAM_REST_DAYS": [np.nan, np.nan],
            "IS_B2B": [np.nan, np.nan],
            "roll10_MIN": [33.0, 12.0],
        })

    def test_changes_exactly_the_named_column(self) -> None:
        # arrange
        frame = self.frame()

        # act
        out = build_counterfactual(frame, {"days_since_last_app": 3.0})

        # assert
        assert changed_columns(frame, out) == ["days_since_last_app"]
        assert out["days_since_last_app"].tolist() == [3.0, 3.0]
        assert frame["days_since_last_app"].tolist() == [170.0, 182.0]

    def test_changes_exactly_both_rest_columns(self) -> None:
        # arrange
        frame = self.frame()

        # act
        out = build_counterfactual(frame, {"TEAM_REST_DAYS": 2.0, "IS_B2B": 0.0})

        # assert
        assert changed_columns(frame, out) == ["TEAM_REST_DAYS", "IS_B2B"]

    def test_per_row_values_from_the_prior_season(self) -> None:
        # arrange
        frame = self.frame()
        prior = pd.DataFrame({"PLAYER_ID": ["2", "1"], "SEASON": ["2024-25"] * 2,
                              "season_appearances": [20.0, 40.0]})

        # act
        overrides = resolve_overrides({"season_appearances": PRIOR}, frame, prior)
        out = build_counterfactual(frame, overrides)

        # assert
        assert out["season_appearances"].tolist() == [40.0, 20.0]
        assert changed_columns(frame, out) == ["season_appearances"]

    def test_unknown_column_is_refused(self) -> None:
        # act + assert
        with pytest.raises(KeyError):
            build_counterfactual(self.frame(), {"not_a_column": 1.0})

    def test_changed_columns_sees_an_unnamed_change(self) -> None:
        # arrange
        frame = self.frame()
        tampered = frame.copy()
        tampered.loc[0, "roll10_MIN"] = 0.0

        # act
        changed = changed_columns(frame, tampered)

        # assert
        assert changed == ["roll10_MIN"]

    def test_with_prior_season_drops_players_with_nothing_to_borrow(self) -> None:
        # arrange
        frame = self.frame()
        prior = pd.DataFrame({"PLAYER_ID": ["1"], "SEASON": ["2024-25"]})

        # act
        kept = with_prior_season(frame, prior)

        # assert
        assert kept["PLAYER_ID"].tolist() == ["1"]


class TestPriorSeasonValues:
    def test_prior_season_values_by_hand(self) -> None:
        # arrange
        frame = history_frame()

        # act
        prior = prior_season_values(frame).set_index(["PLAYER_ID", "SEASON"])

        # assert: player 1's 2023-24 values are keyed to 2024-25
        row = prior.loc[("1", "2024-25")]
        assert row["season_appearances"] == pytest.approx(1.5)
        assert row["std_MIN"] == pytest.approx(32.0)
        assert row["uncond_std_MIN"] == pytest.approx(64.0 / 3.0)
        assert row["avail_rate_std"] == pytest.approx(2.0 / 3.0)
        assert prior.loc[("2", "2024-25")]["std_PTS"] == pytest.approx(5.0)
        assert ("1", "2025-26") in prior.index

    def test_next_season(self) -> None:
        # act + assert
        assert next_season("2024-25") == "2025-26"


class TestSummaries:
    def test_minutes_summary_arithmetic(self) -> None:
        # arrange
        frame = pd.DataFrame({
            WINDOW: [OPENER, OPENER, MIDSEASON, MIDSEASON],
            TIER: [STAR, STAR, STAR, STARTER],
            PRED: [30.0, 28.0, 34.0, 25.0],
            "MIN": [34.0, 36.0, 33.0, 24.0],
        })

        # act
        out = minutes_summary(frame, [WINDOW, TIER]).set_index([WINDOW, TIER])

        # assert
        star_open = out.loc[(OPENER, STAR)]
        assert star_open["rows"] == 2
        assert star_open["mean_pred"] == pytest.approx(29.0)
        assert star_open["mean_realized"] == pytest.approx(35.0)
        assert star_open["bias"] == pytest.approx(-6.0)
        assert star_open["mae"] == pytest.approx(6.0)
        assert out.loc[(MIDSEASON, ALL)]["bias"] == pytest.approx(1.0)
        assert out.loc[(MIDSEASON, ALL)]["rows"] == 2

    def test_opener_contrast_is_opener_bias_minus_midseason_bias(self) -> None:
        # arrange
        frame = pd.DataFrame({
            WINDOW: [OPENER, OPENER, MIDSEASON, MIDSEASON],
            TIER: [STAR, STAR, STAR, STAR],
            PRED: [30.0, 28.0, 34.0, 35.0],
            "MIN": [34.0, 36.0, 33.0, 34.0],
        })

        # act
        contrast = opener_contrast(minutes_summary(frame, [WINDOW, TIER]), [TIER])

        # assert
        star = contrast[contrast[TIER] == STAR].iloc[0]
        assert star["opener_bias"] == pytest.approx(-6.0)
        assert star["midseason_bias"] == pytest.approx(1.0)
        assert star["bias_opener_minus_midseason"] == pytest.approx(-7.0)
        assert contrast[TIER].tolist() == [ALL, STAR]

    def test_shift_table_arithmetic(self) -> None:
        # arrange
        tiers = pd.Series([STAR, STAR, STARTER])
        base = np.array([27.0, 28.0, 20.0])
        cf = np.array([30.0, 29.0, 19.0])

        # act
        out = shift_table(base, cf, tiers, "m", "v").set_index(TIER)

        # assert
        assert out.loc[STAR, "mean_shift"] == pytest.approx(2.0)
        assert out.loc[STAR, "mean_base"] == pytest.approx(27.5)
        assert out.loc[STARTER, "mean_shift"] == pytest.approx(-1.0)
        assert out.loc[ALL, "mean_shift"] == pytest.approx(1.0)
        assert out.loc[ALL, "mean_abs_shift"] == pytest.approx(5.0 / 3.0)
        assert out.loc[ALL, "rows"] == 3


class TestPreseason:
    def test_usual_minutes_is_strictly_before_the_game(self) -> None:
        # arrange
        history = pd.DataFrame({
            "PLAYER_ID": ["1", "1", "1"],
            "GAME_DATE": pd.to_datetime(["2024-03-01", "2024-03-03", "2024-10-10"]),
            "PLAYED": [1, 1, 1],
            "MIN": [30.0, 36.0, 10.0],
        })
        query = pd.DataFrame({
            "PLAYER_ID": ["1", "1", "9"],
            "GAME_DATE": pd.to_datetime(["2024-10-10", "2024-03-02", "2024-10-10"]),
        })

        # act
        usual = usual_minutes_asof(history, query)

        # assert
        assert usual.iloc[0] == pytest.approx(33.0)
        assert usual.iloc[1] == pytest.approx(30.0)
        assert np.isnan(usual.iloc[2])

    def test_usual_minutes_tolerates_mixed_date_resolutions(self) -> None:
        # arrange: parquet history arrives as ms, the sql preseason frame as s
        history = pd.DataFrame({
            "PLAYER_ID": ["1", "1"],
            "GAME_DATE": pd.to_datetime(["2024-03-01", "2024-03-03"]).astype("datetime64[ms]"),
            "PLAYED": [1, 1],
            "MIN": [30.0, 36.0],
        })
        query = pd.DataFrame({
            "PLAYER_ID": ["1"],
            "GAME_DATE": pd.to_datetime(["2024-10-10"]).astype("datetime64[s]"),
        })

        # act
        usual = usual_minutes_asof(history, query)

        # assert
        assert usual.iloc[0] == pytest.approx(33.0)

    def test_preseason_table_by_hand(self) -> None:
        # arrange: the 0-minute row is a DNP and is excluded
        pre = pd.DataFrame({
            "PLAYER_ID": ["1", "2", "3", "4"],
            "SEASON": ["2025-26"] * 4,
            "MIN": [24.0, 20.0, 0.0, 18.0],
            "STARTED": [True, False, True, True],
            "usual": [34.0, 32.0, 31.0, 22.0],
            TIER: [STAR, STAR, STAR, STARTER],
        })

        # act
        out = preseason_table(pre).set_index(["SEASON", TIER])

        # assert
        star = out.loc[(ALL, STAR)]
        assert star["rows"] == 2
        assert star["mean_realized"] == pytest.approx(22.0)
        assert star["mean_usual"] == pytest.approx(33.0)
        assert star["realized_minus_usual"] == pytest.approx(-11.0)
        assert star["mean_realized_started"] == pytest.approx(24.0)
        assert out.loc[("2025-26", ALL)]["rows"] == 3


class TestVerdicts:
    def shifts(self, value: float) -> pd.DataFrame:
        return pd.DataFrame({
            "model": ["m"], "variant": ["days_since_last_app=3"], TIER: [STAR],
            "rows": [40], "mean_shift": [value],
        })

    def test_shift_above_the_bar_supports_the_hypothesis(self) -> None:
        # act + assert
        assert hypothesis_verdict(self.shifts(2.6), "m")["verdict"] == "SUPPORTED"

    def test_shift_at_or_below_the_bar_does_not(self) -> None:
        # act + assert
        assert hypothesis_verdict(self.shifts(2.5), "m")["verdict"] == "NOT SUPPORTED"
        assert hypothesis_verdict(self.shifts(-4.0), "m")["verdict"] == "NOT SUPPORTED"

    def test_missing_model_is_not_measured(self) -> None:
        # act + assert
        assert hypothesis_verdict(self.shifts(9.0), "other")["verdict"] == "NOT MEASURED"

    def test_preseason_within_tolerance_is_accidentally_right(self) -> None:
        # arrange
        table = pd.DataFrame({"SEASON": [ALL], TIER: [STAR], "rows": [50],
                              "mean_realized": [25.0]})

        # act
        verdict = preseason_verdict(table)

        # assert
        assert verdict["verdict"] == "ACCIDENTALLY RIGHT"
        assert verdict["realized"] == pytest.approx(25.0)

    def test_preseason_outside_tolerance_and_absent(self) -> None:
        # arrange
        table = pd.DataFrame({"SEASON": [ALL], TIER: [STAR], "rows": [50],
                              "mean_realized": [31.0]})

        # act + assert
        assert preseason_verdict(table)["verdict"] == "NOT ACCIDENTALLY RIGHT"
        assert preseason_verdict(None)["verdict"] == "NOT MEASURABLE"


class TestReport:
    def test_reading_rule_comes_before_every_result(self) -> None:
        # arrange
        facts = {"generated_at": "now", "dataset": "d.parquet", "version": "v",
                 "retro_seasons": "", "retro_skipped": "", "preseason_note": "absent",
                 "csvs": []}
        verdicts = {
            "hypothesis": {"verdict": "NOT MEASURED", "shift": float("nan"),
                           "rows": 0, "model": "m"},
            "preseason": {"verdict": "NOT MEASURABLE", "realized": float("nan"),
                          "projected": 27.5, "rows": 0},
        }

        # act
        text = render_report(facts, {}, verdicts)

        # assert
        assert READING_RULE in text
        assert text.index("## Pre-registered reading rule") < text.index("## Verdicts")
        assert chr(0x2014) not in text
