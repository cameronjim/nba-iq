from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml import config
from fnba_ml.config import (
    PROSPECTIVE_FALSIFICATION,
    PROSPECTIVE_MODEL_VERSION,
    PROSPECTIVE_RUN_NOTE_LABEL,
    UNKNOWN_TIER,
)
from fnba_ml.scoring import (
    AVAIL_RATE,
    E5_AGGREGATE,
    FAIL,
    MINUTES_TIER,
    NON_BINDING,
    NOT_COMPUTABLE,
    PASS,
    REPORT_ONLY,
    ROLL10_MIN,
    VACATED,
    attach_baselines,
    build_baselines,
    compare_served_shadow,
    ewma_total_column,
    falsification_observations,
    falsification_table,
    pair_runs,
    pivot_predictions,
    pool_label,
    rate_family_column,
    relative_improvement_pct,
    score_run,
    score_runs,
    seeded_rate_baselines,
    snapshot_missing_families,
)
from test_scoring import full_line, long_rows, result, truth_row

TARGET_DATE = "2026-11-10"
DECAY_H5 = 0.5 ** (1 / 5)


def status(player: str, game: str, day: str, played: bool, minutes: float | None = None,
           team: str = "T1", inactive: bool = False, pts: float | None = None) -> dict[str, object]:
    return {"nba_player_id": player, "nba_game_id": game, "team_id": team, "game_date": day,
            "played": played, "listed_inactive": inactive,
            "minutes": minutes if played else None, "pts": pts if played else None}


def target(player: str = "1", game: str = "gT", day: str = TARGET_DATE) -> pd.DataFrame:
    return pd.DataFrame({"nba_player_id": [player], "nba_game_id": [game], "game_date": [day]})


def days_before(n: int) -> str:
    return (pd.Timestamp(TARGET_DATE) - pd.Timedelta(days=n)).date().isoformat()


class TestAsOfBaselines:
    def history(self) -> pd.DataFrame:
        # twelve prior games: the last ten hold 7 appearances. the target game and a
        # later game are planted as non-appearances and must not be read.
        played = [True, False] + [True] * 7 + [False] * 3
        rows = [status("1", f"g{i}", days_before(12 - i), p, 20.0, pts=10.0 + i)
                for i, p in enumerate(played)]
        rows += [status("1", "gT", TARGET_DATE, False),
                 status("1", "gF", "2026-11-12", False)]
        return pd.DataFrame(rows)

    def test_avail_rate_10_reads_only_the_ten_prior_scheduled_rows(self):
        # act
        baselines = build_baselines(target(), self.history())

        # assert
        assert baselines.iloc[0][AVAIL_RATE] == pytest.approx(7 / 10)

    def test_allowing_the_exact_match_leaks_the_planted_target_row(self):
        # act
        honest = build_baselines(target(), self.history())
        leaky = build_baselines(target(), self.history(), allow_exact_matches=True)

        # assert
        assert leaky.iloc[0][AVAIL_RATE] == pytest.approx(6 / 10)
        assert leaky.iloc[0][AVAIL_RATE] != honest.iloc[0][AVAIL_RATE]

    def test_ewma_total_is_a_halflife_5_ewma_of_prior_whole_game_totals(self):
        # arrange: two prior appearances (10, 20), then a planted 100 on the target date
        history = pd.DataFrame([
            status("1", "g1", days_before(2), True, 30.0, pts=10.0),
            status("1", "g2", days_before(1), True, 30.0, pts=20.0),
            status("1", "gT", TARGET_DATE, True, 30.0, pts=100.0),
        ])

        # act
        honest = build_baselines(target(), history)
        leaky = build_baselines(target(), history, allow_exact_matches=True)

        # assert
        expected = (20.0 + DECAY_H5 * 10.0) / (1.0 + DECAY_H5)
        assert honest.iloc[0][ewma_total_column("pts")] == pytest.approx(expected)
        assert leaky.iloc[0][ewma_total_column("pts")] != pytest.approx(expected)

    def test_roll10_minutes_labels_the_tier_and_no_history_is_unknown(self):
        # arrange
        history = pd.DataFrame([status("1", f"g{i}", days_before(5 - i), True, 25.0)
                                for i in range(3)])
        targets = pd.concat([target("1"), target("2")], ignore_index=True)

        # act
        baselines = build_baselines(targets, history)
        frame = attach_baselines(baselines.drop(columns="game_date"), baselines)

        # assert
        assert baselines.iloc[0][ROLL10_MIN] == pytest.approx(25.0)
        assert list(frame[MINUTES_TIER]) == ["starter (20-30)", UNKNOWN_TIER]


