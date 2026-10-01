from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import PROSPECTIVE_COLD_START_THROUGH, PROSPECTIVE_RUN_NOTE_LABEL
from fnba_ml.scoring import (
    COVERAGE_MISS,
    LOG_MISS,
    PENDING,
    SCORED,
    STATS,
    align_truth,
    calibration,
    channel_of,
    pivot_predictions,
    pool_label,
    score_run,
    score_runs,
    summarise,
)

GAME_DATE = "2026-10-08"
LATE_DATE = "2026-12-05"


def long_rows(
    run_id: int, player: str, game: str, game_date: str, p: float, cond: dict[str, float],
    p_model: float | None = None, band: float = 2.0,
) -> list[dict[str, object]]:
    """the store rows for one player-game, shaped as build_prediction_rows writes them."""
    rows = [{"stat": "prob_active", "quantile": None, "value": p, "conditional": False}]
    if p_model is not None:
        rows.append({"stat": "prob_active_model", "quantile": None, "value": p_model,
                     "conditional": False})
    for stat, value in cond.items():
        rows.append({"stat": stat, "quantile": None, "value": value, "conditional": True})
        rows.append({"stat": f"{stat}_uncond", "quantile": None, "value": p * value,
                     "conditional": False})
        for q, offset in ((0.10, -band), (0.50, 0.0), (0.90, band)):
            rows.append({"stat": stat, "quantile": q, "value": value + offset, "conditional": True})
    for row in rows:
        row.update(run_id=run_id, nba_player_id=player, nba_game_id=game, game_date=game_date)
    return rows


def full_line(value: float) -> dict[str, float]:
    return {stat: value for stat in STATS}


def truth_row(player: str, game: str, played: bool | None, line: dict[str, float] | None,
              game_date: str = GAME_DATE, season_type: str = "Pre Season") -> dict[str, object]:
    row: dict[str, object] = {"nba_player_id": player, "nba_game_id": game,
                              "game_date": game_date, "season_type": season_type,
                              "played": played}
    row.update({stat: (line or {}).get(stat, np.nan) for stat in STATS})
    return row


def result(results: pd.DataFrame, endpoint: str, stat: str, cohort: str = "ALL") -> pd.Series:
    match = results[(results["endpoint"] == endpoint) & (results["stat"] == stat)
                    & (results["cohort"] == cohort)]
    assert len(match) == 1, f"expected one {endpoint}/{stat}/{cohort} row, got {len(match)}"
    return match.iloc[0]


class TestPivot:
    def test_it_reproduces_the_stored_values_under_predict_column_names(self):
        # arrange
        long = pd.DataFrame(long_rows(7, "1", "g1", GAME_DATE, 0.8, {"pts": 20.0, "minutes": 30.0},
                                      p_model=0.7))

        # act
        wide = pivot_predictions(long)

        # assert
        row = wide.iloc[0]
        assert len(wide) == 1
        assert row["prob_active"] == pytest.approx(0.8)
        assert row["prob_active_model"] == pytest.approx(0.7)
        assert row["E_PTS_COND"] == pytest.approx(20.0)
        assert row["E_PTS"] == pytest.approx(16.0)
        assert (row["Q10_PTS"], row["Q50_PTS"], row["Q90_PTS"]) == pytest.approx((18.0, 20.0, 22.0))
        assert row["E_MIN_COND"] == pytest.approx(30.0)

    def test_status_rows_are_ignored(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.5, {"pts": 10.0}))
        extra = {"run_id": 1, "nba_player_id": "1", "nba_game_id": "g1", "game_date": GAME_DATE,
                 "stat": "status_override", "quantile": None, "value": 2.0, "conditional": False}

        # act
        wide = pivot_predictions(pd.concat([long, pd.DataFrame([extra])]))

        # assert
        assert not any("status" in column for column in wide.columns)

    def test_a_run_without_the_model_probability_gets_a_nan_column(self):
        # act
        wide = pivot_predictions(pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.5, {"pts": 1.0})))

        # assert
        assert wide["prob_active_model"].isna().all()


