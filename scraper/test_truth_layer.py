import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

import database
import fetching
import injury_report
import run_scraper
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
from database import DryRunCursor, is_write_statement
from odds import map_event_to_nba_game, parse_event_odds, plan_odds_snapshot
from parsing import (
    box_score_violations,
    canonical_player_name,
    cbs_team_abbr,
    cleared_player_ids,
    clearances_for_report,
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
    BOX_DETAILS_SOURCE,
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
    active_dnp_status_rows,
    box_detail_rows_from_traditional,
    build_player_game_log_row,
    build_team_game_log_row,
    derive_game_status_rows,
    game_log_fetch_from,
    merge_dnp_reason,
    normalize_inactive_rows,
    plan_roster_snapshot,
    plan_stint_change,
    player_ids_absent_from_box,
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
            ("Game Time Decision", "questionable"),
            ("GTD", "questionable"),
            ("Available", "available"),
            ("cleared", "cleared"),
        ],
    )
    def test_buckets_known_wording(self, raw, expected):
        assert normalize_injury_status(raw) == expected

    def test_longest_phrase_wins(self):
        assert normalize_injury_status("out for season") == "out"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Expected to be out until at least Nov 1", "out"),
            ("Out for the season", "out"),
            ("Questionable for start of season", "questionable"),
            ("Game Time Decision", "questionable"),
            ("Day-To-Day", "day_to_day"),
            ("Probable", "probable"),
        ],
    )
    def test_buckets_live_cbs_wording(self, raw, expected):
        assert normalize_injury_status(raw) == expected

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

    def fetchall(self):
        return []

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

def _cbs_row(name, cells, href="/nba/players/1/x/"):
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return (
        '<tr class="TableBase-bodyTr"><td>'
        f'<span class="CellPlayerName--short"><a href="{href}">X. Short</a></span>'
        f'<span class="CellPlayerName--long"><a href="{href}">{name}</a></span>'
        f"</td>{tds}</tr>"
    )


def _cbs_table(team_code, headers, rows):
    head = "".join(f'<th class="TableBase-headTh">{h}</th>' for h in headers)
    thead = f'<thead><tr class="TableBase-headTr">{head}</tr></thead>' if headers else ""
    title = (
        '<h4 class="TableBase-title"><span class="TeamName">'
        f'<a href="/nba/teams/{team_code}/team/">Team</a></span></h4>'
    )
    return f'<div class="TableBase">{title}<table>{thead}<tbody>{"".join(rows)}</tbody></table></div>'


CBS_FIVE_HEADERS = ["Player", "Position", "Updated", "Injury", "Injury Status"]

# the 2026-10 layout, with the live wording that broke the positional parser.
CBS_FIVE_COLUMN_HTML = _cbs_table(
    "NO",
    CBS_FIVE_HEADERS,
    [
        _cbs_row(
            "Brandon Ingram",
            ["SF", '<span class="CellGameDate">Mon, Sep 28</span>', "Achilles",
             "Expected to be out until at least Nov 1"],
        ),
    ],
) + _cbs_table(
    "MIA",
    CBS_FIVE_HEADERS,
    [_cbs_row("Jimmy Butler", ["SF", "Tue, Sep 22", "Knee", "Out for the season"])],
)

INJURY_PLAYERS = [
    ("2544", "LeBron James", "LAL"),
    ("201939", "Stephen Curry", "GSW"),
    ("1627742", "Brandon Ingram", "NOP"),
    ("202710", "Jimmy Butler III", "MIA"),
]


class InjuryCursor:
    def __init__(self, previously_listed: list[str]):
        self.previously_listed = previously_listed
        self.statements: list[tuple[str, object]] = []
        self._result: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "injury_status IS NOT NULL" in sql:
            self._result = [(i, f"Player {i}") for i in self.previously_listed]
        elif "SELECT nba_id, name, team" in sql:
            self._result = list(INJURY_PLAYERS)
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

    def status_updates(self):
        return [p for sql, p in self.statements if "WHERE nba_id = %s" in sql]


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

    def test_statuses_are_written_by_nba_id_from_the_five_column_page(self, monkeypatch):
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: CBS_FIVE_COLUMN_HTML)
        conn = InjuryConn(previously_listed=["1627742"])

        scrapes.scrape_injuries(conn)

        assert conn.cursor_.status_updates() == [
            ("Expected to be out until at least Nov 1", "Achilles", "1627742"),
            ("Out for the season", "Knee", "202710"),
        ]
        assert [p[2] for p in conn.cursor_.report_inserts()] == ["out", "out"]
        assert conn.cursor_.clearances() == []

    def test_a_page_matching_no_player_writes_nothing(self, monkeypatch):
        html = _cbs_table("ATL", CBS_FIVE_HEADERS, [_cbs_row("Nobody Known", ["G", "", "Hip", "Out"])])
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: html)
        conn = InjuryConn(previously_listed=["2544"])

        scrapes.scrape_injuries(conn)

        assert all(not is_write_statement(sql) for sql, _ in conn.cursor_.statements)


