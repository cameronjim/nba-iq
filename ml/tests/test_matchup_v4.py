from __future__ import annotations

import hashlib
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
    BLOWOUT_MARGIN,
    BLOWOUT_MODEL_FEATURES,
    BLOWOUT_PRIOR,
    BLOWOUT_PROB,
    BLOWOUT_PROB_CUTOFF,
    BLOWOUT_TARGET,
    FT_POSSESSION_WEIGHT,
    LATE_SEASON_GAMES_REMAINING,
    PACE_MIN_PERIODS,
    PACE_WINDOW,
    REGULAR_SEASON_GAMES,
    START_RATE_TOP_N,
    START_RATE_WINDOW,
    STAKES_LOCKED_RATIO,
)
from fnba_ml.evaluate import cohort_masks  # noqa: E402
from fnba_ml.features import schedule_features  # noqa: E402
from fnba_ml.matchup import (  # noqa: E402
    CTX_DEF_RATING,
    CTX_FG3A_ALLOWED,
    CTX_IS_B2B,
    CTX_NET_RATING,
    CTX_PACE,
    CTX_REST_DAYS,
    CTX_SLOT_MINUTES,
    OUTCOME_COLS,
    ROLLING_CTX_COLS,
    START_RATE_COL,
    STAKES_CTX_COLS,
    _pair_team_games,
    _per_game_rates,
    attach_matchup_features,
    attach_start_rate,
    attach_v4_features,
    auc,
    cross_fit_blowout_probabilities,
    start_rate_features,
    team_game_context,
)
from fnba_ml.models import LeakageError, brier, validate_out_of_fold  # noqa: E402


@pytest.fixture(scope="module")
def context(team_logs: pd.DataFrame) -> pd.DataFrame:
    return team_game_context(team_logs)


@pytest.fixture(scope="module")
def blowout_probabilities(context: pd.DataFrame) -> pd.DataFrame:
    # min_train_rows is dropped to 40 for the fixtures: the production constant
    # would put every row on the prior and never execute the cross-fit branch.
    return cross_fit_blowout_probabilities(context, min_train_rows=40)


@pytest.fixture(scope="module")
def v4_features(
    features_status: pd.DataFrame,
    context: pd.DataFrame,
    blowout_probabilities: pd.DataFrame,
) -> pd.DataFrame:
    attached = attach_matchup_features(features_status, context, blowout_probabilities)
    return attach_start_rate(attached)


@pytest.fixture(scope="module")
def v4_features_via_helper(
    features_status: pd.DataFrame, team_logs: pd.DataFrame
) -> pd.DataFrame:
    return attach_v4_features(features_status, team_logs)


def _synthetic_team_logs(records: list[tuple[str, int, int, int]]) -> pd.DataFrame:
    """a minimal two-team season from (date, home_pts, away_pts, game_index) tuples."""
    rows = []
    for date, home_pts, away_pts, idx in records:
        for team, pts, opp_pts in (("H", home_pts, away_pts), ("A", away_pts, home_pts)):
            rows.append({
                "TEAM_ID": team, "GAME_ID": f"G{idx:04d}", "SEASON": "2024-25",
                "GAME_DATE": pd.Timestamp(date), "PTS": float(pts), "MIN": 240.0,
                "FGA": 90.0, "FTA": 20.0, "TOV": 14.0, "FG3A": 35.0,
            })
    return pd.DataFrame(rows)


def test_the_frozen_feature_contract_did_not_move() -> None:
    digest = hashlib.sha256("\n".join(config.FEATURE_COLS).encode()).hexdigest()
    assert len(config.FEATURE_COLS) == 51
    assert digest == config.PROSPECTIVE_V3_FEATURE_COLS_SHA256
    assert config.FEATURE_VERSION == "v3"


def test_the_candidate_is_additive_and_is_not_the_served_contract() -> None:
    assert config.FEATURE_COLS_V4[: len(config.FEATURE_COLS)] == config.FEATURE_COLS
    assert config.FEATURE_COLS_V4[len(config.FEATURE_COLS):] == config.V4_FEATURE_COLS
    assert len(config.FEATURE_COLS_V4) == len(set(config.FEATURE_COLS_V4))
    assert not set(config.V4_FEATURE_COLS) & set(config.FEATURE_COLS)
    assert config.CANDIDATE_FEATURE_VERSION == "v4"
    assert config.FEATURE_VERSION != config.CANDIDATE_FEATURE_VERSION


def test_the_existing_feature_sets_are_untouched() -> None:
    assert config.FEATURE_SETS["v1"] == config.BASE_FEATURE_COLS
    assert config.FEATURE_SETS["v3-honest"] == config.FEATURE_COLS
    assert config.FEATURE_SETS[config.CANDIDATE_FEATURE_SET] == config.FEATURE_COLS_V4
    assert config.SERVED_FEATURE_SET == "v3-honest"
    assert not set(config.V4_FEATURE_COLS) & set(config.FEATURE_SETS["v1"])


def test_no_candidate_feature_is_an_outcome_column() -> None:
    assert not config.TARGET_COLS & set(config.FEATURE_COLS_V4)
    assert config.BLOWOUT_TARGET in config.TARGET_COLS
    assert config.BLOWOUT_MARGIN_COL in config.TARGET_COLS
    assert BLOWOUT_PROB in config.FEATURE_COLS_V4
    assert BLOWOUT_PROB not in config.TARGET_COLS


def test_origins_were_added_to_and_not_edited() -> None:
    assert len(config.ORIGINS) == 5
    assert config.DEV_ORIGINS[:5] == config.ORIGINS
    assert config.DEV_ORIGINS[5] == config.LATE_SEASON_ORIGIN
    assert len(config.DEV_ORIGINS) == 6