class TestTruthAlignment:
    def test_every_outcome_is_labelled_and_none_is_dropped(self):
        # arrange
        long = pd.DataFrame(
            long_rows(1, "1", "g1", GAME_DATE, 0.9, {"pts": 10.0})
            + long_rows(1, "2", "g1", GAME_DATE, 0.9, {"pts": 10.0})
            + long_rows(1, "3", "g1", GAME_DATE, 0.9, {"pts": 10.0})
            + long_rows(1, "4", "g2", GAME_DATE, 0.9, {"pts": 10.0})
        )
        truth = pd.DataFrame([
            truth_row("1", "g1", True, full_line(10.0)),
            truth_row("2", "g1", None, None),
            truth_row("3", "g1", True, None),
        ])

        # act
        aligned = align_truth(pivot_predictions(long), truth)

        # assert
        outcomes = dict(zip(aligned["nba_player_id"], aligned["outcome"]))
        assert outcomes == {"1": SCORED, "2": COVERAGE_MISS, "3": LOG_MISS, "4": PENDING}

    def test_a_non_appearance_is_a_realized_zero(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.2, {"pts": 10.0}))
        truth = pd.DataFrame([truth_row("1", "g1", False, None)])

        # act
        aligned = align_truth(pivot_predictions(long), truth)

        # assert
        assert aligned.iloc[0]["outcome"] == SCORED
        assert aligned.iloc[0]["A_PTS"] == 0.0

    def test_cold_start_uses_the_frozen_boundary(self):
        # arrange
        boundary = PROSPECTIVE_COLD_START_THROUGH
        after = (pd.Timestamp(boundary) + pd.Timedelta(days=1)).date().isoformat()
        long = pd.DataFrame(long_rows(1, "1", "g1", boundary, 0.5, {"pts": 1.0})
                            + long_rows(1, "1", "g2", after, 0.5, {"pts": 1.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(1.0), boundary),
                              truth_row("1", "g2", True, full_line(1.0), after)])

        # act
        aligned = align_truth(pivot_predictions(long), truth)

        # assert
        flags = dict(zip(aligned["nba_game_id"], aligned["cold_start"]))
        assert flags == {"g1": True, "g2": False}


