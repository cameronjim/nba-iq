from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import daily_run
import predict
from fnba_ml.config import PRESEASON_MINUTES_PRIOR, UNKNOWN_TIER
from fnba_ml.intervals import quantile_columns
from fnba_ml.preseason import (
    PRESEASON_PRIOR_APPLIED,
    PRESEASON_PRIOR_TIER,
    apply_preseason_minutes_prior,
)

STATS = ("PTS", "REB", "FGM", "FGA")
RATES = {"PTS": 0.5, "REB": 0.2, "FGM": 0.18, "FGA": 0.4}
OFFSETS = (-6.0, 0.0, 6.0)
STAR = "star (>=30)"


def predictions(minutes: list[float], p_play: list[float]) -> pd.DataFrame:
    """composed rows: E_stat_COND = rate x minutes and E_stat = P_PLAY x conditional."""
    n = len(minutes)
    cond_min = np.array(minutes, dtype=float)
    p = np.array(p_play, dtype=float)
    frame = pd.DataFrame({
        "PLAYER_ID": [str(i) for i in range(n)],
        "GAME_ID": [f"G{i}" for i in range(n)],
        "P_PLAY": p,
        "E_MIN_COND": cond_min,
        "E_MIN": p * cond_min,
    })
    for column, offset in zip(quantile_columns("MIN").values(), OFFSETS):
        frame[column] = np.clip(cond_min + offset, 0.0, None)
    for stat in STATS:
        cond = cond_min * RATES[stat]
        frame[f"E_{stat}_COND"] = cond
        frame[f"E_{stat}"] = p * cond
        for column, offset in zip(quantile_columns(stat).values(), OFFSETS):
            frame[column] = np.clip(cond + offset / 2.0, 0.0, None)
    return frame


def features(roll10: list[float], season_types: list[str]) -> pd.DataFrame:
    n = len(roll10)
    return pd.DataFrame({
        "PLAYER_ID": [str(i) for i in range(n)],
        "GAME_ID": [f"G{i}" for i in range(n)],
        "roll10_MIN": roll10,
        "SEASON_TYPE": season_types,
    })


def star_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    """one preseason star projected at 27.5 and one regular-season star at 34."""
    return (
        predictions([27.5, 34.0], [0.9, 0.95]),
        features([33.5, 34.2], ["Pre Season", "Regular Season"]),
    )


class TestPreseasonMinutesPrior:
    def test_a_star_takes_the_star_prior_at_full_weight(self) -> None:
        # arrange
        preds, feats = star_frames()

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        assert out.loc[0, "E_MIN_COND"] == pytest.approx(21.65)
        assert out.loc[0, PRESEASON_PRIOR_TIER] == STAR
        assert bool(out.loc[0, PRESEASON_PRIOR_APPLIED]) is True

    def test_half_weight_lands_on_the_midpoint(self) -> None:
        # arrange
        preds, feats = star_frames()

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=0.5)

        # assert
        assert out.loc[0, "E_MIN_COND"] == pytest.approx((21.65 + 27.5) / 2.0)

    def test_every_stat_scales_by_the_minutes_ratio(self) -> None:
        # arrange
        preds, feats = star_frames()
        ratio = 21.65 / 27.5

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        for stat in STATS:
            assert out.loc[0, f"E_{stat}_COND"] == pytest.approx(
                preds.loc[0, f"E_{stat}_COND"] * ratio
            )
            assert out.loc[0, f"E_{stat}"] == pytest.approx(preds.loc[0, f"E_{stat}"] * ratio)

    def test_quantiles_shift_by_the_conditional_delta_and_stay_ordered(self) -> None:
        # arrange
        preds, feats = star_frames()

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        for target in ("MIN", *STATS):
            delta = out.loc[0, f"E_{target}_COND"] - preds.loc[0, f"E_{target}_COND"]
            columns = list(quantile_columns(target).values())
            for column in columns:
                expected = max(preds.loc[0, column] + delta, 0.0)
                assert out.loc[0, column] == pytest.approx(expected)
            values = out.loc[0, columns].to_numpy(dtype=float)
            assert np.all(np.diff(values) >= 0)

    def test_unconditional_is_p_play_times_conditional_and_p_play_is_untouched(
        self,
    ) -> None:
        # arrange
        preds, feats = star_frames()

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        assert out.loc[0, "P_PLAY"] == preds.loc[0, "P_PLAY"]
        for target in ("MIN", *STATS):
            assert out.loc[0, f"E_{target}"] == pytest.approx(
                out.loc[0, "P_PLAY"] * out.loc[0, f"E_{target}_COND"]
            )

    def test_a_regular_season_row_is_untouched_byte_for_byte(self) -> None:
        # arrange
        preds, feats = star_frames()

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        pd.testing.assert_frame_equal(
            out.loc[[1], preds.columns], preds.loc[[1]], check_exact=True
        )
        assert bool(out.loc[1, PRESEASON_PRIOR_APPLIED]) is False
        assert out.loc[1, PRESEASON_PRIOR_TIER] is None

    def test_a_player_with_no_history_gets_the_unknown_prior(self) -> None:
        # arrange
        preds = predictions([9.0], [0.7])
        feats = features([np.nan], ["Pre Season"])

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        assert out.loc[0, PRESEASON_PRIOR_TIER] == UNKNOWN_TIER
        assert out.loc[0, "E_MIN_COND"] == pytest.approx(PRESEASON_MINUTES_PRIOR[UNKNOWN_TIER])

    def test_a_fringe_player_is_raised_to_the_fringe_prior(self) -> None:
        # arrange
        preds = predictions([6.0], [0.6])
        feats = features([5.9], ["Pre Season"])

        # act
        out = apply_preseason_minutes_prior(preds, feats, weight=1.0)

        # assert
        assert out.loc[0, "E_MIN_COND"] == pytest.approx(13.99)

    def test_rows_are_matched_by_key_not_by_position(self) -> None:
        # arrange
        preds, feats = star_frames()
        shuffled = feats.iloc[::-1].reset_index(drop=True)

        # act
        out = apply_preseason_minutes_prior(preds, shuffled, weight=1.0)

        # assert
        assert out[PRESEASON_PRIOR_APPLIED].tolist() == [True, False]

    def test_a_frame_without_a_season_type_is_refused(self) -> None:
        # arrange
        preds, feats = star_frames()

        # act + assert
        with pytest.raises(ValueError, match="SEASON_TYPE"):
            apply_preseason_minutes_prior(preds, feats.drop(columns=["SEASON_TYPE"]))