class TestParseCbsInjuryRows:
    def test_five_column_layout_maps_status_and_injury_by_header(self):
        rows = scrapes._parse_cbs_injury_rows(CBS_FIVE_COLUMN_HTML)

        assert rows[0] == scrapes.CbsInjuryRow(
            player_name="Brandon Ingram",
            status="Expected to be out until at least Nov 1",
            injury="Achilles",
            updated="Mon, Sep 28",
            team_abbr="NOP",
        )
        assert (rows[1].player_name, rows[1].status, rows[1].injury) == (
            "Jimmy Butler", "Out for the season", "Knee",
        )
        assert rows[1].team_abbr == "MIA"

    def test_four_column_legacy_layout_with_headers_still_parses(self):
        html = _cbs_table(
            "LAL",
            ["Player", "Position", "Injury", "Injury Status"],
            [_cbs_row("LeBron James", ["F", "Ankle", "Out"])],
        )

        rows = scrapes._parse_cbs_injury_rows(html)

        assert [(r.player_name, r.status, r.injury, r.updated) for r in rows] == [
            ("LeBron James", "Out", "Ankle", ""),
        ]

    def test_headerless_legacy_table_falls_back_to_positions(self):
        rows = scrapes._parse_cbs_injury_rows(CBS_INJURY_HTML)

        assert [(r.player_name, r.status, r.injury) for r in rows] == [
            ("LeBron James", "Out", "Ankle"),
            ("Stephen Curry", "Questionable", "Knee"),
        ]

    def test_missing_status_header_warns_and_yields_no_rows(self, caplog):
        html = _cbs_table(
            "LAL",
            ["Player", "Position", "Updated", "Injury", "Return"],
            [_cbs_row("LeBron James", ["F", "Mon, Sep 28", "Ankle", "Out"])],
        )

        with caplog.at_level("WARNING", logger="scrapes"):
            rows = scrapes._parse_cbs_injury_rows(html)

        assert rows == []
        assert "status" in caplog.text and "Return" in caplog.text

    def test_missing_status_header_makes_the_scrape_refuse_to_clear(self, monkeypatch):
        html = _cbs_table(
            "LAL", ["Player", "Position", "Updated", "Injury"],
            [_cbs_row("LeBron James", ["F", "Mon, Sep 28", "Ankle"])],
        )
        monkeypatch.setattr(scrapes.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(scrapes, "fetch_injury_page", lambda: html)
        conn = InjuryConn(previously_listed=["2544"])

        scrapes.scrape_injuries(conn)

        assert conn.cursor_.statements == []

    @pytest.mark.parametrize(
        "headers, expected",
        [
            (CBS_FIVE_HEADERS, {"player": 0, "position": 1, "updated": 2, "injury": 3, "status": 4}),
            (["INJURY STATUS", "Injury", "player"], {"status": 0, "injury": 1, "player": 2}),
        ],
    )
    def test_column_mapping_is_by_header_name(self, headers, expected):
        assert scrapes.map_cbs_injury_columns(headers) == expected


class TestCanonicalPlayerName:
    @pytest.mark.parametrize(
        "left, right",
        [
            ("Jimmy Butler III", "Jimmy Butler"),
            ("Luka Dončić", "Luka Doncic"),
            ("P.J. Washington", "PJ Washington"),
            ("Gary Trent Jr.", "Gary Trent"),
            ("De’Aaron Fox", "De'Aaron Fox"),
            ("Robert Williams V", "robert williams"),
        ],
    )
    def test_variants_share_a_canonical_form(self, left, right):
        assert canonical_player_name(left) == canonical_player_name(right)

    def test_a_suffix_alone_is_not_erased(self):
        assert canonical_player_name("V") == "v"

    def test_suffix_letters_inside_a_word_are_kept(self):
        assert canonical_player_name("Ivica Zubac") == "ivica zubac"


class TestMatchCbsInjuryRows:
    def _row(self, name, team=None):
        return scrapes.CbsInjuryRow(name, "Out", "Knee", "", team)

    def test_matches_across_suffix_and_accent_differences(self):
        index = scrapes.index_players_by_canonical_name(
            [("202710", "Jimmy Butler III", "MIA"), ("1629029", "Luka Dončić", "LAL")]
        )

        match = scrapes.match_cbs_injury_rows(
            [self._row("Jimmy Butler"), self._row("Luka Doncic")], index
        )

        assert [nba_id for nba_id, _ in match.matched] == ["202710", "1629029"]

    def test_duplicate_name_is_disambiguated_by_team(self):
        index = scrapes.index_players_by_canonical_name(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )

        match = scrapes.match_cbs_injury_rows([self._row("Jalen Williams", "DEN")], index)

        assert [nba_id for nba_id, _ in match.matched] == ["2"]

    def test_duplicate_name_without_a_team_is_skipped_as_ambiguous(self):
        index = scrapes.index_players_by_canonical_name(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )

        match = scrapes.match_cbs_injury_rows([self._row("Jalen Williams")], index)

        assert match.matched == []
        assert [r.player_name for r in match.ambiguous] == ["Jalen Williams"]

    def test_unknown_player_is_unmatched(self):
        match = scrapes.match_cbs_injury_rows([self._row("Nobody Known")], {})

        assert [r.player_name for r in match.unmatched] == ["Nobody Known"]

    def test_cbs_team_codes_map_to_nba_abbreviations(self):
        assert [cbs_team_abbr(c) for c in ["GS", "NO", "NY", "PHO", "SA", "LAL", "XYZ"]] == [
            "GSW", "NOP", "NYK", "PHX", "SAS", "LAL", None,
        ]


def _box_player(person_id, position="", comment="", minutes="", oreb=0, dreb=0, pf=0):
    # shaped like a real boxscoretraditionalv3 PlayerStats record (0022500001):
    # starters carry a position, a DNP reports "" minutes and zeros everywhere.
    return {
        "gameId": "0022500001", "teamId": 1610612745, "teamTricode": "HOU",
        "personId": person_id, "firstName": "A", "familyName": "Player",
        "position": position, "comment": comment, "jerseyNum": "1",
        "minutes": minutes, "fieldGoalsMade": 0, "fieldGoalsAttempted": 0,
        "reboundsOffensive": oreb, "reboundsDefensive": dreb,
        "reboundsTotal": oreb + dreb, "assists": 0, "steals": 0, "blocks": 0,
        "turnovers": 0, "foulsPersonal": pf, "points": 0, "plusMinusPoints": 0,
    }


BOX_SCORE_V3 = {
    "player_stats": [
        _box_player(1631095, position="F", minutes="41:45", oreb=3, dreb=2, pf=4),
        _box_player(201142, position="G", minutes="47:03", oreb=0, dreb=9, pf=6),
        _box_player(1631106, minutes="21:36", oreb=0, dreb=6, pf=1),
        _box_player(1631120, comment="  DNP - Coach's Decision "),
    ],
    "team_stats": [
        {"gameId": "0022500001", "teamId": 1610612760, "minutes": "290:00",
         "reboundsOffensive": 11, "reboundsDefensive": 27, "foulsPersonal": 27},
        {"gameId": "0022500001", "teamId": 1610612745, "minutes": "290:00",
         "reboundsOffensive": 16, "reboundsDefensive": 36, "foulsPersonal": 26},
    ],
}


class TestBoxDetailRows:
    def _players(self):
        players, _ = box_detail_rows_from_traditional(BOX_SCORE_V3, "0022500001")
        return {row["nba_player_id"]: row for row in players}

    def test_starters_are_the_players_with_a_position(self):
        players = self._players()

        assert players["1631095"]["started"] is True
        assert players["1631095"]["position"] == "F"
        assert players["201142"]["position"] == "G"
        assert players["1631106"]["started"] is False
        assert players["1631106"]["position"] is None

    def test_starter_maps_the_rebound_split_fouls_and_minutes(self):
        starter = self._players()["1631095"]

        assert starter["nba_game_id"] == "0022500001"
        assert starter["team_id"] == "1610612745"
        assert (starter["oreb"], starter["dreb"], starter["pf"]) == (3, 2, 4)
        assert starter["minutes"] == pytest.approx(41.75)
        assert starter["dnp_reason"] is None

    def test_bench_player_who_played_keeps_his_line(self):
        bench = self._players()["1631106"]

        assert bench["minutes"] == pytest.approx(21.6)
        assert (bench["oreb"], bench["dreb"], bench["pf"]) == (0, 6, 1)

    def test_dnp_has_no_minutes_no_stats_and_a_trimmed_verbatim_reason(self):
        dnp = self._players()["1631120"]

        assert dnp["minutes"] is None
        assert (dnp["oreb"], dnp["dreb"], dnp["pf"]) == (None, None, None)
        assert dnp["started"] is False
        assert dnp["dnp_reason"] == "DNP - Coach's Decision"

    @pytest.mark.parametrize(
        "comment", ["DND - Injury/Illness", "NWT - Personal", "DNP - Coach's Decision"]
    )
    def test_reason_prefixes_are_kept_verbatim(self, comment):
        payload = {"player_stats": [_box_player(1, comment=comment)]}

        players, _ = box_detail_rows_from_traditional(payload, "0022500001")

        assert players[0]["dnp_reason"] == comment

    def test_team_totals_carry_the_rebound_split_and_fouls(self):
        _, teams = box_detail_rows_from_traditional(BOX_SCORE_V3, "0022500001")

        by_team = {row["team_id"]: row for row in teams}
        assert by_team["1610612745"] == {
            "team_id": "1610612745", "nba_game_id": "0022500001",
            "oreb": 16, "dreb": 36, "pf": 26,
        }
        assert by_team["1610612760"]["oreb"] == 11

    def test_a_row_without_a_person_id_is_dropped(self):
        payload = {"player_stats": [_box_player(None, minutes="10:00")]}

        players, teams = box_detail_rows_from_traditional(payload, "0022500001")

        assert players == []
        assert teams == []


class TestActiveDnpStatusRows:
    GAME = "0022500001"

    def _box_rows(self, *players):
        payload = {"player_stats": list(players)}
        rows, _ = box_detail_rows_from_traditional(payload, self.GAME)
        return rows

    def test_a_dnp_without_a_status_row_is_inserted_as_available_and_unused(self):
        rows = self._box_rows(_box_player(1631120, comment="DNP - Coach's Decision"))

        inserted = active_dnp_status_rows(rows, set(), self.GAME)

        assert inserted == [{
            "nba_player_id": "1631120", "nba_game_id": self.GAME,
            "team_id": "1610612745", "rostered": True, "listed_inactive": False,
            "started": False, "played": False,
            "dnp_reason": "DNP - Coach's Decision", "minutes": None,
            "source": "boxscoretraditionalv3",
        }]

    def test_a_dnp_with_an_existing_status_row_is_not_inserted(self):
        rows = self._box_rows(_box_player(1631120, comment="DNP - Coach's Decision"))

        inserted = active_dnp_status_rows(rows, {("1631120", self.GAME)}, self.GAME)

        assert inserted == []

    def test_a_player_with_minutes_is_never_inserted(self):
        rows = self._box_rows(_box_player(1631106, minutes="21:36"))

        inserted = active_dnp_status_rows(rows, set(), self.GAME)

        assert inserted == []

    @pytest.mark.parametrize("comment", ["DND - Injury/Illness", "NWT - Personal"])
    def test_dnd_and_nwt_leave_listed_inactive_unknown(self, comment):
        rows = self._box_rows(_box_player(1, comment=comment))

        inserted = active_dnp_status_rows(rows, set(), self.GAME)

        assert inserted[0]["listed_inactive"] is None
        assert inserted[0]["dnp_reason"] == comment
        assert inserted[0]["played"] is False

    def test_an_empty_comment_still_inserts_with_no_reason(self):
        rows = self._box_rows(_box_player(1, comment="   "))

        inserted = active_dnp_status_rows(rows, set(), self.GAME)

        assert len(inserted) == 1
        assert inserted[0]["dnp_reason"] is None
        assert inserted[0]["listed_inactive"] is False

    def test_a_key_for_another_game_does_not_suppress_the_insert(self):
        rows = self._box_rows(_box_player(1, comment="DNP - Coach's Decision"))

        inserted = active_dnp_status_rows(rows, {("1", "0022500002")}, self.GAME)

        assert len(inserted) == 1


class TestMergeDnpReason:
    def test_an_existing_reason_wins(self):
        assert merge_dnp_reason("Inactive - Injury", "DNP - Coach's Decision") == (
            "Inactive - Injury"
        )

    def test_a_null_existing_reason_is_filled(self):
        assert merge_dnp_reason(None, "DNP - Coach's Decision") == "DNP - Coach's Decision"

    def test_null_never_overwrites_a_reason(self):
        assert merge_dnp_reason("DND - Injury/Illness", None) == "DND - Injury/Illness"


class TestPlayersAbsentFromBox:
    def test_logged_players_missing_from_v3_are_reported(self):
        players, _ = box_detail_rows_from_traditional(BOX_SCORE_V3, "0022500001")

        absent = player_ids_absent_from_box(["1631095", "999", "999"], players)

        assert absent == ["999"]


class TestApplyBoxDetails:
    def test_dry_run_sends_every_update_and_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(
            truth_layer, "fetch_box_score_traditional", lambda game_id: BOX_SCORE_V3
        )
        inner = FakeCursor()
        cur = DryRunCursor(inner)

        counts = truth_layer._apply_box_details(cur, "0022500001", ["1631095", "999"])

        assert counts == {
            "players": 4, "teams": 2, "status": 4, "absent": 1, "active_dnp": 1,
        }
        assert all(not is_write_statement(sql) for sql in inner.statements)
        assert cur.skipped_statements == 5

    def test_a_dnp_written_earlier_on_the_cursor_is_not_inserted_again(self, monkeypatch):
        monkeypatch.setattr(
            truth_layer, "fetch_box_score_traditional", lambda game_id: BOX_SCORE_V3
        )
        cur = DryRunCursor(FakeCursor())

        counts = truth_layer._apply_box_details(
            cur, "0022500001", ["1631095"], pending_status_ids=["1631120"]
        )

        assert counts["active_dnp"] == 0

    def test_the_active_dnp_insert_never_overwrites_a_row(self):
        sql = " ".join(truth_layer.ACTIVE_DNP_STATUS_INSERT_SQL.split())

        assert sql.endswith("ON CONFLICT (nba_player_id, nba_game_id) DO NOTHING")

    def test_an_empty_box_score_raises_so_the_game_stays_unstamped(self, monkeypatch):
        monkeypatch.setattr(
            truth_layer,
            "fetch_box_score_traditional",
            lambda game_id: {"player_stats": [], "team_stats": []},
        )

        with pytest.raises(ValueError):
            truth_layer._apply_box_details(DryRunCursor(FakeCursor()), "0022500001", ["1"])

    def test_the_player_update_keeps_an_existing_dnp_reason(self):
        sql = " ".join(truth_layer.BOX_DETAIL_PLAYER_UPDATE_SQL.split())

        assert "dnp_reason = COALESCE(p.dnp_reason, v.dnp_reason::text)" in sql
        assert BOX_DETAILS_SOURCE == "boxscoretraditionalv3"


class TestBoxDetailsCli:
    def test_backfill_box_details_parses_with_season_and_limit(self):
        args = _parse_args(
            ["--backfill-box-details", "--season", "2024-25", "--limit", "300"]
        )

        assert args.backfill_box_details is True
        assert args.season == "2024-25"
        assert args.limit == 300

    def test_limit_defaults_to_unbounded(self):
        assert _parse_args(["--backfill-box-details"]).limit is None


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
            lambda cur, sql, rows, page_size, template=None: batches.append((sql, list(rows))),
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




OFFICIAL_REPORT_PAGES = [
    "Injury Report: 01/20/26 05:00 PM\n"
    "Game Date Game Time Matchup Team Player Name Current Status Reason\n"
    "01/20/2026 07:00 (ET) BOS@NYK Boston Celtics Brown, Jaylen Questionable "
    "Injury/Illness - Left Knee; Soreness\n"
    "Injury/Illness - Right Achilles; Repair\n"
    "Tatum, Jayson Out\n"
    "Management\n"
    "New York Knicks NOT YET SUBMITTED\n"
    "07:30 (ET) LAL@MIA Los Angeles Lakers Doncic, Luka Probable "
    "Injury/Illness - Left Hamstring;\n"
    "Page 1 of 2\n",
    "Injury Report: 01/20/26 05:00 PM\n"
    "Strain\n"
    "Hayes, Jaxson Out G League - Two-Way\n"
    "Miami Heat Jaquez Jr., Jaime Available Injury/Illness - Low Back; Spasm\n"
    "01/21/2026 07:00 (ET) LAC@CHA LA Clippers Beal, Bradley Out Not With Team\n"
    "Page 2 of 2\n",
]
OFFICIAL_PUBLISHED_AT = datetime(2026, 1, 20, 22, 0, tzinfo=timezone.utc)


def _official_rows() -> list[dict]:
    return injury_report.parse_injury_report_text(
        OFFICIAL_REPORT_PAGES, OFFICIAL_PUBLISHED_AT
    )


def _by_player(rows: list[dict]) -> dict[str, dict]:
    return {r["player_name"]: r for r in rows if r["player_name"]}


class TestParseOfficialInjuryReport:
    def test_every_player_and_unfiled_team_becomes_one_row(self):
        # act
        rows = _official_rows()

        # assert
        assert [r["player_name"] for r in rows] == [
            "Jaylen Brown", "Jayson Tatum", None, "Luka Doncic",
            "Jaxson Hayes", "Jaime Jaquez Jr.", "Bradley Beal",
        ]

    def test_columns_left_of_the_player_are_forward_filled(self):
        # act
        tatum = _by_player(_official_rows())["Jayson Tatum"]

        # assert
        assert tatum["game_date"] == date(2026, 1, 20)
        assert tatum["game_time_et"] == "07:00"
        assert tatum["matchup"] == "BOS@NYK"
        assert tatum["team_abbr"] == "BOS"

    def test_a_new_team_keeps_the_matchup_and_time(self):
        # act
        jaquez = _by_player(_official_rows())["Jaime Jaquez Jr."]

        # assert
        assert (jaquez["matchup"], jaquez["team_abbr"]) == ("LAL@MIA", "MIA")
        assert jaquez["game_time_et"] == "07:30"
        assert jaquez["status_normalized"] == "available"

    def test_a_new_game_date_replaces_the_old_one(self):
        # act
        beal = _by_player(_official_rows())["Bradley Beal"]

        # assert
        assert beal["game_date"] == date(2026, 1, 21)
        assert beal["team_abbr"] == "LAC"
        assert beal["reason"] == "Not With Team"

    def test_a_reason_wrapped_around_the_player_line_is_rejoined(self):
        # act
        tatum = _by_player(_official_rows())["Jayson Tatum"]

        # assert
        assert tatum["reason"] == "Injury/Illness - Right Achilles; Repair Management"
        assert tatum["status_raw"] == "Out"
        assert tatum["status_normalized"] == "out"

    def test_a_reason_continued_across_a_page_break_stays_with_its_player(self):
        # act
        players = _by_player(_official_rows())

        # assert
        assert players["Luka Doncic"]["reason"] == "Injury/Illness - Left Hamstring; Strain"
        assert players["Jaxson Hayes"]["reason"] == "G League - Two-Way"
        assert players["Jaxson Hayes"]["matchup"] == "LAL@MIA"

    def test_an_unfiled_team_is_flagged_with_no_player(self):
        # act
        unfiled = [r for r in _official_rows() if r["not_yet_submitted"]]

        # assert
        assert len(unfiled) == 1
        assert (unfiled[0]["matchup"], unfiled[0]["team_abbr"]) == ("BOS@NYK", "NYK")
        assert unfiled[0]["player_name"] is None

    def test_rows_carry_the_report_time_and_the_original_name(self):
        # act
        brown = _by_player(_official_rows())["Jaylen Brown"]

        # assert
        assert brown["report_as_of"] == OFFICIAL_PUBLISHED_AT
        assert brown["player_name_last_first"] == "Brown, Jaylen"

    def test_no_pages_parse_to_no_rows(self):
        # act + assert
        assert injury_report.parse_injury_report_text([], OFFICIAL_PUBLISHED_AT) == []


class TestOfficialReportNames:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Brown, Jaylen", "Jaylen Brown"),
            ("Jaquez Jr., Jaime", "Jaime Jaquez Jr."),
            ("Bagley III, Marvin", "Marvin Bagley III"),
            ("Jones Garcia, David", "David Jones Garcia"),
            ("Nene", "Nene"),
        ],
    )
    def test_last_first_becomes_first_last(self, raw, expected):
        # act + assert
        assert injury_report.last_first_to_first_last(raw) == expected


