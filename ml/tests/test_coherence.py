from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

import predict
import report_coherence
from fnba_ml.coherence import (
    COHERENCE_VARIANTS,
    PTS_IDENTITY_DELTA,
    TEAM_MIN_FACTOR,
    apply_coherence,
    enforce_points_identity,
    renormalise_team_minutes,
    team_minute_sums,
)
from fnba_ml.eval_coherence import (
    ENDPOINT_COLUMNS,
    coherence_endpoints,
    minute_sum_diagnostic,
    paired_errors,
)
from fnba_ml.intervals import quantile_columns

STATS = ("PTS", "FGM", "FG3M", "FTM", "FGA", "FTA")
RATES = {"PTS": 0.5, "FGM": 0.2, "FG3M": 0.05, "FTM": 0.08, "FGA": 0.4, "FTA": 0.1}
OFFSETS = (-3.0, 0.0, 3.0)


def team_rows(game: str, team: int, total: float, n: int = 10) -> pd.DataFrame:
    """n players with P_PLAY 1 whose conditional minutes sum to ``total``."""
    minutes = np.full(n, total / n)
    frame = pd.DataFrame({
        "PLAYER_ID": [team * 100 + i for i in range(n)],
        "GAME_ID": game,
        "TEAM_ID": team,
        "P_PLAY": 1.0,
        "E_MIN_COND": minutes,
        "E_MIN": minutes,
    })
    for level, offset in zip(quantile_columns("MIN").values(), OFFSETS):
        frame[level] = minutes + 10.0 + offset
    for stat in STATS:
        cond = minutes * RATES[stat]
        frame[f"E_{stat}_COND"] = cond
        frame[f"E_{stat}"] = cond
        for level, offset in zip(quantile_columns(stat).values(), OFFSETS):
            frame[level] = cond + 5.0 + offset
    return frame


def synthetic() -> pd.DataFrame:
    return pd.concat(
        [team_rows("G1", 1, 200.0), team_rows("G1", 2, 280.0)], ignore_index=True
    )


def team(frame: pd.DataFrame, team_id: int) -> pd.DataFrame:
    return frame[frame["TEAM_ID"] == team_id]


class TestTeamMinutes:
    def test_factors_are_240_over_the_sum_within_bounds(self) -> None:
        # arrange
        frame = synthetic()

        # act
        out = renormalise_team_minutes(frame)

        # assert
        assert np.allclose(team(out, 1)[TEAM_MIN_FACTOR], 1.2)
        assert np.allclose(team(out, 2)[TEAM_MIN_FACTOR], 240.0 / 280.0)

    def test_the_unclipped_team_sums_to_240_after(self) -> None:
        # arrange
        frame = synthetic()

        # act
        out = renormalise_team_minutes(frame)

        # assert
        assert team_minute_sums(team(out, 1)).iloc[0] == pytest.approx(240.0)
        assert team_minute_sums(team(out, 2)).iloc[0] == pytest.approx(240.0)

    def test_a_team_at_190_is_clipped_to_the_upper_bound_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # arrange
        frame = team_rows("G2", 3, 190.0)

        # act
        with caplog.at_level(logging.INFO, logger="fnba_ml.coherence"):
            out = renormalise_team_minutes(frame)

        # assert
        assert np.allclose(out[TEAM_MIN_FACTOR], 1.25)
        assert team_minute_sums(out).iloc[0] == pytest.approx(190.0 * 1.25)
        assert any("clipped" in r.getMessage() for r in caplog.records)

    def test_every_production_stat_scales_by_the_same_factor(self) -> None:
        # arrange
        frame = synthetic()

        # act
        out = renormalise_team_minutes(frame)

        # assert
        factor = out[TEAM_MIN_FACTOR].to_numpy()
        for stat in ("MIN", *STATS):
            assert np.allclose(out[f"E_{stat}_COND"], frame[f"E_{stat}_COND"] * factor)
            assert np.allclose(out[f"E_{stat}"], frame[f"E_{stat}"] * factor)

    def test_quantiles_shift_by_the_conditional_delta_and_do_not_cross(self) -> None:
        # arrange
        frame = synthetic()

        # act
        out = renormalise_team_minutes(frame)

        # assert
        for stat in ("MIN", *STATS):
            delta = out[f"E_{stat}_COND"] - frame[f"E_{stat}_COND"]
            columns = list(quantile_columns(stat).values())
            for column in columns:
                assert np.allclose(out[column], frame[column] + delta)
            stacked = out[columns].to_numpy()
            assert (np.diff(stacked, axis=1) >= 0).all()

    def test_p_play_is_untouched(self) -> None:
        # arrange
        frame = synthetic()
        frame["P_PLAY"] = np.linspace(0.1, 1.0, len(frame))

        # act
        out = renormalise_team_minutes(frame)

        # assert
        pd.testing.assert_series_equal(out["P_PLAY"], frame["P_PLAY"])

    def test_the_sum_is_weighted_by_p_play(self) -> None:
        # arrange
        frame = team_rows("G3", 4, 240.0)
        frame["P_PLAY"] = 0.5

        # act
        out = renormalise_team_minutes(frame)

        # assert
        assert np.allclose(out[TEAM_MIN_FACTOR], 1.25)


