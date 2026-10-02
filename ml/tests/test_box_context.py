from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import run_p3_bracket as p3  # noqa: E402
from fnba_ml import config  # noqa: E402
from fnba_ml.box_context import (  # noqa: E402
    attach_v6_features,
    box_appearance_history,
    teammate_start_features,
)
from fnba_ml.config import P_CONTEXT, V6_CONTEXT_FEATURE_COLS  # noqa: E402
from fnba_ml.models import MIN_PRED  # noqa: E402
from fnba_ml.overrides import DEFAULT_POLICY  # noqa: E402
from fnba_ml.rate_model import ResidualRateModel, residual_guard  # noqa: E402

KEY = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]


def _box(started: list[bool | None], player: str = "p1", team: str = "t1",
         start: str = "2024-11-01") -> pd.DataFrame:
    n = len(started)
    return pd.DataFrame({
        "PLAYER_ID": player,
        "GAME_ID": [f"g{i:03d}" for i in range(n)],
        "TEAM_ID": team,
        "GAME_DATE": pd.Timestamp(start) + pd.to_timedelta(np.arange(n) * 2, unit="D"),
        "MIN": 30.0,
        "STARTED": pd.array(started, dtype="boolean"),
        "PLUS_MINUS": np.arange(n, dtype=float),
        "OREB": 1.0,
        "DREB": 4.0,
        "PF": 2.0,
    })


def _schedule_rows(box: pd.DataFrame, p: float = 0.9) -> pd.DataFrame:
    return box[KEY + ["GAME_DATE"]].assign(**{P_CONTEXT: p}).reset_index(drop=True)


def test_started_rate_with_fewer_than_ten_appearances_is_the_mean_so_far() -> None:
    # arrange
    box = _box([True, False, True, True])

    # act
    out = attach_v6_features(_schedule_rows(box), box)

    # assert
    np.testing.assert_allclose(
        out["started_rate_10"].to_numpy(), [np.nan, 1.0, 0.5, 2.0 / 3.0]
    )
    np.testing.assert_allclose(out["started_last"].to_numpy(), [np.nan, 1.0, 0.0, 1.0])
    np.testing.assert_allclose(out["starts_streak"].to_numpy(), [np.nan, 1.0, 0.0, 1.0])


def test_started_rate_uses_the_last_ten_appearances_only() -> None:
    # arrange
    started = [False, False] + [True] * 10 + [False]
    box = _box(started)

    # act
    out = attach_v6_features(_schedule_rows(box), box)

    # assert
    # game 12 reads games 2..11 (ten starts); game 13 reads 3..12 (nine of ten)
    assert out["started_rate_10"].iloc[2] == pytest.approx(0.0)
    assert out["started_rate_10"].iloc[12] == pytest.approx(1.0)
    assert out["starts_streak"].iloc[12] == pytest.approx(10.0)


def test_a_null_started_flag_is_missing_in_the_rate_and_breaks_the_streak() -> None:
    # arrange
    box = _box([True, None, True, True])

    # act
    out = attach_v6_features(_schedule_rows(box), box)

    # assert
    assert out["started_rate_10"].iloc[2] == pytest.approx(1.0)
    assert np.isnan(out["started_last"].iloc[2])
    assert out["starts_streak"].iloc[3] == pytest.approx(1.0)


def test_a_feature_for_game_g_reads_only_games_before_g() -> None:
    # arrange
    box = _box([True, True, False, True, True])
    rows = _schedule_rows(box)
    flipped = box.copy()
    flipped.loc[2, "STARTED"] = True
    flipped.loc[2, "PLUS_MINUS"] = 40.0
    flipped.loc[2, "PF"] = 6.0
    flipped.loc[2, "OREB"] = 5.0
    cols = ["started_rate_10", "started_last", "starts_streak", "plus_minus_ewma_10",
            "pf_per36_ewma"]

    # act
    before = attach_v6_features(rows, box)[cols].to_numpy()
    after = attach_v6_features(rows, flipped)[cols].to_numpy()

    # assert
    np.testing.assert_allclose(before[:3], after[:3], equal_nan=True)
    assert not np.allclose(before[3], after[3], equal_nan=True), (
        "flipping game 2 moved no later row, so the invariance above proves nothing"
    )