class TestOfficialReportMatchup:
    def test_away_is_left_of_the_at_sign(self):
        # act + assert
        assert injury_report.parse_report_matchup("BOS@NYK") == ("BOS", "NYK")

    @pytest.mark.parametrize("raw", [None, "", "BOS NYK", "@NYK", "BOS@"])
    def test_an_unreadable_matchup_is_none_not_a_guess(self, raw):
        # act + assert
        assert injury_report.parse_report_matchup(raw) == (None, None)


class TestOfficialReportPublishedAt:
    @pytest.mark.parametrize(
        "name, expected",
        [
            # eastern standard time, utc-5
            ("Injury-Report_2026-01-20_05_00PM.pdf", datetime(2026, 1, 20, 22, 0)),
            ("Injury-Report_2026-01-20_12_15AM.pdf", datetime(2026, 1, 20, 5, 15)),
            ("Injury-Report_2026-01-20_12_00PM.pdf", datetime(2026, 1, 20, 17, 0)),
            # eastern daylight time, utc-4
            ("Injury-Report_2026-04-10_05_00PM.pdf", datetime(2026, 4, 10, 21, 0)),
            # either side of the 2026-03-08 spring-forward
            ("Injury-Report_2026-03-08_01_30AM.pdf", datetime(2026, 3, 8, 6, 30)),
            ("Injury-Report_2026-03-08_03_30AM.pdf", datetime(2026, 3, 8, 7, 30)),
            # the hourly names older seasons used
            ("Injury-Report_2025-03-10_05PM.pdf", datetime(2025, 3, 10, 21, 0)),
        ],
    )
    def test_the_filename_time_is_eastern_converted_to_utc(self, name, expected):
        # act
        published = injury_report.parse_report_published_at(
            injury_report.NBA_INJURY_REPORT_BASE_URL + name
        )

        # assert
        assert published == expected.replace(tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "name", ["", "Injury-Report.pdf", "Injury-Report_2026-01-20_13_00PM.pdf"]
    )
    def test_an_unreadable_name_is_none(self, name):
        # act + assert
        assert injury_report.parse_report_published_at(name) is None