class TestVacatedMinutes:
    def test_absent_teammates_prior_minutes_are_summed_and_own_minutes_excluded(self):
        # arrange: A plays; B sits; C is listed inactive; D sits for the other team
        priors = {"A": 30.0, "B": 20.0, "C": 15.0, "D": 40.0, "E": 10.0}
        teams = {"A": "T1", "B": "T1", "C": "T1", "D": "T2", "E": "T2"}
        rows = [status(p, f"g{i}", days_before(3 - i), True, m, team=teams[p])
                for p, m in priors.items() for i in range(2)]
        rows += [
            status("A", "gT", TARGET_DATE, True, 99.0, team="T1"),
            status("B", "gT", TARGET_DATE, False, team="T1"),
            status("C", "gT", TARGET_DATE, False, team="T1", inactive=True),
            status("D", "gT", TARGET_DATE, False, team="T2"),
            status("E", "gT", TARGET_DATE, True, 99.0, team="T2"),
        ]
        targets = pd.concat([target(p) for p in priors], ignore_index=True)

        # act
        baselines = build_baselines(targets, pd.DataFrame(rows))

        # assert
        vacated = dict(zip(baselines["nba_player_id"], baselines[VACATED]))
        assert vacated == pytest.approx({"A": 35.0, "B": 15.0, "C": 20.0, "D": 0.0, "E": 40.0})

    def test_the_cohorts_split_on_the_oracle_column(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", TARGET_DATE, 0.9, {"pts": 1.0})
                            + long_rows(1, "2", "g1", TARGET_DATE, 0.9, {"pts": 1.0}))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(1.0), TARGET_DATE),
                              truth_row("2", "g1", True, full_line(1.0), TARGET_DATE)])
        baseline = pd.DataFrame({"nba_player_id": ["1", "2"], "nba_game_id": ["g1", "g1"],
                                 VACATED: [35.0, 2.0], ROLL10_MIN: [31.0, np.nan]})

        # act
        results = score_run(pivot_predictions(long), truth, baseline)

        # assert
        assert result(results, "E1_brier", "prob_active", "event: vacated_minutes >= 30")["n"] == 1
        assert result(results, "E1_brier", "prob_active", "control: vacated_minutes < 5")["n"] == 1
        assert result(results, "E1_brier", "prob_active", "star (>=30)")["n"] == 1
        assert result(results, "E1_brier", "prob_active", UNKNOWN_TIER)["n"] == 1
        assert not (results["cohort"] == "event: star_out = 1").any()


class TestSeededRates:
    def appearances(self) -> pd.DataFrame:
        rng = np.random.default_rng(7)
        days = pd.date_range("2025-10-01", periods=30, freq="3D")
        return pd.DataFrame({
            "nba_player_id": "1", "game_date": days,
            "minutes": rng.uniform(2.0, 36.0, size=len(days)).round(1),
            "stl": rng.integers(0, 4, size=len(days)).astype(float),
            "reb": rng.integers(0, 12, size=len(days)).astype(float),
            "tov": rng.integers(0, 5, size=len(days)).astype(float),
            "fg3m": rng.integers(0, 6, size=len(days)).astype(float),
        })

    def pandas_rates(self, app: pd.DataFrame, before: pd.Timestamp) -> dict[str, float]:
        prior = app[app["game_date"] < before]
        ratio = prior["stl"] / prior["minutes"].clip(lower=4.0)
        return {"ewma": float(ratio.ewm(halflife=20.0, adjust=True).mean().iloc[-1]),
                "exp": float(ratio.expanding().mean().iloc[-1])}

    def test_a_seeded_replay_equals_the_full_history_computation(self):
        # arrange: the snapshot is the state just before game 20; the target is after game 30
        app = self.appearances()
        as_of = app["game_date"].iloc[20]
        seed = self.pandas_rates(app, as_of)
        snapshot = pd.DataFrame({"PLAYER_ID": [1], "AS_OF": [as_of],
                                 rate_family_column("stl", "ewma"): [seed["ewma"]],
                                 rate_family_column("stl", "exp"): [seed["exp"]]})
        when = app["game_date"].iloc[-1] + pd.Timedelta(days=2)

        # act
        rates = seeded_rate_baselines(target(day=str(when.date())), app, snapshot,
                                      "2025-10-01", stats=("stl",))

        # assert
        truth = self.pandas_rates(app, when)
        assert rates.iloc[0][rate_family_column("stl", "ewma")] == pytest.approx(truth["ewma"])
        assert rates.iloc[0][rate_family_column("stl", "exp")] == pytest.approx(truth["exp"])

    def test_the_target_days_appearance_does_not_leak_unless_exact_matches_are_allowed(self):
        # arrange
        app = self.appearances()
        snapshot = pd.DataFrame(columns=["PLAYER_ID", "AS_OF"])
        day = str(app["game_date"].iloc[-1].date())

        # act
        honest = seeded_rate_baselines(target(day=day), app, snapshot, "2025-10-01", stats=("stl",))
        leaky = seeded_rate_baselines(target(day=day), app, snapshot, "2025-10-01", stats=("stl",),
                                      allow_exact_matches=True)

        # assert
        column = rate_family_column("stl", "exp")
        assert honest.iloc[0][column] == pytest.approx(
            self.pandas_rates(app, app["game_date"].iloc[-1])["exp"])
        assert leaky.iloc[0][column] != pytest.approx(honest.iloc[0][column])

    def test_the_frozen_artifact_carries_both_families(self):
        # arrange
        from score_runs import load_artifact_seed

        from fnba_ml.config import MODELS_DIR

        # act
        snapshot, start, reason = load_artifact_seed(MODELS_DIR, PROSPECTIVE_MODEL_VERSION)

        # assert
        assert reason == ""
        assert snapshot is not None and snapshot_missing_families(snapshot) == []
        assert start == "2022-10-18"


