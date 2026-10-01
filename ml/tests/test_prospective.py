from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import (
    OPP_FORM_WINDOW,
    PROSPECTIVE_COLD_START_FLAG,
    PROSPECTIVE_COLD_START_THROUGH,
    is_cold_start,
)
from fnba_ml.features import MIN_APPEARANCES_FOR_HISTORY, build_features
from fnba_ml.models import P_PLAY
from fnba_ml.store import STAT_NAMES, build_prediction_rows
from fnba_ml.prospective import (
    SOURCE_PROSPECTIVE,
    build_prospective_features,
    history_from_dataset,
    known_schedule,
    prospective_universe,
    roster_assignments,
)
from fnba_ml.universe import UNIVERSE_COLS

# a season after everything the fixtures contain
FUTURE_SEASON = "2025-26"
FUTURE_DATES = ("2025-10-21", "2025-10-23", "2025-10-25")


class TestColdStartFlag:
    def test_the_last_day_of_the_window_is_inside_it(self):
        assert is_cold_start(PROSPECTIVE_COLD_START_THROUGH) is True

    def test_the_day_after_is_outside(self):
        after = pd.Timestamp(PROSPECTIVE_COLD_START_THROUGH) + pd.Timedelta(days=1)
        assert is_cold_start(after) is False

    def test_october_of_the_prospective_season_is_flagged(self):
        assert is_cold_start("2026-10-20") is True

    def test_the_window_runs_past_october_on_purpose(self):
        # tied to MAGNITUDE_SHRINK_K = 10 rather than to a calendar month
        assert is_cold_start("2026-11-15") is True
        assert is_cold_start("2026-12-01") is False

    def test_an_evening_tipoff_on_the_boundary_day_is_still_inside(self):
        assert is_cold_start(f"{PROSPECTIVE_COLD_START_THROUGH} 19:30:00") is True

    def test_a_series_returns_a_boolean_series_aligned_to_it(self):
        dates = pd.Series(pd.to_datetime(
            ["2026-10-20", "2026-11-30", "2026-12-01", "2027-04-11"]
        ))

        flags = is_cold_start(dates)

        assert isinstance(flags, pd.Series)
        assert flags.tolist() == [True, True, False, False]

    def test_an_unreadable_date_is_not_flagged_either_way(self):
        flags = is_cold_start(pd.Series(["2026-10-20", None, "not a date"]))

        assert flags.tolist() == [True, False, False]

    def test_it_reads_the_frozen_constant_rather_than_a_literal(self):
        assert is_cold_start("2026-11-30", through="2026-10-31") is False


class TestColdStartDoesNotEnterTheStore:
    def test_the_flag_name_is_not_a_stored_stat_name(self):
        assert PROSPECTIVE_COLD_START_FLAG not in set(STAT_NAMES.values())
        assert PROSPECTIVE_COLD_START_FLAG not in set(STAT_NAMES)

    def test_a_frame_carrying_the_flag_emits_no_row_for_it(self):
        frame = pd.DataFrame({
            "PLAYER_ID": ["2544"],
            "GAME_ID": ["0022600001"],
            "GAME_DATE": [pd.Timestamp("2026-10-20")],
            P_PLAY: [0.91],
            "E_MIN_COND": [33.0],
            "E_MIN": [30.0],
            PROSPECTIVE_COLD_START_FLAG: [True],
        })

        rows = build_prediction_rows(frame, ("MIN",))

        assert rows, "the frame should still produce ordinary stat rows"
        assert PROSPECTIVE_COLD_START_FLAG not in {r["stat"] for r in rows}

    def test_the_flag_is_recomputable_from_a_stored_row_alone(self):
        # the reason no migration is needed: game_date is on every stored row
        stored_game_date = pd.Timestamp("2026-10-20")

        assert is_cold_start(stored_game_date) is True


@pytest.fixture(scope="module")
def history(universe_status: pd.DataFrame) -> pd.DataFrame:
    return universe_status.copy()