class TestOfficialReportCandidateUrls:
    def test_newest_slot_first_back_to_midnight_eastern(self):
        # arrange
        now = datetime(2026, 1, 20, 22, 7, tzinfo=timezone.utc)

        # act
        urls = injury_report.candidate_report_urls(now)

        # assert
        assert urls[0].endswith("Injury-Report_2026-01-20_05_00PM.pdf")
        assert urls[1].endswith("Injury-Report_2026-01-20_04_45PM.pdf")
        assert urls[-1].endswith("Injury-Report_2026-01-20_12_00AM.pdf")
        assert len(urls) == 17 * 4 + 1

    def test_the_day_is_the_eastern_day_not_the_utc_one(self):
        # arrange
        now = datetime(2026, 1, 21, 3, 0, tzinfo=timezone.utc)

        # act
        urls = injury_report.candidate_report_urls(now)

        # assert
        assert urls[0].endswith("Injury-Report_2026-01-20_10_00PM.pdf")
        assert all("2026-01-20" in url for url in urls)


OFFICIAL_SCHEDULE = [
    {"nba_game_id": "0022500601", "game_date": date(2026, 1, 20),
     "home_team_abbr": "NYK", "away_team_abbr": "BOS"},
    {"nba_game_id": "0022500602", "game_date": date(2026, 1, 20),
     "home_team_abbr": "MIA", "away_team_abbr": "LAL"},
]
OFFICIAL_PLAYERS = [
    ("1628369", "Jayson Tatum", "BOS"),
    ("1627759", "Jaylen Brown", "BOS"),
    ("1629029", "Luka Doncic", "LAL"),
    ("1629637", "Jaxson Hayes", "LAL"),
    # stale team: the report lists him under MIA
    ("1630173", "Jaime Jaquez Jr.", "LAL"),
    ("203078", "Bradley Beal", "LAC"),
]