def test_the_late_season_origin_is_outside_the_selection_holdout() -> None:
    _, start, end = config.LATE_SEASON_ORIGIN
    holdout_start = pd.Timestamp("2026-02-01")
    assert pd.Timestamp(end) < holdout_start, (
        "the late-season development origin must not reach into the Feb-Apr 2026 "
        "selection holdout"
    )
    assert pd.Timestamp(start).month >= 3


def test_the_blowout_model_features_are_all_pregame() -> None:
    assert not set(BLOWOUT_MODEL_FEATURES) & set(OUTCOME_COLS)
    assert not set(BLOWOUT_MODEL_FEATURES) & config.TARGET_COLS
    assert not set(BLOWOUT_MODEL_FEATURES) & set(config.FEATURE_COLS_V4)


def test_the_first_team_game_of_a_season_has_null_rolling_rates(
    context: pd.DataFrame,
) -> None:
    first = context.sort_values("GAME_DATE").groupby(["TEAM_ID", "SEASON"]).head(1)
    for column in ROLLING_CTX_COLS:
        assert first[column].isna().all(), (
            f"{column} is populated on a team's first game of a season, so it read "
            f"either that game or the previous season"
        )
    assert first[CTX_REST_DAYS].isna().all()


def test_rolling_rates_are_null_until_min_periods(context: pd.DataFrame) -> None:
    ordered = context.sort_values(["TEAM_ID", "SEASON", "GAME_DATE"])
    nth = ordered.groupby(["TEAM_ID", "SEASON"]).cumcount()
    assert ordered.loc[nth < PACE_MIN_PERIODS, CTX_PACE].isna().all()
    reached = ordered.loc[nth >= PACE_MIN_PERIODS, CTX_PACE]
    assert len(reached) > 0
    assert reached.notna().any()


def test_rolling_pace_equals_a_hand_computed_prior_mean(
    context: pd.DataFrame, team_logs: pd.DataFrame
) -> None:
    raw = _per_game_rates(_pair_team_games(team_logs))
    key = raw.groupby(["TEAM_ID", "SEASON"]).size().idxmax()
    team, season = key
    own = raw[(raw["TEAM_ID"] == team) & (raw["SEASON"] == season)]
    own = own.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    got = context[(context["TEAM_ID"] == team) & (context["SEASON"] == season)]
    got = got.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    assert len(own) == len(got) >= PACE_MIN_PERIODS + 2

    for k in range(len(own)):
        prior = own["_pace"].iloc[max(0, k - PACE_WINDOW):k]
        expected = prior.mean() if len(prior) >= PACE_MIN_PERIODS else np.nan
        actual = got[CTX_PACE].iloc[k]
        if np.isnan(expected):
            assert np.isnan(actual), f"row {k} should be null, got {actual}"
        else:
            assert actual == pytest.approx(expected, rel=1e-9), f"row {k}"


def test_negative_control_an_unshifted_pace_provably_differs(
    context: pd.DataFrame, team_logs: pd.DataFrame
) -> None:
    raw = _per_game_rates(_pair_team_games(team_logs))
    raw = raw.sort_values(["TEAM_ID", "SEASON", "GAME_DATE", "GAME_ID"])
    leaky = raw.groupby(["TEAM_ID", "SEASON"])["_pace"].transform(
        lambda s: s.rolling(PACE_WINDOW, min_periods=PACE_MIN_PERIODS).mean()
    )
    raw = raw.assign(_leaky=leaky)
    merged = context.merge(
        raw[["SEASON", "TEAM_ID", "GAME_ID", "_leaky"]],
        on=["SEASON", "TEAM_ID", "GAME_ID"], how="inner",
    )
    both = merged[merged[CTX_PACE].notna() & merged["_leaky"].notna()]
    assert len(both) > 0
    assert not np.allclose(both[CTX_PACE], both["_leaky"]), (
        "the shipped rolling pace is identical to an UNSHIFTED one, so the shift is "
        "not doing anything"
    )


def test_possession_and_rating_arithmetic_is_what_was_documented(
    team_logs: pd.DataFrame,
) -> None:
    """poss = FGA + 0.44*FTA + TOV (no OREB term), pace per 48, def rating per 100."""
    assert config.POSSESSION_USES_OREB is False
    raw = _per_game_rates(_pair_team_games(team_logs))
    row = raw.iloc[0]
    poss = row["FGA"] + FT_POSSESSION_WEIGHT * row["FTA"] + row["TOV"]
    opp_poss = row["OPP_FGA"] + FT_POSSESSION_WEIGHT * row["OPP_FTA"] + row["OPP_TOV"]
    assert row["_poss"] == pytest.approx(poss)
    assert row["_slot_minutes"] == pytest.approx(row["MIN"] / 5.0)
    assert row["_pace"] == pytest.approx(poss / (row["MIN"] / 5.0) * 48.0)
    # the DEFENSIVE rating's denominator is the OPPONENT's possessions.
    assert row["_def_rating"] == pytest.approx(row["OPP_PTS"] / opp_poss * 100.0)
    assert row["_off_rating"] == pytest.approx(row["PTS"] / poss * 100.0)
    assert row["_fg3a_allowed_per100"] == pytest.approx(
        row["OPP_FG3A"] / opp_poss * 100.0
    )


def test_team_rest_days_agree_with_the_v3_schedule_features(
    context: pd.DataFrame, features_status: pd.DataFrame
) -> None:
    v3 = schedule_features(features_status)
    merged = context.merge(
        v3[["SEASON", "TEAM_ID", "GAME_ID", "TEAM_REST_DAYS", "IS_B2B"]],
        on=["SEASON", "TEAM_ID", "GAME_ID"], how="inner",
    )
    assert len(merged) > 0
    both = merged[merged[CTX_REST_DAYS].notna() & merged["TEAM_REST_DAYS"].notna()]
    assert len(both) > 0
    assert (both[CTX_REST_DAYS] == both["TEAM_REST_DAYS"]).all()
    assert (both[CTX_IS_B2B] == both["IS_B2B"]).all()