class TestBaselineEndpoints:
    def test_e5_by_hand_on_two_stats(self):
        # arrange: p1 plays (20 pts, 5 ast); p2 sits. composed on prob_active_model.
        cond = {"pts": 18.0, "ast": 6.0}
        long = pd.DataFrame(long_rows(1, "1", "g1", TARGET_DATE, 0.9, cond, p_model=0.8)
                            + long_rows(1, "2", "g1", TARGET_DATE, 0.9, cond, p_model=0.5))
        truth = pd.DataFrame([truth_row("1", "g1", True, {**full_line(0.0), "pts": 20.0, "ast": 5.0},
                                        TARGET_DATE),
                              truth_row("2", "g1", False, None, TARGET_DATE)])
        baseline = pd.DataFrame({"nba_player_id": ["1", "2"], "nba_game_id": ["g1", "g1"],
                                 ewma_total_column("pts"): [15.0, 15.0],
                                 ewma_total_column("ast"): [4.0, 4.0]})

        # act
        results = score_run(pivot_predictions(long), truth, baseline)

        # assert
        pts = 100 * (7.75 - 7.3) / 7.75     # base |12-20|,|7.5-0|; model |14.4-20|,|9-0|
        ast = 100 * (1.9 - 1.6) / 1.9       # base |3.2-5|,|2-0|;  model |4.8-5|,|3-0|
        assert result(results, "E5_rel_improvement_pct", "pts")["value"] == pytest.approx(pts)
        assert result(results, "E5_rel_improvement_pct", "ast")["value"] == pytest.approx(ast)
        aggregate = result(results, "E5_rel_improvement_pct", E5_AGGREGATE)
        assert aggregate["value"] == pytest.approx((pts + ast) / 2)
        assert aggregate["n"] == 2

    def test_brier_skill_is_positive_when_better_than_the_rate_and_negative_when_worse(self):
        # arrange
        long = pd.DataFrame(long_rows(1, "1", "g1", TARGET_DATE, 0.9, {"pts": 1.0}, p_model=0.9))
        truth = pd.DataFrame([truth_row("1", "g1", True, full_line(1.0), TARGET_DATE)])
        def base(rate: float) -> pd.DataFrame:
            return pd.DataFrame({"nba_player_id": ["1"], "nba_game_id": ["g1"], AVAIL_RATE: [rate]})

        # act
        better = score_run(pivot_predictions(long), truth, base(0.5))
        worse = score_run(pivot_predictions(long), truth, base(0.95))

        # assert
        assert result(better, "E1_brier_skill", "prob_active_model")["value"] > 0
        assert result(worse, "E1_brier_skill", "prob_active_model")["value"] < 0

    def test_relative_improvement_is_positive_when_the_model_wins(self):
        # act + assert
        assert relative_improvement_pct(9.0, 10.0) == pytest.approx(10.0)
        assert relative_improvement_pct(11.0, 10.0) == pytest.approx(-10.0)
        assert np.isnan(relative_improvement_pct(1.0, 0.0))