def _official_match() -> injury_report.ReportMatch:
    index = injury_report.index_players(OFFICIAL_PLAYERS)
    return injury_report.match_report_rows(_official_rows(), index, OFFICIAL_SCHEDULE)


class TestMatchOfficialReportRows:
    def test_players_and_games_are_attached(self):
        # act
        match = _official_match()

        # assert
        ids = {r["player_name"]: (r["nba_player_id"], r["nba_game_id"]) for r in match.matched}
        assert ids["Jayson Tatum"] == ("1628369", "0022500601")
        assert ids["Luka Doncic"] == ("1629029", "0022500602")
        assert match.matched[0]["team_id"] == "1610612738"

    def test_a_unique_name_on_another_team_still_matches(self):
        # act
        match = _official_match()

        # assert
        jaquez = next(r for r in match.matched if r["player_name"] == "Jaime Jaquez Jr.")
        assert jaquez["nba_player_id"] == "1630173"
        assert jaquez["team_abbr"] == "MIA"

    def test_a_game_missing_from_the_schedule_is_counted_not_dropped(self):
        # act
        match = _official_match()

        # assert
        beal = next(r for r in match.matched if r["player_name"] == "Bradley Beal")
        assert beal["nba_game_id"] is None
        assert match.unmatched_games == 1

    def test_an_unfiled_team_is_kept_aside_with_its_game(self):
        # act
        match = _official_match()

        # assert
        assert [(r["team_abbr"], r["nba_game_id"]) for r in match.not_submitted] == [
            ("NYK", "0022500601")
        ]

    def test_duplicate_names_are_told_apart_by_team(self):
        # arrange
        index = injury_report.index_players(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )
        rows = [
            {"player_name": "Jalen Williams", "team_abbr": "OKC", "matchup": "OKC@DEN",
             "game_date": date(2026, 1, 20), "not_yet_submitted": False},
            {"player_name": "Jalen Williams", "team_abbr": "DEN", "matchup": "OKC@DEN",
             "game_date": date(2026, 1, 20), "not_yet_submitted": False},
        ]

        # act
        match = injury_report.match_report_rows(rows, index, [])

        # assert
        assert [(r["team_abbr"], r["nba_player_id"]) for r in match.matched] == [
            ("OKC", "1"), ("DEN", "2")
        ]

    def test_a_duplicate_name_on_neither_team_is_skipped_and_counted(self):
        # arrange
        index = injury_report.index_players(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )
        rows = [
            {"player_name": "Jalen Williams", "team_abbr": "BOS", "matchup": "BOS@NYK",
             "game_date": date(2026, 1, 20), "not_yet_submitted": False},
            {"player_name": "Nobody Known", "team_abbr": "BOS", "matchup": "BOS@NYK",
             "game_date": date(2026, 1, 20), "not_yet_submitted": False},
        ]

        # act
        match = injury_report.match_report_rows(rows, index, [])

        # assert
        assert match.matched == []
        assert len(match.unmatched_players) == 2


