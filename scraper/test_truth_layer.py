import sqlite3
from datetime import date, timedelta

import pytest
import requests

import database
import fetching
import odds
import scrapes
import truth_layer
from backfill import NOT_POSTPONED_PREDICATE
from config import (
    current_season,
    GAME_LOG_CORRECTION_WINDOW_DAYS,
    ROSTER_SNAPSHOT_SOURCE,
    SEASON,
    STATS_HEADERS,
    STATS_PROBE_TIMEOUT_SECONDS,
    V2_INACTIVE_UNRELIABLE_FROM,
)
from database import is_write_statement
from odds import map_event_to_nba_game, parse_event_odds, plan_odds_snapshot
from parsing import (
    box_score_violations,
    cleared_player_ids,
    extract_next_data,
    in_season,
    normalize_injury_status,
    parse_game_date,
    parse_matchup,
    parse_minutes,
    season_end_date,
    season_start_date,
    season_type_from_game_id,
    v2_inactive_is_unreliable,
)
from rows import (
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
    build_player_game_log_row,
    build_team_game_log_row,
    derive_game_status_rows,
    game_log_fetch_from,
    normalize_inactive_rows,
    plan_roster_snapshot,
    plan_stint_change,
    player_rows_from_nba_players_index,
    roster_rows_from_nba_players_index,
    schedule_rows_from_league_schedule,
    schedule_rows_from_nba_web,
    schedule_rows_from_team_logs,
    split_rows_on_season_boundary,
    stint_is_newer_than_game_log,
    supplement_player_log_rows,
)
from run_scraper import _parse_args, _run_phase
from truth_layer import fetch_nba_web_schedule_rows

# fixtures use the real column names from the endpoints this scraper calls,
# copied from nba_api 1.11.4's own expected_data declarations: a fixture that
# drifts from those shapes is a test that passes while production breaks.

PLAYER_GAME_LOG_ROW = {
    "SEASON_YEAR": "2024-25",
    "PLAYER_ID": 1628369,
    "PLAYER_NAME": "Jayson Tatum",
    "TEAM_ID": 1610612738,
    "TEAM_ABBREVIATION": "BOS",
    "TEAM_NAME": "Boston Celtics",
    "GAME_ID": "0022400061",
    "GAME_DATE": "2024-10-22T00:00:00",
    "MATCHUP": "BOS vs. NYK",
    "WL": "W",
    "MIN": 34.2,
    "FGM": 12,
    "FGA": 25,
    "FG_PCT": 0.48,
    "FG3M": 5,
    "FG3A": 12,
    "FG3_PCT": 0.417,
    "FTM": 8,
    "FTA": 9,
    "FT_PCT": 0.889,
    "OREB": 1,
    "DREB": 10,
    "REB": 11,
    "AST": 5,
    "TOV": 3,
    "STL": 2,
    "BLK": 1,
    "PTS": 37,
    "PLUS_MINUS": 14,
}

TEAM_GAME_LOG_ROWS = [
    {
        "SEASON_ID": "22024",
        "TEAM_ID": 1610612738,
        "TEAM_ABBREVIATION": "BOS",
        "TEAM_NAME": "Boston Celtics",
        "GAME_ID": "0022400061",
        "GAME_DATE": "2024-10-22",
        "MATCHUP": "BOS vs. NYK",
        "WL": "W",
        "MIN": 240,
        "FGM": 46,
        "FGA": 96,
        "FG3M": 22,
        "FG3A": 61,
        "FTM": 18,
        "FTA": 22,
        "REB": 46,
        "AST": 28,
        "STL": 8,
        "BLK": 5,
        "TOV": 12,
        "PTS": 132,
        "PLUS_MINUS": 23,
    },
    {
        "SEASON_ID": "22024",
        "TEAM_ID": 1610612752,
        "TEAM_ABBREVIATION": "NYK",
        "TEAM_NAME": "New York Knicks",
        "GAME_ID": "0022400061",
        "GAME_DATE": "2024-10-22",
        "MATCHUP": "NYK @ BOS",
        "WL": "L",
        "MIN": 240,
        "FGM": 40,
        "FGA": 89,
        "FG3M": 12,
        "FG3A": 36,
        "FTM": 17,
        "FTA": 20,
        "REB": 39,
        "AST": 22,
        "STL": 5,
        "BLK": 3,
        "TOV": 15,
        "PTS": 109,
        "PLUS_MINUS": -23,
    },
]

INACTIVE_ROWS_V3 = [
    {
        "gameId": "0022400061",
        "teamId": 1610612738,
        "personId": 1629684,
        "firstName": "Xavier",
        "familyName": "Tillman",
        "jerseyNum": "26",
    }
]

INACTIVE_ROWS_V2 = [
    {
        "PLAYER_ID": 1629684,
        "FIRST_NAME": "Xavier",
        "LAST_NAME": "Tillman",
        "JERSEY_NUM": "26",
        "TEAM_ID": 1610612738,
        "TEAM_CITY": "Boston",
        "TEAM_NAME": "Celtics",
        "TEAM_ABBREVIATION": "BOS",
    }
]

SEASON_GAME_ROW = {
    "leagueId": "00",
    "seasonYear": "2025-26",
    "gameDate": "2026-03-04T00:00:00",
    "gameId": "0022500789",
    "gameStatus": 1,
    "gameStatusText": "7:30 pm ET",
    "gameDateTimeUTC": "2026-03-05T00:30:00Z",
    "postponedStatus": "A",
    "homeTeam_teamId": 1610612738,
    "homeTeam_teamTricode": "BOS",
    "awayTeam_teamId": 1610612747,
    "awayTeam_teamTricode": "LAL",
}


class TestParseMinutes:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("34:12", 34.2),
            ("0:36", 0.6),
            ("12:00", 12.0),
            ("PT34M12.00S", 34.2),
            ("PT0M36.00S", 0.6),
            ("PT40M", 40.0),
            (34.2, 34.2),
            (240, 240.0),
            ("38", 38.0),
        ],
    )
    def test_parses_every_shape_the_endpoints_return(self, raw, expected):
        assert parse_minutes(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [None, "", "   ", "DNP", float("nan"), True])
    def test_missing_input_is_none_not_zero(self, raw):
        assert parse_minutes(raw) is None


class TestParseGameDate:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("2024-10-22", date(2024, 10, 22)),
            ("2024-10-22T00:00:00", date(2024, 10, 22)),
            ("OCT 22, 2024", date(2024, 10, 22)),
            ("10/22/2024", date(2024, 10, 22)),
            ("10/22/2026 00:00:00", date(2026, 10, 22)),
            (date(2024, 10, 22), date(2024, 10, 22)),
        ],
    )
    def test_parses_each_endpoint_format(self, raw, expected):
        assert parse_game_date(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "not a date"])
    def test_unparseable_is_none(self, raw):
        assert parse_game_date(raw) is None


class TestParseMatchup:
    def test_home_matchup(self):
        assert parse_matchup("BOS vs. NYK") == (True, "NYK")

    def test_away_matchup(self):
        assert parse_matchup("NYK @ BOS") == (False, "BOS")

    @pytest.mark.parametrize("raw", [None, "", "BOS", "something else entirely"])
    def test_unparseable_yields_no_guess(self, raw):
        assert parse_matchup(raw) == (None, None)


class TestSeasonTypeFromGameId:
    @pytest.mark.parametrize(
        "game_id, expected",
        [
            ("0022400061", "Regular Season"),
            ("0012400002", "Pre Season"),
            ("0032400001", "All Star"),
            ("0042300401", "Playoffs"),
            ("0052300011", "PlayIn"),
        ],
    )
    def test_known_prefixes(self, game_id, expected):
        assert season_type_from_game_id(game_id) == expected

    @pytest.mark.parametrize("game_id", ["0092400061", "", None, "x"])
    def test_unknown_prefix_is_not_silently_regular_season(self, game_id):
        assert season_type_from_game_id(game_id) == "Unknown"


