from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import CHAMPIONS
from fnba_ml.count_model import (
    COUNT_CORE_FEATURES,
    COUNT_OPTIONAL_FEATURES,
    COUNT_PRED_CUTOFF,
    COUNT_TARGETS,
    CountModel,
    count_features,
    count_pred_column,
    count_unconditional,
    log_offset,
    param_grid,
    poisson_deviance,
    select_count_params,
)
from fnba_ml.eval_core import split
from fnba_ml.eval_counts import (
    ENDPOINT_COND_MAE,
    ENDPOINT_DEVIANCE,
    ENDPOINT_UNCOND_MAE,
    VARIANT_CHAMPION,
    VARIANT_COUNT,
    cond_column,
    count_endpoints,
    score_count_origin,
    uncond_column,
)
from fnba_ml.features import available_features
from fnba_ml.models import (
    MIN_PRED_CUTOFF,
    P_PLAY,
    P_PLAY_CUTOFF,
    LeakageError,
    MinutesModel,
)

CUTOFF = pd.Timestamp("2024-12-01")
VALID_END = "2024-12-31"


def constant_rate_frame(rate: float, n: int = 3000, seed: int = 17) -> pd.DataFrame:
    """appearances whose BLK count is poisson(rate x ewma_MIN), features pure noise."""
    rng = np.random.default_rng(seed)
    minutes = rng.uniform(5.0, 36.0, n)
    frame = pd.DataFrame({
        "GAME_DATE": pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 200, n), "D"),
        "PLAYED": 1,
        "ewma_MIN": minutes,
        "BLK": rng.poisson(rate * minutes).astype(float),
        "ewma_BLK_per_min": rng.normal(rate, 0.001, n),
    })
    for column in COUNT_CORE_FEATURES:
        frame[column] = rng.normal(0.0, 1.0, n)
    return frame


@pytest.fixture(scope="module")
def origin(features_status):
    train, valid = split(features_status, CUTOFF, VALID_END)
    return train, valid


@pytest.fixture(scope="module")
def minutes_scored(origin):
    train, valid = origin
    train_app = train[train["PLAYED"] == 1]
    feats = available_features(train)
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feats, CUTOFF)
    return minutes.attach(valid[valid["PLAYED"] == 1])


def test_the_count_model_fits_and_predicts_non_negative_values(origin, minutes_scored):
    # arrange
    train, _ = origin
    train_app = train[train["PLAYED"] == 1]

    for target in COUNT_TARGETS:
        # act
        model = CountModel(target).fit(train_app, CUTOFF)
        scored = model.attach(minutes_scored)

        # assert
        values = scored[count_pred_column(target)].to_numpy(dtype=float)
        assert np.isfinite(values).all()
        assert (values >= 0.0).all()
        assert (scored[COUNT_PRED_CUTOFF] == CUTOFF).all()


def test_the_count_model_refuses_training_rows_on_or_after_its_cutoff(origin):
    # arrange
    train, valid = origin
    leaked = pd.concat([train, valid])
    leaked = leaked[leaked["PLAYED"] == 1]

    # act + assert
    with pytest.raises(LeakageError, match="on or after the cutoff"):
        CountModel("BLK").fit(leaked, CUTOFF)


def test_attach_refuses_a_minutes_forecast_from_another_cutoff(origin, minutes_scored):
    # arrange
    train, _ = origin
    model = CountModel("BLK").fit(
        train[(train["PLAYED"] == 1) & (train["GAME_DATE"] < CUTOFF - pd.Timedelta(days=7))],
        CUTOFF - pd.Timedelta(days=7),
    )

    # act + assert
    with pytest.raises(LeakageError, match="different cutoff"):
        model.attach(minutes_scored)


def test_attach_refuses_an_in_fold_minutes_forecast(origin, minutes_scored):
    # arrange
    train, _ = origin
    model = CountModel("BLK").fit(train[train["PLAYED"] == 1], CUTOFF)
    in_fold = minutes_scored.assign(**{MIN_PRED_CUTOFF: pd.Timestamp("2025-06-01")})

    # act + assert
    with pytest.raises(LeakageError, match="IN-FOLD"):
        model.attach(in_fold)


def test_doubling_predicted_minutes_doubles_the_conditional_expectation():
    # arrange
    rate = 0.05
    frame = constant_rate_frame(rate)
    model = CountModel("BLK", params=param_grid()[0]).fit(frame, pd.Timestamp("2025-01-01"))
    probe = frame.iloc[[0, 1, 2]]

    # act
    at_ten = model.predict_conditional(probe, np.full(3, 10.0))
    at_twenty = model.predict_conditional(probe, np.full(3, 20.0))

    # assert
    np.testing.assert_allclose(at_twenty / at_ten, 2.0, rtol=1e-9)
    np.testing.assert_allclose(at_twenty, rate * 20.0, rtol=0.15)