T1 = datetime(2026, 1, 20, 17, 0, tzinfo=timezone.utc)
T0 = T1 - timedelta(hours=2)


def _prev(player: str, game: str, team: str, as_of: datetime, status: str = "out") -> dict:
    return {"nba_player_id": player, "nba_game_id": game, "team_abbr": team,
            "status_normalized": status, "report_as_of": as_of}


def _current(
    matched: list[tuple[str, str, str]], unfiled: list[tuple[str, str]]
) -> injury_report.ReportMatch:
    return injury_report.ReportMatch(
        matched=[{"nba_player_id": p, "nba_game_id": g, "team_abbr": t} for p, g, t in matched],
        not_submitted=[{"nba_game_id": g, "team_abbr": t} for g, t in unfiled],
    )


class TestOfficialClearances:
    def test_a_player_dropped_from_a_filed_team_is_cleared_for_that_game(self):
        # arrange
        previous = [_prev("A", "G1", "BOS", T1), _prev("B", "G1", "BOS", T1)]
        current = _current([("A", "G1", "BOS")], [])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert cleared == [{"nba_player_id": "B", "nba_game_id": "G1", "team_abbr": "BOS"}]

    def test_an_unfiled_team_clears_nobody(self):
        # arrange
        previous = [_prev("A", "G1", "BOS", T1), _prev("C", "G1", "NYK", T1)]
        current = _current([("A", "G1", "BOS")], [("G1", "NYK")])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert cleared == []

    def test_a_game_gone_from_the_report_clears_nobody(self):
        # arrange
        previous = [_prev("E", "G2", "LAL", T1)]
        current = _current([("A", "G1", "BOS")], [])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert cleared == []

    def test_only_the_latest_earlier_report_for_the_team_is_compared(self):
        # arrange
        previous = [_prev("D", "G1", "BOS", T0), _prev("A", "G1", "BOS", T1)]
        current = _current([("A", "G1", "BOS")], [])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert cleared == []

    def test_an_already_cleared_player_is_not_cleared_again(self):
        # arrange
        previous = [_prev("A", "G1", "BOS", T1), _prev("B", "G1", "BOS", T1, "cleared")]
        current = _current([("A", "G1", "BOS")], [])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert cleared == []

    def test_a_team_absent_from_a_game_still_on_the_report_counts_as_filed(self):
        # arrange
        previous = [_prev("C", "G1", "NYK", T1)]
        current = _current([("A", "G1", "BOS")], [])

        # act
        cleared = injury_report.official_clearances(previous, current)

        # assert
        assert [c["nba_player_id"] for c in cleared] == ["C"]