def shadow_runs(gap_minutes: float = 5.0) -> pd.DataFrame:
    boundary = pd.Timestamp("2026-11-01 17:00", tz="UTC")
    return pd.DataFrame({
        "id": [10, 11],
        "model_version": ["20260818", "20260818-v1"],
        "feature_version": ["v3", "v1"],
        "predicted_at": [boundary, boundary + pd.Timedelta(minutes=1)],
        "forecast_cutoff_at": [boundary, boundary + pd.Timedelta(minutes=gap_minutes)],
        "notes": [f"{PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v3-honest; channel=production",
                  f"{PROSPECTIVE_RUN_NOTE_LABEL}; feature_set=v1; channel=shadow"],
    })


def paired_predictions(days: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    truth: list[dict[str, object]] = []
    for i, day in enumerate(days):
        game = f"g{i}"
        rows += long_rows(10, "1", game, day, 0.9, {"pts": 20.0, "minutes": 30.0}, p_model=0.9)
        rows += long_rows(11, "1", game, day, 0.6, {"pts": 15.0, "minutes": 25.0}, p_model=0.6)
        truth.append(truth_row("1", game, True, {**full_line(0.0), "pts": 20.0, "minutes": 30.0},
                               day, "Regular Season"))
    return pd.DataFrame(rows), pd.DataFrame(truth)


class TestServedVsShadow:
    def test_a_shadow_pairs_with_the_served_run_at_the_same_boundary(self):
        # arrange
        predictions, _ = paired_predictions(["2026-11-02"])

        # act
        near = pair_runs(pivot_predictions(predictions), shadow_runs(5.0))
        far = pair_runs(pivot_predictions(predictions), shadow_runs(30.0))

        # assert
        assert list(zip(near["production_run_id"], near["shadow_run_id"])) == [(10, 11)]
        assert bool(near.iloc[0]["prospective"]) is True
        assert far.empty

    def test_a_shadow_on_a_different_slate_stays_unpaired(self):
        # arrange
        predictions, _ = paired_predictions(["2026-11-02"])
        predictions.loc[predictions["run_id"] == 11, "game_date"] = "2026-11-03"

        # act
        pairs = pair_runs(pivot_predictions(predictions), shadow_runs())

        # assert
        assert pairs.empty

    def test_v3_beating_v1_is_a_negative_delta_on_all_three_endpoints(self):
        # arrange
        days = [d.date().isoformat() for d in pd.date_range("2026-11-02", periods=10)]
        predictions, truth = paired_predictions(days)

        # act
        pairs, comparison = compare_served_shadow(predictions, shadow_runs(), truth)

        # assert
        assert len(pairs) == 1
        by = comparison.set_index("endpoint")
        brier = 100 * (0.1**2 - 0.4**2) / 0.4**2
        assert by.loc["availability_brier_v3_vs_v1", "delta_pct"] == pytest.approx(brier)
        assert by.loc["minutes_mae_v3_vs_v1", "delta_pct"] == pytest.approx(-100.0)
        assert by.loc["pts_uncond_mae_v3_vs_v1", "delta_pct"] == pytest.approx(
            100 * (2.0 - 11.0) / 11.0)
        assert (by["n_rows"] == 10).all()

    def test_the_comparison_feeds_the_falsification_observations(self):
        # arrange
        days = [d.date().isoformat() for d in pd.date_range("2026-11-02", periods=3)]
        predictions, truth = paired_predictions(days)
        _, comparison = compare_served_shadow(predictions, shadow_runs(), truth)
        results = score_runs(predictions, shadow_runs(), truth)

        # act
        observed = falsification_observations(results, comparison)

        # assert
        assert observed["minutes_mae_v3_vs_v1"][0] == pytest.approx(-100.0)
        value, n, note = observed["stl_expanding_vs_h20_ewma"]
        assert np.isnan(value) and n == 0 and "ewma_state" in note


class TestFalsificationTable:
    def observations(self, **values: float) -> dict[str, tuple[float, int, str]]:
        return {key: (value, 100, "") for key, value in values.items()}

    def status_of(self, table: pd.DataFrame, key: str) -> tuple[str, str]:
        row = table[table["endpoint"] == key].iloc[0]
        return row["observed"], row["status"]

    def test_each_row_is_read_against_the_frozen_bar_in_its_direction(self):
        # arrange
        observed = self.observations(
            availability_brier_skill_vs_shifted_rate=0.30,  # >= 0.25: pass
            ninecat_aggregate_vs_ewma_total=0.20,           # < 0.50: fail
            availability_brier_v3_vs_v1=2.0,                # > +1.00 at dec1: fail
            minutes_mae_v3_vs_v1=-1.0,                      # <= +0.50: pass
            stl_expanding_vs_h20_ewma=3.0,                  # no dec1 bar: report-only
        )

        # act
        table = falsification_table(observed, "dec1", rows_scored=8_000)

        # assert
        assert self.status_of(table, "availability_brier_skill_vs_shifted_rate") == (PASS, PASS)
        assert self.status_of(table, "ninecat_aggregate_vs_ewma_total") == (FAIL, FAIL)
        assert self.status_of(table, "availability_brier_v3_vs_v1") == (FAIL, FAIL)
        assert self.status_of(table, "minutes_mae_v3_vs_v1") == (PASS, PASS)
        assert self.status_of(table, "stl_expanding_vs_h20_ewma") == ("", REPORT_ONLY)
        assert self.status_of(table, "pts_uncond_mae_v3_vs_v1") == ("", NOT_COMPUTABLE)

    def test_a_look_short_of_its_row_minimum_is_non_binding(self):
        # arrange
        observed = self.observations(ninecat_aggregate_vs_ewma_total=0.20)

        # act
        table = falsification_table(observed, "dec1", rows_scored=7_499)

        # assert
        assert self.status_of(table, "ninecat_aggregate_vs_ewma_total") == (FAIL, NON_BINDING)

    def test_every_row_reads_its_threshold_from_the_frozen_config(self):
        # act
        table = falsification_table({}, "season_end", rows_scored=40_000)

        # assert
        for _, row in table.iterrows():
            assert row["threshold"] == PROSPECTIVE_FALSIFICATION[row["endpoint"]]["thresholds"]["season_end"]
        assert list(dict.fromkeys(table["row"])) == [f"F{i}" for i in range(1, 11)]

    def test_f10_is_the_override_increment_with_its_sign_flipped(self):
        # arrange: the override helped, so the scorer's increment is negative
        results = pd.DataFrame([{"run_id": pool_label("production", True), "cohort": "ALL",
                                 "endpoint": "override_increment", "stat": "prob_active",
                                 "n": 50, "value": -0.01}])

        # act
        observed = falsification_observations(results, pd.DataFrame(columns=["endpoint"]))
        table = falsification_table(observed, "all_star", rows_scored=25_000)

        # assert
        assert observed["override_layer_brier_increment"][0] == pytest.approx(0.01)
        assert self.status_of(table, "override_layer_brier_increment") == (PASS, PASS)


class TestLookCli:
    def test_look_defaults_the_window_to_the_season_before_its_cutoff(self):
        # arrange
        import score_runs

        # act
        args = score_runs.parse_args(["--look", "dec1"])

        # assert
        assert args.look == "dec1"
        assert args.since.isoformat() == "2026-10-01"
        assert args.until.isoformat() == "2026-11-30"
        assert args.version == PROSPECTIVE_MODEL_VERSION
        assert "dec1" in args.md.name

    def test_an_unknown_look_is_rejected(self):
        # arrange
        import score_runs

        # act + assert
        with pytest.raises(SystemExit):
            score_runs.parse_args(["--look", "january"])

    def test_without_look_the_window_is_the_last_thirty_days(self):
        # arrange
        import score_runs

        # act
        args = score_runs.parse_args([])

        # assert
        assert args.look is None
        assert (args.until - args.since).days == score_runs.DEFAULT_LOOKBACK_DAYS

    def test_a_look_run_writes_the_look_report(self, monkeypatch, tmp_path):
        # arrange
        import score_runs

        runs = shadow_runs()
        predictions, truth = paired_predictions(["2026-11-02", "2026-12-05"])
        history = pd.DataFrame([status("1", "gH", "2026-10-25", True, 30.0, pts=18.0)])
        frames = {
            "information_schema": pd.DataFrame({"column_name": list(runs.columns)}),
            "WITH involved": history,
            "FROM player_game_logs l": pd.DataFrame(columns=["nba_player_id", "game_date",
                                                             "minutes", "stl", "reb", "tov",
                                                             "fg3m"]),
            "WITH predicted": truth,
            "FROM player_game_predictions": predictions,
            "FROM prediction_runs": runs,
        }

        def read(sql: str, params: dict[str, object] | None = None) -> pd.DataFrame:
            return next(frame for marker, frame in frames.items() if marker in sql)

        monkeypatch.setattr(score_runs, "_read_sql", read)
        md, csv = tmp_path / "look.md", tmp_path / "look.csv"

        # act
        code = score_runs.main(["--look", "dec1", "--md", str(md), "--csv", str(csv)])

        # assert
        text = md.read_text(encoding="utf-8")
        assert code == 0
        assert "## Look report: dec1" in text
        assert "NON-BINDING" in text
        assert "v3 served vs v1 shadow" in text
        scored = pd.read_csv(csv)
        assert (scored["endpoint"] == "v3_vs_v1_delta_pct").any()
        assert pd.to_datetime(predictions["game_date"]).max() > pd.Timestamp("2026-12-01")
        assert not scored[scored["endpoint"] == "coverage"]["n"].gt(1).any()


class TestBaselinesFollowThePostseasonSwitch:
    def history(self) -> pd.DataFrame:
        # ten scheduled regular-season games, the first a DNP, then two playoff
        # games at 40 minutes just before the target; the target is regular season.
        rows = [{**status("1", f"g{i}", days_before(20 - i), i > 0, 20.0, pts=10.0),
                 "season_type": "Regular Season"} for i in range(10)]
        rows += [{**status("1", f"p{i}", days_before(3 - i), True, 40.0, pts=36.0),
                  "season_type": "Playoffs"} for i in range(2)]
        rows += [{**status("1", "gT", TARGET_DATE, False), "season_type": "Regular Season"}]
        return pd.DataFrame(rows)

    def test_off_reads_the_regular_season_only(self):
        # act
        baselines = build_baselines(target(), self.history(), include_postseason=False)

        # assert
        assert baselines.iloc[0]["ewma_MIN"] == pytest.approx(20.0)
        assert baselines.iloc[0][ROLL10_MIN] == pytest.approx(20.0)

    def test_on_reads_the_playoff_appearances_as_features_py_does(self):
        # arrange
        minutes = pd.Series([20.0] * 9 + [40.0, 40.0])
        expected = float(minutes.ewm(halflife=5.0, adjust=True).mean().iloc[-1])

        # act
        baselines = build_baselines(target(), self.history(), include_postseason=True)

        # assert
        assert baselines.iloc[0]["ewma_MIN"] == pytest.approx(expected)
        assert baselines.iloc[0][ROLL10_MIN] == pytest.approx((8 * 20.0 + 2 * 40.0) / 10)

    def test_availability_stays_on_scheduled_regular_season_rows_either_way(self):
        # act
        off = build_baselines(target(), self.history(), include_postseason=False)
        on = build_baselines(target(), self.history(), include_postseason=True)

        # assert
        assert off.iloc[0][AVAIL_RATE] == pytest.approx(9 / 10)
        assert on.iloc[0][AVAIL_RATE] == pytest.approx(9 / 10)

    def test_the_default_follows_the_model_switch(self):
        # act
        default = build_baselines(target(), self.history())
        off = build_baselines(target(), self.history(), include_postseason=False)

        # assert
        assert config.RATE_HISTORY_INCLUDES_POSTSEASON is False
        assert default.iloc[0]["ewma_MIN"] == off.iloc[0]["ewma_MIN"]

    def test_the_rate_families_drop_playoff_appearances_when_off(self):
        # arrange
        app = pd.DataFrame({
            "nba_player_id": ["1", "1", "1"],
            "game_date": pd.to_datetime([days_before(5), days_before(3), days_before(1)]),
            "season_type": ["Regular Season", "Regular Season", "Playoffs"],
            "minutes": [20.0, 20.0, 20.0],
            "stl": [1.0, 1.0, 5.0],
        })
        snapshot = pd.DataFrame(columns=["PLAYER_ID", "AS_OF"])

        # act
        off = seeded_rate_baselines(target(), app, snapshot, "2025-10-01", stats=("stl",),
                                    include_postseason=False)
        on = seeded_rate_baselines(target(), app, snapshot, "2025-10-01", stats=("stl",),
                                   include_postseason=True)

        # assert
        assert off.iloc[0][rate_family_column("stl", "exp")] == pytest.approx(1.0 / 20.0)
        assert on.iloc[0][rate_family_column("stl", "exp")] == pytest.approx(7.0 / 60.0)
