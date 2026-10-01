from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import (
    EWMA_HALFLIFE,
    FEATURE_COLS,
    OPP_FORM_MIN_PERIODS,
    OPP_FORM_WINDOW,
    RATE_MINUTES_FLOOR,
    RATE_TARGETS,
    TARGET_COLS,
)
from fnba_ml.features import (
    MIN_APPEARANCES_FOR_HISTORY,
    attach_per_minute_rates,
    build_features,
    rate_column,
    schedule_features,
)

RNG = np.random.default_rng(7)

# NOTE: use drop_duplicates, NOT groupby(...).first()
CAREER_COLS = [
    "roll3_MIN", "roll5_MIN", "roll10_MIN", "roll3_PTS", "roll5_PTS", "roll10_PTS",
    "ewma_MIN", "ewma_PTS", "n_appearances", "days_since_last_app",
    "avail_rate_10", "avail_rate_20", "games_since_last_app",
    "ewma_PTS_per_min", "ewma_AST_per_min",
    "usg_ewma",
]

SEASON_SCOPED_COLS = [
    "std_MIN", "std_PTS", "std_AST", "std_FGA", "avail_rate_std", "uncond_std_PTS",
]


def _first_rows(feats: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    return feats.sort_values(["GAME_DATE", "GAME_ID"]).drop_duplicates(subset=keys, keep="first")


def _busiest_player(raw_logs: pd.DataFrame, season: str = "2024-25") -> str:
    return str(raw_logs[raw_logs["SEASON"] == season]["PLAYER_ID"].value_counts().index[0])


def _manual_rolling(logs, player_id, target_date, stat, window, season=None):
    """mean of `stat` over the last `window` appearances STRICTLY BEFORE target_date."""
    m = (logs["PLAYER_ID"] == player_id) & (logs["GAME_DATE"] < target_date)
    if season is not None:
        m &= logs["SEASON"] == season
    prior = logs[m].sort_values("GAME_DATE")
    if prior.empty:
        return np.nan
    return prior[stat].tail(window).mean()


def test_first_ever_row_has_null_career_features(feats):
    first = _first_rows(feats, ["PLAYER_ID"])
    assert len(first) > 0

    for col in CAREER_COLS:
        n_bad = int(first[col].notna().sum())
        assert n_bad == 0, (
            f"{col}: {n_bad} of {len(first)} first-ever rows have a non-null value "
            f"- history leaked into a player's first game"
        )


def test_first_row_of_each_season_has_null_season_to_date_features(feats):
    first = _first_rows(feats, ["PLAYER_ID", "SEASON"])

    for col in SEASON_SCOPED_COLS:
        n_bad = int(first[col].notna().sum())
        assert n_bad == 0, (
            f"{col}: {n_bad} of {len(first)} first-of-season rows are non-null "
            f"- season-to-date state carried across the season boundary"
        )


def test_career_windows_do_carry_across_seasons(feats):
    first_second_season = _first_rows(feats[feats["SEASON"] == "2024-25"], ["PLAYER_ID"])

    returning = first_second_season[first_second_season["n_appearances"].notna()]

    assert len(returning) >= 20, (
        f"only {len(returning)} players carried form into 2024-25 - the "
        f"career-scoped as-of join appears to have been restricted by season"
    )


def test_rolling_features_match_manual_recomputation(feats, raw_logs):
    player_id = _busiest_player(raw_logs)
    rows = feats[
        (feats["PLAYER_ID"] == player_id)
        & (feats["SEASON"] == "2024-25")
        & (feats["n_appearances"].notna())
    ].sort_values("GAME_DATE")
    assert len(rows) >= 10, "need a decent sample to spot-check"
    sample = rows.iloc[RNG.choice(len(rows), size=min(15, len(rows)), replace=False)]

    checked = 0
    for _, row in sample.iterrows():
        for stat in ("MIN", "PTS"):
            for w in (3, 5, 10):
                expected = _manual_rolling(raw_logs, player_id, row["GAME_DATE"], stat, w)
                actual = row[f"roll{w}_{stat}"]
                assert np.isclose(actual, expected, rtol=1e-9, atol=1e-9), (
                    f"player {player_id} on {row['GAME_DATE'].date()}: "
                    f"roll{w}_{stat} pipeline={actual} manual={expected}"
                )
                checked += 1
    assert checked > 0


def test_season_to_date_means_match_manual_recomputation(feats, raw_logs):
    player_id = _busiest_player(raw_logs)
    rows = feats[
        (feats["PLAYER_ID"] == player_id)
        & (feats["SEASON"] == "2024-25")
        & (feats["std_MIN"].notna())
    ].sort_values("GAME_DATE")
    sample = rows.iloc[RNG.choice(len(rows), size=min(12, len(rows)), replace=False)]

    for _, row in sample.iterrows():
        for stat in ("MIN", "PTS"):
            expected = _manual_rolling(
                raw_logs, player_id, row["GAME_DATE"], stat, window=10_000, season="2024-25"
            )
            actual = row[f"std_{stat}"]
            assert np.isclose(actual, expected, rtol=1e-9, atol=1e-9), (
                f"player {player_id} on {row['GAME_DATE'].date()}: "
                f"std_{stat} pipeline={actual} manual={expected}"
            )


def test_rolling_would_differ_if_target_game_included(feats, raw_logs):
    """negative control - confirms the recomputation tests have teeth."""
    player_id = _busiest_player(raw_logs)
    rows = feats[
        (feats["PLAYER_ID"] == player_id)
        & (feats["SEASON"] == "2024-25")
        & (feats["PLAYED"] == 1)
        & (feats["n_appearances"] >= 5)
    ].sort_values("GAME_DATE")
    assert len(rows) > 0

    n_differ = 0
    for _, row in rows.iterrows():
        leaky = raw_logs[
            (raw_logs["PLAYER_ID"] == player_id)
            & (raw_logs["SEASON"] == "2024-25")
            & (raw_logs["GAME_DATE"] <= row["GAME_DATE"])  # <= leaks the target
        ].sort_values("GAME_DATE")["PTS"].tail(5).mean()
        if not np.isclose(leaky, row["roll5_PTS"], atol=1e-9):
            n_differ += 1

    assert n_differ / len(rows) > 0.9, (
        f"only {n_differ}/{len(rows)} rows differ from the leaky version - the "
        f"as-of join may not actually be excluding the target game"
    )


def test_opponent_def_form_excludes_target_game(feats):
    sched = (
        feats[["SEASON", "TEAM_ID", "GAME_ID", "GAME_DATE", "TEAM_PTS_ALLOWED"]]
        .drop_duplicates(["SEASON", "TEAM_ID", "GAME_ID"])
        .sort_values(["TEAM_ID", "GAME_DATE"])
        .reset_index(drop=True)
    )
    candidates = feats[feats["OPP_DEF_FORM"].notna()]
    sample = candidates.iloc[RNG.choice(len(candidates), size=40, replace=False)]

    n_differ_from_leaky = 0
    for _, row in sample.iterrows():
        opp_games = sched[
            (sched["SEASON"] == row["SEASON"]) & (sched["TEAM_ID"] == row["OPP_TEAM_ID"])
        ].sort_values("GAME_DATE")

        prior = opp_games[opp_games["GAME_DATE"] < row["GAME_DATE"]]
        expected = prior["TEAM_PTS_ALLOWED"].tail(10).mean()
        assert np.isclose(row["OPP_DEF_FORM"], expected, rtol=1e-9, atol=1e-9), (
            f"OPP_DEF_FORM mismatch for game {row['GAME_ID']} vs team "
            f"{row['OPP_TEAM_ID']}: pipeline={row['OPP_DEF_FORM']} manual={expected}"
        )

        inclusive = opp_games[
            opp_games["GAME_DATE"] <= row["GAME_DATE"]
        ]["TEAM_PTS_ALLOWED"].tail(10).mean()
        if not np.isclose(inclusive, row["OPP_DEF_FORM"], atol=1e-9):
            n_differ_from_leaky += 1

    assert n_differ_from_leaky / len(sample) > 0.9, (
        "opponent form is suspiciously close to the leaky version"
    )


def test_avail_rate_excludes_own_outcome(feats):
    """avail_rate_10 = mean(PLAYED) over the player's previous 10 SCHEDULED rows."""
    player_id = str(feats["PLAYER_ID"].value_counts().index[3])
    rows = (
        feats[feats["PLAYER_ID"] == player_id]
        .sort_values(["GAME_DATE", "GAME_ID"])
        .reset_index(drop=True)
    )
    played = rows["PLAYED"].to_numpy()

    for i in range(1, len(rows)):
        expected = played[max(0, i - 10):i].mean()
        actual = rows.loc[i, "avail_rate_10"]
        assert np.isclose(actual, expected, atol=1e-9), (
            f"avail_rate_10 mismatch at row {i} for player {player_id}: "
            f"pipeline={actual} manual={expected}"
        )


def test_last_appearance_strictly_precedes_target(feats):
    have = feats[feats["LAST_APP_DATE"].notna()]

    bad = int((have["LAST_APP_DATE"] >= have["GAME_DATE"]).sum())
    assert bad == 0, f"{bad} rows matched an appearance on/after the target date"


def test_days_since_last_app_positive(feats):
    have = feats[feats["days_since_last_app"].notna()]
    assert (have["days_since_last_app"] >= 1).all(), \
        "days_since_last_app must be >= 1 (strictly prior appearance)"


def test_no_target_columns_in_feature_list():
    overlap = TARGET_COLS & set(FEATURE_COLS)
    assert not overlap, f"target/outcome columns leaked into FEATURE_COLS: {overlap}"


def test_universe_covers_every_real_appearance(feats, raw_logs):
    played = feats[feats["PLAYED"] == 1]

    universe_keys = set(zip(played["PLAYER_ID"], played["GAME_ID"]))
    log_keys = set(zip(raw_logs["PLAYER_ID"], raw_logs["GAME_ID"]))

    missing = log_keys - universe_keys
    assert not missing, f"{len(missing)} real appearances missing from the universe"


def test_groupby_first_cannot_detect_the_leak():
    frame = pd.DataFrame({
        "PLAYER_ID": ["p1", "p1", "p1"],
        "GAME_DATE": pd.to_datetime(["2024-01-01", "2024-01-03", "2024-01-05"]),
        "GAME_ID": ["g1", "g2", "g3"],
        "roll5_PTS": [np.nan, 10.0, 12.0],
    })

    via_groupby = frame.sort_values(["GAME_DATE", "GAME_ID"]).groupby("PLAYER_ID").first()
    via_drop_dup = _first_rows(frame, ["PLAYER_ID"])

    assert via_groupby["roll5_PTS"].notna().all(), (
        "groupby().first() is expected to skip the null - if this ever changes, "
        "the warning in this module can be relaxed"
    )
    assert via_drop_dup["roll5_PTS"].isna().all(), (
        "drop_duplicates(keep='first') must preserve the null so a leak is visible"
    )


def test_insufficient_history_flag_matches_appearance_count(feats):
    flagged = feats["insufficient_history"] == 1
    enough = feats["n_appearances"] >= MIN_APPEARANCES_FOR_HISTORY

    assert not (flagged & enough & feats["roll5_MIN"].notna()).any(), (
        "rows with enough appearances and a rolling mean must not be flagged as "
        "insufficient history"
    )
    assert flagged[feats["n_appearances"].isna()].all(), (
        "every row with no appearance history must be flagged"
    )


@pytest.mark.parametrize("column", ["IS_HOME", "TEAM_REST_DAYS", "OPP_DEF_FORM"])
def test_schedule_features_present(feats, column):
    assert column in feats.columns
    assert feats[column].notna().mean() > 0.5, f"{column} is mostly null"


def test_per_minute_rate_is_the_ewma_of_prior_ratios(feats, raw_logs):
    """the denominator floor is part of the definition, not a post-hoc clamp."""
    player = _busiest_player(raw_logs)
    rows = feats[(feats["PLAYER_ID"] == player) & feats["ewma_PTS_per_min"].notna()]
    assert len(rows) > 10
    target = rows.sort_values("GAME_DATE").iloc[-1]

    logs = raw_logs[
        (raw_logs["PLAYER_ID"] == player)
        & (raw_logs["GAME_DATE"] < target["GAME_DATE"])
        & (raw_logs["MIN"] > 0)
    ].sort_values("GAME_DATE")

    ratios = logs["PTS"] / logs["MIN"].clip(lower=RATE_MINUTES_FLOOR)
    expected = ratios.ewm(halflife=EWMA_HALFLIFE, adjust=True).mean().iloc[-1]

    assert target["ewma_PTS_per_min"] == pytest.approx(expected, rel=1e-6)


def test_the_backfill_reproduces_the_built_in_rate_columns(feats):
    columns = [rate_column(t) for t in RATE_TARGETS]
    stripped = feats.drop(columns=columns)

    restored = attach_per_minute_rates(stripped)

    for column in columns:
        pd.testing.assert_series_equal(
            restored[column].reset_index(drop=True),
            feats[column].reset_index(drop=True),
            check_names=True,
        )
    # row order is preserved: callers hold arrays positionally aligned to the frame
    assert restored["PLAYER_ID"].tolist() == feats["PLAYER_ID"].tolist()
    assert restored["GAME_ID"].tolist() == feats["GAME_ID"].tolist()


def test_the_backfill_is_a_noop_when_the_columns_are_already_there(feats):
    assert attach_per_minute_rates(feats) is feats


def test_the_rate_columns_are_not_model_features():
    """they are composition inputs, not predictors."""
    assert not {rate_column(t) for t in RATE_TARGETS} & set(FEATURE_COLS)


def _legacy_schedule_features(universe: pd.DataFrame) -> pd.DataFrame:
    """schedule_features as it was before the schedule argument existed."""
    sched = (
        universe[["SEASON", "TEAM_ID", "GAME_ID", "GAME_DATE", "TEAM_PTS_ALLOWED"]]
        .drop_duplicates(["SEASON", "TEAM_ID", "GAME_ID"])
        .sort_values(["TEAM_ID", "GAME_DATE"])
        .reset_index(drop=True)
    )
    grp = sched.groupby(["TEAM_ID", "SEASON"])
    sched["TEAM_REST_DAYS"] = (sched["GAME_DATE"] - grp["GAME_DATE"].shift(1)).dt.days
    sched["IS_B2B"] = (sched["TEAM_REST_DAYS"] == 1).astype(float)
    sched.loc[sched["TEAM_REST_DAYS"].isna(), "IS_B2B"] = np.nan
    sched["DEF_FORM"] = grp["TEAM_PTS_ALLOWED"].transform(
        lambda s: s.shift(1).rolling(OPP_FORM_WINDOW, min_periods=OPP_FORM_MIN_PERIODS).mean()
    )
    return sched[["SEASON", "TEAM_ID", "GAME_ID", "TEAM_REST_DAYS", "IS_B2B", "DEF_FORM"]]


def test_schedule_features_without_a_schedule_is_unchanged(features_status):
    expected = _legacy_schedule_features(features_status)

    actual = schedule_features(features_status, schedule=None)

    pd.testing.assert_frame_equal(actual, expected)


def test_build_features_without_a_schedule_is_the_default_build(
    universe_status, features_status
):
    rebuilt = build_features(universe_status, schedule=None)

    pd.testing.assert_frame_equal(rebuilt, features_status)
    legacy = _legacy_schedule_features(features_status)
    merged = features_status.merge(
        legacy.rename(columns={"TEAM_REST_DAYS": "_rest", "IS_B2B": "_b2b"}),
        on=["SEASON", "TEAM_ID", "GAME_ID"], how="left",
    )
    np.testing.assert_array_equal(merged["TEAM_REST_DAYS"], merged["_rest"])
    np.testing.assert_array_equal(merged["IS_B2B"], merged["_b2b"])