class TestGameLogWatermark:
    def test_walks_back_by_the_correction_window(self):
        latest = date(2026, 3, 10)

        result = game_log_fetch_from(latest, "2025-26")

        assert result == latest - timedelta(days=GAME_LOG_CORRECTION_WINDOW_DAYS)

    def test_empty_table_fetches_the_whole_season(self):
        assert game_log_fetch_from(None, "2025-26") == season_start_date("2025-26")

    def test_never_reaches_back_past_the_season_boundary(self):
        floor = season_start_date("2025-26")

        result = game_log_fetch_from(floor, "2025-26", correction_window_days=30)

        assert result == floor

    def test_season_start_bound_cannot_overlap_the_previous_season(self):
        assert season_start_date("2025-26") == date(2025, 7, 1)
        assert season_start_date("2024-25") < season_start_date("2025-26")


class TestPlanStintChange:
    def test_same_team_is_no_change(self):
        open_stint = ("1610612738", date(2025, 10, 21))

        change = plan_stint_change(
            open_stint, "1610612738", date(2026, 3, 1), date(2026, 3, 1)
        )

        assert change is None

    def test_first_ever_stint_opens_without_closing_anything(self):
        change = plan_stint_change(None, "1610612738", date(2025, 10, 21), None)

        assert change["open_team_id"] == "1610612738"
        assert change["open_valid_from"] == date(2025, 10, 21)
        assert change["close_team_id"] is None

    def test_trade_closes_the_old_stint_on_his_last_game_for_it(self):
        open_stint = ("1610612738", date(2025, 10, 21))

        change = plan_stint_change(
            open_stint, "1610612747", date(2026, 2, 8), date(2026, 2, 4)
        )

        assert change["close_team_id"] == "1610612738"
        assert change["close_valid_from"] == date(2025, 10, 21)
        assert change["close_valid_to"] == date(2026, 2, 4)
        assert change["open_team_id"] == "1610612747"
        assert change["open_valid_from"] == date(2026, 2, 8)

    def test_close_date_never_precedes_the_stint_it_closes(self):
        open_stint = ("1610612738", date(2026, 2, 1))

        change = plan_stint_change(
            open_stint, "1610612747", date(2026, 2, 8), date(2026, 1, 3)
        )

        assert change["close_valid_to"] == date(2026, 2, 1)

    def test_close_date_never_reaches_the_new_stints_start(self):
        open_stint = ("1610612738", date(2025, 10, 21))

        change = plan_stint_change(
            open_stint, "1610612747", date(2026, 2, 8), date(2026, 2, 20)
        )

        assert change["close_valid_to"] == date(2026, 2, 7)


class TestBoxScoreViolations:
    def test_consistent_row_has_no_violations(self):
        assert box_score_violations(
            {"fgm": 12, "fga": 25, "fg3m": 5, "fg3a": 12, "ftm": 8, "fta": 9}
        ) == []

    def test_more_makes_than_attempts_is_caught(self):
        assert "fgm_le_fga" in box_score_violations({"fgm": 12, "fga": 5})

    def test_three_pointers_must_also_be_field_goals(self):
        row = {"fgm": 4, "fga": 10, "fg3m": 6, "fg3a": 8}

        violations = box_score_violations(row)

        assert violations == ["fg3m_le_fgm"]

    def test_free_throws(self):
        assert box_score_violations({"ftm": 10, "fta": 4}) == ["ftm_le_fta"]

    def test_nulls_are_not_violations(self):
        assert box_score_violations({"fgm": None, "fga": 2, "fg3m": 1, "fg3a": None}) == []


class TestNormalizeInjuryStatus:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Out", "out"),
            ("Out For Season", "out"),
            ("Season-Ending Surgery", "out"),
            ("Doubtful", "doubtful"),
            ("Questionable", "questionable"),
            ("Probable", "probable"),
            ("Day-To-Day", "day_to_day"),
            ("Game Time Decision", "day_to_day"),
            ("GTD", "day_to_day"),
            ("Available", "available"),
            ("cleared", "cleared"),
        ],
    )
    def test_buckets_known_wording(self, raw, expected):
        assert normalize_injury_status(raw) == expected

    def test_longest_phrase_wins(self):
        assert normalize_injury_status("out for season") == "out"

    @pytest.mark.parametrize("raw", [None, "", "Reconditioning", "G League Two-Way"])
    def test_unrecognised_degrades_to_unknown(self, raw):
        assert normalize_injury_status(raw) == "unknown"


class TestClearedPlayerIds:
    def test_previously_listed_players_missing_from_the_page_are_cleared(self):
        cleared = cleared_player_ids(["1", "2", "3"], ["2", "4"])

        assert cleared == ["1", "3"]

    def test_an_empty_current_set_clears_nobody(self):
        cleared = cleared_player_ids(["1", "2"], [])

        assert cleared == []

    def test_still_listed_players_are_never_cleared(self):
        cleared = cleared_player_ids(["1", "2"], ["1", "2"])

        assert cleared == []

    def test_ids_are_compared_as_strings_and_blanks_are_ignored(self):
        cleared = cleared_player_ids([2544, "", None, "203507"], ["2544"])

        assert cleared == ["203507"]


class TestNormalizeInactiveRows:
    def test_reads_the_v3_column_names(self):
        assert normalize_inactive_rows(INACTIVE_ROWS_V3) == [
            {"nba_player_id": "1629684", "team_id": "1610612738"}
        ]

    def test_reads_the_v2_column_names(self):
        assert normalize_inactive_rows(INACTIVE_ROWS_V2) == [
            {"nba_player_id": "1629684", "team_id": "1610612738"}
        ]

    def test_ids_stay_text(self):
        # NBA ids are TEXT; parsing them as numbers loses leading zeros
        assert normalize_inactive_rows(INACTIVE_ROWS_V3)[0]["nba_player_id"] == "1629684"

    def test_rows_without_a_player_are_dropped(self):
        assert normalize_inactive_rows([{"personId": None, "teamId": 1}]) == []


class TestDeriveGameStatusRows:
    def _played(self, player_id, minutes=30.0, dnp_reason=None):
        return {
            "nba_player_id": player_id,
            "team_id": "1610612738",
            "started": True,
            "minutes": minutes,
            "dnp_reason": dnp_reason,
        }

    def test_an_appearance_is_played_and_active(self):
        rows = derive_game_status_rows(
            "0022400061", [self._played("1628369")], [], "test"
        )

        assert rows == [
            {
                "nba_player_id": "1628369",
                "nba_game_id": "0022400061",
                "team_id": "1610612738",
                "rostered": True,
                "listed_inactive": False,
                "started": True,
                "played": True,
                "dnp_reason": None,
                "minutes": 30.0,
                "source": "test",
            }
        ]

    def test_an_inactive_entry_is_rostered_but_did_not_play(self):
        rows = derive_game_status_rows("0022400061", [], INACTIVE_ROWS_V3, "test")

        assert len(rows) == 1
        assert rows[0]["nba_player_id"] == "1629684"
        assert rows[0]["rostered"] is True
        assert rows[0]["listed_inactive"] is True
        assert rows[0]["played"] is False
        assert rows[0]["minutes"] is None

    def test_a_dressed_dnp_is_rostered_active_and_did_not_play(self):
        played = [self._played("1234", minutes=None, dnp_reason="DNP - Coach's Decision")]

        rows = derive_game_status_rows("0022400061", played, [], "test")

        assert rows[0]["rostered"] is True
        assert rows[0]["listed_inactive"] is False
        assert rows[0]["played"] is False
        assert rows[0]["dnp_reason"] == "DNP - Coach's Decision"

    def test_blank_comment_still_counts_as_played(self):
        rows = derive_game_status_rows(
            "0022400061", [self._played("1234", dnp_reason="   ")], [], "test"
        )
        assert rows[0]["played"] is True
        assert rows[0]["dnp_reason"] is None

    def test_the_universe_is_the_union_of_both_populations(self):
        rows = derive_game_status_rows(
            "0022400061", [self._played("1628369")], INACTIVE_ROWS_V3, "test"
        )

        assert {r["nba_player_id"] for r in rows} == {"1628369", "1629684"}
        assert all(r["rostered"] for r in rows)
        assert sum(1 for r in rows if r["played"]) == 1

    def test_a_contradiction_stays_visible_instead_of_being_resolved(self):
        played = [self._played("1629684")]

        rows = derive_game_status_rows("0022400061", played, INACTIVE_ROWS_V3, "test")

        assert len(rows) == 1
        assert rows[0]["played"] is True
        assert rows[0]["listed_inactive"] is True


