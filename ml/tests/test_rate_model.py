from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

from fnba_ml import config  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    POS_GROUP_ORDER,
    RATE_CONTEXT_COLS,
    RATE_MINUTES_FLOOR,
    RATE_MODEL_CUTOFF,
)
from fnba_ml.features import build_features  # noqa: E402
from fnba_ml.matchup import attach_v4_features  # noqa: E402
from fnba_ml.models import (  # noqa: E402
    MIN_PRED,
    MIN_PRED_CUTOFF,
    P_PLAY,
    P_PLAY_CUTOFF,
    LeakageError,
    PerMinuteRate,
)
from fnba_ml.rate_model import (  # noqa: E402
    DESIGN_COLUMNS,
    ResidualRateModel,
    challenger_rate_column,
    design_matrix,
    residual_rate_estimates,
    residual_target,
)

CUTOFF = pd.Timestamp("2024-12-01")
KEY = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]


def _synthetic_appearances(n: int, seed: int, home_effect: float = 0.0) -> pd.DataFrame:
    """appearance rows whose residual from the served rate is pure noise, plus a
    planted per-minute effect on home games."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "PLAYER_ID": [f"p{i % 300}" for i in range(n)],
        "GAME_ID": [f"g{i}" for i in range(n)],
        "TEAM_ID": "t",
        "GAME_DATE": pd.Timestamp("2024-10-01")
        + pd.to_timedelta(rng.integers(0, 60, n), unit="D"),
        "PLAYED": 1,
        "MIN": rng.uniform(6.0, 38.0, n),
        "POS_GROUP": rng.choice(list(POS_GROUP_ORDER), n),
    })
    for col in RATE_CONTEXT_COLS:
        frame[col] = rng.normal(0.0, 1.0, n)
    frame["IS_HOME"] = rng.integers(0, 2, n)
    frame["ewma_MIN"] = frame["MIN"] + rng.normal(0.0, 3.0, n)
    frame["ewma_PTS_per_min"] = rng.uniform(0.3, 0.8, n)
    ratio = (
        frame["ewma_PTS_per_min"]
        + rng.normal(0.0, 0.15, n)
        + home_effect * frame["IS_HOME"]
    )
    frame["PTS"] = ratio * frame["MIN"].clip(lower=RATE_MINUTES_FLOOR)
    return frame


def _shuffle_context(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = frame.copy()
    for col in [*RATE_CONTEXT_COLS, "POS_GROUP"]:
        out[col] = rng.permutation(out[col].to_numpy())
    return out


def _improvement_over_zero(model: ResidualRateModel, holdout: pd.DataFrame) -> float:
    scored = holdout.assign(**{MIN_PRED: holdout["ewma_MIN"]})
    y = residual_target(holdout, "PTS", model.served_rate(holdout))
    pred = model.predict_residual(scored)
    return 1.0 - np.mean(np.abs(y - pred)) / np.mean(np.abs(y))


def _home_gap(model: ResidualRateModel, holdout: pd.DataFrame) -> float:
    scored = holdout.assign(**{MIN_PRED: holdout["ewma_MIN"]})
    pred = model.predict_residual(scored)
    home = holdout["IS_HOME"].to_numpy() == 1
    return float(pred[home].mean() - pred[~home].mean())


@pytest.fixture(scope="module")
def v4_frame(features_status: pd.DataFrame, team_logs: pd.DataFrame) -> pd.DataFrame:
    return attach_v4_features(features_status, team_logs)


def test_the_residual_target_is_the_floored_ratio_minus_the_served_rate() -> None:
    # arrange
    frame = pd.DataFrame({"PTS": [12.0, 2.0, 0.0], "MIN": [24.0, 2.0, 30.0]})
    served = np.array([0.4, 0.25, 0.1])

    # act
    target = residual_target(frame, "PTS", served)

    # assert
    # 12/24 - 0.4; 2/max(2, 4) - 0.25; 0/30 - 0.1
    np.testing.assert_allclose(target, [0.1, 0.25, -0.1])


def test_fit_refuses_training_rows_on_or_after_the_cutoff() -> None:
    # arrange
    frame = _synthetic_appearances(400, seed=1)
    frame.loc[0, "GAME_DATE"] = CUTOFF

    # act + assert
    with pytest.raises(LeakageError, match="on or after the cutoff"):
        ResidualRateModel(cutoff=CUTOFF).fit(frame, "PTS")


def test_predictions_carry_the_cutoff_and_scoring_requires_min_pred() -> None:
    # arrange
    train = _synthetic_appearances(2000, seed=2)
    model = ResidualRateModel(cutoff=CUTOFF).fit(train, "PTS")
    valid = _synthetic_appearances(50, seed=3)

    # act
    stamped = model.attach(valid.assign(**{MIN_PRED: valid["ewma_MIN"]}))

    # assert
    assert (stamped[RATE_MODEL_CUTOFF] == CUTOFF).all()
    assert (stamped[challenger_rate_column("PTS")] >= 0.0).all()
    with pytest.raises(ValueError, match=MIN_PRED):
        model.predict(valid)


def test_shuffled_context_buys_nothing_and_a_planted_home_effect_is_recovered() -> None:
    # arrange
    null_train = _shuffle_context(_synthetic_appearances(8000, seed=4), seed=5)
    null_holdout = _synthetic_appearances(4000, seed=6)
    planted_train = _synthetic_appearances(8000, seed=4, home_effect=0.1)
    planted_holdout = _synthetic_appearances(4000, seed=6, home_effect=0.1)
    shuffled_planted = _shuffle_context(planted_train, seed=5)

    # act
    null_model = ResidualRateModel(cutoff=CUTOFF).fit(null_train, "PTS")
    planted_model = ResidualRateModel(cutoff=CUTOFF).fit(planted_train, "PTS")
    shuffled_model = ResidualRateModel(cutoff=CUTOFF).fit(shuffled_planted, "PTS")

    # assert
    # pure noise costs the shallow booster ~1-2% of residual MAE across seeds
    # and never buys anything.
    assert -0.03 < _improvement_over_zero(null_model, null_holdout) < 0.005
    assert _home_gap(planted_model, planted_holdout) == pytest.approx(0.1, abs=0.03)
    assert _improvement_over_zero(planted_model, planted_holdout) > 0.05
    assert abs(_home_gap(shuffled_model, planted_holdout)) < 0.02


def test_a_rows_own_box_score_moves_its_target_but_not_its_features_or_prediction(
    universe_status: pd.DataFrame, team_logs: pd.DataFrame, v4_frame: pd.DataFrame
) -> None:
    # arrange
    train = v4_frame[v4_frame["GAME_DATE"] < CUTOFF]
    model = ResidualRateModel(cutoff=CUTOFF).fit(train, "PTS")
    later = universe_status[
        (pd.to_datetime(universe_status["GAME_DATE"]) >= CUTOFF)
        & (universe_status["PLAYED"] == 1)
        & (universe_status["MIN"] > 0)
    ].sort_values("GAME_DATE")
    victim = later.groupby("PLAYER_ID").head(1).iloc[0]
    victim_key = tuple(victim[KEY])
    flipped_universe = universe_status.copy()
    hit = (flipped_universe[KEY] == pd.Series(victim_key, index=KEY)).all(axis=1)
    flipped_universe.loc[hit, "PTS"] = flipped_universe.loc[hit, "PTS"] + 40.0
    flipped = attach_v4_features(build_features(flipped_universe), team_logs)

    def victim_rows(frame: pd.DataFrame) -> pd.DataFrame:
        rows = frame[(frame[KEY] == pd.Series(victim_key, index=KEY)).all(axis=1)]
        rows = rows.reset_index(drop=True)
        return rows.assign(**{MIN_PRED: rows["ewma_MIN"].fillna(10.0)})

    before, after = victim_rows(v4_frame), victim_rows(flipped)

    # act
    x_before = design_matrix(before, model.served_rate(before), before[MIN_PRED])
    x_after = design_matrix(after, model.served_rate(after), after[MIN_PRED])
    target_before = residual_target(before, "PTS", model.served_rate(before))
    target_after = residual_target(after, "PTS", model.served_rate(after))

    # assert
    pd.testing.assert_frame_equal(x_before, x_after)
    np.testing.assert_array_equal(model.predict(before), model.predict(after))
    assert not np.allclose(target_before, target_after)
    # the counter-assertion: the flip took effect, on the player's later rows
    player = victim["PLAYER_ID"]
    nxt_before = v4_frame[(v4_frame["PLAYER_ID"] == player)
                          & (v4_frame["GAME_DATE"] > victim["GAME_DATE"])]
    nxt_after = flipped[(flipped["PLAYER_ID"] == player)
                        & (flipped["GAME_DATE"] > victim["GAME_DATE"])]
    assert len(nxt_before) and len(nxt_after)
    assert not np.allclose(
        nxt_before.sort_values("GAME_DATE")["ewma_PTS_per_min"].to_numpy(dtype=float),
        nxt_after.sort_values("GAME_DATE")["ewma_PTS_per_min"].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_estimates_compose_through_minutes_and_refuse_a_mismatched_cutoff(
    v4_frame: pd.DataFrame,
) -> None:
    # arrange
    train = v4_frame[v4_frame["GAME_DATE"] < CUTOFF]
    valid = v4_frame[v4_frame["GAME_DATE"] >= CUTOFF].reset_index(drop=True)
    scored = valid.assign(**{
        P_PLAY: 0.8, P_PLAY_CUTOFF: CUTOFF,
        MIN_PRED: valid["ewma_MIN"].fillna(10.0), MIN_PRED_CUTOFF: CUTOFF,
    })
    model = ResidualRateModel(cutoff=CUTOFF).fit(train, "PTS")
    early = ResidualRateModel(cutoff=CUTOFF - pd.Timedelta(days=7)).fit(
        train[train["GAME_DATE"] < CUTOFF - pd.Timedelta(days=7)], "PTS"
    )

    # act
    cond, uncond = residual_rate_estimates(scored, {"PTS": model})["PTS"]

    # assert
    np.testing.assert_allclose(cond, scored[MIN_PRED].to_numpy() * model.predict(scored))
    np.testing.assert_allclose(uncond, 0.8 * cond)
    with pytest.raises(LeakageError, match="different cutoffs"):
        residual_rate_estimates(scored, {"PTS": early})


def test_the_served_rate_is_the_champion_per_minute_rate(v4_frame: pd.DataFrame) -> None:
    # arrange
    train = v4_frame[v4_frame["GAME_DATE"] < CUTOFF]
    valid = v4_frame[v4_frame["GAME_DATE"] >= CUTOFF]

    # act
    model = ResidualRateModel(cutoff=CUTOFF).fit(train, "REB")

    # assert
    champion = PerMinuteRate("REB").fit(train[train["PLAYED"] == 1])
    np.testing.assert_allclose(model.served_rate(valid), champion.predict(valid))
    assert list(model.feature_gain().index.sort_values()) == sorted(DESIGN_COLUMNS)


def test_v5_stakes_is_the_seven_stakes_columns_and_nothing_dropped() -> None:
    # arrange
    dropped = {
        "late_season", "stakes_lockedness", "top5_min_share_10",
        *config.BLOWOUT_FEATURE_COLS, *config.MATCHUP_FEATURE_COLS,
    }

    # act
    added = config.FEATURE_SETS["v5-stakes"][len(config.FEATURE_COLS):]

    # assert
    assert added == [
        "team_games_remaining", "team_win_pct", "team_games_over_500",
        "stakes_late_x_over500", "stakes_x_minutes_share", "stakes_x_veteran",
        "minutes_share",
    ]
    assert config.FEATURE_SETS["v5-stakes"][: len(config.FEATURE_COLS)] == (
        config.FEATURE_COLS
    )
    assert not dropped & set(config.FEATURE_SETS["v5-stakes"])
    assert config.CANDIDATE_FEATURE_VERSION_V5 == "v5" != config.FEATURE_VERSION
    assert set(added) <= set(config.V4_FEATURE_COLS)


def test_the_rate_model_reads_no_outcome_column() -> None:
    # act + assert
    assert not config.TARGET_COLS & set(DESIGN_COLUMNS)
    assert not config.TARGET_COLS & set(RATE_CONTEXT_COLS)
    assert config.RATE_MODEL_TARGETS == ("PTS", "AST", "REB", "FGA")