def test_flipping_a_games_box_score_moves_no_feature_of_that_game(
    team_logs: pd.DataFrame, context: pd.DataFrame
) -> None:
    ordered = context.sort_values("GAME_DATE")
    candidates = ordered[ordered[CTX_PACE].notna()]
    target_game = str(candidates["GAME_ID"].iloc[len(candidates) // 2])
    target_date = pd.Timestamp(
        candidates.loc[candidates["GAME_ID"] == target_game, "GAME_DATE"].iloc[0]
    )

    flipped = team_logs.copy()
    hit = flipped["GAME_ID"] == target_game
    assert int(hit.sum()) == 2
    flipped.loc[hit, "PTS"] = [200.0, 40.0]
    flipped.loc[hit, "FGA"] = 200.0
    flipped.loc[hit, "FTA"] = 100.0
    flipped.loc[hit, "TOV"] = 60.0
    flipped.loc[hit, "FG3A"] = 150.0
    after = team_game_context(flipped)

    columns = [*ROLLING_CTX_COLS, CTX_NET_RATING, *STAKES_CTX_COLS]
    key = ["SEASON", "TEAM_ID", "GAME_ID"]
    before_rows = context[context["GAME_ID"] == target_game].sort_values(key)
    after_rows = after[after["GAME_ID"] == target_game].sort_values(key)
    pd.testing.assert_frame_equal(
        before_rows[[*key, *columns]].reset_index(drop=True),
        after_rows[[*key, *columns]].reset_index(drop=True),
        check_exact=False, rtol=0, atol=0,
    )

    later = context.merge(after, on=key, suffixes=("_before", "_after"))
    later = later[pd.to_datetime(later["GAME_DATE_before"]) > target_date]
    moved = ~np.isclose(
        later[f"{CTX_PACE}_before"].to_numpy(dtype=float),
        later[f"{CTX_PACE}_after"].to_numpy(dtype=float),
        equal_nan=True,
    )
    assert moved.any(), (
        "flipping a box score changed no LATER row either, so the flip did not take "
        "effect and the invariance assertion above proves nothing"
    )


def test_the_opponent_column_is_the_opponents_prior_form_not_its_own(
    context: pd.DataFrame, features_status: pd.DataFrame
) -> None:
    attached = attach_matchup_features(features_status, context)
    sample = attached[attached["opp_def_rating"].notna()].head(200)
    lookup = context.set_index(["SEASON", "TEAM_ID", "GAME_ID"])[CTX_DEF_RATING]
    for _, row in sample.iterrows():
        expected = lookup.loc[(row["SEASON"], row["OPP_TEAM_ID"], row["GAME_ID"])]
        assert row["opp_def_rating"] == pytest.approx(float(expected))
    own = lookup.reindex(
        pd.MultiIndex.from_arrays(
            [sample["SEASON"], sample["TEAM_ID"], sample["GAME_ID"]]
        )
    ).to_numpy(dtype=float)
    assert not np.allclose(sample["opp_def_rating"].to_numpy(dtype=float), own), (
        "opp_def_rating equals the row's OWN team's defensive rating; the own / "
        "opponent merge is crossed"
    )


def test_the_blowout_label_is_symmetric_across_a_games_two_sides(
    context: pd.DataFrame,
) -> None:
    per_game = context.groupby("GAME_ID")[BLOWOUT_TARGET].nunique()
    assert (per_game == 1).all()
    margins = context.groupby("GAME_ID")[config.BLOWOUT_MARGIN_COL].sum()
    assert np.allclose(margins.to_numpy(dtype=float), 0.0), (
        "the two sides' signed margins do not sum to zero, so they are not the same "
        "game's margin"
    )
    expected = (context[config.BLOWOUT_MARGIN_COL].abs() >= BLOWOUT_MARGIN).astype(float)
    assert (context[BLOWOUT_TARGET] == expected).all()


def test_every_blowout_probability_is_out_of_fold(
    blowout_probabilities: pd.DataFrame,
) -> None:
    validate_out_of_fold(
        blowout_probabilities, BLOWOUT_PROB, BLOWOUT_PROB_CUTOFF, "P(blowout)"
    )
    cutoff = pd.to_datetime(blowout_probabilities[BLOWOUT_PROB_CUTOFF])
    dates = pd.to_datetime(blowout_probabilities["GAME_DATE"])
    assert (cutoff <= dates).all()
    assert blowout_probabilities[BLOWOUT_PROB].between(0.0, 1.0).all()
    assert blowout_probabilities[BLOWOUT_PROB].notna().all()


def test_a_tampered_blowout_cutoff_raises(
    blowout_probabilities: pd.DataFrame,
) -> None:
    tampered = blowout_probabilities.copy()
    tampered.loc[tampered.index[0], BLOWOUT_PROB_CUTOFF] = pd.Timestamp("2099-01-01")
    with pytest.raises(LeakageError):
        validate_out_of_fold(
            tampered, BLOWOUT_PROB, BLOWOUT_PROB_CUTOFF, "P(blowout)"
        )


def test_a_blowout_probability_does_not_depend_on_its_own_block_or_later(
    context: pd.DataFrame, blowout_probabilities: pd.DataFrame
) -> None:
    ordered = context.sort_values("GAME_DATE")
    boundary = pd.Timestamp(ordered["GAME_DATE"].quantile(0.60)).normalize()
    # truncate to the start of the boundary's month, so whole blocks are removed
    truncate_from = boundary.replace(day=1)
    truncated = cross_fit_blowout_probabilities(
        context[pd.to_datetime(context["GAME_DATE"]) < truncate_from],
        min_train_rows=40,
    )
    key = ["SEASON", "TEAM_ID", "GAME_ID"]
    merged = truncated.merge(
        blowout_probabilities[[*key, BLOWOUT_PROB]], on=key,
        suffixes=("_truncated", "_full"),
    )
    assert len(merged) > 0
    assert np.allclose(
        merged[f"{BLOWOUT_PROB}_truncated"].to_numpy(dtype=float),
        merged[f"{BLOWOUT_PROB}_full"].to_numpy(dtype=float),
    ), (
        "removing later games changed an earlier row's blowout probability, so the "
        "cross-fit is not forward-chaining"
    )


def test_peeked_blowout_model_scores_suspiciously_better(
    context: pd.DataFrame, blowout_probabilities: pd.DataFrame
) -> None:
    peeked = cross_fit_blowout_probabilities(context, peek=True)
    key = ["SEASON", "TEAM_ID", "GAME_ID"]
    joint = context[[*key, BLOWOUT_TARGET]].merge(
        blowout_probabilities[[*key, BLOWOUT_PROB]], on=key
    ).merge(peeked[[*key, BLOWOUT_PROB]], on=key, suffixes=("_honest", "_peeked"))
    y = joint[BLOWOUT_TARGET].to_numpy(dtype=float)
    honest = brier(y, joint[f"{BLOWOUT_PROB}_honest"].to_numpy(dtype=float))
    leaky = brier(y, joint[f"{BLOWOUT_PROB}_peeked"].to_numpy(dtype=float))
    assert leaky < honest * 0.95, (
        f"the peeked model ({leaky:.4f}) is not conspicuously better than the "
        f"out-of-fold one ({honest:.4f}); either the cross-fit is leaking or the "
        f"control is not a control"
    )
    assert peeked["BLOWOUT_SOURCE"].eq("PEEKED").all(), (
        "the peeked output must label itself, so it can never be mistaken for a "
        "dataset column"
    )
    assert auc(y, joint[f"{BLOWOUT_PROB}_peeked"].to_numpy(dtype=float)) > auc(
        y, joint[f"{BLOWOUT_PROB}_honest"].to_numpy(dtype=float)
    )


def test_the_blowout_classifier_refuses_an_outcome_column(
    context: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fnba_ml import matchup

    monkeypatch.setattr(
        matchup, "BLOWOUT_MODEL_FEATURES",
        (*BLOWOUT_MODEL_FEATURES, config.BLOWOUT_MARGIN_COL),
    )
    with pytest.raises(ValueError, match="outcome columns"):
        cross_fit_blowout_probabilities(context, min_train_rows=40)


def test_thin_blocks_fall_back_to_the_prior_rather_than_to_a_model(
    context: pd.DataFrame
) -> None:
    out = cross_fit_blowout_probabilities(context, min_train_rows=10**9)
    assert out["BLOWOUT_SOURCE"].eq("prior").all()
    assert np.allclose(out[BLOWOUT_PROB].to_numpy(dtype=float), BLOWOUT_PRIOR)


def test_games_played_and_remaining_count_only_prior_games() -> None:
    logs = _synthetic_team_logs([
        ("2024-10-22", 110, 100, 1),
        ("2024-10-24", 90, 120, 2),
        ("2024-10-26", 105, 104, 3),
    ])
    ctx = team_game_context(logs)
    home = ctx[ctx["TEAM_ID"] == "H"].sort_values("GAME_DATE")
    assert list(home["team_games_played"]) == [0.0, 1.0, 2.0]
    assert list(home["team_games_remaining"]) == [
        float(REGULAR_SEASON_GAMES), float(REGULAR_SEASON_GAMES - 1),
        float(REGULAR_SEASON_GAMES - 2),
    ]


def test_win_record_excludes_the_target_games_own_result() -> None:
    logs = _synthetic_team_logs([
        ("2024-10-22", 110, 100, 1),
        ("2024-10-24", 90, 120, 2),
        ("2024-10-26", 105, 104, 3),
    ])
    ctx = team_game_context(logs)
    home = ctx[ctx["TEAM_ID"] == "H"].sort_values("GAME_DATE").reset_index(drop=True)
    assert list(home["team_wins_to_date"]) == [0.0, 1.0, 1.0]
    assert np.isnan(home["team_win_pct"].iloc[0])
    assert home["team_win_pct"].iloc[1] == pytest.approx(1.0)
    assert home["team_win_pct"].iloc[2] == pytest.approx(0.5)
    assert home["team_games_over_500"].iloc[1] == pytest.approx(0.5)
    assert home["team_games_over_500"].iloc[2] == pytest.approx(0.0)


def test_flipping_a_result_leaves_that_rows_record_alone_and_moves_the_next(
) -> None:
    base = [("2024-10-22", 110, 100, 1), ("2024-10-24", 90, 120, 2),
            ("2024-10-26", 105, 104, 3)]
    flipped = [("2024-10-22", 100, 110, 1), ("2024-10-24", 90, 120, 2),
               ("2024-10-26", 105, 104, 3)]
    a = team_game_context(_synthetic_team_logs(base))
    b = team_game_context(_synthetic_team_logs(flipped))
    a = a[a["TEAM_ID"] == "H"].sort_values("GAME_DATE").reset_index(drop=True)
    b = b[b["TEAM_ID"] == "H"].sort_values("GAME_DATE").reset_index(drop=True)
    assert a["team_wins_to_date"].iloc[0] == b["team_wins_to_date"].iloc[0] == 0.0
    assert a["team_wins_to_date"].iloc[1] != b["team_wins_to_date"].iloc[1]


def test_lockedness_is_zero_outside_the_late_season_window() -> None:
    logs = _synthetic_team_logs([
        ("2024-10-22", 140, 90, 1), ("2024-10-24", 140, 90, 2),
        ("2024-10-26", 140, 90, 3),
    ])
    ctx = team_game_context(logs)
    assert (ctx["late_season"] == 0.0).all()
    assert (ctx["stakes_lockedness"] == 0.0).all()
    assert (ctx["stakes_late_x_over500"] == 0.0).all()


def test_lockedness_formula_on_a_hand_built_late_season_record() -> None:
    records = []
    for i in range(1, 81):
        home, away = (130, 100) if i <= 60 else (100, 130)
        records.append((f"2024-10-{i:02d}" if i <= 31 else
                        (pd.Timestamp("2024-10-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d"),
                        home, away, i))
    records = [
        ((pd.Timestamp("2024-10-22") + pd.Timedelta(days=2 * i)).strftime("%Y-%m-%d"),
         130 if i < 60 else 100, 100 if i < 60 else 130, i + 1)
        for i in range(80)
    ]
    ctx = team_game_context(_synthetic_team_logs(records))
    home = ctx[ctx["TEAM_ID"] == "H"].sort_values("GAME_DATE").reset_index(drop=True)

    last = home.iloc[-1]
    assert last["team_games_played"] == 79.0
    assert last["team_games_remaining"] == 3.0
    assert last["late_season"] == 1.0
    assert last["team_wins_to_date"] == 60.0
    assert last["team_games_over_500"] == pytest.approx((2 * 60 - 79) / 2.0)
    expected = min(1.0, abs(last["team_games_over_500"]) / max(3.0, 1.0))
    assert last["stakes_lockedness"] == pytest.approx(expected)
    assert last["stakes_lockedness"] == pytest.approx(1.0)
    assert last["stakes_late_x_over500"] == pytest.approx(last["team_games_over_500"])

    early = home[home["team_games_remaining"] > float(LATE_SEASON_GAMES_REMAINING)]
    assert len(early) > 0
    assert (early["stakes_lockedness"] == 0.0).all()


def test_lockedness_is_bounded_and_symmetric_in_sign(context: pd.DataFrame) -> None:
    values = context["stakes_lockedness"].dropna()
    assert (values >= 0.0).all()
    assert (values <= 1.0).all()


def test_stakes_interactions_are_the_documented_products(
    v4_features: pd.DataFrame,
) -> None:
    frame = v4_features
    expected = frame["stakes_lockedness"] * frame["minutes_share"]
    both = frame["stakes_x_minutes_share"].notna() & expected.notna()
    assert both.any()
    assert np.allclose(frame.loc[both, "stakes_x_minutes_share"],
                       expected[both])
    veteran = np.log1p(pd.to_numeric(frame["n_appearances"], errors="coerce"))
    expected_vet = frame["stakes_lockedness"] * veteran
    both = frame["stakes_x_veteran"].notna() & expected_vet.notna()
    assert both.any()
    assert np.allclose(frame.loc[both, "stakes_x_veteran"], expected_vet[both])


def test_the_stakes_cohort_threshold_matches_the_config_constant() -> None:
    labels = dict((c[0], c) for c in config.V4_DESCRIPTIVE_COHORTS)
    stakes = labels["v4: stakes-flagged (locked, late)"]
    assert stakes[1] == "stakes_lockedness"
    assert stakes[2] == ">="
    assert stakes[3] == STAKES_LOCKED_RATIO


def test_start_rate_is_null_on_a_players_first_scheduled_row(
    universe_status: pd.DataFrame,
) -> None:
    proxy = start_rate_features(universe_status)
    merged = universe_status.merge(
        proxy, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left"
    ).sort_values(["PLAYER_ID", "GAME_DATE"])
    first = merged.groupby("PLAYER_ID").head(1)
    assert first[START_RATE_COL].isna().all(), (
        "a player's first scheduled row has a start rate, so the window read the "
        "target game"
    )


def test_start_rate_is_a_hand_computable_share_of_prior_games() -> None:
    rows = []
    dates = pd.date_range("2024-10-22", periods=5, freq="2D")
    for g, date in enumerate(dates):
        for player, minutes, played in (
            ("P1", 34.0, 1), ("P2", 24.0, 1), ("P3", 0.0, 0 if g < 3 else 1),
        ):
            rows.append({
                "PLAYER_ID": player, "GAME_ID": f"G{g}", "TEAM_ID": "T",
                "GAME_DATE": date, "PLAYED": played,
                "MIN": 12.0 if (player == "P3" and played) else minutes,
            })
    frame = pd.DataFrame(rows)
    proxy = start_rate_features(frame)
    merged = frame.merge(proxy, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"])
    merged = merged.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    p1 = merged[merged["PLAYER_ID"] == "P1"][START_RATE_COL].tolist()
    assert np.isnan(p1[0])
    assert p1[1:] == [1.0, 1.0, 1.0, 1.0]

    p3 = merged[merged["PLAYER_ID"] == "P3"][START_RATE_COL].tolist()
    assert np.isnan(p3[0])
    # a non-appearance counts as 0, not as missing.
    assert p3[1] == pytest.approx(0.0)
    assert p3[2] == pytest.approx(0.0)
    assert p3[3] == pytest.approx(0.0)
    assert p3[4] == pytest.approx(0.25)


def test_start_rate_ranks_by_minutes_and_cuts_at_top_n() -> None:
    dates = pd.date_range("2024-10-22", periods=3, freq="2D")
    rows = []
    minutes = [40.0, 35.0, 30.0, 25.0, 20.0, 5.0]
    for g, date in enumerate(dates):
        for i, m in enumerate(minutes):
            rows.append({
                "PLAYER_ID": f"P{i}", "GAME_ID": f"G{g}", "TEAM_ID": "T",
                "GAME_DATE": date, "PLAYED": 1, "MIN": m,
            })
    frame = pd.DataFrame(rows)
    proxy = start_rate_features(frame)
    merged = frame.merge(proxy, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"])
    last = merged[merged["GAME_ID"] == "G2"].set_index("PLAYER_ID")[START_RATE_COL]
    for i in range(START_RATE_TOP_N):
        assert last[f"P{i}"] == pytest.approx(1.0), f"P{i} should be top-{START_RATE_TOP_N}"
    assert last[f"P{START_RATE_TOP_N}"] == pytest.approx(0.0)


def test_start_rate_window_is_the_configured_length(universe_status) -> None:
    proxy = start_rate_features(universe_status)
    merged = universe_status.merge(
        proxy, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left"
    )
    merged["GAME_DATE"] = pd.to_datetime(merged["GAME_DATE"])
    merged = merged.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"]).reset_index(
        drop=True
    )
    minutes = pd.to_numeric(merged["MIN"], errors="coerce")
    appeared = (merged["PLAYED"] == 1) & minutes.notna() & (minutes > 0)
    rank = minutes.where(appeared).groupby(
        [merged["GAME_ID"], merged["TEAM_ID"]]
    ).rank(method="first", ascending=False)
    is_top = (rank <= float(START_RATE_TOP_N)).astype(float)
    expected = is_top.groupby(merged["PLAYER_ID"]).transform(
        lambda s: s.shift(1).rolling(START_RATE_WINDOW, min_periods=1).mean()
    )
    both = merged[START_RATE_COL].notna() & expected.notna()
    assert both.any()
    assert np.allclose(merged.loc[both, START_RATE_COL], expected[both])


def test_negative_control_an_unshifted_start_rate_provably_differs(
    universe_status: pd.DataFrame,
) -> None:
    frame = universe_status[
        ["PLAYER_ID", "GAME_ID", "TEAM_ID", "GAME_DATE", "PLAYED", "MIN"]
    ].copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    frame = frame.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"]).reset_index(
        drop=True
    )
    minutes = pd.to_numeric(frame["MIN"], errors="coerce")
    appeared = (frame["PLAYED"] == 1) & minutes.notna() & (minutes > 0)
    rank = minutes.where(appeared).groupby(
        [frame["GAME_ID"], frame["TEAM_ID"]]
    ).rank(method="first", ascending=False)
    is_top = (rank <= float(START_RATE_TOP_N)).astype(float)
    leaky = is_top.groupby(frame["PLAYER_ID"]).transform(
        lambda s: s.rolling(START_RATE_WINDOW, min_periods=1).mean()
    )
    honest = frame.merge(
        start_rate_features(frame), on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left"
    )[START_RATE_COL]
    both = honest.notna() & leaky.notna()
    assert both.any()
    assert not np.allclose(honest[both], leaky[both]), (
        "the shipped start-rate proxy is identical to an UNSHIFTED one, so the shift "
        "is not doing anything and the column reads the target game's minutes"
    )


def test_flipping_the_target_games_minutes_does_not_move_its_own_start_rate(
    universe_status: pd.DataFrame,
) -> None:
    frame = universe_status[
        ["PLAYER_ID", "GAME_ID", "TEAM_ID", "GAME_DATE", "PLAYED", "MIN"]
    ].copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    frame = frame.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"]).reset_index(
        drop=True
    )
    minutes = pd.to_numeric(frame["MIN"], errors="coerce")
    appeared = (frame["PLAYED"] == 1) & minutes.notna() & (minutes > 0)
    rank = minutes.where(appeared).groupby(
        [frame["GAME_ID"], frame["TEAM_ID"]]
    ).rank(method="first", ascending=False)
    nth = frame.groupby("PLAYER_ID").cumcount()
    remaining = frame.groupby("PLAYER_ID")["GAME_ID"].transform("size") - nth
    eligible = frame.index[
        appeared & (rank > float(START_RATE_TOP_N))
        & (nth > START_RATE_WINDOW) & (remaining > 3)
    ]
    assert len(eligible) > 0, "the fixtures contain no out-of-rotation appearance"
    victim = eligible[len(eligible) // 2]
    player = str(frame.loc[victim, "PLAYER_ID"])
    target_date = frame.loc[victim, "GAME_DATE"]

    flipped = frame.copy()
    flipped.loc[victim, "MIN"] = 48.0

    before = frame.merge(
        start_rate_features(frame), on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left"
    )
    after = flipped.merge(
        start_rate_features(flipped), on=["PLAYER_ID", "GAME_ID", "TEAM_ID"],
        how="left",
    )
    key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
    joint = before[[*key, "GAME_DATE", START_RATE_COL]].merge(
        after[[*key, START_RATE_COL]], on=key, suffixes=("_before", "_after")
    )
    same_game = joint[joint["GAME_ID"] == frame.loc[victim, "GAME_ID"]]
    assert len(same_game) > 0
    assert np.allclose(
        same_game[f"{START_RATE_COL}_before"].to_numpy(dtype=float),
        same_game[f"{START_RATE_COL}_after"].to_numpy(dtype=float),
        equal_nan=True,
    ), "flipping a game's minutes moved that game's own start rate"

    later = joint[
        (joint["PLAYER_ID"] == player) & (joint["GAME_DATE"] > target_date)
    ]
    moved = ~np.isclose(
        later[f"{START_RATE_COL}_before"].to_numpy(dtype=float),
        later[f"{START_RATE_COL}_after"].to_numpy(dtype=float),
        equal_nan=True,
    )
    assert moved.any(), (
        "flipping the minutes changed no LATER row either, so the flip did not take "
        "effect and the invariance assertion above proves nothing"
    )


def test_the_started_column_is_still_unusable_if_it_is_present_at_all(
    universe_status: pd.DataFrame,
) -> None:
    assert "STARTED" not in universe_status.columns, (
        "STARTED is now on the universe. If the truth layer has been backfilled, "
        "replace top5_min_share_10 with a real rolling start rate "
        "(config.START_RATE_WINDOW documents the measurement that condemned it)"
    )


def test_attaching_the_candidate_family_preserves_rows_and_order(
    features_status: pd.DataFrame, v4_features: pd.DataFrame
) -> None:
    assert len(v4_features) == len(features_status)
    key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
    pd.testing.assert_frame_equal(
        features_status[key].reset_index(drop=True),
        v4_features[key].reset_index(drop=True),
    )
    for column in config.FEATURE_COLS:
        if column in features_status.columns:
            pd.testing.assert_series_equal(
                features_status[column].reset_index(drop=True),
                v4_features[column].reset_index(drop=True),
                check_names=False,
            )


def test_all_candidate_columns_are_present_after_attachment(
    v4_features: pd.DataFrame,
) -> None:
    missing = [c for c in config.V4_FEATURE_COLS if c not in v4_features.columns]
    assert not missing, f"attach_v4_features did not produce {missing}"


def test_the_game_pace_summaries_are_the_documented_functions(
    v4_features: pd.DataFrame,
) -> None:
    both = v4_features["own_pace"].notna() & v4_features["opp_pace"].notna()
    assert both.any()
    frame = v4_features[both]
    assert np.allclose(frame["game_pace_mean"],
                       (frame["own_pace"] + frame["opp_pace"]) / 2.0)
    assert np.allclose(frame["game_pace_product"],
                       frame["own_pace"] * frame["opp_pace"] / 100.0)


def test_minutes_share_is_prior_minutes_over_a_prior_slot(
    v4_features: pd.DataFrame, context: pd.DataFrame
) -> None:
    slot = context.set_index(["SEASON", "TEAM_ID", "GAME_ID"])[CTX_SLOT_MINUTES]
    idx = pd.MultiIndex.from_arrays([
        v4_features["SEASON"], v4_features["TEAM_ID"], v4_features["GAME_ID"]
    ])
    expected = (
        pd.to_numeric(v4_features["roll10_MIN"], errors="coerce").to_numpy(dtype=float)
        / slot.reindex(idx).to_numpy(dtype=float)
    )
    got = v4_features["minutes_share"].to_numpy(dtype=float)
    both = np.isfinite(expected) & np.isfinite(got)
    assert both.any()
    assert np.allclose(got[both], expected[both])
    assert np.nanmax(got) <= 1.05


def test_the_blowout_interaction_is_the_product_it_claims_to_be(
    features_status: pd.DataFrame, context: pd.DataFrame,
    blowout_probabilities: pd.DataFrame,
) -> None:
    attached = attach_matchup_features(
        features_status, context, blowout_probabilities
    )
    expected = attached[BLOWOUT_PROB] * attached["minutes_share"]
    both = attached["blowout_x_minutes_share"].notna() & expected.notna()
    assert both.any()
    assert np.allclose(attached.loc[both, "blowout_x_minutes_share"], expected[both])


def test_without_probabilities_the_blowout_columns_are_simply_absent(
    features_status: pd.DataFrame, context: pd.DataFrame
) -> None:
    """a partial attachment must omit, not impute."""
    attached = attach_matchup_features(features_status, context, None)
    assert BLOWOUT_PROB not in attached.columns
    assert "blowout_x_minutes_share" not in attached.columns
    for column in config.MATCHUP_FEATURE_COLS + config.STAKES_FEATURE_COLS:
        assert column in attached.columns


def test_absent_fg3a_yields_a_null_column_and_not_an_exception(
    team_logs: pd.DataFrame,
) -> None:
    without = team_logs.drop(columns=["FG3A"])
    ctx = team_game_context(without)
    assert ctx[CTX_FG3A_ALLOWED].isna().all()
    assert ctx[CTX_PACE].notna().any()
    assert ctx[CTX_DEF_RATING].notna().any()


def test_a_one_sided_game_is_dropped_rather_than_half_computed(
    team_logs: pd.DataFrame,
) -> None:
    victim = str(team_logs["GAME_ID"].iloc[0])
    mangled = team_logs.drop(
        team_logs[team_logs["GAME_ID"] == victim].index[:1]
    )
    ctx = team_game_context(mangled)
    assert victim not in set(ctx["GAME_ID"])


def test_the_v4_cohorts_appear_only_when_their_columns_do(
    features_status: pd.DataFrame, v4_features: pd.DataFrame
) -> None:
    v3_labels = {label for label, _ in cohort_masks(features_status)}
    v4_labels = {label for label, _ in cohort_masks(v4_features)}
    for label in config.V4_DESCRIPTIVE_COHORT_ORDER:
        assert label not in v3_labels, (
            "a v3 dataset produced a v4 cohort, so the cohort output is no longer "
            "byte-identical to what section 13 froze"
        )
    assert set(config.V4_DESCRIPTIVE_COHORT_ORDER) <= v4_labels


def test_the_blowout_decile_cohort_is_a_decile(v4_features: pd.DataFrame) -> None:
    masks = dict(cohort_masks(v4_features))
    mask = masks["v4: blowout_prob top decile"]
    share = mask.mean()
    # ties in a weakly-informative probability can push the top decile above 10%
    assert 0.09 <= share <= 0.20, share


def test_a_constant_blowout_probability_yields_no_decile_cohort_at_all(
    v4_features: pd.DataFrame,
) -> None:
    """a quantile cohort over a constant column is undefined, not universal."""
    flat = v4_features.assign(**{BLOWOUT_PROB: BLOWOUT_PRIOR})
    labels = {label for label, _ in cohort_masks(flat)}
    assert "v4: blowout_prob top decile" not in labels
    assert "v4: stakes-flagged (locked, late)" in {
        label for label, _ in cohort_masks(v4_features)
    }


def test_attach_v4_features_runs_end_to_end_through_the_helper(
    features_status: pd.DataFrame, v4_features_via_helper: pd.DataFrame
) -> None:
    assert len(v4_features_via_helper) == len(features_status)
    for column in config.V4_FEATURE_COLS:
        assert column in v4_features_via_helper.columns
    assert v4_features_via_helper[BLOWOUT_PROB].notna().all()


def test_the_frozen_event_cohorts_are_still_the_frozen_event_cohorts() -> None:
    assert config.EVENT_COHORTS == (
        ("event: vacated_minutes >= 30", "vacated_minutes", ">=", 30.0),
        ("event: star_out = 1", "star_out", ">=", 1.0),
        ("control: vacated_minutes < 5", "vacated_minutes", "<", 5.0),
    )
    assert not set(config.EVENT_COHORT_ORDER) & set(
        config.V4_DESCRIPTIVE_COHORT_ORDER
    )


def test_the_promotion_bar_is_the_pre_registered_one() -> None:
    assert config.P2_PROMOTION_FLOOR == 0.01
    assert config.P2_COHORT_REGRESSION_TOLERANCE == 0.01
    assert config.P2_PROMOTION_ENDPOINTS == ("minutes_mae", "availability_brier")


def test_the_bootstrap_is_the_tournaments_own_implementation() -> None:
    from fnba_ml import promotion

    assert promotion.BLOCK_DAYS == 7
    assert promotion.N_REPLICATES == 2000
    assert promotion.TOURNAMENT_BOOTSTRAP_PATH.exists()


def test_identical_passes_produce_no_effect_and_do_not_clear_the_bar() -> None:
    from fnba_ml.promotion import ENDPOINT_MINUTES, decide, paired_endpoint_bootstrap

    rng = np.random.default_rng(17)
    n = 900
    frame = pd.DataFrame({
        "origin": np.repeat(["O1", "O2", "O3"], n // 3),
        "GAME_DATE": pd.to_datetime("2025-01-01") + pd.to_timedelta(
            rng.integers(0, 28, n), unit="D"
        ),
        "row_key": [f"r{i}" for i in range(n)],
        "loss": rng.gamma(2.0, 2.0, n),
    })
    result = paired_endpoint_bootstrap(frame, frame.copy(), ENDPOINT_MINUTES)
    assert result.relative == pytest.approx(0.0)
    assert not result.ci_excludes_zero
    assert not result.clears
    verdict = decide([result], pd.DataFrame())
    assert not verdict.promoted
    assert "NOT PROMOTED" in verdict.reason


def test_a_large_uniform_improvement_does_clear_the_bar() -> None:
    from fnba_ml.promotion import ENDPOINT_MINUTES, decide, paired_endpoint_bootstrap

    rng = np.random.default_rng(17)
    n = 900
    base = rng.gamma(2.0, 2.0, n)
    incumbent = pd.DataFrame({
        "origin": np.repeat(["O1", "O2", "O3"], n // 3),
        "GAME_DATE": pd.to_datetime("2025-01-01") + pd.to_timedelta(
            rng.integers(0, 28, n), unit="D"
        ),
        "row_key": [f"r{i}" for i in range(n)],
        "loss": base,
    })
    candidate = incumbent.assign(loss=base * 0.80)
    result = paired_endpoint_bootstrap(incumbent, candidate, ENDPOINT_MINUTES)
    assert result.relative == pytest.approx(0.20, abs=1e-9)
    assert result.ci_excludes_zero
    assert result.clears
    verdict = decide([result], pd.DataFrame())
    assert verdict.promoted


def test_a_regressing_cohort_blocks_a_promotion_that_otherwise_clears() -> None:
    from fnba_ml.promotion import ENDPOINT_MINUTES, decide, paired_endpoint_bootstrap

    rng = np.random.default_rng(17)
    n = 600
    base = rng.gamma(2.0, 2.0, n)
    incumbent = pd.DataFrame({
        "origin": np.repeat(["O1", "O2"], n // 2),
        "GAME_DATE": pd.to_datetime("2025-01-01") + pd.to_timedelta(
            rng.integers(0, 28, n), unit="D"
        ),
        "row_key": [f"r{i}" for i in range(n)],
        "loss": base,
    })
    result = paired_endpoint_bootstrap(
        incumbent, incumbent.assign(loss=base * 0.80), ENDPOINT_MINUTES
    )
    assert result.clears
    regressions = pd.DataFrame([
        {"cohort": "fringe (<10)", "n": 100, "incumbent": 5.0, "candidate": 5.2,
         "delta_pct": 0.04, "regresses": True},
    ])
    verdict = decide([result], regressions)
    assert not verdict.promoted
    assert "fringe (<10)" in verdict.reason


def test_misaligned_passes_raise_rather_than_reporting_a_plausible_zero() -> None:
    from fnba_ml.promotion import ENDPOINT_MINUTES, paired_endpoint_bootstrap

    frame = pd.DataFrame({
        "origin": ["O1"] * 10,
        "GAME_DATE": pd.to_datetime("2025-01-01"),
        "row_key": [f"r{i}" for i in range(10)],
        "loss": np.arange(10.0),
    })
    shuffled = frame.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="row keys"):
        paired_endpoint_bootstrap(frame, shuffled, ENDPOINT_MINUTES)
    with pytest.raises(ValueError, match="different row counts"):
        paired_endpoint_bootstrap(frame, frame.head(5), ENDPOINT_MINUTES)


def test_auc_is_the_mann_whitney_statistic() -> None:
    assert auc([0, 0, 1, 1], [0.1, 0.2, 0.3, 0.4]) == pytest.approx(1.0)
    assert auc([0, 0, 1, 1], [0.4, 0.3, 0.2, 0.1]) == pytest.approx(0.0)
    assert auc([0, 0, 1, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)
    assert np.isnan(auc([1, 1, 1], [0.1, 0.2, 0.3]))