class TestBuildPlayerGameLogRow:
    def test_maps_a_real_response_row(self):
        row = build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2024-25", 7)

        (
            player_id, game_id, season, season_type, game_date, team_id, team_abbr,
            opponent_team_id, is_home, started, minutes, pts, reb, ast, stl, blk,
            tov, fgm, fga, fg3m, fg3a, ftm, fta, plus_minus, dnp_reason, source,
            run_id,
        ) = row

        assert player_id == "1628369"
        assert game_id == "0022400061"
        assert season == "2024-25"
        assert season_type == "Regular Season"
        assert game_date == date(2024, 10, 22)
        assert team_id == "1610612738"
        assert team_abbr == "BOS"
        assert opponent_team_id == "1610612752"
        assert is_home is True
        assert minutes == pytest.approx(34.2)
        assert (pts, reb, ast, stl, blk, tov) == (37, 11, 5, 2, 1, 3)
        assert (fgm, fga, fg3m, fg3a, ftm, fta) == (12, 25, 5, 12, 8, 9)
        assert plus_minus == 14
        assert source == "playergamelogs"
        assert run_id == 7

    def test_started_and_dnp_are_null_because_this_endpoint_cannot_report_them(self):
        row = build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2024-25", None)
        assert row[9] is None
        assert row[24] is None

    def test_ids_keep_their_leading_zeros(self):
        row = build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2024-25", None)
        assert row[1] == "0022400061"

    @pytest.mark.parametrize(
        "missing", ["PLAYER_ID", "GAME_ID", "GAME_DATE"]
    )
    def test_a_row_that_cannot_be_joined_is_dropped(self, missing):
        raw = dict(PLAYER_GAME_LOG_ROW, **{missing: None})

        assert build_player_game_log_row(raw, "2024-25", None) is None

    def test_a_leaguegamelog_row_maps_with_its_own_source_tag(self):
        raw = dict(PLAYER_GAME_LOG_ROW, MIN=34)
        del raw["SEASON_YEAR"]

        row = build_player_game_log_row(raw, "2024-25", 7, source="leaguegamelog")

        assert row[2] == "2024-25"
        assert row[10] == pytest.approx(34.0)
        assert row[25] == "leaguegamelog"


class TestSupplementPlayerLogRows:
    def test_only_missing_keys_are_taken_from_the_league_log(self):
        primary = [build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2024-25", 7)]
        zero_minute = dict(
            PLAYER_GAME_LOG_ROW, PLAYER_ID=203471, PLAYER_NAME="Dennis Schröder",
            MIN=0, PTS=0, FGM=0, FGA=0,
        )
        league = [dict(PLAYER_GAME_LOG_ROW, MIN=34), zero_minute]

        supplements = supplement_player_log_rows(primary, league, "2024-25", 7)

        assert len(supplements) == 1
        assert supplements[0][0] == "203471"
        assert supplements[0][10] == pytest.approx(0.0)
        assert supplements[0][25] == "leaguegamelog"

    def test_duplicate_league_rows_are_taken_once(self):
        row = dict(PLAYER_GAME_LOG_ROW, MIN=0)
        supplements = supplement_player_log_rows([], [row, dict(row)], "2024-25", None)
        assert len(supplements) == 1


class TestBuildTeamGameLogRow:
    def test_maps_the_away_side(self):
        row = build_team_game_log_row(TEAM_GAME_LOG_ROWS[1], "2024-25", None)

        assert row[0] == "1610612752"
        assert row[1] == "0022400061"
        assert row[6] == "1610612738"
        assert row[7] is False
        assert row[9] == 109


class TestScheduleFromTeamLogs:
    def test_two_team_rows_collapse_into_one_game(self):
        rows = schedule_rows_from_team_logs(TEAM_GAME_LOG_ROWS, "2024-25")

        assert len(rows) == 1
        game = rows[0]
        assert game["nba_game_id"] == "0022400061"
        assert game["home_team_id"] == "1610612738"
        assert game["away_team_id"] == "1610612752"
        assert game["home_team_abbr"] == "BOS"
        assert game["away_team_abbr"] == "NYK"
        assert game["game_date"] == date(2024, 10, 22)
        assert game["season_type"] == "Regular Season"
        assert game["source"] == "leaguegamelog"

    def test_completed_games_only(self):
        rows = schedule_rows_from_team_logs(TEAM_GAME_LOG_ROWS, "2024-25")
        assert rows[0]["game_status"] == "Final"
        assert rows[0]["scheduled_at"] is None

    def test_a_lone_team_row_still_produces_a_game(self):
        rows = schedule_rows_from_team_logs([TEAM_GAME_LOG_ROWS[1]], "2024-25")

        assert rows[0]["away_team_id"] == "1610612752"
        assert rows[0]["home_team_id"] is None
        assert rows[0]["home_team_abbr"] == "BOS"

    def test_a_neutral_site_game_keeps_both_teams(self):
        # neutral-site games report an "@" matchup for BOTH teams, modelled on
        # the real rows for 0022400147
        neutral = [
            dict(TEAM_GAME_LOG_ROWS[0], GAME_ID="0022400147", MATCHUP="MIA @ WAS",
                 TEAM_ID=1610612748, TEAM_ABBREVIATION="MIA", GAME_DATE="2024-11-02"),
            dict(TEAM_GAME_LOG_ROWS[1], GAME_ID="0022400147", MATCHUP="WAS @ MIA",
                 TEAM_ID=1610612764, TEAM_ABBREVIATION="WAS", GAME_DATE="2024-11-02"),
        ]

        rows = schedule_rows_from_team_logs(neutral, "2024-25")

        assert len(rows) == 1
        game = rows[0]
        assert {game["home_team_id"], game["away_team_id"]} == {
            "1610612748", "1610612764",
        }
        assert {game["home_team_abbr"], game["away_team_abbr"]} == {"MIA", "WAS"}

    def test_a_double_home_claim_also_keeps_both_teams(self):
        both_home = [
            dict(TEAM_GAME_LOG_ROWS[0], MATCHUP="BOS vs. NYK"),
            dict(TEAM_GAME_LOG_ROWS[1], MATCHUP="NYK vs. BOS"),
        ]

        rows = schedule_rows_from_team_logs(both_home, "2024-25")

        game = rows[0]
        assert {game["home_team_id"], game["away_team_id"]} == {
            "1610612738", "1610612752",
        }


class TestScheduleFromLeagueSchedule:
    def test_maps_an_unplayed_game(self):
        rows = schedule_rows_from_league_schedule([SEASON_GAME_ROW], "2025-26")

        game = rows[0]
        assert game["nba_game_id"] == "0022500789"
        assert game["season"] == "2025-26"
        assert game["game_date"] == date(2026, 3, 4)
        assert game["home_team_abbr"] == "BOS"
        assert game["away_team_abbr"] == "LAL"
        assert game["game_status"] == "7:30 pm ET"
        assert game["postponed_status"] == "A"
        assert game["source"] == "scheduleleaguev2"

    def test_utc_tipoff_is_kept_separately_from_the_et_game_date(self):
        game = schedule_rows_from_league_schedule([SEASON_GAME_ROW], "2025-26")[0]
        assert game["game_date"] == date(2026, 3, 4)
        assert game["scheduled_at"].isoformat() == "2026-03-05T00:30:00+00:00"

    def test_rows_without_a_game_id_are_dropped(self):
        raw = dict(SEASON_GAME_ROW, gameId="")
        assert schedule_rows_from_league_schedule([raw], "2025-26") == []

    def test_the_nba_not_postponed_marker_is_stored_verbatim(self):
        raw = dict(SEASON_GAME_ROW, postponedStatus="N")
        assert schedule_rows_from_league_schedule([raw], "2025-26")[0][
            "postponed_status"
        ] == "N"


class TestPostponedStatusFilter:
    # postponed_status holds the NBA's own postponedStatus, and 'N' means NOT
    # postponed. It is written on every future row, so a filter meaning "not
    # postponed" must admit both NULL and 'N'.

    @staticmethod
    def _filtered(where):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE s (nba_game_id TEXT, postponed_status TEXT)")
        conn.executemany(
            "INSERT INTO s VALUES (?, ?)",
            [("not_set", None), ("not_postponed", "N"), ("postponed", "A")],
        )
        try:
            return {
                row[0] for row in conn.execute(f"SELECT nba_game_id FROM s WHERE {where}")
            }
        finally:
            conn.close()

    def test_the_predicate_keeps_null_and_the_n_marker(self):
        assert self._filtered(NOT_POSTPONED_PREDICATE) == {"not_set", "not_postponed"}

    def test_the_predicate_excludes_a_really_postponed_game(self):
        assert "postponed" not in self._filtered(NOT_POSTPONED_PREDICATE)

    def test_a_bare_is_null_filter_would_drop_the_future_schedule(self):
        assert self._filtered("s.postponed_status IS NULL") == {"not_set"}


class TestIsWriteStatement:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO player_game_logs VALUES %s",
            "  update players set x = 1",
            "DELETE FROM games",
            "CREATE TABLE t (id INT)",
        ],
    )
    def test_writes_are_detected(self, sql):
        assert is_write_statement(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT MAX(game_date) FROM player_game_logs",
            "\n    SELECT 1\n",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "",
        ],
    )
    def test_reads_are_not_skipped(self, sql):
        assert is_write_statement(sql) is False

    def test_a_leading_comment_does_not_disguise_a_write(self):
        assert is_write_statement("-- upsert the logs\nINSERT INTO t VALUES (1)") is True

    def test_a_data_modifying_cte_is_treated_as_a_write(self):
        sql = "WITH moved AS (DELETE FROM a RETURNING *) INSERT INTO b SELECT * FROM moved"
        assert is_write_statement(sql) is True