OFFICIAL_URL = (
    injury_report.NBA_INJURY_REPORT_BASE_URL + "Injury-Report_2026-01-20_05_00PM.pdf"
)


class OfficialCursor:
    def __init__(self, previous: list[tuple], already_ingested: bool = False):
        self.previous = previous
        self.already_ingested = already_ingested
        self.statements: list[tuple[str, object]] = []
        self._result: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "FROM nba_schedule" in sql:
            self._result = [
                (g["nba_game_id"], g["game_date"], g["home_team_abbr"], g["away_team_abbr"])
                for g in OFFICIAL_SCHEDULE
            ]
        elif "FROM players" in sql:
            self._result = list(OFFICIAL_PLAYERS)
        elif "report_url = %s" in sql:
            self._result = [(1,)] if self.already_ingested else []
        elif "report_as_of < %s" in sql:
            self._result = self.previous
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def close(self):
        pass

    def player_updates(self):
        return [p for sql, p in self.statements if sql.strip().startswith("UPDATE players")]


class OfficialConn:
    def __init__(self, previous: list[tuple] | None = None, already_ingested: bool = False):
        self.cursor_ = OfficialCursor(previous or [], already_ingested)

    def cursor(self):
        return self.cursor_


class TestScrapeOfficialInjuries:
    NOW = datetime(2026, 1, 20, 22, 7, tzinfo=timezone.utc)

    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        self.inserted: list[tuple] = []
        self.finished: list[dict] = []

        def record_insert(cur, sql, rows, template=None):
            self.inserted.extend(rows)
            return len(rows)

        def record_finish(conn, run_id, status, rows, notes=None, watermark_to=None):
            self.finished.append({"status": status, "rows": rows, "notes": notes})

        monkeypatch.setattr(
            injury_report, "fetch_latest_report", lambda now: (OFFICIAL_URL, b"%PDF")
        )
        monkeypatch.setattr(
            injury_report, "extract_pdf_pages", lambda data: OFFICIAL_REPORT_PAGES
        )
        monkeypatch.setattr(injury_report, "_start_ingestion_run", lambda *a, **k: 7)
        monkeypatch.setattr(injury_report, "_finish_ingestion_run", record_finish)
        monkeypatch.setattr(injury_report, "_batch_upsert", record_insert)

    def test_rows_are_game_scoped_and_timestamped_by_the_report(self):
        # arrange
        conn = OfficialConn()

        # act
        ok = injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        assert ok is True
        tatum = next(row for row in self.inserted if row[0] == "1628369")
        assert tatum == (
            "1628369", "0022500601", OFFICIAL_PUBLISHED_AT, "Out", "out",
            "Injury/Illness - Right Achilles; Repair Management", "nba_official",
            "1610612738", OFFICIAL_URL,
        )

    def test_an_unfiled_team_writes_nothing_and_is_noted(self):
        # arrange
        conn = OfficialConn()

        # act
        injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        assert len(self.inserted) == 6
        assert all(row[7] != "1610612752" for row in self.inserted)
        assert "not_yet_submitted_teams=1" in self.finished[-1]["notes"]

    def test_a_dropped_player_gets_a_game_scoped_clearance(self):
        # arrange
        previous = [
            ("1628369", "0022500601", "1610612738", "out", T1),
            ("1627759", "0022500601", "1610612738", "questionable", T1),
            ("201950", "0022500601", "1610612738", "out", T1),
        ]
        conn = OfficialConn(previous)

        # act
        injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        clearances = [row for row in self.inserted if row[3] == "cleared"]
        assert clearances == [(
            "201950", "0022500601", OFFICIAL_PUBLISHED_AT, "cleared", "cleared", None,
            "nba_official", "1610612738", OFFICIAL_URL,
        )]
        assert ("201950",) in conn.cursor_.player_updates()

    def test_players_get_the_official_designation(self):
        # arrange
        conn = OfficialConn()

        # act
        injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        assert ("Out", "Not With Team", "203078") in conn.cursor_.player_updates()

    def test_a_report_already_ingested_is_not_appended_again(self):
        # arrange
        conn = OfficialConn(already_ingested=True)

        # act
        injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        assert self.inserted == []
        assert conn.cursor_.player_updates()

    def test_no_report_found_writes_nothing_and_reports_failure(self, monkeypatch):
        # arrange
        monkeypatch.setattr(injury_report, "fetch_latest_report", lambda now: None)
        conn = OfficialConn()

        # act
        ok = injury_report.scrape_official_injuries(conn, now=self.NOW)

        # assert
        assert ok is False
        assert self.inserted == []
        assert conn.cursor_.player_updates() == []

    def test_no_games_scheduled_skips_the_fetch(self, monkeypatch):
        # arrange
        def must_not_fetch(now):
            raise AssertionError("fetched with no games scheduled")

        monkeypatch.setattr(injury_report, "fetch_latest_report", must_not_fetch)
        conn = OfficialConn()

        # act
        ok = injury_report.scrape_official_injuries(
            conn, now=datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
        )

        # assert
        assert ok is True
        assert self.inserted == []

    def test_dry_run_reads_but_writes_nothing(self, monkeypatch):
        # arrange
        monkeypatch.undo()
        monkeypatch.setattr(
            injury_report, "fetch_latest_report", lambda now: (OFFICIAL_URL, b"%PDF")
        )
        monkeypatch.setattr(
            injury_report, "extract_pdf_pages", lambda data: OFFICIAL_REPORT_PAGES
        )
        conn = OfficialConn([("201950", "0022500601", "1610612738", "out", T1)])

        # act
        injury_report.scrape_official_injuries(conn, dry_run=True, now=self.NOW)

        # assert
        executed = [sql for sql, _ in conn.cursor_.statements]
        assert executed and all(not is_write_statement(sql) for sql in executed)