class TestScoreRun:
    def test_a_perfect_forecast_scores_zero(self):
        # arrange
        long = pd.DataFrame(
            long_rows(1, "1", "g1", GAME_DATE, 1.0, full_line(12.0), p_model=1.0)
            + long_rows(1, "2", "g1", GAME_DATE, 0.0, full_line(9.0), p_model=0.0)
        )
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(12.0)),
                              truth_row("2", "g1", False, None)])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        assert result(results, "E1_brier", "prob_active")["value"] == pytest.approx(0.0)
        assert result(results, "E1_brier", "prob_active_model")["value"] == pytest.approx(0.0)
        assert result(results, "E3_minutes_mae", "minutes")["value"] == pytest.approx(0.0)
        for stat in STATS:
            row = result(results, "E4_uncond_mae", stat)
            assert row["value"] == pytest.approx(0.0)
            assert row["n"] == 2

    def test_coverage_misses_are_counted_not_dropped(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.9, {"pts": 10.0})
                            + long_rows(1, "2", "g1", GAME_DATE, 0.9, {"pts": 10.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(10.0)),
                              truth_row("2", "g1", None, None)])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        miss = result(results, "coverage", COVERAGE_MISS)
        assert miss["n"] == 1
        assert miss["value"] == pytest.approx(0.5)
        assert result(results, "E1_brier", "prob_active")["n"] == 1

    def test_non_appearances_count_unconditionally_and_not_conditionally(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.5, {"pts": 10.0, "minutes": 30.0})
                            + long_rows(1, "2", "g1", GAME_DATE, 0.5, {"pts": 10.0, "minutes": 30.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, {**full_line(10.0), "minutes": 30.0}),
                              truth_row("2", "g1", False, None)])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        uncond = result(results, "E4_uncond_mae", "pts")
        assert uncond["n"] == 2
        assert uncond["value"] == pytest.approx((5.0 + 5.0) / 2)
        minutes = result(results, "E3_minutes_mae", "minutes")
        assert minutes["n"] == 1
        assert minutes["value"] == pytest.approx(0.0)
        assert result(results, "interval_coverage", "pts")["n"] == 1

    def test_the_override_increment_is_negative_when_the_override_helped(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.05, {"pts": 10.0}, p_model=0.8))
        truth = pd.DataFrame([truth_row("1", "g1", False, None)])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        increment = result(results, "override_increment", "prob_active")
        assert increment["value"] == pytest.approx(0.05**2 - 0.8**2)
        assert increment["value"] < 0

    def test_interval_coverage_on_a_hand_built_case(self):
        # arrange: band [8, 12] around 10; actuals 9 (in), 12 (edge, in), 13 (out), 5 (out)
        players = {"1": 9.0, "2": 12.0, "3": 13.0, "4": 5.0}
        long = pd.DataFrame([row for p in players
                             for row in long_rows(1, p, "g1", GAME_DATE, 0.9, {"pts": 10.0})])
        truth = pd.DataFrame([truth_row(p, "g1", True, full_line(v)) for p, v in players.items()])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        coverage = result(results, "interval_coverage", "pts")
        assert coverage["n"] == 4
        assert coverage["value"] == pytest.approx(0.5)

    def test_mean_bias_is_prediction_minus_actual(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 1.0, {"pts": 10.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(14.0))])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        assert result(results, "bias_uncond", "pts")["value"] == pytest.approx(-4.0)

    def test_cohorts_split_by_cold_start_and_season_type(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.5, {"pts": 1.0})
                            + long_rows(1, "1", "g2", LATE_DATE, 0.5, {"pts": 1.0}))
        truth = pd.DataFrame([
            truth_row("1", "g1", True, full_line(1.0), GAME_DATE, "Pre Season"),
            truth_row("1", "g2", True, full_line(1.0), LATE_DATE, "Regular Season"),
        ])

        # act
        results = score_run(pivot_predictions(long), truth)

        # assert
        assert result(results, "E1_brier", "prob_active", "cold_start=true")["n"] == 1
        assert result(results, "E1_brier", "prob_active", "cold_start=false")["n"] == 1
        assert result(results, "E1_brier", "prob_active", "season_type=Pre Season")["n"] == 1
        assert result(results, "E1_brier", "prob_active", "ALL")["n"] == 2

    def test_brier_skill_is_skipped_without_a_baseline_and_computed_with_one(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.9, {"pts": 1.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(1.0))])
        baseline = pd.DataFrame({"nba_player_id": ["1"], "nba_game_id": ["g1"],
                                 "baseline_prob": [0.5]})

        # act
        without = score_run(pivot_predictions(long), truth)
        with_base = score_run(pivot_predictions(long), truth, baseline=baseline)

        # assert
        skipped = result(without, "E1_brier_skill", "prob_active")
        assert skipped["n"] == 0 and np.isnan(skipped["value"])
        skill = result(with_base, "E1_brier_skill", "prob_active")
        assert skill["value"] == pytest.approx(1 - 0.01 / 0.25)


class TestCalibration:
    def test_a_well_calibrated_sample_has_slope_near_one_and_intercept_near_zero(self):
        # arrange
        rng = np.random.default_rng(20261020)
        p = rng.uniform(0.02, 0.98, size=20_000)
        y = (rng.random(p.size) < p).astype(int)

        # act
        intercept, slope = calibration(pd.Series(p), pd.Series(y))

        # assert
        assert slope == pytest.approx(1.0, abs=0.06)
        assert intercept == pytest.approx(0.0, abs=0.06)

    def test_a_single_class_sample_is_not_fitted(self):
        # act
        intercept, slope = calibration(pd.Series([0.4, 0.6]), pd.Series([1, 1]))

        # assert
        assert np.isnan(intercept) and np.isnan(slope)