class TestV2InactiveReliability:
    def test_day_before_cutoff_is_trusted(self):
        assert v2_inactive_is_unreliable(V2_INACTIVE_UNRELIABLE_FROM - timedelta(days=1)) is False

    def test_cutoff_day_itself_is_unreliable(self):
        assert v2_inactive_is_unreliable(V2_INACTIVE_UNRELIABLE_FROM) is True

    def test_after_cutoff_is_unreliable(self):
        assert v2_inactive_is_unreliable(date(2026, 1, 15)) is True

    def test_unknown_date_is_unreliable_not_trusted(self):
        assert v2_inactive_is_unreliable(None) is True

    def test_old_seasons_still_use_v2_freely(self):
        assert v2_inactive_is_unreliable(date(2023, 3, 1)) is False


class TestSeasonWindow:
    def test_start_is_july_first_of_the_first_year(self):
        assert season_start_date("2026-27") == date(2026, 7, 1)

    def test_end_is_june_thirtieth_of_the_second_year(self):
        assert season_end_date("2026-27") == date(2027, 6, 30)

    def test_consecutive_seasons_tile_the_calendar_without_a_gap(self):
        assert season_end_date("2025-26") + timedelta(days=1) == season_start_date("2026-27")

    def test_a_game_inside_the_window_is_in_season(self):
        assert in_season(date(2026, 10, 20), "2026-27") is True
        assert in_season(date(2027, 4, 11), "2026-27") is True

    def test_a_game_from_the_previous_season_is_not(self):
        assert in_season(date(2026, 4, 12), "2026-27") is False

    def test_a_game_from_the_next_season_is_not(self):
        assert in_season(date(2027, 10, 20), "2026-27") is False

    def test_an_unknown_date_is_never_in_season(self):
        assert in_season(None, "2026-27") is False