class TestInjuryPhases:
    def test_an_official_failure_does_not_cost_the_cbs_pass(self, monkeypatch):
        # arrange
        calls: list[str] = []

        def official(conn, dry_run=False):
            raise RuntimeError("pdf down")

        monkeypatch.setattr(
            run_scraper, "scrape_injuries", lambda conn, dry_run=False: calls.append("cbs")
        )
        monkeypatch.setattr(run_scraper, "scrape_official_injuries", official)

        # act
        run_scraper._injury_phases(object(), False)

        # assert
        assert calls == ["cbs"]

    def test_a_cbs_failure_does_not_cost_the_official_pass_which_runs_second(
        self, monkeypatch
    ):
        # arrange
        calls: list[str] = []

        def cbs(conn, dry_run=False):
            calls.append("cbs")
            raise RuntimeError("cbs down")

        monkeypatch.setattr(run_scraper, "scrape_injuries", cbs)
        monkeypatch.setattr(
            run_scraper, "scrape_official_injuries",
            lambda conn, dry_run=False: calls.append("official"),
        )

        # act
        run_scraper._injury_phases(object(), False)

        # assert
        assert calls == ["cbs", "official"]

    def test_the_official_only_flag_is_parsed(self):
        # act + assert
        assert _parse_args(["--official-injuries-only"]).official_injuries_only is True


class TestClearancesForReport:
    def test_a_complete_report_clears_the_dropped_player(self):
        # arrange
        previous = [("1", "A One"), ("2", "B Two")]

        # act
        cleared = clearances_for_report(previous, ["1"])

        # assert
        assert cleared == ["2"]

    def test_an_incomplete_report_clears_nobody(self):
        # arrange
        previous = [("1", "A One"), ("2", "B Two")]

        # act
        cleared = clearances_for_report(previous, ["1"], complete=False)

        # assert
        assert cleared == []

    def test_a_listed_but_unmatched_name_is_not_cleared(self):
        # arrange: B Two is on the page but could not be matched to an id
        previous = [("1", "A One"), ("2", "B Two"), ("3", "C Three")]

        # act
        cleared = clearances_for_report(previous, ["1"], unmatched_names=["B. Two"])

        # assert
        assert cleared == ["3"]


class TestPartialPageWritesNoClearances:
    def test_a_skipped_table_blocks_clearances(self, monkeypatch):
        # arrange: one good five-column table and one table whose status header is gone
        row = _cbs_row("Zed Good", ["SF", "Thu, Oct 1", "Knee", "Out for the season"])
        good = _cbs_table("GS", CBS_FIVE_HEADERS, [row])
        bad = _cbs_table("LAC", ["Player", "Position", "Updated", "Injury", "Status Note"], [row])
        html = good + bad
        rows, skipped = scrapes._parse_cbs_injury_tables(html)

        # act + assert
        assert skipped == 1
        assert len(rows) == 1
        assert clearances_for_report([("9", "Old Listed")], ["1"], complete=skipped == 0) == []