def test_the_offset_floors_minutes_at_one_and_fills_missing():
    # act
    offset = log_offset(np.array([0.0, 0.5, np.nan, 20.0]), fill=12.0)

    # assert
    np.testing.assert_allclose(offset, np.log([1.0, 1.0, 12.0, 20.0]))


def test_inner_fold_selection_returns_a_grid_member_deterministically(origin):
    # arrange
    train, _ = origin

    # act
    first, evidence_first = select_count_params(train, "BLK")
    second, evidence_second = select_count_params(train, "BLK")

    # assert
    assert first in param_grid()
    assert first == second
    assert not evidence_first.empty
    pd.testing.assert_frame_equal(evidence_first, evidence_second)


def test_the_grid_is_the_pre_registered_eight_members():
    # act
    members = param_grid()

    # assert
    assert len(members) == 8
    assert {m["n_estimators"] for m in members} == {100, 200}
    assert {m["num_leaves"] for m in members} == {7, 15}
    assert {m["min_child_samples"] for m in members} == {100, 300}


def test_count_features_skip_absent_optional_columns_and_require_the_core():
    # arrange
    frame = constant_rate_frame(0.05, n=10)
    with_optional = frame.assign(opp_pace=100.0)

    # act
    without = count_features(frame, "BLK")
    with_one = count_features(with_optional, "BLK")

    # assert
    assert without == ["ewma_BLK_per_min", *COUNT_CORE_FEATURES]
    assert with_one[-1] == "opp_pace"
    assert not set(COUNT_OPTIONAL_FEATURES) & set(without)
    with pytest.raises(ValueError, match="usg_ewma"):
        count_features(frame.drop(columns=["usg_ewma"]), "BLK")


def test_the_unconditional_count_is_p_play_times_the_conditional():
    # arrange
    scored = pd.DataFrame({
        "GAME_DATE": pd.to_datetime(["2025-01-05"] * 3),
        P_PLAY: [1.0, 0.5, 0.0],
        P_PLAY_CUTOFF: pd.to_datetime(["2025-01-01"] * 3),
    })

    # act
    uncond = count_unconditional(scored, np.array([2.0, 2.0, 2.0]))

    # assert
    np.testing.assert_allclose(uncond, [2.0, 1.0, 0.0])


def test_poisson_deviance_by_hand():
    # arrange
    y = np.array([0.0, 2.0])
    mu = np.array([1.0, 1.0])
    expected = np.mean([2.0 * (0.0 - (0.0 - 1.0)), 2.0 * (2.0 * np.log(2.0) - 1.0)])

    # act
    value = poisson_deviance(y, mu)

    # assert
    assert value == pytest.approx(expected)


def test_count_endpoints_by_hand():
    # arrange
    scored = pd.DataFrame({
        "PLAYED": [1, 1, 0],
        "MIN_TIER": ["star (>=30)", "fringe (<10)", "fringe (<10)"],
        "BLK": [2.0, 0.0, 0.0],
        cond_column(VARIANT_COUNT, "BLK"): [1.0, 1.0, 1.0],
        uncond_column(VARIANT_COUNT, "BLK"): [1.0, 0.5, 0.25],
    })

    # act
    out = count_endpoints(scored, stats=("BLK",), variants=(VARIANT_COUNT,))

    # assert
    assert list(out.columns) == ["variant", "endpoint", "stat", "cohort", "n", "value"]
    get = out.set_index(["endpoint", "cohort"])
    assert get.loc[(ENDPOINT_COND_MAE, "ALL"), "value"] == pytest.approx(1.0)
    assert get.loc[(ENDPOINT_COND_MAE, "ALL"), "n"] == 2
    assert get.loc[(ENDPOINT_UNCOND_MAE, "ALL"), "value"] == pytest.approx((1.0 + 0.5 + 0.25) / 3)
    assert get.loc[(ENDPOINT_UNCOND_MAE, "fringe (<10)"), "value"] == pytest.approx(0.375)
    assert get.loc[(ENDPOINT_DEVIANCE, "star (>=30)"), "value"] == pytest.approx(
        2.0 * (2.0 * np.log(2.0) - 1.0)
    )
    assert get.loc[(ENDPOINT_DEVIANCE, "fringe (<10)"), "value"] == pytest.approx(2.0)


def test_score_count_origin_produces_both_variants_on_the_fixture(origin):
    # arrange
    train, valid = origin
    feats = available_features(train)

    # act
    scored, chosen = score_count_origin(train, valid, feats, CUTOFF, stats=("BLK",))
    endpoints = count_endpoints(scored, stats=("BLK",))

    # assert
    assert chosen["BLK"] in param_grid()
    assert set(endpoints["variant"]) == {VARIANT_CHAMPION, VARIANT_COUNT}
    assert set(endpoints["endpoint"]) == {ENDPOINT_COND_MAE, ENDPOINT_UNCOND_MAE,
                                          ENDPOINT_DEVIANCE}
    assert np.isfinite(endpoints["value"]).all()