class TestSeasonBoundaryGuard:
    @staticmethod
    def _player_row(game_date):
        row = list(build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2026-27", None))
        row[PLAYER_LOG_DATE_INDEX] = game_date
        return tuple(row)

    def test_rows_inside_the_season_are_kept(self):
        rows = [self._player_row(date(2026, 10, 20)), self._player_row(date(2027, 3, 1))]

        inside, outside = split_rows_on_season_boundary(
            rows, "2026-27", PLAYER_LOG_DATE_INDEX
        )

        assert inside == rows
        assert outside == []

    def test_a_previous_season_row_is_split_out_not_written(self):
        keep = self._player_row(date(2026, 10, 20))
        stray = self._player_row(date(2026, 4, 12))

        inside, outside = split_rows_on_season_boundary(
            [keep, stray], "2026-27", PLAYER_LOG_DATE_INDEX
        )

        assert inside == [keep]
        assert outside == [stray]

    def test_a_next_season_row_is_split_out_too(self):
        stray = self._player_row(date(2027, 10, 21))

        inside, outside = split_rows_on_season_boundary(
            [stray], "2026-27", PLAYER_LOG_DATE_INDEX
        )

        assert inside == []
        assert outside == [stray]

    def test_the_team_log_date_index_finds_the_date_column(self):
        row = build_team_game_log_row(TEAM_GAME_LOG_ROWS[0], "2024-25", None)

        assert isinstance(row[TEAM_LOG_DATE_INDEX], date)

        inside, outside = split_rows_on_season_boundary(
            [row], "2024-25", TEAM_LOG_DATE_INDEX
        )
        assert inside == [row]
        assert outside == []

    def test_an_empty_season_partitions_to_two_empty_lists(self):
        assert split_rows_on_season_boundary(
            [], "2026-27", PLAYER_LOG_DATE_INDEX
        ) == ([], [])


SNAPSHOT_DAY = date(2026, 9, 15)
LAL = "1610612747"
BOS = "1610612738"
GSW = "1610612744"


class TestPlanRosterSnapshot:
    def test_a_player_already_on_the_right_team_is_no_change(self):
        changes = plan_roster_snapshot(
            {"201939": GSW}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )

        assert changes == []

    def test_a_moved_player_opens_a_new_stint_on_the_snapshot_date(self):
        changes = plan_roster_snapshot(
            {"201939": LAL}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )

        assert len(changes) == 1
        assert changes[0]["player_id"] == "201939"
        assert changes[0]["open_team_id"] == LAL
        assert changes[0]["open_valid_from"] == SNAPSHOT_DAY

    def test_the_old_stint_closes_the_day_before_the_snapshot(self):
        changes = plan_roster_snapshot(
            {"201939": LAL}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )

        assert changes[0]["close_team_id"] == GSW
        assert changes[0]["close_valid_from"] == date(2025, 10, 21)
        assert changes[0]["close_valid_to"] == SNAPSHOT_DAY - timedelta(days=1)

    def test_a_stint_never_closes_before_it_opened(self):
        changes = plan_roster_snapshot(
            {"201939": LAL}, {"201939": (GSW, SNAPSHOT_DAY)}, SNAPSHOT_DAY
        )

        assert changes[0]["close_valid_to"] == SNAPSHOT_DAY
        assert changes[0]["close_valid_to"] >= changes[0]["close_valid_from"]

    def test_a_player_with_no_open_stint_only_opens_one(self):
        changes = plan_roster_snapshot({"1642268": BOS}, {}, SNAPSHOT_DAY)

        assert len(changes) == 1
        assert changes[0]["close_team_id"] is None
        assert changes[0]["close_valid_from"] is None
        assert changes[0]["close_valid_to"] is None
        assert changes[0]["open_team_id"] == BOS

    def test_a_player_missing_from_every_roster_is_left_open(self):
        changes = plan_roster_snapshot(
            {}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )

        assert changes == []

    def test_several_players_are_planned_independently(self):
        changes = plan_roster_snapshot(
            {"1": LAL, "2": BOS, "3": GSW},
            {"1": (GSW, date(2025, 11, 1)), "2": (BOS, date(2025, 11, 1))},
            SNAPSHOT_DAY,
        )

        moved = {c["player_id"]: c for c in changes}
        assert set(moved) == {"1", "3"}
        assert moved["1"]["close_team_id"] == GSW
        assert moved["3"]["close_team_id"] is None

    def test_the_plan_is_idempotent_once_applied(self):
        first = plan_roster_snapshot(
            {"201939": LAL}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )
        applied = {"201939": (first[0]["open_team_id"], first[0]["open_valid_from"])}

        assert plan_roster_snapshot({"201939": LAL}, applied, SNAPSHOT_DAY) == []

    def test_the_source_label_is_distinct_from_the_game_log_one(self):
        assert ROSTER_SNAPSHOT_SOURCE == "roster_snapshot"
        assert ROSTER_SNAPSHOT_SOURCE != "playergamelogs"


class TestSeasonCli:
    def test_season_defaults_to_the_module_constant(self):
        assert _parse_args([]).season == SEASON

    def test_season_can_be_overridden(self):
        assert _parse_args(["--season", "2026-27"]).season == "2026-27"

    def test_sync_truth_and_roster_snapshot_are_off_by_default(self):
        args = _parse_args([])
        assert args.sync_truth is False
        assert args.roster_snapshot is False

    def test_the_opening_week_command_parses(self):
        args = _parse_args(["--dev", "--sync-truth", "--season", "2026-27"])

        assert args.target == "dev"
        assert args.sync_truth is True
        assert args.season == "2026-27"

    def test_the_roster_snapshot_command_parses_with_dry_run(self):
        args = _parse_args(
            ["--dev", "--roster-snapshot", "--season", "2026-27", "--dry-run"]
        )

        assert args.roster_snapshot is True
        assert args.season == "2026-27"
        assert args.dry_run is True

    def test_the_backfill_to_season_default_is_unchanged(self):
        # --season must not have quietly become --to; they are different bounds
        assert _parse_args(["--season", "2026-27"]).to_season == SEASON


def _card(game_id, home=("1610612765", "DET"), away=("1610612738", "BOS"), **extra):
    data = {
        "gameId": game_id,
        "seasonYear": "2026-27",
        "seasonType": "Regular Season",
        "gameStatus": 1,
        "gameStatusText": "7:00 pm ET",
        "gameTimeUtc": "2026-10-20T23:00:00Z",
        "homeTeam": {"teamId": int(home[0]), "teamTricode": home[1]},
        "awayTeam": {"teamId": int(away[0]), "teamTricode": away[1]},
    }
    data.update(extra)
    return {"cardData": data}


def _games_page(*cards):
    return {"props": {"pageProps": {"gameCardFeed": {"modules": [{"cards": list(cards)}]}}}}


class TestCurrentSeason:
    def test_before_july_belongs_to_the_season_that_started_last_year(self):
        assert current_season(date(2026, 6, 30)) == "2025-26"

    def test_july_first_starts_the_next_season(self):
        assert current_season(date(2026, 7, 1)) == "2026-27"

    def test_fall_and_spring_of_one_season_agree(self):
        assert current_season(date(2026, 9, 30)) == "2026-27"
        assert current_season(date(2027, 4, 1)) == "2026-27"

    def test_century_rollover_keeps_two_digits(self):
        assert current_season(date(2099, 10, 1)) == "2099-00"

    def test_agrees_with_the_season_window_helpers(self):
        for day in (date(2026, 6, 30), date(2026, 7, 1), date(2027, 6, 30)):
            assert in_season(day, current_season(day))


class TestExtractNextData:
    def test_reads_the_embedded_json(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props": {"pageProps": {"a": 1}}}</script></html>'
        )
        assert extract_next_data(html) == {"props": {"pageProps": {"a": 1}}}

    def test_a_page_without_the_payload_raises(self):
        with pytest.raises(ValueError):
            extract_next_data("<html>blocked</html>")


class TestScheduleFromNbaWeb:
    def test_preseason_and_regular_games_use_the_game_id_for_season_type(self):
        page = _games_page(_card("0012600009"), _card("0022600001"))

        rows = schedule_rows_from_nba_web(page, date(2026, 10, 20), "2026-27")

        assert [r["season_type"] for r in rows] == ["Pre Season", "Regular Season"]
        assert rows[0]["nba_game_id"] == "0012600009"

    def test_row_matches_the_shape_of_the_primary_source(self):
        primary = schedule_rows_from_league_schedule([SEASON_GAME_ROW], "2025-26")[0]

        row = schedule_rows_from_nba_web(
            _games_page(_card("0022600001")), date(2026, 10, 20), "2026-27"
        )[0]

        assert set(row) == set(primary)
        assert row["home_team_id"] == "1610612765"
        assert row["away_team_abbr"] == "BOS"
        assert row["game_date"] == date(2026, 10, 20)
        assert row["game_status"] == "7:00 pm ET"
        assert row["postponed_status"] is None
        assert row["source"] == "nba_web"

    def test_scheduled_at_is_utc_aware(self):
        row = schedule_rows_from_nba_web(
            _games_page(_card("0022600001")), date(2026, 10, 20), "2026-27"
        )[0]
        assert row["scheduled_at"].isoformat() == "2026-10-20T23:00:00+00:00"

    def test_cards_without_card_data_or_game_id_are_skipped(self):
        page = _games_page({"cardType": "promo"}, _card(""), _card("0022600001"))
        page["props"]["pageProps"]["gameCardFeed"]["modules"].append({})

        rows = schedule_rows_from_nba_web(page, date(2026, 10, 20), "2026-27")

        assert [r["nba_game_id"] for r in rows] == ["0022600001"]

    def test_missing_season_year_falls_back_to_the_argument(self):
        page = _games_page(_card("0022600001", seasonYear=None))
        row = schedule_rows_from_nba_web(page, date(2026, 10, 20), "2026-27")[0]
        assert row["season"] == "2026-27"

    def test_a_page_with_no_feed_yields_nothing(self):
        assert schedule_rows_from_nba_web({}, date(2026, 10, 20), "2026-27") == []


class TestNbaWebScheduleCrawl:
    def test_gives_up_after_consecutive_failures(self, monkeypatch):
        calls = []

        def boom(game_date):
            calls.append(game_date)
            raise OSError("blocked")

        monkeypatch.setattr("truth_layer._fetch_nba_web_games", boom)

        rows = fetch_nba_web_schedule_rows("2026-27", date(2026, 10, 1), 0)

        assert rows == []
        assert len(calls) == 3

    def test_covers_the_window_around_today(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            "truth_layer._fetch_nba_web_games",
            lambda game_date: seen.append(game_date) or _games_page(_card("0022600001")),
        )

        rows = fetch_nba_web_schedule_rows("2026-27", date(2026, 10, 10), 0)

        assert seen[0] == date(2026, 10, 7)
        assert seen[-1] == date(2026, 10, 31)
        assert len(rows) == len(seen)


class TestRosterFromNbaPlayersIndex:
    def test_maps_player_to_team_as_strings(self):
        data = {"props": {"pageProps": {"players": [
            {"PERSON_ID": 1630173, "TEAM_ID": 1610612758},
        ]}}}
        assert roster_rows_from_nba_players_index(data) == {"1630173": "1610612758"}

    def test_free_agents_and_unknown_teams_are_skipped(self):
        data = {"props": {"pageProps": {"players": [
            {"PERSON_ID": 1, "TEAM_ID": 0},
            {"PERSON_ID": 2, "TEAM_ID": None},
            {"PERSON_ID": 3, "TEAM_ID": 999},
            {"PERSON_ID": None, "TEAM_ID": 1610612758},
            {"PERSON_ID": 4, "TEAM_ID": 1610612738},
        ]}}}
        assert roster_rows_from_nba_players_index(data) == {"4": "1610612738"}

    def test_feeds_the_stint_planner_idempotently(self):
        snapshot = roster_rows_from_nba_players_index(
            {"props": {"pageProps": {"players": [
                {"PERSON_ID": 4, "TEAM_ID": 1610612738},
            ]}}}
        )
        open_stints = {"4": ("1610612738", date(2026, 1, 1))}
        assert plan_roster_snapshot(snapshot, open_stints, date(2026, 10, 1)) == []


class TestRunPhase:
    def test_a_handled_failure_is_reported(self):
        assert _run_phase("x", lambda: False) is False

    def test_an_exception_is_reported_not_raised(self):
        def boom():
            raise RuntimeError("down")

        assert _run_phase("x", boom) is False

    def test_a_phase_returning_nothing_counts_as_success(self):
        assert _run_phase("x", lambda: None) is True


class TestSnapshotStintVersusGameLog:
    def test_snapshot_opened_stint_newer_than_the_last_game_is_kept(self):
        open_stint = ("1610612738", date(2026, 2, 5))

        assert stint_is_newer_than_game_log(open_stint, date(2026, 2, 1)) is True

    def test_a_game_log_change_after_the_stint_began_still_produces_a_change(self):
        open_stint = ("1610612738", date(2025, 12, 1))

        skipped = stint_is_newer_than_game_log(open_stint, date(2026, 1, 10))
        change = plan_stint_change(
            open_stint, "1610612752", date(2026, 1, 10), date(2026, 1, 8)
        )

        assert skipped is False
        assert change is not None
        assert change["open_team_id"] == "1610612752"

    def test_no_open_stint_is_never_skipped(self):
        assert stint_is_newer_than_game_log(None, date(2026, 1, 10)) is False


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.cursor_ = FakeCursor()

    def cursor(self):
        return self.cursor_


PLAYERS_INDEX = {"props": {"pageProps": {"players": [
    {
        "PERSON_ID": 203507, "PLAYER_FIRST_NAME": "Giannis",
        "PLAYER_LAST_NAME": "Antetokounmpo", "TEAM_ID": 1610612748,
        "TEAM_ABBREVIATION": "MIA", "POSITION": "F",
    },
    {
        "PERSON_ID": 1630163, "PLAYER_FIRST_NAME": "LaMelo",
        "PLAYER_LAST_NAME": "Ball", "TEAM_ID": 1610612750, "POSITION": "G",
    },
    {
        "PERSON_ID": 5, "PLAYER_FIRST_NAME": "Some", "PLAYER_LAST_NAME": "Rookie",
        "TEAM_ID": 1610612738, "POSITION": None,
    },
    {"PERSON_ID": 6, "PLAYER_FIRST_NAME": "Free", "PLAYER_LAST_NAME": "Agent", "TEAM_ID": 0},
    {"PERSON_ID": 7, "PLAYER_FIRST_NAME": "", "PLAYER_LAST_NAME": "", "TEAM_ID": 1610612738},
]}}}


class TestPlayerRowsFromIndex:
    def test_maps_team_abbreviation_name_position_and_headshot(self):
        rows = player_rows_from_nba_players_index(PLAYERS_INDEX)

        assert rows[0] == (
            "203507", "Giannis Antetokounmpo", "MIA", "SF,PF",
            "https://cdn.nba.com/headshots/nba/latest/1040x760/203507.png",
        )
        assert rows[1][:4] == ("1630163", "LaMelo Ball", "MIN", "PG,SG")

    def test_missing_position_maps_to_empty_and_unusable_rows_are_skipped(self):
        rows = player_rows_from_nba_players_index(PLAYERS_INDEX)

        assert [r[0] for r in rows] == ["203507", "1630163", "5"]
        assert rows[2][3] == ""


class TestScrapePlayersFromWeb:
    def test_dry_run_counts_rows_and_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(scrapes, "_fetch_nba_web_players", lambda: PLAYERS_INDEX)
        conn = FakeConn()

        written = scrapes.scrape_players_from_web(conn, dry_run=True)

        assert written == 3
        assert conn.cursor_.statements == []

    def test_a_failed_index_fetch_writes_nothing(self, monkeypatch):
        def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(scrapes, "_fetch_nba_web_players", boom)

        assert scrapes.scrape_players_from_web(FakeConn(), dry_run=True) == 0

    def test_unreachable_stats_goes_straight_to_the_web_path(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            scrapes, "scrape_players_from_web",
            lambda conn, dry_run=False: calls.append(dry_run),
        )
        monkeypatch.setattr(
            scrapes, "_fetch_cbs_positions",
            lambda: pytest.fail("cbs must not be fetched"),
        )

        scrapes.scrape_players(FakeConn(), dry_run=True, stats_reachable=False)

        assert calls == [True]

    def test_a_stats_failure_falls_back_to_the_web_path(self, monkeypatch):
        calls = []
        monkeypatch.setattr(scrapes, "_fetch_cbs_positions", lambda: {})
        monkeypatch.setattr(scrapes, "_fetch_nba_positions", lambda: {})
        monkeypatch.setattr(scrapes.time, "sleep", lambda s: None)

        def boom(season):
            raise RuntimeError("blocked")

        monkeypatch.setattr(scrapes, "fetch_player_stats", boom)
        monkeypatch.setattr(
            scrapes, "scrape_players_from_web",
            lambda conn, dry_run=False: calls.append(dry_run),
        )

        scrapes.scrape_players(FakeConn(), dry_run=True)

        assert calls == [True]


class TestStatsReachability:
    def test_a_timeout_means_unreachable(self, monkeypatch):
        def timeout(*args, **kwargs):
            raise requests.exceptions.ReadTimeout("tarpit")

        monkeypatch.setattr(fetching.requests, "get", timeout)

        assert fetching.stats_nba_reachable() is False

    def test_an_ok_response_means_reachable_and_sends_browser_headers(self, monkeypatch):
        seen = {}

        class Resp:
            def raise_for_status(self):
                pass

        def fake_get(url, **kwargs):
            seen.update(kwargs)
            return Resp()

        monkeypatch.setattr(fetching.requests, "get", fake_get)

        assert fetching.stats_nba_reachable() is True
        assert seen["headers"] is STATS_HEADERS
        assert seen["timeout"] == STATS_PROBE_TIMEOUT_SECONDS

    def test_schedule_skips_stats_when_unreachable_and_fails_if_nba_com_is_empty(
        self, monkeypatch
    ):
        monkeypatch.setattr(truth_layer, "_start_ingestion_run", lambda *a, **k: 1)
        monkeypatch.setattr(truth_layer, "_finish_ingestion_run", lambda *a, **k: None)
        monkeypatch.setattr(truth_layer, "fetch_nba_web_schedule_rows", lambda s: [])
        monkeypatch.setattr(
            truth_layer, "_fetch_league_schedule",
            lambda s: pytest.fail("stats.nba.com must not be called"),
        )
        monkeypatch.setattr(
            truth_layer, "_fetch_team_game_logs",
            lambda *a, **k: pytest.fail("stats.nba.com must not be called"),
        )

        ok = truth_layer.scrape_schedule(FakeConn(), "2026-27", stats_reachable=False)

        assert ok is False


CBS_INJURY_HTML = """
<div class="TableBase"><table>
<tr class="TableBase-bodyTr">
  <td><span class="CellPlayerName--long"><a>LeBron James</a></span></td>
  <td>F</td><td>Ankle</td><td>Out</td>
</tr>
<tr class="TableBase-bodyTr">
  <td><span class="CellPlayerName--long"><a>Stephen Curry</a></span></td>
  <td>G</td><td>Knee</td><td>Questionable</td>
</tr>
</table></div>
"""

INJURY_IDS_BY_NAME = {"lebron james": "2544", "stephen curry": "201939"}


class InjuryCursor:
    def __init__(self, previously_listed: list[str]):
        self.previously_listed = previously_listed
        self.statements: list[tuple[str, object]] = []
        self._result: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "injury_status IS NOT NULL" in sql:
            self._result = [(i,) for i in self.previously_listed]
        elif "= ANY(" in sql:
            self._result = [
                (INJURY_IDS_BY_NAME[n],) for n in params[0] if n in INJURY_IDS_BY_NAME
            ]
        elif "RETURNING nba_id" in sql:
            nba_id = INJURY_IDS_BY_NAME.get(params[2].lower())
            self._result = [(nba_id,)] if nba_id else []
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def close(self):
        pass

    def report_inserts(self):
        return [p for sql, p in self.statements if "INSERT INTO player_injury_reports" in sql]

    def clearances(self):
        return [
            p for sql, p in self.statements
            if "INSERT INTO player_injury_reports" in sql and "'cleared'" in sql
        ]


class InjuryConn:
    def __init__(self, previously_listed: list[str]):
        self.cursor_ = InjuryCursor(previously_listed)

    def cursor(self):
        return self.cursor_


class TestScrapeInjuries:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(scrapes.time, "sleep", lambda seconds: None)

    def test_a_recovered_player_gets_an_explicit_clearance_row(self, monkeypatch):
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: CBS_INJURY_HTML)
        conn = InjuryConn(previously_listed=["2544", "1629029"])

        scrapes.scrape_injuries(conn)

        assert conn.cursor_.clearances() == [("1629029",)]
        assert len(conn.cursor_.report_inserts()) == 3

    def test_a_failed_fetch_writes_nothing(self, monkeypatch):
        def boom():
            raise requests.ConnectionError("down")

        monkeypatch.setattr(scrapes, "fetch_injury_page", boom)
        conn = InjuryConn(previously_listed=["2544"])

        scrapes.scrape_injuries(conn)

        assert conn.cursor_.statements == []

    def test_a_page_with_no_rows_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: "<html></html>")
        conn = InjuryConn(previously_listed=["2544"])

        scrapes.scrape_injuries(conn)

        assert conn.cursor_.statements == []

    def test_dry_run_reads_but_writes_no_reset_reports_or_clearances(self, monkeypatch):
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: CBS_INJURY_HTML)
        conn = InjuryConn(previously_listed=["2544", "1629029"])

        scrapes.scrape_injuries(conn, dry_run=True)

        executed = [sql for sql, _ in conn.cursor_.statements]
        assert executed and all(not is_write_statement(sql) for sql in executed)