class TestScoreRuns:
    def runs(self) -> pd.DataFrame:
        return pd.DataFrame({
            "id": [1, 2, 3],
            "model_version": ["m"] * 3,
            "feature_version": ["v3"] * 3,
            "predicted_at": pd.to_datetime(["2026-10-07 12:00", "2026-10-08 12:00",
                                            "2026-10-08 13:00"], utc=True),
            "forecast_cutoff_at": pd.to_datetime(["2026-10-07"] * 3, utc=True),
            "notes": [f"{PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v3-honest; shadow=false",
                      f"{PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v3-honest; shadow=false",
                      f"{PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v1; shadow=true"],
        })

    def test_each_run_and_each_pool_is_scored_and_a_pool_keeps_the_latest_forecast(self):
        # arrange: runs 1 and 2 both forecast the same player-game; run 2 is later and right
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.0, {"pts": 10.0})
                            + long_rows(2, "1", "g1", GAME_DATE, 1.0, {"pts": 10.0})
                            + long_rows(3, "1", "g1", GAME_DATE, 0.5, {"pts": 10.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(10.0))])

        # act
        results = score_runs(long, self.runs(), truth)

        # assert
        labels = set(results["run_id"])
        production = pool_label("production", True)
        assert labels == {"1", "2", "3", production, pool_label("shadow", True)}
        pooled = result(results[results["run_id"] == production], "E1_brier", "prob_active")
        assert pooled["n"] == 1
        assert pooled["value"] == pytest.approx(0.0)

    def test_the_channel_column_wins_over_the_notes_when_present(self):
        # arrange
        runs = self.runs().assign(channel=["shadow", None, None])

        # act
        channels = list(channel_of(runs))

        # assert
        assert channels == ["shadow", "production", "shadow"]

    def test_a_channel_token_in_the_notes_is_read_when_the_column_is_absent(self):
        # arrange
        runs = self.runs().assign(notes=["feature_set=v1; channel=shadow",
                                         "feature_set=v3-honest; channel=production", None])

        # act
        channels = list(channel_of(runs))

        # assert
        assert channels == ["shadow", "production", "production"]

    def test_summarise_renders_a_table_per_family_and_names_what_it_skipped(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", GAME_DATE, 0.9, full_line(10.0)))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(10.0))])

        # act
        markdown = summarise(score_runs(long, self.runs(), truth))

        # assert
        for heading in ("### coverage", "### E1 availability", "### E2 calibration",
                        "### E3 minutes", "### E4 unconditional MAE"):
            assert heading in markdown
        assert "E5 is not computed" in markdown
        assert "no shifted-appearance-rate baseline" in markdown

    def test_nothing_to_score_is_an_empty_frame(self):
        # act
        results = score_runs(pd.DataFrame(columns=["run_id", "nba_player_id", "nba_game_id",
                                                   "game_date", "stat", "quantile", "value",
                                                   "conditional"]),
                             self.runs(), pd.DataFrame())

        # assert
        assert results.empty
        assert summarise(results) == "_nothing scored._\n"


class TestCli:
    def fake_reader(self, frames: dict[str, pd.DataFrame]):
        def read(sql: str, params: dict[str, object] | None = None) -> pd.DataFrame:
            for marker, frame in frames.items():
                if marker in sql:
                    return frame
            raise AssertionError(f"unexpected query: {sql[:60]!r}")
        return read

    def test_no_runs_exits_zero_and_writes_nothing(self, monkeypatch, tmp_path, capsys):
        # arrange
        import score_runs

        monkeypatch.setattr(score_runs, "_read_sql", self.fake_reader({
            "information_schema": pd.DataFrame({"column_name": ["id", "notes"]}),
            "FROM prediction_runs": pd.DataFrame(columns=["id", "notes"]),
        }))

        # act
        code = score_runs.main(["--out-dir", str(tmp_path)])

        # assert
        assert code == 0
        assert "nothing to score" in capsys.readouterr().out
        assert list(tmp_path.iterdir()) == []

    def test_a_scored_window_writes_markdown_and_csv_beside_it(self, monkeypatch, tmp_path):
        # arrange
        import score_runs

        runs = TestScoreRuns().runs().iloc[:1]
        monkeypatch.setattr(score_runs, "_read_sql", self.fake_reader({
            "information_schema": pd.DataFrame({"column_name": list(runs.columns)}),
            "WITH predicted": pd.DataFrame([truth_row("1", "g1", True, full_line(10.0))]),
            "FROM player_game_predictions": pd.DataFrame(
                long_rows(1, "1", "g1", GAME_DATE, 0.9, full_line(10.0))),
            "FROM prediction_runs": runs,
        }))
        md, csv = tmp_path / "look.md", tmp_path / "look_results.csv"

        # act
        code = score_runs.main(["--md", str(md), "--csv", str(csv)])

        # assert
        assert code == 0
        assert "### E4 unconditional MAE" in md.read_text(encoding="utf-8")
        assert set(pd.read_csv(csv).columns) == {"run_id", "cohort", "endpoint", "stat", "n", "value"}