@pytest.fixture(scope="module")
def rosters(history: pd.DataFrame) -> pd.DataFrame:
    """every fixture player, on the team he last played for."""
    last = (
        history[history["PLAYED"] == 1]
        .sort_values("GAME_DATE")
        .drop_duplicates("PLAYER_ID", keep="last")
    )
    return last[["PLAYER_ID", "TEAM_ID"]].reset_index(drop=True)


@pytest.fixture(scope="module")
def future_schedule(history: pd.DataFrame) -> pd.DataFrame:
    """a synthetic schedule for a season with no game logs at all."""
    teams = sorted(history["TEAM_ID"].unique())
    pairs = list(zip(teams[0::2], teams[1::2]))
    rows = []
    for date_index, game_date in enumerate(FUTURE_DATES):
        for pair_index, (home, away) in enumerate(pairs):
            rows.append({
                "GAME_ID": f"9{date_index}{pair_index:04d}",
                "SEASON": FUTURE_SEASON,
                "SEASON_TYPE": "Regular Season",
                "GAME_DATE": game_date,
                "SCHEDULED_AT": f"{game_date}T23:30:00Z",
                "HOME_TEAM_ID": home,
                "AWAY_TEAM_ID": away,
                "GAME_STATUS": "scheduled",
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def future(future_schedule, rosters) -> pd.DataFrame:
    return prospective_universe(
        future_schedule, rosters, FUTURE_DATES[0], FUTURE_DATES[-1]
    )


@pytest.fixture(scope="module")
def built(history, future) -> pd.DataFrame:
    """the whole projected week, built one date at a time."""
    return build_prospective_features(history, future)


class TestRosterAssignments:
    def test_it_accepts_the_database_column_names(self):
        frame = pd.DataFrame({
            "nba_player_id": ["1", "2"], "team_id": ["10", "20"], "extra": [1, 2],
        })

        out = roster_assignments(frame)

        assert list(out.columns) == ["PLAYER_ID", "TEAM_ID"]
        assert out["PLAYER_ID"].tolist() == ["1", "2"]

    def test_a_player_on_two_rosters_is_deduplicated_not_multiplied(self):
        frame = pd.DataFrame({"PLAYER_ID": ["1", "1"], "TEAM_ID": ["10", "20"]})

        out = roster_assignments(frame)

        assert len(out) == 1
        assert out["TEAM_ID"].iloc[0] == "20"

    def test_a_frame_without_the_columns_is_refused(self):
        with pytest.raises(ValueError, match="PLAYER_ID"):
            roster_assignments(pd.DataFrame({"player": ["1"]}))


class TestProspectiveUniverse:
    def test_it_produces_one_row_per_rostered_player_per_team_game(self, future, rosters):
        team_games = future.groupby(["GAME_ID", "TEAM_ID"]).ngroups

        assert len(future) == len(rosters) * len(FUTURE_DATES)
        assert team_games == future["TEAM_ID"].nunique() * len(FUTURE_DATES)

    def test_every_row_is_labelled_prospective(self, future):
        assert set(future["UNIVERSE_SOURCE"]) == {SOURCE_PROSPECTIVE}

    def test_it_carries_the_universe_schema(self, future):
        assert not [c for c in UNIVERSE_COLS if c not in future.columns]

    def test_outcomes_are_written_as_a_non_appearance(self, future):
        assert (future["PLAYED"] == 0).all()
        assert (future["MIN"] == 0).all()
        assert (future["PTS"] == 0).all()

    def test_listed_inactive_is_null_not_false(self, future):
        assert future["LISTED_INACTIVE"].isna().all()

    def test_team_totals_are_null_because_no_box_score_exists(self, future):
        assert future["TEAM_PTS"].isna().all()
        assert future["TEAM_PTS_ALLOWED"].isna().all()

    def test_a_player_on_no_scheduled_team_contributes_no_rows(self, future_schedule):
        rosters = pd.DataFrame({"PLAYER_ID": ["999"], "TEAM_ID": ["not-a-team"]})

        with pytest.raises(ValueError, match="no roster assignment matched"):
            prospective_universe(
                future_schedule, rosters, FUTURE_DATES[0], FUTURE_DATES[-1]
            )

    def test_an_empty_window_is_an_error_not_an_empty_frame(self, future_schedule, rosters):
        with pytest.raises(ValueError, match="no scheduled games"):
            prospective_universe(future_schedule, rosters, "2030-01-01", "2030-01-02")


class TestHistoryFromDataset:
    def test_a_built_feature_frame_can_give_its_universe_back(self, features_status):
        recovered = history_from_dataset(features_status)

        assert len(recovered) == len(features_status)
        assert not [c for c in UNIVERSE_COLS if c not in recovered.columns]

    def test_a_frame_missing_universe_columns_is_refused(self, features_status):
        with pytest.raises(ValueError, match="missing"):
            history_from_dataset(features_status.drop(columns=["PLAYED"]))


class TestZeroCurrentSeasonHistory:
    def test_every_scheduled_row_survives_the_feature_build(self, built, future):
        assert len(built) == len(future)

    def test_a_returning_player_keeps_his_prior_season_form(self, built, history):
        appearances = history[history["PLAYED"] == 1].groupby("PLAYER_ID").size()
        established = set(appearances[appearances >= MIN_APPEARANCES_FOR_HISTORY].index)
        rows = built[built["PLAYER_ID"].isin(established)]

        assert len(rows) > 0
        # the CAREER-scoped as-of joins may span the offseason
        assert rows["roll5_MIN"].notna().all()
        assert (rows["has_history"] == 1).all()
        assert (rows["insufficient_history"] == 0).all()

    def test_a_thinly_established_player_is_flagged_rather_than_dropped(
        self, built, history
    ):
        appearances = history[history["PLAYED"] == 1].groupby("PLAYER_ID").size()
        thin = set(appearances[appearances < MIN_APPEARANCES_FOR_HISTORY].index)
        rows = built[built["PLAYER_ID"].isin(thin)]

        if rows.empty:
            pytest.skip("this fixture set has no player with 1-2 career appearances")
        assert (rows["insufficient_history"] == 1).all()

    def test_the_season_scoped_windows_are_null_and_that_is_correct(self, built):
        # season-to-date means reset at the season boundary, and this season has no games
        assert built["std_PTS"].isna().all()
        assert built["season_appearances"].isna().all()

    def test_a_player_with_no_nba_history_gets_a_flagged_row_not_silence(
        self, history, future_schedule
    ):
        rookie = pd.DataFrame({
            "PLAYER_ID": ["rookie-1"],
            "TEAM_ID": [sorted(history["TEAM_ID"].unique())[0]],
        })
        future = prospective_universe(
            future_schedule, rookie, FUTURE_DATES[0], FUTURE_DATES[0]
        )

        built = build_prospective_features(history, future)

        assert len(built) == len(future) > 0
        assert (built["has_history"] == 0).all()
        assert (built["insufficient_history"] == 1).all()
        assert built["PLAYER_ID"].tolist() == ["rookie-1"] * len(built)

    def test_every_projected_row_falls_inside_the_cold_start_window_when_it_should(
        self, built
    ):
        flags = is_cold_start(built["GAME_DATE"], through="2025-12-31")

        assert bool(flags.all())


class TestOneDateAtATime:
    def test_the_per_date_build_matches_scoring_that_date_alone(
        self, history, future, built
    ):
        last_date = pd.Timestamp(FUTURE_DATES[-1])
        day = future[pd.to_datetime(future["GAME_DATE"]) == last_date]

        alone = build_features(
            pd.concat([history, day], ignore_index=True),
            schedule=known_schedule(history, future),
        )
        alone = alone[alone["UNIVERSE_SOURCE"] == SOURCE_PROSPECTIVE]
        key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
        from_week = (
            built[pd.to_datetime(built["GAME_DATE"]) == last_date]
            .set_index(key).sort_index()
        )
        from_alone = alone.set_index(key).sort_index()

        assert from_week.index.equals(from_alone.index)
        for column in (
            "avail_rate_10", "avail_rate_20", "roll5_MIN", "ewma_MIN",
            "TEAM_REST_DAYS", "IS_B2B", "OPP_REST_DAYS", "OPP_DEF_FORM",
        ):
            np.testing.assert_allclose(
                from_week[column].to_numpy(dtype=float),
                from_alone[column].to_numpy(dtype=float),
                equal_nan=True,
                err_msg=f"{column} depends on an earlier future date",
            )

    def test_the_naive_single_build_really_does_leak(self, history, future, built):
        naive = build_features(
            pd.concat([history, future], ignore_index=True)
        )
        naive = naive[naive["UNIVERSE_SOURCE"] == SOURCE_PROSPECTIVE]
        last_date = pd.Timestamp(FUTURE_DATES[-1])
        naive_last = naive[pd.to_datetime(naive["GAME_DATE"]) == last_date]
        correct = built[pd.to_datetime(built["GAME_DATE"]) == last_date]

        key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
        left = naive_last.set_index(key).sort_index()["avail_rate_10"]
        right = correct.set_index(key).sort_index()["avail_rate_10"]

        # the naive build has folded two fabricated absences into the window
        assert left.index.equals(right.index)
        assert (left.to_numpy() < right.to_numpy()).any(), (
            "the naive whole-week build no longer differs from the per-date one; "
            "either the leak is gone or this test can no longer see it"
        )


@pytest.fixture(scope="module")
def b2b_teams(history) -> tuple[str, str, str, str]:
    a, b, c, e = sorted(history["TEAM_ID"].unique())[:4]
    return a, b, c, e


@pytest.fixture(scope="module")
def b2b_season(history) -> str:
    return str(history.loc[history["GAME_DATE"].idxmax(), "SEASON"])


@pytest.fixture(scope="module")
def b2b_day(history) -> pd.Timestamp:
    return pd.Timestamp(history["GAME_DATE"].max()).normalize() + pd.Timedelta(days=4)


@pytest.fixture(scope="module")
def b2b_future(b2b_teams, b2b_season, b2b_day, rosters) -> pd.DataFrame:
    a, b, c, e = b2b_teams
    season, day = b2b_season, b2b_day
    games = [
        ("8000001", day - pd.Timedelta(days=1), b, e),
        ("8000002", day, a, c),
        ("8000003", day + pd.Timedelta(days=1), a, b),
    ]
    schedule = pd.DataFrame([
        {
            "GAME_ID": game_id,
            "SEASON": season,
            "SEASON_TYPE": "Regular Season",
            "GAME_DATE": game_date.strftime("%Y-%m-%d"),
            "HOME_TEAM_ID": home,
            "AWAY_TEAM_ID": away,
            "GAME_STATUS": "scheduled",
        }
        for game_id, game_date, home, away in games
    ])
    return prospective_universe(
        schedule, rosters, day - pd.Timedelta(days=1), day + pd.Timedelta(days=1)
    )


@pytest.fixture(scope="module")
def b2b_built(history, b2b_future) -> pd.DataFrame:
    return build_prospective_features(history, b2b_future)


class TestScheduleIsReadWhole:
    """a back-to-back after the last played game of a season in progress.

    team B plays on D-1, team A plays C on D, and A hosts B on D+1.
    """

    @staticmethod
    def _team_rows(frame: pd.DataFrame, team: str, game_date: pd.Timestamp) -> pd.DataFrame:
        rows = frame[
            (frame["TEAM_ID"] == team) & (pd.to_datetime(frame["GAME_DATE"]) == game_date)
        ]
        assert len(rows) > 0, f"no rostered rows for team {team} on {game_date.date()}"
        return rows

    @staticmethod
    def _last_played(history: pd.DataFrame, team: str, season: str) -> pd.Timestamp:
        games = history[(history["TEAM_ID"] == team) & (history["SEASON"] == season)]
        return pd.Timestamp(games["GAME_DATE"].max())

    def test_the_second_night_of_a_future_back_to_back_is_a_back_to_back(
        self, b2b_built, b2b_teams, b2b_day
    ):
        a = b2b_teams[0]

        rows = self._team_rows(b2b_built, a, b2b_day + pd.Timedelta(days=1))

        assert (rows["TEAM_REST_DAYS"] == 1).all()
        assert (rows["IS_B2B"] == 1).all()

    def test_the_first_future_game_measures_rest_from_the_last_played_game(
        self, b2b_built, history, b2b_teams, b2b_season, b2b_day
    ):
        a = b2b_teams[0]
        expected = (b2b_day - self._last_played(history, a, b2b_season)).days

        rows = self._team_rows(b2b_built, a, b2b_day)

        assert expected > 1
        assert (rows["TEAM_REST_DAYS"] == expected).all()
        assert (rows["IS_B2B"] == 0).all()

    def test_the_opponents_rest_reads_the_opponents_own_future_schedule(
        self, b2b_built, history, b2b_teams, b2b_season, b2b_day
    ):
        a, b = b2b_teams[0], b2b_teams[1]
        target = b2b_day + pd.Timedelta(days=1)
        from_history = (target - self._last_played(history, b, b2b_season)).days

        rows = self._team_rows(b2b_built, a, target)

        # b last played on D-1, which is a future game, not a historical one
        assert (rows["OPP_REST_DAYS"] == 2).all()
        assert from_history != 2

    def test_outcome_features_ignore_the_other_future_dates(
        self, b2b_built, b2b_future, history, b2b_teams, b2b_day
    ):
        a = b2b_teams[0]
        target = b2b_day + pd.Timedelta(days=1)
        without_d = b2b_future[pd.to_datetime(b2b_future["GAME_DATE"]) != b2b_day]

        rebuilt = build_prospective_features(history, without_d)

        key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
        with_rows = self._team_rows(b2b_built, a, target).set_index(key).sort_index()
        without_rows = self._team_rows(rebuilt, a, target).set_index(key).sort_index()
        assert with_rows.index.equals(without_rows.index)
        for column in (
            "avail_rate_10", "avail_rate_20", "avail_rate_std", "games_since_last_app",
            "roll5_MIN", "roll10_PTS", "ewma_MIN", "ewma_PTS",
        ):
            np.testing.assert_allclose(
                with_rows[column].to_numpy(dtype=float),
                without_rows[column].to_numpy(dtype=float),
                equal_nan=True,
                err_msg=f"{column} depends on another future date",
            )
        # the schedule columns are the ones that are meant to differ
        assert (without_rows["TEAM_REST_DAYS"] > 1).all()

    def test_future_defensive_form_is_the_mean_of_the_last_played_games(
        self, b2b_built, b2b_future, history, b2b_teams, b2b_season, b2b_day
    ):
        a, b = b2b_teams[0], b2b_teams[1]
        target = b2b_day + pd.Timedelta(days=1)
        played = (
            history[(history["TEAM_ID"] == b) & (history["SEASON"] == b2b_season)]
            .drop_duplicates("GAME_ID")
            .sort_values("GAME_DATE")
        )
        expected = played["TEAM_PTS_ALLOWED"].dropna().tail(OPP_FORM_WINDOW).mean()
        only_target = b2b_future[pd.to_datetime(b2b_future["GAME_DATE"]) == target]

        rebuilt = build_prospective_features(history, only_target)

        # b's unplayed D-1 game must not dilute the window
        for frame in (b2b_built, rebuilt):
            rows = self._team_rows(frame, a, target)
            np.testing.assert_allclose(
                rows["OPP_DEF_FORM"].to_numpy(dtype=float), expected, rtol=1e-12
            )

    def test_a_new_seasons_second_game_reads_rest_from_its_first(self, built):
        dates = pd.to_datetime(built["GAME_DATE"])

        first = built[dates == pd.Timestamp(FUTURE_DATES[0])]
        second = built[dates == pd.Timestamp(FUTURE_DATES[1])]

        # no game of the new season precedes the opener, and the boundary is respected
        assert first["TEAM_REST_DAYS"].isna().all()
        assert (second["TEAM_REST_DAYS"] == 2).all()
        assert (second["OPP_REST_DAYS"] == 2).all()