# espn scoreboard shapes, trimmed to the fields the odds parser reads.
def _espn_event(odds_node=None, event_id="401810001", home="NY", away="GS",
                status="STATUS_SCHEDULED", when="2026-10-21T23:30Z"):
    competition = {
        "competitors": [
            {"homeAway": "home", "team": {"abbreviation": home, "displayName": "Home"}},
            {"homeAway": "away", "team": {"abbreviation": away, "displayName": "Away"}},
        ],
    }
    if odds_node is not None:
        competition["odds"] = [odds_node]
    return {
        "id": event_id,
        "date": when,
        "status": {"type": {"name": status}},
        "competitions": [competition],
    }


FULL_ODDS = {
    "provider": {"name": "ESPN BET"},
    "details": "NY -2.5",
    "overUnder": 224.5,
    "spread": -2.5,
    "pointSpread": {
        "home": {"close": {"line": "-2.5", "odds": "-110"}},
        "away": {"close": {"line": "+2.5", "odds": "-110"}},
    },
    "total": {
        "over": {"close": {"line": "o224.5", "odds": "-105"}},
        "under": {"close": {"line": "u224.5", "odds": "-115"}},
    },
    "moneyline": {
        "home": {"close": {"odds": "-140"}},
        "away": {"close": {"odds": "+120"}},
    },
}