def test_the_fixture_build_is_invariant_to_its_own_games_box_line(
    features_status: pd.DataFrame, box_details: pd.DataFrame
) -> None:
    # arrange
    box = box_details.copy()
    target = pd.Timestamp("2024-12-01")
    day = box["GAME_DATE"] == box.loc[box["GAME_DATE"] >= target, "GAME_DATE"].min()
    flipped = box.copy()
    flipped.loc[day, "STARTED"] = ~flipped.loc[day, "STARTED"].astype(bool)
    flipped.loc[day, "PLUS_MINUS"] = flipped.loc[day, "PLUS_MINUS"] + 30.0
    flipped.loc[day, "OREB"] = flipped.loc[day, "OREB"] + 3.0
    flipped.loc[day, "PF"] = 6.0
    flip_date = box.loc[day, "GAME_DATE"].iloc[0]

    # act
    before = attach_v6_features(features_status, box)
    after = attach_v6_features(features_status, flipped)

    # assert
    upto = pd.to_datetime(before["GAME_DATE"]) <= flip_date
    np.testing.assert_allclose(
        before.loc[upto, V6_CONTEXT_FEATURE_COLS].to_numpy(dtype=float),
        after.loc[upto, V6_CONTEXT_FEATURE_COLS].to_numpy(dtype=float),
        equal_nan=True,
    )
    later = ~upto
    assert not np.allclose(
        before.loc[later, V6_CONTEXT_FEATURE_COLS].to_numpy(dtype=float),
        after.loc[later, V6_CONTEXT_FEATURE_COLS].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_teammate_starters_out_counts_a_star_listed_out() -> None:
    # arrange
    out_p = DEFAULT_POLICY.probability("out", 0.9)
    frame = pd.DataFrame({
        "PLAYER_ID": ["star", "starter", "bench", "other"],
        "GAME_ID": ["g1", "g1", "g1", "g1"],
        "TEAM_ID": ["t1", "t1", "t1", "t2"],
        "started_rate_10": [1.0, 0.6, 0.2, 1.0],
        P_CONTEXT: [out_p, 0.9, 0.8, 0.1],
    })
    available = frame.assign(**{P_CONTEXT: [0.95, 0.9, 0.8, 0.1]})

    # act
    out = teammate_start_features(frame)
    healthy = teammate_start_features(available)

    # assert
    assert out_p == pytest.approx(0.02)
    bench = out.set_index("PLAYER_ID").loc["bench"]
    assert bench["team_starters_out_exp"] == pytest.approx(0.98 + 0.1)
    assert bench["exp_vacated_starts"] == pytest.approx(1.0 * 0.98 + 0.6 * 0.1)
    # the star's own row excludes him, and the other team's starter is not a teammate
    star = out.set_index("PLAYER_ID").loc["star"]
    assert star["team_starters_out_exp"] == pytest.approx(0.1)
    assert star["exp_vacated_starts"] == pytest.approx(0.6 * 0.1 + 0.2 * 0.2)
    assert out.set_index("PLAYER_ID").loc["other", "team_starters_out_exp"] == 0.0
    gap = (bench["team_starters_out_exp"]
           - healthy.set_index("PLAYER_ID").loc["bench", "team_starters_out_exp"])
    assert gap == pytest.approx(0.98 - 0.05)


def test_teammate_starters_out_reads_the_as_of_start_rate_not_the_game() -> None:
    # arrange
    star = _box([True] * 4, player="star")
    bench = _box([False] * 4, player="bench")
    box = pd.concat([star, bench], ignore_index=True)
    rows = _schedule_rows(box).assign(**{P_CONTEXT: 0.5})
    benched_tonight = box.copy()
    benched_tonight.loc[3, "STARTED"] = False

    # act
    before = attach_v6_features(rows, box)
    after = attach_v6_features(rows, benched_tonight)

    # assert
    bench_rows = before["PLAYER_ID"] == "bench"
    # game 0: the star has no history yet, so he is not a usual starter
    np.testing.assert_allclose(
        before.loc[bench_rows, "team_starters_out_exp"].to_numpy(), [0.0, 0.5, 0.5, 0.5]
    )
    np.testing.assert_allclose(
        before["team_starters_out_exp"].to_numpy(), after["team_starters_out_exp"].to_numpy()
    )


def test_rebound_shares_are_of_the_team_games_total_and_fouls_are_per_36() -> None:
    # arrange
    a = _box([True, True], player="a")
    b = _box([True, True], player="b")
    b["OREB"] = 3.0
    b["DREB"] = 0.0
    a.loc[0, "MIN"] = 2.0
    box = pd.concat([a, b], ignore_index=True)

    # act
    history = box_appearance_history(box)
    out = attach_v6_features(_schedule_rows(box), box)

    # assert
    first_a = history[history["PLAYER_ID"] == "a"].iloc[0]
    assert first_a["oreb_share_ewma"] == pytest.approx(0.25)
    assert first_a["dreb_share_ewma"] == pytest.approx(1.0)
    # 2 fouls in a 2-minute cameo floored at 4 minutes
    assert first_a["pf_per36_ewma"] == pytest.approx(2.0 * 36.0 / 4.0)
    row = out[(out["PLAYER_ID"] == "b") & (out["GAME_ID"] == "g001")].iloc[0]
    assert row["oreb_share_ewma"] == pytest.approx(0.75)
    assert row["dreb_share_ewma"] == pytest.approx(0.0)


def test_attaching_preserves_rows_order_and_every_existing_column(
    features_status: pd.DataFrame, box_details: pd.DataFrame
) -> None:
    # act
    out = attach_v6_features(features_status, box_details)

    # assert
    assert len(out) == len(features_status)
    pd.testing.assert_frame_equal(
        out[features_status.columns].reset_index(drop=True),
        features_status.reset_index(drop=True),
    )
    assert set(V6_CONTEXT_FEATURE_COLS) <= set(out.columns)
    assert out["team_starters_out_exp"].notna().all()


def test_no_box_details_leaves_the_frame_unchanged(features_status: pd.DataFrame) -> None:
    # act
    out = attach_v6_features(features_status, None)

    # assert
    assert out is features_status


def test_the_v6_contract_is_v5_stakes_plus_the_box_family() -> None:
    # act
    v6 = config.FEATURE_SETS[config.CANDIDATE_FEATURE_SET_V6]

    # assert
    assert v6[: len(config.FEATURE_COLS_V5_STAKES)] == config.FEATURE_COLS_V5_STAKES
    assert v6[len(config.FEATURE_COLS_V5_STAKES):] == [
        "started_rate_10", "started_last", "starts_streak",
        "team_starters_out_exp", "exp_vacated_starts",
        "plus_minus_ewma_10", "pf_per36_ewma", "oreb_share_ewma", "dreb_share_ewma",
    ]
    assert not set(config.V6_BOX_DETAIL_COLS) & set(v6)
    assert not config.TARGET_COLS & set(v6)
    assert config.CANDIDATE_FEATURE_VERSION_V6 == "v6" != config.FEATURE_VERSION
    assert config.FEATURE_VERSION == "v3"
    assert config.SERVED_FEATURE_SET == "v3-honest"


def test_the_fringe_guard_is_written_down_before_any_result() -> None:
    # act + assert
    assert config.RATE_RESIDUAL_MIN_MINUTES == 10.0
    assert config.RATE_CONTEXT_COLS_V6[: len(config.RATE_CONTEXT_COLS)] == (
        config.RATE_CONTEXT_COLS
    )
    assert config.RATE_CONTEXT_COLS_V6[len(config.RATE_CONTEXT_COLS):] == [
        "started_rate_10", "started_last", "team_starters_out_exp", "exp_vacated_starts",
    ]
    np.testing.assert_array_equal(
        residual_guard(np.array([0.0, 9.99, 10.0, 34.0, np.nan]), 10.0),
        [0.0, 0.0, 1.0, 1.0, 0.0],
    )
    np.testing.assert_array_equal(residual_guard(np.array([1.0, 30.0]), None), [1.0, 1.0])


def _rate_rows(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "PLAYER_ID": [f"p{i % 200}" for i in range(n)],
        "GAME_ID": [f"g{i}" for i in range(n)],
        "TEAM_ID": "t",
        "GAME_DATE": pd.Timestamp("2024-10-01")
        + pd.to_timedelta(rng.integers(0, 60, n), unit="D"),
        "PLAYED": 1,
        "MIN": rng.uniform(2.0, 38.0, n),
        "POS_GROUP": rng.choice(list(config.POS_GROUP_ORDER), n),
        "ewma_PTS_per_min": rng.uniform(0.3, 0.8, n),
    })
    for col in config.RATE_CONTEXT_COLS_V6:
        frame[col] = rng.normal(0.0, 1.0, n)
    frame["ewma_MIN"] = frame["MIN"] + rng.normal(0.0, 3.0, n)
    planted = 0.2 * frame["started_last"]
    frame["PTS"] = (frame["ewma_PTS_per_min"] + planted + rng.normal(0, 0.1, n)) * (
        frame["MIN"].clip(lower=config.RATE_MINUTES_FLOOR)
    )
    return frame