class TestPredictFlag:
    def test_the_prior_defaults_to_off(self) -> None:
        # act + assert
        assert predict.parse_args(["--version", "v"]).preseason_prior == "off"

    def test_the_flag_parses_on(self) -> None:
        # act
        args = predict.parse_args(["--version", "v", "--preseason-prior", "on"])

        # assert
        assert args.preseason_prior == "on"

    def test_scenarios_and_the_prior_are_refused_together(self) -> None:
        # act + assert
        with pytest.raises(SystemExit):
            predict.parse_args(["--version", "v", "--preseason-prior", "on", "--scenarios"])

    def test_notes_carry_the_token_only_when_on(self) -> None:
        # act
        on = predict.run_notes(3, 10, "slate", "none", "on")
        off = predict.run_notes(3, 10, "slate", "none", "off")

        # assert
        assert on.endswith("preseason_prior=on")
        assert "preseason_prior" not in off
        assert off == predict.run_notes(3, 10, "slate")


class TestDailyRunArgv:
    @staticmethod
    def _argv(**overrides: object) -> list[str]:
        kwargs: dict[str, object] = dict(
            dataset_path=Path("prospective_extended.parquet"),
            models_dir=Path("models"),
            out_path=Path("predictions_extended.parquet"),
            notes="note",
            horizon="none",
            window_start=date(2026, 10, 3),
            statuses_as_of=pd.Timestamp("2026-10-03T15:30:00Z"),
            statuses_path=None,
            history_through=date(2026, 4, 12),
            write_db=True,
        )
        kwargs.update(overrides)
        return daily_run.predict_argv(**kwargs)

    def test_run_b_argv_carries_the_flag_and_parses(self) -> None:
        # act
        argv = self._argv(preseason_prior="on")

        # assert
        assert argv[argv.index("--preseason-prior") + 1] == "on"
        assert predict.parse_args(argv).preseason_prior == "on"

    def test_run_a_argv_has_no_flag(self) -> None:
        # act
        argv = self._argv(horizon="gameday")

        # assert
        assert "--preseason-prior" not in argv
        assert predict.parse_args(argv).preseason_prior == "off"

    def test_the_season_type_is_looked_up_by_game(self) -> None:
        # arrange
        frame = pd.DataFrame({"GAME_ID": ["0012600001", "0022600001"], "PLAYER_ID": ["1", "2"]})
        schedule = pd.DataFrame({
            "GAME_ID": ["0022600001", "0012600001"],
            "SEASON_TYPE": ["Regular Season", "Pre Season"],
        })

        # act
        out = daily_run.with_season_type(frame, schedule)

        # assert
        assert out["SEASON_TYPE"].tolist() == ["Pre Season", "Regular Season"]
        assert "SEASON_TYPE" not in frame.columns