def _by_key(rows):
    return {(r["market"], r["selection"]): r for r in rows}


class TestParseEventOdds:
    def test_full_prices_give_six_rows_with_observed_prices(self):
        rows = _by_key(parse_event_odds(_espn_event(FULL_ODDS)))

        assert set(rows) == {
            ("spread", "home"), ("spread", "away"), ("total", "over"),
            ("total", "under"), ("moneyline", "home"), ("moneyline", "away"),
        }
        assert rows[("spread", "home")]["line"] == -2.5
        assert rows[("spread", "home")]["price"] == -110
        assert rows[("total", "over")]["line"] == 224.5
        assert rows[("total", "under")]["line"] == 224.5
        assert rows[("total", "under")]["price"] == -115
        assert rows[("moneyline", "home")]["price"] == -140
        assert rows[("moneyline", "away")]["price"] == 120
        assert rows[("moneyline", "away")]["line"] is None
        assert all(r["price_observed"] for r in rows.values())
        assert all(r["provider"] == "ESPN BET" for r in rows.values())
        assert all(r["espn_event_id"] == "401810001" for r in rows.values())

    def test_the_game_date_is_the_eastern_date_not_utc(self):
        # 02:00 utc on the 22nd is 10pm et on the 21st
        event = _espn_event(FULL_ODDS, when="2026-10-22T02:00Z")

        rows = parse_event_odds(event)

        assert {r["game_date"] for r in rows} == {date(2026, 10, 21)}

    def test_the_away_spread_line_is_the_home_line_sign_flipped(self):
        node = {
            "provider": {"name": "ESPN BET"},
            "pointSpread": {
                "home": {"close": {"line": "+4.5", "odds": "-108"}},
                "away": {"close": {"line": "-4.5", "odds": "-112"}},
            },
        }

        rows = _by_key(parse_event_odds(_espn_event(node)))

        assert rows[("spread", "home")]["line"] == 4.5
        assert rows[("spread", "away")]["line"] == -4.5
        assert rows[("spread", "away")]["price"] == -112

    def test_missing_spread_prices_are_null_and_unobserved_never_defaulted(self):
        node = {
            "provider": {"name": "ESPN BET"},
            "spread": -3.0,
            "overUnder": 219.0,
            "homeTeamOdds": {"moneyLine": -150},
            "awayTeamOdds": {"moneyLine": 130},
        }

        rows = _by_key(parse_event_odds(_espn_event(node)))

        for key in (("spread", "home"), ("spread", "away"), ("total", "over")):
            assert rows[key]["price"] is None
            assert rows[key]["price_observed"] is False
        assert rows[("spread", "home")]["line"] == -3.0
        assert rows[("spread", "away")]["line"] == 3.0
        assert rows[("moneyline", "home")]["price"] == -150
        assert rows[("moneyline", "home")]["price_observed"] is True

    def test_even_prices_read_as_plus_one_hundred(self):
        node = {
            "provider": {"name": "ESPN BET"},
            "pointSpread": {
                "home": {"close": {"line": "-1.5", "odds": "EVEN"}},
                "away": {"close": {"line": "+1.5", "odds": "-120"}},
            },
            "moneyline": {
                "home": {"close": {"odds": "EVEN"}},
                "away": {"close": {"odds": "-120"}},
            },
        }

        rows = _by_key(parse_event_odds(_espn_event(node)))

        assert rows[("spread", "home")]["price"] == 100
        assert rows[("moneyline", "home")]["price"] == 100

    def test_even_details_is_a_pick_em_with_no_negative_zero(self):
        rows = _by_key(parse_event_odds(_espn_event({"details": "EVEN"})))

        assert rows[("spread", "home")]["line"] == 0.0
        assert str(rows[("spread", "away")]["line"]) == "0.0"

    def test_a_details_only_spread_is_read_relative_to_the_home_team(self):
        # the away team (GS) is favoured by 6
        node = {"provider": {"name": "ESPN BET"}, "details": "GS -6"}

        rows = _by_key(parse_event_odds(_espn_event(node)))

        assert set(rows) == {("spread", "home"), ("spread", "away")}
        assert rows[("spread", "home")]["line"] == 6.0
        assert rows[("spread", "away")]["line"] == -6.0
        assert rows[("spread", "home")]["price_observed"] is False

    def test_an_unpublished_price_string_is_null_not_zero(self):
        node = {
            "pointSpread": {
                "home": {"close": {"line": "-2.5", "odds": "OFF"}},
                "away": {"close": {"line": "+2.5"}},
            },
        }

        rows = _by_key(parse_event_odds(_espn_event(node)))

        assert rows[("spread", "home")]["price"] is None
        assert rows[("spread", "away")]["price"] is None

    def test_no_odds_node_gives_no_rows(self):
        assert parse_event_odds(_espn_event(None)) == []