def test_the_guarded_rate_is_the_champion_rate_below_ten_projected_minutes() -> None:
    # arrange
    cutoff = pd.Timestamp("2024-12-15")
    train = _rate_rows(3000, seed=5)
    valid = _rate_rows(400, seed=6).assign(GAME_DATE=cutoff)
    valid[MIN_PRED] = np.linspace(0.0, 40.0, len(valid))
    kwargs = {"cutoff": cutoff, "context_cols": tuple(config.RATE_CONTEXT_COLS_V6)}
    guarded = ResidualRateModel(
        **kwargs, residual_min_minutes=config.RATE_RESIDUAL_MIN_MINUTES
    ).fit(train, "PTS")
    unguarded = ResidualRateModel(**kwargs).fit(train, "PTS")

    # act
    rate = guarded.predict(valid)
    reference = unguarded.predict(valid)

    # assert
    low = valid[MIN_PRED].to_numpy() < config.RATE_RESIDUAL_MIN_MINUTES
    np.testing.assert_array_equal(rate[low], guarded.served_rate(valid)[low])
    np.testing.assert_allclose(rate[~low], reference[~low])
    assert not np.allclose(rate[~low], guarded.served_rate(valid)[~low])
    assert set(guarded.feature_gain().index) >= set(config.RATE_CONTEXT_COLS_V6)