class TestPointsIdentity:
    def test_pts_is_set_to_the_formula_and_the_unconditional_follows(self) -> None:
        # arrange
        frame = synthetic()
        frame["P_PLAY"] = 0.8

        # act
        out = enforce_points_identity(frame)

        # assert
        expected = 2 * frame["E_FGM_COND"] + frame["E_FG3M_COND"] + frame["E_FTM_COND"]
        assert np.allclose(out["E_PTS_COND"], expected)
        assert np.allclose(out["E_PTS"], 0.8 * expected)
        assert np.allclose(out[PTS_IDENTITY_DELTA], expected - frame["E_PTS_COND"])
        for column in quantile_columns("PTS").values():
            assert np.allclose(out[column], frame[column] + out[PTS_IDENTITY_DELTA])

    def test_applying_it_twice_is_idempotent(self) -> None:
        # arrange
        once = enforce_points_identity(synthetic())

        # act
        twice = enforce_points_identity(once)

        # assert
        pd.testing.assert_frame_equal(
            twice.drop(columns=[PTS_IDENTITY_DELTA]),
            once.drop(columns=[PTS_IDENTITY_DELTA]),
        )
        assert np.allclose(twice[PTS_IDENTITY_DELTA], 0.0)

    def test_a_frame_missing_ftm_leaves_pts_untouched(self) -> None:
        # arrange
        frame = synthetic().drop(columns=["E_FTM_COND", "E_FTM"])

        # act
        out = enforce_points_identity(frame)

        # assert
        pd.testing.assert_frame_equal(out, frame)


class TestApplyCoherence:
    def test_none_is_a_byte_identical_identity(self) -> None:
        # arrange
        frame = synthetic()
        before = frame.copy()

        # act
        out = apply_coherence(frame, "none")

        # assert
        pd.testing.assert_frame_equal(out, before)
        assert list(out.columns) == list(before.columns)

    def test_all_applies_minutes_then_identity(self) -> None:
        # arrange
        frame = synthetic()

        # act
        out = apply_coherence(frame, "all")

        # assert
        assert TEAM_MIN_FACTOR in out.columns
        expected = 2 * out["E_FGM_COND"] + out["E_FG3M_COND"] + out["E_FTM_COND"]
        assert np.allclose(out["E_PTS_COND"], expected)

    def test_an_unknown_variant_is_refused(self) -> None:
        # act + assert
        with pytest.raises(ValueError):
            apply_coherence(synthetic(), "everything")


class TestEvaluation:
    def scored(self) -> pd.DataFrame:
        frame = synthetic()
        frame["PLAYED"] = 1
        frame["MIN"] = 24.0
        frame["PTS"] = 12.0
        return frame

    def test_endpoints_are_tidy_and_cover_every_variant(self) -> None:
        # arrange
        frame = self.scored()

        # act
        table = coherence_endpoints(frame)

        # assert
        assert list(table.columns) == ENDPOINT_COLUMNS
        assert set(table["variant"]) == set(COHERENCE_VARIANTS)
        all_rows = table[table["cohort"] == "ALL"]
        assert (all_rows["n"] == len(frame)).all()

    def test_team_minutes_removes_the_minute_error_when_truth_is_240(self) -> None:
        # arrange
        frame = self.scored()

        # act
        table = coherence_endpoints(frame).set_index(["variant", "endpoint", "cohort"])

        # assert
        assert table.loc[("team_minutes", "cond_min_mae", "ALL"), "mae"] == pytest.approx(0.0)
        assert table.loc[("none", "cond_min_mae", "ALL"), "mae"] == pytest.approx(4.0)

    def test_paired_errors_align_rows(self) -> None:
        # arrange
        frame = self.scored()

        # act
        paired = paired_errors(frame, "team_minutes")

        # assert
        cond = paired[paired["endpoint"] == "cond_min_mae"]
        assert len(cond) == len(frame)
        assert np.allclose(cond["team_minutes"], 0.0)

    def test_minute_sum_diagnostic(self) -> None:
        # act
        diag = minute_sum_diagnostic(self.scored())

        # assert
        assert diag["team_games"] == 2
        assert diag["mean"] == pytest.approx(240.0)
        assert diag["share_outside"] == pytest.approx(1.0)


class TestWiring:
    def test_predict_accepts_the_coherence_flag(self) -> None:
        # act
        args = predict.parse_args(["--version", "v", "--coherence", "all"])

        # assert
        assert args.coherence == "all"

    def test_predict_defaults_to_none(self) -> None:
        # act + assert
        assert predict.parse_args(["--version", "v"]).coherence == "none"

    def test_notes_carry_the_coherence_choice(self) -> None:
        # act
        notes = predict.run_notes(3, 10, "slate", "all")

        # assert
        assert notes.endswith("coherence=all")
        assert "slate" in notes

    def test_report_cli_parses_its_args(self) -> None:
        # act
        args = report_coherence.parse_args(["--dataset", "x.parquet", "--version", "v"])

        # assert
        assert args.version == "v"