SCHEDULE_ROWS = [
    {"nba_game_id": "0022600011", "game_date": date(2026, 10, 21),
     "home_team_abbr": "BOS", "away_team_abbr": "MIA"},
    {"nba_game_id": "0022600012", "game_date": date(2026, 10, 21),
     "home_team_abbr": "NYK", "away_team_abbr": "GSW"},
    {"nba_game_id": "0022600031", "game_date": date(2026, 10, 23),
     "home_team_abbr": "NYK", "away_team_abbr": "GSW"},
]


class TestMapEventToNbaGame:
    def test_an_exact_tricode_match_maps(self):
        game_id = map_event_to_nba_game(date(2026, 10, 21), "BOS", "MIA", SCHEDULE_ROWS)

        assert game_id == "0022600011"

    def test_espn_abbreviations_are_aliased_to_nba_tricodes(self):
        game_id = map_event_to_nba_game(date(2026, 10, 21), "NY", "GS", SCHEDULE_ROWS)

        assert game_id == "0022600012"

    def test_the_date_disambiguates_a_repeat_matchup(self):
        game_id = map_event_to_nba_game(date(2026, 10, 23), "NY", "GS", SCHEDULE_ROWS)

        assert game_id == "0022600031"

    def test_no_match_maps_to_none(self):
        game_id = map_event_to_nba_game(date(2026, 10, 21), "UTAH", "WSH", SCHEDULE_ROWS)

        assert game_id is None

    def test_swapped_home_and_away_do_not_match(self):
        game_id = map_event_to_nba_game(date(2026, 10, 21), "MIA", "BOS", SCHEDULE_ROWS)

        assert game_id is None


class TestPlanOddsSnapshot:
    def test_unmapped_events_keep_their_rows_with_a_null_game_id(self):
        events = [
            _espn_event(FULL_ODDS),
            _espn_event(FULL_ODDS, event_id="401810002", home="UTAH", away="WSH"),
        ]

        rows, mappings, unmapped = plan_odds_snapshot(events, SCHEDULE_ROWS, {})

        assert unmapped == 1
        assert {r["nba_game_id"] for r in rows if r["espn_event_id"] == "401810002"} == {None}
        assert [m["nba_game_id"] for m in mappings] == ["0022600012"]
        assert mappings[0]["mapped_by"] == "date_abbr"
        assert mappings[0]["home_team_abbr"] == "NYK"

    def test_a_known_mapping_is_reused_and_not_rewritten(self):
        rows, mappings, unmapped = plan_odds_snapshot(
            [_espn_event(FULL_ODDS)], [], {"401810001": "0022600099"}
        )

        assert {r["nba_game_id"] for r in rows} == {"0022600099"}
        assert mappings == []
        assert unmapped == 0

    def test_games_already_underway_are_not_snapshotted(self):
        rows, _, _ = plan_odds_snapshot(
            [_espn_event(FULL_ODDS, status="STATUS_IN_PROGRESS")], SCHEDULE_ROWS, {}
        )

        assert rows == []


class OddsCursor:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def fetchall(self):
        if "FROM nba_schedule" in self.statements[-1]:
            return [
                (r["nba_game_id"], r["game_date"], r["home_team_abbr"], r["away_team_abbr"])
                for r in SCHEDULE_ROWS
            ]
        return []

    def fetchone(self):
        return (7,) if "RETURNING id" in self.statements[-1] else None

    def close(self):
        pass


class OddsConn:
    def __init__(self):
        self.cursor_ = OddsCursor()

    def cursor(self):
        return self.cursor_


class TestScrapeOddsSnapshots:
    @pytest.fixture
    def written(self, monkeypatch):
        batches: list[tuple[str, list]] = []
        monkeypatch.setattr(
            database, "execute_values",
            lambda cur, sql, rows, page_size: batches.append((sql, list(rows))),
        )
        return batches

    def _serve(self, monkeypatch, events):
        monkeypatch.setattr(
            odds, "fetch_espn_scoreboard_events",
            lambda day: events if day == date(2026, 10, 21) else [],
        )

    def test_snapshot_rows_and_the_new_mapping_are_written(self, monkeypatch, written):
        self._serve(monkeypatch, [_espn_event(FULL_ODDS)])

        ok = odds.scrape_odds_snapshots(OddsConn(), today=date(2026, 10, 21))

        inserts = [rows for sql, rows in written if "INSERT INTO odds_snapshots" in sql]
        maps = [rows for sql, rows in written if "INSERT INTO espn_event_map" in sql]
        assert ok is True
        assert len(inserts[0]) == 6
        assert {row[1] for row in inserts[0]} == {"0022600012"}
        assert {row[10:] for row in inserts[0]} == {("espn_scoreboard", 7)}
        assert maps[0][0][:2] == ("401810001", "0022600012")

    def test_dry_run_reads_but_writes_nothing(self, monkeypatch, written):
        self._serve(monkeypatch, [_espn_event(FULL_ODDS)])
        conn = OddsConn()

        ok = odds.scrape_odds_snapshots(conn, dry_run=True, today=date(2026, 10, 21))

        assert ok is True
        assert written == []
        assert conn.cursor_.statements
        assert all(not is_write_statement(sql) for sql in conn.cursor_.statements)

    def test_a_failed_fetch_writes_nothing_and_reports_failure(self, monkeypatch, written):
        def boom(day):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(odds, "fetch_espn_scoreboard_events", boom)
        conn = OddsConn()

        ok = odds.scrape_odds_snapshots(conn, dry_run=True, today=date(2026, 10, 21))

        assert ok is False
        assert written == []
        assert conn.cursor_.statements == []

    def test_the_fetch_window_is_today_through_two_days_out(self, monkeypatch, written):
        seen: list[date] = []
        monkeypatch.setattr(
            odds, "fetch_espn_scoreboard_events", lambda day: seen.append(day) or []
        )

        odds.scrape_odds_snapshots(OddsConn(), dry_run=True, today=date(2026, 10, 21))

        assert seen == [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]

    def test_one_failed_day_still_snapshots_the_others(self, monkeypatch, written):
        def flaky(day):
            if day == date(2026, 10, 22):
                raise requests.ConnectionError("down")
            return [_espn_event(FULL_ODDS)] if day == date(2026, 10, 21) else []

        monkeypatch.setattr(odds, "fetch_espn_scoreboard_events", flaky)

        ok = odds.scrape_odds_snapshots(OddsConn(), today=date(2026, 10, 21))

        inserts = [rows for sql, rows in written if "INSERT INTO odds_snapshots" in sql]
        assert ok is True
        assert len(inserts[0]) == 6


class TestOddsCli:
    def test_odds_only_is_off_by_default(self):
        assert _parse_args([]).odds_only is False

    def test_odds_only_parses_with_dry_run(self):
        args = _parse_args(["--odds-only", "--dry-run"])

        assert args.odds_only is True
        assert args.dry_run is True