def test_the_bracket_registers_both_v6_candidates_at_their_parents_bars() -> None:
    # act + assert
    assert p3.FEATURE_SET_COMPARISONS[p3.COMPARISON_V6] == "v6-context"
    assert p3.RATE_COMPARISONS[p3.COMPARISON_RATE_V6] == (
        tuple(config.RATE_CONTEXT_COLS_V6), config.RATE_RESIDUAL_MIN_MINUTES
    )
    assert p3.RATE_COMPARISONS[p3.COMPARISON_RATE] == (tuple(config.RATE_CONTEXT_COLS), None)
    assert p3.COMPARISON_GATES[p3.COMPARISON_V6] == p3.COMPARISON_GATES[p3.COMPARISON_V5]
    assert p3.COMPARISON_GATES[p3.COMPARISON_RATE_V6] == (
        p3.COMPARISON_GATES[p3.COMPARISON_RATE]
    )
    assert p3.INCUMBENT_LABEL[p3.COMPARISON_V6] == config.SERVED_FEATURE_SET
    assert p3.INCUMBENT_LABEL[p3.COMPARISON_RATE_V6] == "champion rate"
    assert config.P3_PROMOTION_FLOOR == 0.01
    assert config.P3_COHORT_REGRESSION_TOLERANCE == 0.01
    assert p3.is_binding(p3.COMPARISON_V6) and p3.is_binding(p3.COMPARISON_RATE_V6)
    assert not p3.is_binding(p3.COMPARISON_V5)
    assert not p3.is_binding(p3.COMPARISON_RATE)
