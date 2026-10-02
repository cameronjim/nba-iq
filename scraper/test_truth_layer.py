import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg2.errors
import pytest
import requests

import backfill
import database
import espn_injuries
import fetching
import injury_report
import roster_snapshot
import run_scraper
import odds
import props
import scrapes
import truth_layer
from backfill import NOT_POSTPONED_PREDICATE
from config import (
    current_season,
    GAME_LOG_CORRECTION_WINDOW_DAYS,
    PROPS_DEFAULT_MARKETS,
    PROPS_MARKET_MAP,
    PROPS_MONTHLY_BUDGET,
    PROPS_RESERVE_CREDITS,
    ROSTER_SNAPSHOT_SOURCE,
    SEASON,
    SEASON_TYPES_INGESTED,
    STATS_HEADERS,
    STATS_PROBE_TIMEOUT_SECONDS,
    TEAM_ID_TO_ABBR,
    V2_INACTIVE_UNRELIABLE_FROM,
)
from database import DryRunCursor, is_write_statement
from fetching import box_score_game_from_next_data
from odds import map_event_to_nba_game, parse_event_odds, plan_odds_snapshot
from props import map_prop_event, match_prop_player, parse_event_props
from parsing import (
    box_score_violations,
    canonical_player_name,
    cbs_team_abbr,
    cleared_player_ids,
    clearances_for_report,
    extract_next_data,
    format_processed_line,
    in_season,
    normalize_injury_status,
    parse_game_date,
    parse_matchup,
    parse_minutes,
    parse_processed_line,
    parse_season_types,
    season_end_date,
    season_start_date,
    season_type_from_game_id,
    v2_inactive_is_unreliable,
)
from rows import (
    BOX_DETAILS_SOURCE,
    Stint,
    derive_stints,
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
    absence_closure,
    active_dnp_status_rows,
    box_detail_rows_from_traditional,
    box_detail_rows_from_web,
    build_player_game_log_row,
    build_team_game_log_row,
    derive_game_status_rows,
    discovered_rows_for_season,
    discovery_dates,
    enumerate_game_id_groups,
    game_log_fetch_from,
    game_log_rows_from_web,
    ingested_schedule_rows,
    merge_dnp_reason,
    normalize_inactive_rows,
    plan_roster_snapshot,
    player_ids_absent_from_box,
    player_rows_from_nba_players_index,
    roster_rows_from_nba_players_index,
    roster_snapshot_is_complete,
    schedule_row_from_web_game,
    schedule_rows_from_league_schedule,
    schedule_rows_from_nba_web,
    schedule_rows_from_team_logs,
    season_types_to_fetch,
    split_rows_on_season_boundary,
    supplement_player_log_rows,
    web_game_is_final,
    web_inactive_rows,
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

    def test_a_player_missing_from_an_unconfirmed_snapshot_is_left_open(self):
        # complete defaults to False: absence may be one team's fetch failing
        changes = plan_roster_snapshot(
            {}, {"201939": (GSW, date(2025, 10, 21))}, SNAPSHOT_DAY
        )

        assert changes == []

    def test_a_complete_snapshot_closes_an_absent_player_the_day_before(self):
        changes = plan_roster_snapshot(
            {"1": LAL},
            {"1": (LAL, date(2025, 10, 21)), "202685": (GSW, date(2024, 10, 22))},
            SNAPSHOT_DAY,
            complete=True,
        )

        assert changes == [{
            "player_id": "202685",
            "open_team_id": None,
            "open_valid_from": None,
            "close_team_id": GSW,
            "close_valid_from": date(2024, 10, 22),
            "close_valid_to": SNAPSHOT_DAY - timedelta(days=1),
        }]

    def test_an_absence_closure_never_closes_before_the_stint_opened(self):
        changes = plan_roster_snapshot(
            {}, {"202685": (GSW, SNAPSHOT_DAY)}, SNAPSHOT_DAY, complete=True
        )

        assert changes[0]["close_valid_to"] == SNAPSHOT_DAY

    def test_a_complete_snapshot_still_plans_moves_alongside_closures(self):
        changes = plan_roster_snapshot(
            {"1": BOS},
            {"1": (LAL, date(2025, 10, 21)), "2": (GSW, date(2025, 10, 21))},
            SNAPSHOT_DAY,
            complete=True,
        )

        by_player = {c["player_id"]: c for c in changes}
        assert by_player["1"]["open_team_id"] == BOS
        assert by_player["1"]["close_team_id"] == LAL
        assert by_player["2"]["open_team_id"] is None
        assert by_player["2"]["close_team_id"] == GSW

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


def _full_rosters(team_ids, per_team=13):
    return {
        f"{team_id}-{n}": team_id for team_id in team_ids for n in range(per_team)
    }


ALL_TEAM_IDS = sorted(TEAM_ID_TO_ABBR)


class TestRosterSnapshotIsComplete:
    def test_every_team_with_a_full_roster_is_complete(self):
        assert roster_snapshot_is_complete(_full_rosters(ALL_TEAM_IDS), ALL_TEAM_IDS)

    def test_a_missing_team_is_incomplete(self):
        snapshot = _full_rosters(ALL_TEAM_IDS[1:])

        assert not roster_snapshot_is_complete(snapshot, ALL_TEAM_IDS)

    def test_a_team_under_the_minimum_is_incomplete(self):
        snapshot = _full_rosters(ALL_TEAM_IDS)
        del snapshot[f"{ALL_TEAM_IDS[0]}-0"]

        assert not roster_snapshot_is_complete(snapshot, ALL_TEAM_IDS)

    def test_the_minimum_can_be_lowered(self):
        snapshot = _full_rosters(ALL_TEAM_IDS, per_team=5)

        assert roster_snapshot_is_complete(snapshot, ALL_TEAM_IDS, min_players=5)

    def test_an_empty_snapshot_is_incomplete(self):
        assert not roster_snapshot_is_complete({}, ALL_TEAM_IDS)


class RosterCursor:
    def __init__(self, open_stints):
        self.open_stints = open_stints
        self.writes = []
        self.result = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.result = []
        if "WHERE valid_to IS NULL" in text and text.startswith("SELECT"):
            self.result = list(self.open_stints)
        elif text.startswith(("UPDATE", "INSERT")):
            self.writes.append((text.split()[0], params))

    def fetchall(self):
        return list(self.result)

    def close(self):
        pass


class RosterConn:
    def __init__(self, open_stints):
        self.cursor_ = RosterCursor(open_stints)

    def cursor(self):
        return self.cursor_


class TestScrapeRosterSnapshotAbsence:
    def _run(self, monkeypatch, web_snapshot, open_stints, dry_run=False):
        recorded = []
        monkeypatch.setattr(roster_snapshot, "_start_ingestion_run", lambda *a, **k: 1)
        monkeypatch.setattr(
            roster_snapshot, "_finish_ingestion_run",
            lambda conn, run_id, status, rows, notes=None: recorded.append(notes),
        )
        monkeypatch.setattr(roster_snapshot, "fetch_web_roster_snapshot", lambda: web_snapshot)
        conn = RosterConn(open_stints)
        roster_snapshot.scrape_roster_snapshot(
            conn, season=SEASON, dry_run=dry_run, snapshot_date=SNAPSHOT_DAY,
            stats_reachable=False,
        )
        return conn.cursor_.writes, recorded

    def test_a_complete_web_snapshot_closes_a_player_on_no_roster(self, monkeypatch):
        # arrange
        web = _full_rosters(ALL_TEAM_IDS)
        open_stints = [("202685", GSW, date(2024, 10, 22))]

        # act
        writes, notes = self._run(monkeypatch, web, open_stints)

        # assert
        assert ("UPDATE", (SNAPSHOT_DAY - timedelta(days=1), "202685", GSW,
                           date(2024, 10, 22))) in writes
        assert not any(kind == "INSERT" and params[0] == "202685" for kind, params in writes)
        assert "1 closed for absence" in notes[0]

    def test_an_incomplete_web_snapshot_closes_nobody(self, monkeypatch):
        # arrange
        web = _full_rosters(ALL_TEAM_IDS[1:])
        open_stints = [("202685", GSW, date(2024, 10, 22))]

        # act
        writes, notes = self._run(monkeypatch, web, open_stints)

        # assert
        assert not any(kind == "UPDATE" for kind, _ in writes)
        assert "0 closed for absence" in notes[0]

    def test_a_dry_run_writes_no_absence_closure(self, monkeypatch):
        # arrange
        web = _full_rosters(ALL_TEAM_IDS)
        open_stints = [("202685", GSW, date(2024, 10, 22))]

        # act
        writes, _ = self._run(monkeypatch, web, open_stints, dry_run=True)

        # assert
        assert writes == []

    def _run_per_team(self, monkeypatch, snapshot, failed, open_stints):
        recorded = []
        monkeypatch.setattr(roster_snapshot, "_start_ingestion_run", lambda *a, **k: 1)
        monkeypatch.setattr(
            roster_snapshot, "_finish_ingestion_run",
            lambda conn, run_id, status, rows, notes=None: recorded.append(notes),
        )
        monkeypatch.setattr(
            roster_snapshot, "fetch_roster_snapshot", lambda season, delay: (snapshot, failed)
        )
        monkeypatch.setattr(roster_snapshot, "fetch_web_roster_snapshot", lambda: {})
        conn = RosterConn(open_stints)
        roster_snapshot.scrape_roster_snapshot(
            conn, season=SEASON, snapshot_date=SNAPSHOT_DAY, stats_reachable=True,
        )
        return conn.cursor_.writes, recorded

    def test_thirty_successful_team_pages_also_close_an_absent_player(self, monkeypatch):
        # arrange
        snapshot = _full_rosters(ALL_TEAM_IDS)
        open_stints = [("202685", GSW, date(2024, 10, 22))]

        # act
        writes, notes = self._run_per_team(monkeypatch, snapshot, [], open_stints)

        # assert
        assert any(kind == "UPDATE" and params[1] == "202685" for kind, params in writes)
        assert "1 closed for absence" in notes[0]

    def test_a_failed_team_page_closes_nobody(self, monkeypatch):
        # arrange: every team parsed but one page failed, so absence is ambiguous
        snapshot = _full_rosters(ALL_TEAM_IDS)
        open_stints = [("202685", GSW, date(2024, 10, 22))]

        # act
        writes, notes = self._run_per_team(monkeypatch, snapshot, ["GSW"], open_stints)

        # assert
        assert not any(kind == "UPDATE" for kind, _ in writes)
        assert "0 closed for absence" in notes[0]


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

        match = scrapes.match_injury_rows(
            [self._row("Jimmy Butler"), self._row("Luka Doncic")], index
        )

        assert [nba_id for nba_id, _ in match.matched] == ["202710", "1629029"]

    def test_duplicate_name_is_disambiguated_by_team(self):
        index = scrapes.index_players_by_canonical_name(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )

        match = scrapes.match_injury_rows([self._row("Jalen Williams", "DEN")], index)

        assert [nba_id for nba_id, _ in match.matched] == ["2"]

    def test_duplicate_name_without_a_team_is_skipped_as_ambiguous(self):
        index = scrapes.index_players_by_canonical_name(
            [("1", "Jalen Williams", "OKC"), ("2", "Jalen Williams", "DEN")]
        )

        match = scrapes.match_injury_rows([self._row("Jalen Williams")], index)

        assert match.matched == []
        assert [r.player_name for r in match.ambiguous] == ["Jalen Williams"]

    def test_unknown_player_is_unmatched(self):
        match = scrapes.match_injury_rows([self._row("Nobody Known")], {})

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


# a trimmed capture of www.nba.com/game/0022500001/box-score's __NEXT_DATA__:
# three players per team (starter, bench, dnp) plus the real team totals.
WEB_BOX_FIXTURE = Path(__file__).parent / "fixtures" / "nba_web_box_score_0022500001.json"
WEB_GAME_ID = "0022500001"
OKC = "1610612760"
HOU = "1610612745"


def _web_next_data():
    return json.loads(WEB_BOX_FIXTURE.read_text(encoding="utf-8"))


def _web_game():
    return box_score_game_from_next_data(_web_next_data(), WEB_GAME_ID)


def _unplayed_web_game():
    # what the page serves before tip-off: no players, a placeholder for totals.
    game = _web_game()
    game["gameStatus"] = 1
    for side in ("homeTeam", "awayTeam"):
        game[side]["players"] = []
        game[side]["statistics"] = {"dummyKey": "dummyValue"}
    return game


class TestBoxScoreWebPage:
    def test_the_game_object_is_read_from_page_props(self):
        game = _web_game()

        assert game["homeTeam"]["teamTricode"] == "OKC"
        assert game["awayTeam"]["teamTricode"] == "HOU"

    @pytest.mark.parametrize(
        "next_data",
        [{}, {"props": {"pageProps": {}}}, {"props": {"pageProps": {"game": {"gameId": WEB_GAME_ID}}}}],
    )
    def test_a_missing_game_object_fails_loudly(self, next_data):
        with pytest.raises(ValueError, match="props.pageProps.game"):
            box_score_game_from_next_data(next_data, WEB_GAME_ID)

    def test_a_page_for_another_game_is_rejected(self):
        with pytest.raises(ValueError, match="describes game"):
            box_score_game_from_next_data(_web_next_data(), "0022500002")

    def test_the_fetch_requests_the_game_box_score_page(self, monkeypatch):
        seen = {}

        def fake_page(label, url, params=None):
            seen["url"] = url
            return _web_next_data()

        monkeypatch.setattr(fetching, "_fetch_nba_web_page", fake_page)

        game = fetching.fetch_box_score_web(WEB_GAME_ID)

        assert seen["url"] == "https://www.nba.com/game/0022500001/box-score"
        assert game["gameId"] == WEB_GAME_ID

    def test_only_status_3_is_final(self):
        assert web_game_is_final(_web_game()) is True
        assert web_game_is_final(_unplayed_web_game()) is False
        assert web_game_is_final({"gameStatus": "x"}) is False


class TestBoxDetailRowsFromWeb:
    def _players(self, game=None):
        players, _ = box_detail_rows_from_web(game or _web_game(), WEB_GAME_ID)
        return {row["nba_player_id"]: row for row in players}

    def test_every_listed_player_gets_a_row_with_his_team(self):
        players = self._players()

        assert len(players) == 6
        assert players["1628983"]["team_id"] == OKC
        assert players["1631095"]["team_id"] == HOU

    def test_a_starter_is_the_player_with_a_position(self):
        players = self._players()

        assert players["1628983"]["started"] is True
        assert players["1628983"]["position"] == "G"
        assert players["1631119"]["started"] is False
        assert players["1631119"]["position"] is None

    def test_an_appearance_carries_minutes_and_the_rebound_split(self):
        row = self._players()["1631095"]

        assert row["minutes"] == round(41 + 45 / 60, 2)
        assert (row["oreb"], row["dreb"], row["pf"]) == (3, 2, 4)
        assert row["dnp_reason"] is None

    @pytest.mark.parametrize(
        ("player_id", "reason"),
        [("1631172", "DNP - Coach's Decision"), ("1627827", "DND - Injury/Illness")],
    )
    def test_a_dnp_has_its_comment_and_no_minutes_or_stats(self, player_id, reason):
        row = self._players()[player_id]

        assert row["minutes"] is None
        assert (row["oreb"], row["dreb"], row["pf"]) == (None, None, None)
        assert row["started"] is False
        assert row["dnp_reason"] == reason

    def test_team_totals_carry_the_rebound_split_and_fouls(self):
        _, teams = box_detail_rows_from_web(_web_game(), WEB_GAME_ID)

        by_team = {row["team_id"]: row for row in teams}
        assert by_team[HOU] == {
            "team_id": HOU, "nba_game_id": WEB_GAME_ID, "oreb": 16, "dreb": 36, "pf": 26,
        }
        assert by_team[OKC]["oreb"] == 11

    def test_the_web_rows_match_the_v3_rows_for_the_same_players(self):
        web = self._players()
        v3, _ = box_detail_rows_from_traditional(BOX_SCORE_V3, WEB_GAME_ID)

        for row in v3:
            if row["nba_player_id"] in web:
                assert web[row["nba_player_id"]]["started"] == row["started"]
                assert web[row["nba_player_id"]]["oreb"] == row["oreb"]

    def test_an_unplayed_game_yields_nothing(self):
        players, teams = box_detail_rows_from_web(_unplayed_web_game(), WEB_GAME_ID)

        assert players == []
        assert teams == []

    @pytest.mark.parametrize(
        ("minutes", "comment", "expected"),
        [
            ("34:12", "", 34.2),
            ("PT34M12.00S", "", 34.2),
            ("0:06", "", 0.1),
            ("PT00M00.00S", "DNP - Coach's Decision", None),
            ("", "DNP - Coach's Decision", None),
            (None, "", None),
        ],
    )
    def test_minutes_arrive_as_clock_or_iso_duration(self, minutes, comment, expected):
        game = _web_game()
        player = game["homeTeam"]["players"][0]
        player["statistics"]["minutes"] = minutes
        player["comment"] = comment

        row = self._players(game)[str(player["personId"])]

        assert row["minutes"] == expected

    def test_the_active_dnp_rows_carry_the_web_source(self):
        players, _ = box_detail_rows_from_web(_web_game(), WEB_GAME_ID)

        rows = active_dnp_status_rows(players, set(), WEB_GAME_ID, source="nba_web_boxscore")

        assert {row["nba_player_id"] for row in rows} == {"1631172", "1627827"}
        assert {row["source"] for row in rows} == {"nba_web_boxscore"}
        by_id = {row["nba_player_id"]: row for row in rows}
        assert by_id["1627827"]["listed_inactive"] is None
        assert by_id["1631172"]["listed_inactive"] is False

    def test_the_inactive_list_is_read_per_team(self):
        rows = normalize_inactive_rows(web_inactive_rows(_web_game()))

        assert rows == [
            {"nba_player_id": "1642850", "team_id": OKC},
            {"nba_player_id": "1627832", "team_id": HOU},
        ]


class TestGameLogRowsFromWeb:
    def _rows(self, game=None, season_type="Regular Season"):
        return game_log_rows_from_web(
            game or _web_game(), WEB_GAME_ID, "2025-26", season_type,
            date(2025, 10, 21), run_id=7,
        )

    def test_only_appearances_get_a_player_row(self):
        players, _ = self._rows()

        assert sorted(row[0] for row in players) == ["1628983", "1631095", "1631106", "1631119"]

    def test_rows_have_the_shape_the_log_builders_produce(self):
        players, teams = self._rows()
        built_player = build_player_game_log_row(PLAYER_GAME_LOG_ROW, "2025-26", 7)
        built_team = build_team_game_log_row(TEAM_GAME_LOG_ROWS[0], "2025-26", 7)

        assert {len(row) for row in players} == {len(built_player)}
        assert {len(row) for row in teams} == {len(built_team)}
        assert players[0][PLAYER_LOG_DATE_INDEX] == date(2025, 10, 21)
        assert teams[0][TEAM_LOG_DATE_INDEX] == date(2025, 10, 21)

    def test_a_player_row_maps_every_web_field(self):
        players, _ = self._rows()

        sga = next(row for row in players if row[0] == "1628983")
        assert sga == (
            "1628983", WEB_GAME_ID, "2025-26", "Regular Season", date(2025, 10, 21),
            OKC, "OKC", HOU, True, True, round(47 + 13 / 60, 2),
            35, 5, 5, 2, 2, 3, 12, 26, 1, 9, 10, 14, 3,
            None, "nba_web_boxscore", 7,
        )

    def test_the_away_side_is_not_home_and_faces_the_home_team(self):
        players, _ = self._rows()

        bench = next(row for row in players if row[0] == "1631106")
        assert (bench[5], bench[6], bench[7], bench[8], bench[9]) == (HOU, "HOU", OKC, False, False)

    def test_team_rows_carry_the_totals(self):
        _, teams = self._rows()

        by_team = {row[0]: row for row in teams}
        assert by_team[OKC] == (
            OKC, WEB_GAME_ID, "2025-26", "Regular Season", date(2025, 10, 21),
            "OKC", HOU, True, 290.0,
            125, 38, 29, 12, 4, 11, 46, 104, 13, 52, 20, 25, 1,
            "nba_web_boxscore", 7,
        )
        assert by_team[HOU][9] == 124
        assert by_team[HOU][21] == -1

    def test_the_season_type_is_the_callers(self):
        players, teams = self._rows(season_type="Pre Season")

        assert {row[3] for row in players + teams} == {"Pre Season"}

    def test_an_unplayed_game_yields_nothing(self):
        assert self._rows(_unplayed_web_game()) == ([], [])


class TestBoxSourceResolution:
    def test_auto_picks_web_when_the_probe_fails(self):
        assert truth_layer.resolve_box_source("auto", probe=lambda: False) == "web"

    def test_auto_picks_stats_when_the_probe_answers(self):
        assert truth_layer.resolve_box_source("auto", probe=lambda: True) == "stats"

    @pytest.mark.parametrize("source", ["web", "stats"])
    def test_an_explicit_source_never_probes(self, source):
        def probe():
            raise AssertionError("probed")

        assert truth_layer.resolve_box_source(source, probe=probe) == source

    def test_auto_defaults_to_the_stats_probe(self, monkeypatch):
        monkeypatch.setattr(truth_layer, "stats_nba_reachable", lambda: False)

        assert truth_layer.resolve_box_source("auto") == "web"

    def test_an_unknown_source_is_rejected(self):
        with pytest.raises(ValueError):
            truth_layer.resolve_box_source("espn")

    def test_the_web_pages_pace_faster_than_stats(self):
        assert truth_layer.box_delay_seconds("web") == 2.0
        assert truth_layer.box_delay_seconds("stats") == 5.0

    def test_the_web_source_feeds_the_box_detail_writes(self, monkeypatch):
        monkeypatch.setattr(truth_layer, "fetch_box_score_web", lambda game_id: _web_game())
        monkeypatch.setattr(
            truth_layer, "fetch_box_score_traditional",
            lambda game_id: pytest.fail("stats.nba.com was called"),
        )

        counts = truth_layer._apply_box_details(
            DryRunCursor(FakeCursor()), WEB_GAME_ID, ["1628983"], source="web"
        )

        assert counts == {"players": 6, "teams": 2, "status": 6, "absent": 0, "active_dnp": 2}

    def test_an_unfinished_web_game_is_not_applied(self, monkeypatch):
        monkeypatch.setattr(truth_layer, "fetch_box_score_web", lambda game_id: _unplayed_web_game())

        with pytest.raises(ValueError, match="final"):
            truth_layer._apply_box_details(
                DryRunCursor(FakeCursor()), WEB_GAME_ID, [], source="web"
            )


class TestApplyWebGame:
    def test_one_page_fills_logs_status_and_details_in_a_dry_run(self, monkeypatch):
        monkeypatch.setattr(truth_layer, "fetch_box_score_web", lambda game_id: _web_game())
        inner = FakeCursor()
        cur = DryRunCursor(inner)

        counts = truth_layer._apply_web_game(
            cur, WEB_GAME_ID, "2025-26", "Regular Season", date(2025, 10, 21), None
        )

        # 4 appearances plus 2 inactive-list entries are derived status rows;
        # the 2 dressed dnps are on neither list, so they are active-dnp inserts.
        assert counts == {
            "player_logs": 4, "team_logs": 2, "status": 6, "details": 6, "active_dnp": 2,
        }
        assert all(not is_write_statement(sql) for sql in inner.statements)

    def test_an_unfinished_game_is_left_for_the_next_run(self, monkeypatch):
        monkeypatch.setattr(truth_layer, "fetch_box_score_web", lambda game_id: _unplayed_web_game())

        with pytest.raises(ValueError, match="final"):
            truth_layer._apply_web_game(
                DryRunCursor(FakeCursor()), WEB_GAME_ID, "2025-26",
                "Regular Season", date(2025, 10, 21), None,
            )

    def test_the_selection_skips_logged_and_postponed_games(self):
        sql = " ".join(truth_layer.WEB_GAME_LOGS_NEEDED_SQL.split())

        assert "NOT EXISTS (SELECT 1 FROM team_game_logs t" in sql
        assert "s.postponed_status IS NULL OR s.postponed_status = 'N'" in sql
        assert "ORDER BY s.game_date, s.nba_game_id" in sql


class TestWebBackfillCli:
    def test_source_defaults_to_auto(self):
        assert _parse_args(["--backfill-game-logs"]).source == "auto"

    def test_web_game_logs_parse_with_season_and_limit(self):
        args = _parse_args(
            ["--backfill-game-logs", "--source", "web", "--season", "2026-27", "--limit", "50"]
        )

        assert (args.backfill_game_logs, args.source, args.season, args.limit) == (
            True, "web", "2026-27", 50,
        )

    def test_an_unknown_source_is_a_usage_error(self):
        with pytest.raises(SystemExit):
            _parse_args(["--backfill-box-details", "--source", "espn"])


class TestBoxDetailsWorkflow:
    WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "box_details_backfill.yml"

    def test_the_workflow_reads_the_web_pages(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")

        assert "--source web" in text
        assert "stats_nba_reachable" not in text and "unreachable" not in text

    def test_the_workflow_can_run_either_mode_with_a_600_game_default(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")

        assert "--backfill-game-logs" in text and "--backfill-box-details" in text
        assert 'default: "600"' in text


class TestDiscoveryDates:
    def test_the_preseason_window_runs_september_15_to_october_31(self):
        dates = discovery_dates("2025-26", ["Pre Season"], date(2026, 9, 1))

        assert (dates[0], dates[-1], len(dates)) == (date(2025, 9, 15), date(2025, 10, 31), 47)

    def test_the_postseason_window_runs_april_10_to_june_30_of_the_second_year(self):
        playin = discovery_dates("2025-26", ["PlayIn"], date(2026, 9, 1))
        playoffs = discovery_dates("2025-26", ["Playoffs"], date(2026, 9, 1))
        both = discovery_dates("2025-26", ["PlayIn", "Playoffs"], date(2026, 9, 1))

        assert playin == playoffs == both
        assert (both[0], both[-1], len(both)) == (date(2026, 4, 10), date(2026, 6, 30), 82)

    def test_all_three_types_are_both_windows_oldest_first(self):
        dates = discovery_dates("2025-26", ["Pre Season", "PlayIn", "Playoffs"], date(2026, 9, 1))

        assert len(dates) == 47 + 82
        assert dates == sorted(dates)

    def test_dates_after_today_are_skipped(self):
        preseason = discovery_dates("2026-27", ["Pre Season"], date(2026, 10, 1))
        playoffs = discovery_dates("2026-27", ["Playoffs"], date(2026, 10, 1))

        assert (preseason[0], preseason[-1], len(preseason)) == (
            date(2026, 9, 15), date(2026, 10, 1), 17,
        )
        assert playoffs == []

    def test_the_regular_season_has_no_discovery_window(self):
        assert discovery_dates("2025-26", ["Regular Season"], date(2026, 9, 1)) == []


class TestEnumerateGameIds:
    def test_preseason_ids_are_one_group_numbered_1_to_120(self):
        groups = enumerate_game_id_groups("2025-26", "Pre Season")

        assert len(groups) == 1 and len(groups[0]) == 120
        assert (groups[0][0], groups[0][-1]) == ("0012500001", "0012500120")

    def test_play_in_ids_match_the_league_numbering(self):
        groups = enumerate_game_id_groups("2025-26", "PlayIn")

        assert [g[0] for g in groups] == [
            "0052500101", "0052500111", "0052500121", "0052500131",
            "0052500201", "0052500211",
        ]
        assert all(len(g) == 1 for g in groups)

    def test_playoff_ids_are_one_group_of_seven_per_series(self):
        groups = enumerate_game_id_groups("2025-26", "Playoffs")
        ids = [game_id for group in groups for game_id in group]

        assert len(groups) == 8 + 4 + 2 + 1
        assert all(len(g) == 7 for g in groups)
        assert len(ids) == len(set(ids)) == 105
        assert groups[0] == [f"00425001{0}{n}" for n in range(1, 8)]
        assert groups[7][0] == "0042500171"
        assert groups[-1][0] == "0042500401"

    def test_ids_carry_the_season_start_year_and_its_type(self):
        for season_type in ("Pre Season", "PlayIn", "Playoffs"):
            for group in enumerate_game_id_groups("2099-00", season_type):
                for game_id in group:
                    assert len(game_id) == 10 and game_id[3:5] == "99"
                    assert season_type_from_game_id(game_id) == season_type

    def test_the_regular_season_is_not_enumerated(self):
        assert enumerate_game_id_groups("2025-26", "Regular Season") == []


POSTSEASON_PAGE = _games_page(
    _card("0022501230", seasonYear="2025-26", gameTimeUtc="2026-04-12T23:30:00Z"),
    _card("0052500101", seasonYear="2025-26", gameTimeUtc="2026-04-15T23:30:00Z"),
    _card(
        "0042500404", home=("1610612752", "NYK"), away=("1610612760", "OKC"),
        seasonYear="2026", gameTimeUtc="2026-06-11T00:30:00Z",
        gameStatusText="Final",
    ),
    _card("0042400101", seasonYear="2024-25"),
)


class TestDiscoveredRowsForSeason:
    def _rows(self):
        raw = schedule_rows_from_nba_web(POSTSEASON_PAGE, date(2026, 6, 10), "2025-26")
        return discovered_rows_for_season(raw, "2025-26", ["PlayIn", "Playoffs"])

    def test_play_in_and_playoff_rows_carry_their_types(self):
        rows = self._rows()

        assert [(r["nba_game_id"], r["season_type"]) for r in rows] == [
            ("0042500404", "Playoffs"), ("0052500101", "PlayIn"),
        ]

    def test_a_june_game_is_labelled_with_the_season_it_belongs_to(self):
        finals = next(r for r in self._rows() if r["nba_game_id"] == "0042500404")

        assert finals["season"] == "2025-26"
        assert finals["game_date"] == date(2026, 6, 10)
        assert finals["scheduled_at"].isoformat() == "2026-06-11T00:30:00+00:00"
        assert (finals["home_team_abbr"], finals["away_team_abbr"]) == ("NYK", "OKC")

    def test_other_seasons_types_and_duplicates_are_dropped(self):
        raw = schedule_rows_from_nba_web(POSTSEASON_PAGE, date(2026, 6, 10), "2025-26")

        rows = discovered_rows_for_season(raw + raw, "2025-26", ["PlayIn", "Playoffs"])

        assert len(rows) == 2
        assert "0042400101" not in {r["nba_game_id"] for r in rows}


class TestScheduleRowFromWebGame:
    def test_the_box_score_page_gives_the_eastern_date_and_tipoff(self):
        row = schedule_row_from_web_game(_web_game(), "2025-26")

        assert row["nba_game_id"] == WEB_GAME_ID
        assert row["season"] == "2025-26"
        assert row["season_type"] == "Regular Season"
        assert row["game_date"] == date(2025, 10, 21)
        assert row["scheduled_at"].isoformat() == "2025-10-21T23:30:00+00:00"
        assert (row["home_team_id"], row["away_team_id"]) == (OKC, HOU)

    def test_a_game_outside_the_season_is_rejected(self):
        assert schedule_row_from_web_game(_web_game(), "2024-25") is None


class TestParseSeasonTypes:
    def test_defaults_to_every_discoverable_type(self):
        assert parse_season_types(None) == ("Pre Season", "PlayIn", "Playoffs")

    def test_matches_case_insensitively_in_canonical_order(self):
        assert parse_season_types("playoffs, PLAYIN") == ("PlayIn", "Playoffs")

    @pytest.mark.parametrize("raw", ["", "Regular Season", "Playoffs,Finals"])
    def test_anything_else_is_a_usage_error(self, raw):
        with pytest.raises(ValueError):
            parse_season_types(raw)

    def test_the_discover_command_parses(self):
        args = _parse_args(
            ["--discover-schedule", "--season", "2025-26", "--season-types", "PlayIn,Playoffs"]
        )

        assert (args.discover_schedule, args.season, args.season_types) == (
            True, "2025-26", "PlayIn,Playoffs",
        )


def _final_web_game(game_id, game_et):
    game = _web_game()
    game["gameId"] = game_id
    game["gameEt"] = game_et
    return game


class TestDiscoverScheduleRows:
    def test_probes_only_ids_no_date_page_listed_and_stops_each_series_at_a_miss(
        self, monkeypatch
    ):
        def games_page(game_date):
            if game_date == date(2026, 4, 19):
                return _games_page(_card("0042500101", seasonYear="2025-26"))
            return _games_page()

        probed = []

        def box_score(game_id):
            probed.append(game_id)
            if game_id == "0042500102":
                return _final_web_game(game_id, "2026-04-21T19:30:00Z")
            raise requests.HTTPError("503 Server Error")

        monkeypatch.setattr(truth_layer, "_fetch_nba_web_games", games_page)
        monkeypatch.setattr(truth_layer, "fetch_box_score_web", box_score)

        rows = truth_layer.discover_schedule_rows(
            "2025-26", ["Playoffs"], today=date(2026, 9, 1), delay_seconds=0
        )

        assert [(r["nba_game_id"], r["game_date"]) for r in rows] == [
            ("0042500101", date(2026, 4, 19)), ("0042500102", date(2026, 4, 21)),
        ]
        assert "0042500101" not in probed
        assert probed[:2] == ["0042500102", "0042500103"]
        assert len(probed) == 2 + 14

    def test_an_unreachable_site_ends_the_crawl_without_probing(self, monkeypatch):
        calls = []

        def boom(game_date):
            calls.append(game_date)
            raise OSError("blocked")

        monkeypatch.setattr(truth_layer, "_fetch_nba_web_games", boom)
        monkeypatch.setattr(
            truth_layer, "fetch_box_score_web",
            lambda game_id: pytest.fail("probed while nba.com was down"),
        )

        rows = truth_layer.discover_schedule_rows(
            "2025-26", ["PlayIn"], today=date(2026, 9, 1), delay_seconds=0
        )

        assert rows == [] and len(calls) == 3

    def test_a_type_with_no_past_dates_fetches_nothing(self, monkeypatch):
        monkeypatch.setattr(
            truth_layer, "_fetch_nba_web_games",
            lambda game_date: pytest.fail("fetched a future date"),
        )

        rows = truth_layer.discover_schedule_rows(
            "2026-27", ["PlayIn", "Playoffs"], today=date(2026, 10, 1), delay_seconds=0
        )

        assert rows == []


class ScheduleTypesCursor(FakeCursor):
    def __init__(self, present):
        super().__init__()
        self.present = present
        self.last = ""

    def execute(self, sql, params=None):
        super().execute(sql, params)
        self.last = sql

    def fetchall(self):
        if "DISTINCT season_type" in self.last:
            return [(season_type,) for season_type in self.present]
        return []


class TestWebBackfillDiscovery:
    def _fake_fetchers(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            truth_layer, "_fetch_nba_web_games",
            lambda game_date: seen.append(game_date) or _games_page(),
        )
        monkeypatch.setattr(
            truth_layer, "fetch_box_score_web",
            lambda game_id: (_ for _ in ()).throw(requests.HTTPError("503")),
        )
        return seen

    def test_discovers_first_when_the_schedule_lacks_those_types(self, monkeypatch):
        seen = self._fake_fetchers(monkeypatch)
        conn = FakeConn()
        conn.cursor_ = ScheduleTypesCursor(["Regular Season"])

        processed = truth_layer.backfill_game_logs_from_web(
            conn, "2025-26", dry_run=True, delay_seconds=0, today=date(2026, 9, 1)
        )

        assert processed == 0
        assert (seen[0], seen[-1], len(seen)) == (date(2025, 9, 15), date(2026, 6, 30), 129)

    def test_only_the_missing_types_are_discovered(self, monkeypatch):
        seen = self._fake_fetchers(monkeypatch)
        conn = FakeConn()
        conn.cursor_ = ScheduleTypesCursor(["Regular Season", "PlayIn", "Playoffs"])

        truth_layer.backfill_game_logs_from_web(
            conn, "2025-26", dry_run=True, delay_seconds=0, today=date(2026, 9, 1)
        )

        assert (seen[0], seen[-1], len(seen)) == (date(2025, 9, 15), date(2025, 10, 31), 47)

    def test_a_schedule_with_every_type_skips_discovery(self, monkeypatch):
        seen = self._fake_fetchers(monkeypatch)
        conn = FakeConn()
        conn.cursor_ = ScheduleTypesCursor(
            ["Pre Season", "Regular Season", "PlayIn", "Playoffs"]
        )

        truth_layer.backfill_game_logs_from_web(
            conn, "2025-26", dry_run=True, delay_seconds=0, today=date(2026, 9, 1)
        )

        assert seen == []


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
        monkeypatch.setattr(
            run_scraper, "scrape_espn_injuries",
            lambda conn, dry_run=False: calls.append("espn"),
        )
        monkeypatch.setattr(run_scraper, "scrape_official_injuries", official)

        # act
        run_scraper._injury_phases(object(), False)

        # assert
        assert calls == ["cbs", "espn"]

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
            run_scraper, "scrape_espn_injuries",
            lambda conn, dry_run=False: calls.append("espn"),
        )
        monkeypatch.setattr(
            run_scraper, "scrape_official_injuries",
            lambda conn, dry_run=False: calls.append("official"),
        )

        # act
        run_scraper._injury_phases(object(), False)

        # assert
        assert calls == ["cbs", "espn", "official"]

    def test_an_espn_failure_does_not_cost_the_official_pass(self, monkeypatch):
        # arrange
        calls: list[str] = []

        def espn(conn, dry_run=False):
            raise RuntimeError("espn down")

        monkeypatch.setattr(
            run_scraper, "scrape_injuries", lambda conn, dry_run=False: calls.append("cbs")
        )
        monkeypatch.setattr(run_scraper, "scrape_espn_injuries", espn)
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


ESPN_INJURIES_FIXTURE = Path(__file__).parent / "fixtures" / "espn_injuries_2026-10-02.json"
ESPN_NOW = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
ESPN_PLAYERS = [
    ("204001", "Kristaps Porzingis", "GSW"),
    ("202710", "Jimmy Butler III", "GSW"),
    ("1626204", "Larry Nance Jr.", "CLE"),
]
# (nba_game_id, game_date, home, away, home_team_id, away_team_id)
ESPN_SCHEDULE = [
    ("0012600010", date(2026, 10, 5), "GSW", "LAL", "1610612744", "1610612747"),
    ("0012600020", date(2026, 10, 14), "POR", "GSW", "1610612757", "1610612744"),
    ("0022600030", date(2026, 10, 30), "GSW", "PHX", "1610612744", "1610612756"),
    ("0012600040", date(2026, 10, 6), "IND", "CLE", "1610612754", "1610612739"),
]


def _espn_payload() -> dict:
    return json.loads(ESPN_INJURIES_FIXTURE.read_text(encoding="utf-8"))


def _espn_rows_by_name() -> dict[str, espn_injuries.EspnInjuryRow]:
    return {r.player_name: r for r in espn_injuries.parse_espn_injuries(_espn_payload())}


def _espn_schedule_dicts() -> list[dict]:
    return [
        {
            "nba_game_id": game_id, "game_date": game_date,
            "home_team_abbr": home, "away_team_abbr": away,
            "home_team_id": home_id, "away_team_id": away_id,
        }
        for game_id, game_date, home, away, home_id, away_id in ESPN_SCHEDULE
    ]


def _espn_row(status="out", return_date=None, team="GSW"):
    return espn_injuries.EspnInjuryRow(
        player_name="Kristaps Porzingis", team_abbr=team, status_raw="Day-To-Day",
        status_normalized=status, reason="Undisclosed", comment="",
        return_date=return_date,
        reported_at=datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc),
    )


class TestParseEspnInjuries:
    def test_an_indefinite_absence_filed_as_day_to_day_reads_as_out(self):
        # act
        porzingis = _espn_rows_by_name()["Kristaps Porzingis"]

        # assert
        assert porzingis.status_raw == "Day-To-Day"
        assert porzingis.status_normalized == "out"
        assert porzingis.return_date == date(2026, 10, 13)
        assert porzingis.team_abbr == "GSW"
        assert porzingis.reason == "Undisclosed"
        assert porzingis.reported_at == datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc)

    def test_a_plain_day_to_day_row_is_questionable(self):
        # act
        toohey = _espn_rows_by_name()["Alex Toohey"]

        # assert
        assert toohey.status_normalized == "questionable"
        assert toohey.return_date == date(2026, 10, 4)

    def test_an_out_row_is_out(self):
        # act
        butler = _espn_rows_by_name()["Jimmy Butler III"]

        # assert
        assert butler.status_raw == "Out"
        assert butler.status_normalized == "out"

    def test_the_listing_team_wins_over_the_athletes_stale_team(self):
        # act
        nance = _espn_rows_by_name()["Larry Nance Jr."]

        # assert
        assert nance.team_abbr == "IND"

    def test_an_empty_payload_parses_to_nothing(self):
        # act + assert
        assert espn_injuries.parse_espn_injuries({}) == []


class TestNormalizeEspnStatus:
    @pytest.mark.parametrize(
        ("status", "fantasy_abbr", "comment", "expected"),
        [
            ("Out", None, "", "out"),
            ("Out", "GTD", "", "out"),
            ("Day-To-Day", "O", "", "out"),
            ("Day-To-Day", "OUT", "", "out"),
            ("Out", "OFS", "", "out"),
            ("Day-To-Day", "D", "", "doubtful"),
            ("Day-To-Day", "Q", "", "questionable"),
            ("Day-To-Day", "GTD", "", "questionable"),
            ("Day-To-Day", "P", "", "probable"),
            ("Day-To-Day", None, "", "questionable"),
            ("Day-To-Day", "GTD", "He is OUT INDEFINITELY with a knee issue.", "out"),
            ("Day-To-Day", "P", "He was ruled out for Friday.", "probable"),
            ("Day-To-Day", "GTD", "There is no timetable for his return.", "out"),
            ("Day-To-Day", "GTD", "He will miss the start of training camp.", "questionable"),
            ("Day-To-Day", "GTD", "He suffered a season-ending injury.", "out"),
            ("Day-To-Day", "GTD", "He remains sidelined indefinitely.", "out"),
            ("Day-To-Day", "GTD", "He is limited in practice.", "questionable"),
        ],
    )
    def test_status_table(self, status, fantasy_abbr, comment, expected):
        # act + assert
        assert espn_injuries.normalize_espn_status(status, fantasy_abbr, comment) == expected

    def test_a_stale_long_term_note_does_not_force_out(self):
        # arrange: a summer note still on the feed in october
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        july = datetime(2026, 7, 10, tzinfo=timezone.utc)

        # act
        stale = espn_injuries.normalize_espn_status(
            "Day-To-Day", "GTD", "He is out indefinitely.", july, now
        )
        fresh = espn_injuries.normalize_espn_status(
            "Day-To-Day", "GTD", "He is out indefinitely.", datetime(2026, 9, 28, tzinfo=timezone.utc), now
        )

        # assert
        assert stale == "questionable"
        assert fresh == "out"


class TestEspnGameScopedStatuses:
    AS_OF = date(2026, 10, 2)

    def test_games_before_the_return_date_are_out_and_the_rest_are_not(self):
        # arrange
        row = _espn_row(status="questionable", return_date=date(2026, 10, 13))

        # act
        statuses = espn_injuries.game_scoped_statuses(
            [("204001", row)], _espn_schedule_dicts(), self.AS_OF
        )

        # assert
        assert [(s["nba_game_id"], s["status_normalized"]) for s in statuses] == [
            ("0012600010", "out"),
        ]
        assert statuses[0]["team_id"] == "1610612744"
        assert statuses[0]["reason"] == "Undisclosed; expected return 2026-10-13"

    def test_nothing_is_scoped_beyond_the_window(self):
        # arrange
        row = _espn_row(status="questionable", return_date=date(2027, 1, 1))

        # act
        statuses = espn_injuries.game_scoped_statuses(
            [("204001", row)], _espn_schedule_dicts(), self.AS_OF
        )

        # assert
        assert [s["nba_game_id"] for s in statuses] == ["0012600010", "0012600020"]
        assert statuses[1]["team_id"] == "1610612744"

    def test_games_already_past_are_skipped(self):
        # arrange
        row = _espn_row(status="questionable", return_date=date(2026, 10, 13))

        # act
        statuses = espn_injuries.game_scoped_statuses(
            [("204001", row)], _espn_schedule_dicts(), date(2026, 10, 6)
        )

        # assert
        assert statuses == []

    def test_no_return_date_writes_only_the_general_row(self):
        # arrange
        row = _espn_row(status="questionable", return_date=None)

        # act
        statuses = espn_injuries.game_scoped_statuses(
            [("204001", row)], _espn_schedule_dicts(), self.AS_OF
        )

        # assert
        assert statuses == [{
            "nba_player_id": "204001", "nba_game_id": None, "team_id": "1610612744",
            "report_as_of": row.reported_at, "status_raw": "Day-To-Day",
            "status_normalized": "questionable", "reason": "Undisclosed",
        }]

    def test_an_out_row_with_a_return_date_also_writes_the_general_row(self):
        # arrange
        row = _espn_row(status="out", return_date=date(2026, 10, 13))

        # act
        statuses = espn_injuries.game_scoped_statuses(
            [("204001", row)], _espn_schedule_dicts(), self.AS_OF
        )

        # assert
        assert [(s["nba_game_id"], s["status_normalized"]) for s in statuses] == [
            (None, "out"), ("0012600010", "out"),
        ]


class EspnCursor:
    def __init__(self):
        self.statements: list[tuple[str, object]] = []
        self._result: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "FROM nba_schedule" in sql:
            self._result = list(ESPN_SCHEDULE)
        elif "FROM players" in sql:
            self._result = list(ESPN_PLAYERS)
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def close(self):
        pass


class EspnConn:
    def __init__(self):
        self.cursor_ = EspnCursor()

    def cursor(self):
        return self.cursor_


class TestScrapeEspnInjuries:
    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        self.inserted: list[tuple] = []
        self.finished: list[dict] = []

        def record_insert(cur, sql, rows, template=None):
            self.inserted.extend(rows)
            return len(rows)

        def record_finish(conn, run_id, status, rows, notes=None, watermark_to=None):
            self.finished.append({"status": status, "rows": rows, "notes": notes})

        monkeypatch.setattr(espn_injuries, "fetch_espn_injuries", _espn_payload)
        monkeypatch.setattr(espn_injuries, "_start_ingestion_run", lambda *a, **k: 9)
        monkeypatch.setattr(espn_injuries, "_finish_ingestion_run", record_finish)
        monkeypatch.setattr(espn_injuries, "_batch_upsert", record_insert)

    def test_porzingis_is_out_for_the_games_before_his_return(self):
        # arrange
        reported = datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc)

        # act
        written = espn_injuries.scrape_espn_injuries(EspnConn(), now=ESPN_NOW)

        # assert
        porzingis = [row for row in self.inserted if row[0] == "204001"]
        assert porzingis == [
            (
                "204001", None, ESPN_NOW, reported, "Day-To-Day", "out",
                "Undisclosed", "espn_injuries", "1610612744", ESPN_URL,
            ),
            (
                "204001", "0012600010", ESPN_NOW, reported, "Day-To-Day", "out",
                "Undisclosed; expected return 2026-10-13", "espn_injuries",
                "1610612744", ESPN_URL,
            ),
        ]
        assert written == len(self.inserted)
        assert self.finished[-1]["status"] == "succeeded"

    def test_a_questionable_player_gets_only_game_rows_for_his_listing_team(self):
        # act
        espn_injuries.scrape_espn_injuries(EspnConn(), now=ESPN_NOW)

        # assert
        nance = [(row[1], row[5], row[8]) for row in self.inserted if row[0] == "1626204"]
        assert nance == [("0012600040", "out", "1610612754")]

    def test_unmatched_names_are_skipped_and_logged(self, caplog):
        # act
        with caplog.at_level("INFO", logger="espn_injuries"):
            espn_injuries.scrape_espn_injuries(EspnConn(), now=ESPN_NOW)

        # assert
        assert {row[0] for row in self.inserted} == {"204001", "202710", "1626204"}
        assert "matched no player" in caplog.text
        assert "Alex Toohey" in caplog.text

    def test_players_injury_status_is_never_touched(self):
        # arrange
        conn = EspnConn()

        # act
        espn_injuries.scrape_espn_injuries(conn, now=ESPN_NOW)

        # assert
        assert not any("UPDATE players" in sql for sql, _ in conn.cursor_.statements)

    def test_an_empty_feed_writes_nothing_and_fails_the_run(self, monkeypatch):
        # arrange
        monkeypatch.setattr(espn_injuries, "fetch_espn_injuries", lambda: {"injuries": []})

        # act
        written = espn_injuries.scrape_espn_injuries(EspnConn(), now=ESPN_NOW)

        # assert
        assert written == 0
        assert self.inserted == []
        assert self.finished[-1]["status"] == "failed"

    def test_dry_run_reads_but_writes_nothing(self, monkeypatch):
        # arrange
        monkeypatch.undo()
        monkeypatch.setattr(espn_injuries, "fetch_espn_injuries", _espn_payload)
        conn = EspnConn()

        # act
        written = espn_injuries.scrape_espn_injuries(conn, dry_run=True, now=ESPN_NOW)

        # assert
        executed = [sql for sql, _ in conn.cursor_.statements]
        assert written > 0
        assert executed and all(not is_write_statement(sql) for sql in executed)

    def test_the_espn_only_flag_is_parsed(self):
        # act + assert
        assert _parse_args(["--espn-injuries-only"]).espn_injuries_only is True
        assert _parse_args([]).espn_injuries_only is False


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


def test_processed_line_round_trips():
    # act + assert
    assert format_processed_line(0) == "box_details_processed=0"
    assert parse_processed_line(format_processed_line(287)) == 287


def test_processed_line_found_among_log_output():
    # arrange
    text = "INFO box details: 2 game(s)\nbox_details_processed=2\n"

    # act + assert
    assert parse_processed_line(text) == 2


def test_processed_line_missing_or_malformed_is_none():
    # act + assert
    assert parse_processed_line("INFO nothing\n") is None
    assert parse_processed_line("box_details_processed=abc") is None
    assert parse_processed_line("see box_details_processed=5 here") is None


# the odds api's documented event-odds example, moved to basketball markets.
PROP_EVENT = {
    "id": "a512a48a58c4329048174217b2cc7ce0",
    "sport_key": "basketball_nba",
    "commence_time": "2026-10-21T23:30:00Z",
    "home_team": "New York Knicks",
    "away_team": "Golden State Warriors",
}

PROP_PAYLOAD = {
    **PROP_EVENT,
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "last_update": "2026-10-21T15:00:00Z",
            "markets": [
                {
                    "key": "player_points",
                    "last_update": "2026-10-21T15:31:29Z",
                    "outcomes": [
                        {"name": "Over", "description": "Jalen Brunson", "price": -115, "point": 26.5},
                        {"name": "Under", "description": "Jalen Brunson", "price": -105, "point": 26.5},
                        {"name": "Over", "description": "Stephen Curry", "price": 110, "point": 27.5},
                    ],
                },
                {
                    "key": "player_double_double",
                    "outcomes": [{"name": "Yes", "description": "Jalen Brunson", "price": 400}],
                },
            ],
        },
        {
            "key": "fanduel",
            "title": "FanDuel",
            "last_update": "2026-10-21T15:10:00Z",
            "markets": [
                {
                    "key": "player_threes",
                    "outcomes": [
                        {"name": "Over", "description": "Stephen Curry", "price": -130, "point": 4.5},
                        {"name": "Under", "description": "Stephen Curry", "price": 100, "point": 4.5},
                    ],
                },
                {
                    "key": "player_points",
                    "last_update": "2026-10-21T15:20:00Z",
                    "outcomes": [
                        {"name": "Over", "description": "Jalen Brunson", "price": -110, "point": 25.5},
                        {"name": "Under", "description": "Jalen Brunson", "price": -110, "point": 25.5},
                    ],
                },
            ],
        },
    ],
}

PROP_PLAYERS = props.index_players_by_canonical_name([
    ("1628973", "Jalen Brunson", "NYK"),
    ("201939", "Stephen Curry", "GSW"),
    ("1630228", "Jaren Jackson Jr.", "MEM"),
    ("900001", "Jalen Williams", "OKC"),
    ("900002", "Jalen Williams", "GSW"),
])


def _props_by_key(rows):
    return {(r["bookmaker"], r["market"], r["player_name"], r["line"]): r for r in rows}


class TestParseEventProps:
    def test_two_bookmakers_give_one_row_per_book_market_player_and_line(self):
        rows = _props_by_key(parse_event_props(PROP_PAYLOAD, PROPS_MARKET_MAP))

        assert set(rows) == {
            ("draftkings", "pts", "Jalen Brunson", 26.5),
            ("draftkings", "pts", "Stephen Curry", 27.5),
            ("fanduel", "fg3m", "Stephen Curry", 4.5),
            ("fanduel", "pts", "Jalen Brunson", 25.5),
        }

    def test_over_and_under_on_the_same_line_pair_into_one_row(self):
        row = _props_by_key(parse_event_props(PROP_PAYLOAD, PROPS_MARKET_MAP))[
            ("draftkings", "pts", "Jalen Brunson", 26.5)
        ]

        assert row["over_price"] == -115
        assert row["under_price"] == -105
        assert row["provider_updated_at"] == datetime(2026, 10, 21, 15, 31, 29, tzinfo=timezone.utc)

    def test_a_one_sided_line_keeps_the_missing_price_null(self):
        row = _props_by_key(parse_event_props(PROP_PAYLOAD, PROPS_MARKET_MAP))[
            ("draftkings", "pts", "Stephen Curry", 27.5)
        ]

        assert row["over_price"] == 110
        assert row["under_price"] is None

    def test_the_bookmaker_update_time_backs_a_market_without_one(self):
        row = _props_by_key(parse_event_props(PROP_PAYLOAD, PROPS_MARKET_MAP))[
            ("fanduel", "fg3m", "Stephen Curry", 4.5)
        ]

        assert row["provider_updated_at"] == datetime(2026, 10, 21, 15, 10, tzinfo=timezone.utc)

    def test_an_unknown_market_key_is_skipped_with_a_warning(self, caplog):
        with caplog.at_level("WARNING", logger="props"):
            rows = parse_event_props(PROP_PAYLOAD, PROPS_MARKET_MAP)

        assert {r["market"] for r in rows} == {"pts", "fg3m"}
        assert "player_double_double" in caplog.text

    def test_every_requested_market_normalises_to_a_nine_cat_key(self):
        assert sorted(PROPS_MARKET_MAP.values()) == sorted(
            ["pts", "reb", "ast", "fg3m", "pra", "stl", "blk", "tov"]
        )

    def test_a_payload_with_no_bookmakers_gives_no_rows(self):
        assert parse_event_props({"id": "x"}, PROPS_MARKET_MAP) == []


class TestMatchPropPlayer:
    def test_a_generational_suffix_difference_still_matches(self):
        assert match_prop_player("Jaren Jackson", ("MEM", "LAL"), PROP_PLAYERS) == "1630228"

    def test_the_event_teams_disambiguate_a_shared_name(self):
        assert match_prop_player("Jalen Williams", ("NYK", "GSW"), PROP_PLAYERS) == "900002"

    def test_a_shared_name_on_neither_team_matches_no_one(self):
        assert match_prop_player("Jalen Williams", ("BOS", "MIA"), PROP_PLAYERS) is None

    def test_an_unknown_name_matches_no_one(self):
        assert match_prop_player("Nobody Atall", ("NYK", "GSW"), PROP_PLAYERS) is None


class TestMapPropEvent:
    def test_full_team_names_and_the_eastern_date_map_to_the_game(self):
        assert map_prop_event(PROP_EVENT, SCHEDULE_ROWS) == (date(2026, 10, 21), "0022600012")

    def test_a_late_tip_uses_the_eastern_date_not_utc(self):
        event = {**PROP_EVENT, "commence_time": "2026-10-24T01:00:00Z"}

        assert map_prop_event(event, SCHEDULE_ROWS) == (date(2026, 10, 23), "0022600031")

    def test_the_la_clippers_spelling_resolves(self):
        assert props.team_abbr_from_name("LA Clippers") == "LAC"
        assert props.team_abbr_from_name("Los Angeles Clippers") == "LAC"

    def test_an_unknown_team_name_maps_to_no_game(self):
        event = {**PROP_EVENT, "home_team": "Seattle SuperSonics"}

        assert map_prop_event(event, SCHEDULE_ROWS) == (date(2026, 10, 21), None)


PROPS_NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)


class TestPropsWindow:
    def test_the_default_window_ends_with_the_current_eastern_day(self):
        start, end = props.props_window(PROPS_NOW)

        assert start == PROPS_NOW
        assert end.astimezone(odds.EASTERN).date() == date(2026, 10, 21)
        assert (end + timedelta(seconds=1)).astimezone(odds.EASTERN).hour == 0

    def test_events_already_tipped_or_past_the_window_are_dropped(self):
        start, end = props.props_window(PROPS_NOW)
        events = [
            {**PROP_EVENT, "id": "tipped", "commence_time": "2026-10-21T15:00:00Z"},
            {**PROP_EVENT, "id": "tonight"},
            {**PROP_EVENT, "id": "tomorrow", "commence_time": "2026-10-23T02:00:00Z"},
            {**PROP_EVENT, "id": "far", "commence_time": "2026-10-25T23:00:00Z"},
        ]

        kept = props.events_in_window(events, start, end)

        assert [e["id"] for e in kept] == ["tonight"]

    def test_a_run_is_due_only_after_the_minimum_gap(self):
        assert props.props_run_due(None, PROPS_NOW, 20) is True
        assert props.props_run_due(PROPS_NOW - timedelta(hours=19), PROPS_NOW, 20) is False
        assert props.props_run_due(PROPS_NOW - timedelta(hours=20), PROPS_NOW, 20) is True
        assert props.props_run_due(PROPS_NOW, PROPS_NOW, 0) is True


class FakePropsProvider:
    name = "the_odds_api"

    def __init__(self, events, payloads, remaining=480, cost=2, actual_cost=None):
        self.events = events
        self.payloads = payloads
        self.requests_remaining = None
        self._remaining = remaining
        self._cost = cost
        self._actual_cost = cost if actual_cost is None else actual_cost
        self.odds_calls: list[str] = []

    def fetch_events(self, start, end):
        self.requests_remaining = self._remaining
        return self.events

    def fetch_event_props(self, event_id):
        self.odds_calls.append(event_id)
        self._remaining -= self._actual_cost
        self.requests_remaining = self._remaining
        return self.payloads.get(event_id, {})

    def event_props_cost(self):
        return self._cost


class PropsCursor(OddsCursor):
    def __init__(self, last_started=None):
        super().__init__()
        self.last_started = last_started

    def fetchall(self):
        if "FROM players" in self.statements[-1]:
            return [("1628973", "Jalen Brunson", "NYK"), ("201939", "Stephen Curry", "GSW")]
        return super().fetchall()

    def fetchone(self):
        if "MAX(started_at)" in self.statements[-1]:
            return (self.last_started,)
        return super().fetchone()


class PropsConn(OddsConn):
    def __init__(self, last_started=None):
        self.cursor_ = PropsCursor(last_started)



class TestPropsCredits:
    def test_credits_are_events_times_markets(self):
        assert props.credits_for_snapshot(7, 2) == 14
        assert props.credits_for_snapshot(0, 2) == 0
        assert props.credits_for_snapshot(-1, 2) == 0

    def test_a_typical_season_fits_the_monthly_budget(self):
        nightly = props.credits_for_snapshot(7, len(PROPS_DEFAULT_MARKETS))

        assert nightly * 30 <= PROPS_MONTHLY_BUDGET - PROPS_RESERVE_CREDITS

    def test_the_reserve_bounds_what_a_snapshot_may_spend(self):
        assert props.snapshot_allowed(64, 14) is True
        assert props.snapshot_allowed(63, 14) is False
        assert props.snapshot_allowed(None, 14) is True


class TestPropsMarkets:
    def test_no_override_means_the_default_subset(self):
        assert props.parse_props_markets(None) == ("pts", "pra")
        assert props.parse_props_markets("  ") == ("pts", "pra")

    def test_an_override_is_parsed_in_order_without_duplicates(self):
        assert props.parse_props_markets("reb, Pts,reb") == ("reb", "pts")

    def test_an_unknown_market_is_an_error(self):
        with pytest.raises(ValueError, match="dunks"):
            props.parse_props_markets("pts,dunks")

    def test_the_default_request_asks_for_exactly_two_market_keys(self):
        seen: list[dict] = []

        def fake_get(url, params, timeout):
            seen.append(params)
            return type("R", (), {
                "headers": {}, "raise_for_status": lambda self: None,
                "json": lambda self: {},
            })()

        provider = props.TheOddsApiProvider("k", get=fake_get)
        provider.fetch_event_props("evt")

        assert seen[0]["markets"] == "player_points,player_points_rebounds_assists"
        assert provider.event_props_cost() == 2

    def test_the_env_provider_requests_the_chosen_markets(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "k")

        provider = props.provider_from_env(("reb", "ast", "stl"))

        assert provider.event_props_cost() == 3


class TestScrapePropOdds:
    @pytest.fixture
    def written(self, monkeypatch):
        batches: list[tuple[str, list]] = []
        monkeypatch.setattr(
            database, "execute_values",
            lambda cur, sql, rows, page_size, template=None: batches.append((sql, list(rows))),
        )
        return batches

    def test_rows_are_written_with_game_and_player_ids_and_the_quota_noted(
        self, monkeypatch, written
    ):
        provider = FakePropsProvider([PROP_EVENT], {PROP_EVENT["id"]: PROP_PAYLOAD})
        finished: list[tuple] = []
        monkeypatch.setattr(
            props, "_finish_ingestion_run",
            lambda conn, run_id, status, rows, notes=None: finished.append(
                (run_id, status, rows, notes)
            ),
        )

        ok = props.scrape_prop_odds(PropsConn(), provider=provider, now=PROPS_NOW)

        inserts = [rows for sql, rows in written if "INSERT INTO prop_odds_snapshots" in sql]
        assert ok is True
        assert len(inserts[0]) == 4
        assert {row[2] for row in inserts[0]} == {"0022600012"}
        assert {(row[6], row[7]) for row in inserts[0]} == {
            ("Jalen Brunson", "1628973"), ("Stephen Curry", "201939"),
        }
        assert {row[12:] for row in inserts[0]} == {("the_odds_api", 7)}
        assert finished == [(7, "succeeded", 4, "requests remaining 478")]

    def test_no_odds_call_for_an_event_outside_the_window(self, written):
        far = {**PROP_EVENT, "id": "far", "commence_time": "2026-10-26T23:00:00Z"}
        tipped = {**PROP_EVENT, "id": "tipped", "commence_time": "2026-10-21T12:00:00Z"}
        provider = FakePropsProvider([PROP_EVENT, far, tipped], {PROP_EVENT["id"]: PROP_PAYLOAD})

        props.scrape_prop_odds(PropsConn(), dry_run=True, provider=provider, now=PROPS_NOW)

        assert provider.odds_calls == [PROP_EVENT["id"]]

    def test_a_recent_run_skips_every_provider_call(self, written):
        provider = FakePropsProvider([PROP_EVENT], {PROP_EVENT["id"]: PROP_PAYLOAD})
        conn = PropsConn(last_started=PROPS_NOW - timedelta(hours=2))

        ok = props.scrape_prop_odds(conn, provider=provider, now=PROPS_NOW)

        assert ok is True
        assert provider.requests_remaining is None
        assert provider.odds_calls == []
        assert written == []

    def test_a_low_quota_stops_the_odds_calls(self, written):
        second = {**PROP_EVENT, "id": "second"}
        provider = FakePropsProvider(
            [PROP_EVENT, second], {}, remaining=70, cost=2, actual_cost=69
        )

        props.scrape_prop_odds(PropsConn(), dry_run=True, provider=provider, now=PROPS_NOW)

        assert provider.odds_calls == [PROP_EVENT["id"]]

    def test_a_snapshot_that_would_dip_into_the_reserve_is_refused(self, monkeypatch, written):
        second = {**PROP_EVENT, "id": "second"}
        provider = FakePropsProvider([PROP_EVENT, second], {}, remaining=53, cost=2)
        finished: list[tuple] = []
        monkeypatch.setattr(
            props, "_finish_ingestion_run",
            lambda conn, run_id, status, rows, notes=None: finished.append((status, rows, notes)),
        )

        ok = props.scrape_prop_odds(PropsConn(), provider=provider, now=PROPS_NOW)

        assert ok is False
        assert provider.odds_calls == []
        assert written == []
        assert finished[0][0] == "failed"
        assert "refused" in finished[0][2]

    def test_a_snapshot_that_exactly_leaves_the_reserve_runs(self, written):
        second = {**PROP_EVENT, "id": "second"}
        provider = FakePropsProvider([PROP_EVENT, second], {}, remaining=54, cost=2)

        props.scrape_prop_odds(PropsConn(), dry_run=True, provider=provider, now=PROPS_NOW)

        assert provider.odds_calls == [PROP_EVENT["id"], "second"]

    def test_dry_run_reads_but_writes_nothing(self, written):
        provider = FakePropsProvider([PROP_EVENT], {PROP_EVENT["id"]: PROP_PAYLOAD})
        conn = PropsConn()

        ok = props.scrape_prop_odds(conn, dry_run=True, provider=provider, now=PROPS_NOW)

        assert ok is True
        assert written == []
        assert conn.cursor_.statements
        assert all(not is_write_statement(sql) for sql in conn.cursor_.statements)

    def test_no_key_skips_without_touching_the_database(self, monkeypatch, caplog):
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        conn = PropsConn()

        with caplog.at_level("INFO", logger="props"):
            ok = props.scrape_prop_odds(conn)

        assert ok is True
        assert conn.cursor_.statements == []
        assert "props provider not configured" in caplog.text

    def test_a_failed_events_fetch_reports_failure(self, written):
        class Down(FakePropsProvider):
            def fetch_events(self, start, end):
                raise requests.ConnectionError("down")

        ok = props.scrape_prop_odds(PropsConn(), provider=Down([], {}), now=PROPS_NOW)

        assert ok is False
        assert written == []


class _FakeResponse:
    def __init__(self, payload, headers):
        self._payload = payload
        self.headers = headers

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestTheOddsApiProvider:
    def test_the_event_odds_request_names_the_markets_and_records_the_quota(self):
        calls: list[tuple[str, dict]] = []

        def get(url, params, timeout):
            calls.append((url, params))
            return _FakeResponse(PROP_PAYLOAD, {"x-requests-remaining": "492"})

        provider = props.TheOddsApiProvider(
            "k", markets=tuple(PROPS_MARKET_MAP.values()), get=get
        )

        payload = provider.fetch_event_props("abc")

        url, params = calls[0]
        assert url.endswith("/v4/sports/basketball_nba/events/abc/odds")
        assert params["markets"].split(",") == list(PROPS_MARKET_MAP)
        assert params["regions"] == "us"
        assert params["oddsFormat"] == "american"
        assert params["apiKey"] == "k"
        assert payload["id"] == PROP_EVENT["id"]
        assert provider.requests_remaining == 492
        assert provider.event_props_cost() == 8

    def test_the_events_request_sends_whole_second_utc_bounds(self):
        calls: list[dict] = []

        def get(url, params, timeout):
            calls.append(params)
            return _FakeResponse([PROP_EVENT], {})

        provider = props.TheOddsApiProvider("k", get=get)

        events = provider.fetch_events(*props.props_window(PROPS_NOW))

        assert events == [PROP_EVENT]
        assert calls[0]["commenceTimeFrom"] == "2026-10-21T16:00:00Z"
        assert calls[0]["commenceTimeTo"] == "2026-10-22T03:59:59Z"
        assert provider.requests_remaining is None

    def test_an_env_key_builds_the_provider_and_a_blank_key_builds_none(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "  ")
        assert props.provider_from_env() is None
        monkeypatch.setenv("ODDS_API_KEY", "abc")
        assert isinstance(props.provider_from_env(), props.TheOddsApiProvider)


class TestPropsCli:
    def test_props_only_is_off_by_default(self):
        assert _parse_args([]).props_only is False

    def test_props_markets_defaults_to_unset_and_takes_an_override(self):
        assert _parse_args([]).props_markets is None
        assert _parse_args(["--props-markets", "pts,reb"]).props_markets == "pts,reb"

    def test_the_odds_lane_runs_props_after_the_espn_snapshot(self, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr(
            run_scraper, "scrape_odds_snapshots", lambda conn, dry_run: order.append("espn")
        )
        monkeypatch.setattr(
            run_scraper, "scrape_prop_odds", lambda conn, dry_run, markets: order.append("props")
        )

        run_scraper._odds_lane(object(), dry_run=True, markets=("pts",))

        assert order == ["espn", "props"]

    def test_a_props_failure_does_not_escape_the_odds_lane(self, monkeypatch):
        def boom(conn, dry_run, markets):
            raise RuntimeError("provider down")

        monkeypatch.setattr(run_scraper, "scrape_odds_snapshots", lambda conn, dry_run: True)
        monkeypatch.setattr(run_scraper, "scrape_prop_odds", boom)

        run_scraper._odds_lane(object(), dry_run=True, markets=("pts",))


PLAYOFF_GAME_ID = "0042400101"
PLAYIN_GAME_ID = "0052400111"
PRESEASON_GAME_ID = "0012400005"


def _log_row(game_id, game_date, player_id=1628369):
    return {
        **PLAYER_GAME_LOG_ROW,
        "PLAYER_ID": player_id,
        "GAME_ID": game_id,
        "GAME_DATE": f"{game_date}T00:00:00",
    }


def _team_rows(game_id, game_date):
    return [{**row, "GAME_ID": game_id, "GAME_DATE": game_date} for row in TEAM_GAME_LOG_ROWS]


class TestSeasonTypesIngested:
    def test_every_competition_a_player_logs_minutes_in_is_ingested(self):
        # act + assert
        assert SEASON_TYPES_INGESTED == ("Pre Season", "Regular Season", "PlayIn", "Playoffs")

    def test_the_stored_labels_match_what_the_game_id_says(self):
        # arrange
        derived = {
            season_type_from_game_id(PRESEASON_GAME_ID),
            season_type_from_game_id("0022400061"),
            season_type_from_game_id(PLAYIN_GAME_ID),
            season_type_from_game_id(PLAYOFF_GAME_ID),
        }

        # act + assert
        assert derived == set(SEASON_TYPES_INGESTED)


class TestSeasonTypesToFetch:
    def test_an_october_run_fetches_the_preseason_and_regular_season(self):
        # act
        result = season_types_to_fetch("2026-27", date(2026, 10, 25))

        # assert
        assert result == ("Pre Season", "Regular Season")

    def test_a_november_run_fetches_the_regular_season_only(self):
        # act
        result = season_types_to_fetch("2026-27", date(2026, 11, 1))

        # assert
        assert result == ("Regular Season",)

    def test_the_preseason_window_opens_on_september_fifteenth(self):
        # act
        before = season_types_to_fetch("2026-27", date(2026, 9, 14))
        opening = season_types_to_fetch("2026-27", date(2026, 9, 15))

        # assert
        assert "Pre Season" not in before
        assert "Pre Season" in opening

    def test_the_preseason_window_closes_after_october_thirty_first(self):
        # act
        closing = season_types_to_fetch("2026-27", date(2026, 10, 31))

        # assert
        assert closing == ("Pre Season", "Regular Season")

    def test_a_july_run_fetches_no_preseason(self):
        # act
        result = season_types_to_fetch("2026-27", date(2026, 7, 20))

        # assert
        assert result == ("Regular Season",)

    def test_from_april_every_ingested_type_is_fetched_in_order(self):
        # act
        result = season_types_to_fetch("2025-26", date(2026, 4, 1))

        # assert
        assert result == ("Regular Season", "PlayIn", "Playoffs")

    def test_a_past_season_fetches_its_postseason(self):
        # act
        result = season_types_to_fetch("2024-25", date(2026, 10, 1))

        # assert
        assert result == ("Regular Season", "PlayIn", "Playoffs")

    def test_the_last_day_of_march_is_still_regular_season_only(self):
        # act
        result = season_types_to_fetch("2025-26", date(2026, 3, 31))

        # assert
        assert result == ("Regular Season",)


class TestPostseasonRowsAreLabelledFaithfully:
    def test_a_playoff_player_row_is_stored_as_playoffs(self):
        # arrange
        raw = _log_row(PLAYOFF_GAME_ID, "2025-04-20")

        # act
        row = build_player_game_log_row(raw, "2024-25", run_id=1)

        # assert
        assert row[3] == "Playoffs"

    def test_a_play_in_team_row_is_stored_as_play_in(self):
        # arrange
        raw = _team_rows(PLAYIN_GAME_ID, "2025-04-15")[0]

        # act
        row = build_team_game_log_row(raw, "2024-25", run_id=1)

        # assert
        assert row[3] == "PlayIn"

    def test_a_playoff_game_rebuilt_from_team_logs_keeps_its_type(self):
        # arrange
        team_rows = _team_rows(PLAYOFF_GAME_ID, "2025-04-20")

        # act
        games = schedule_rows_from_team_logs(team_rows, "2024-25")

        # assert
        assert [g["season_type"] for g in games] == ["Playoffs"]


class TestIngestedScheduleRows:
    def test_all_star_rows_are_dropped_and_preseason_rows_kept(self):
        # arrange
        rows = [
            {"nba_game_id": "0012400002", "season_type": "Pre Season"},
            {"nba_game_id": "0022400061", "season_type": "Regular Season"},
            {"nba_game_id": "0032400001", "season_type": "All Star"},
            {"nba_game_id": PLAYIN_GAME_ID, "season_type": "PlayIn"},
            {"nba_game_id": PLAYOFF_GAME_ID, "season_type": "Playoffs"},
        ]

        # act
        kept = ingested_schedule_rows(rows)

        # assert
        assert [r["nba_game_id"] for r in kept] == [
            "0012400002", "0022400061", PLAYIN_GAME_ID, PLAYOFF_GAME_ID,
        ]


def _stub_ingestion(monkeypatch, module, finished):
    monkeypatch.setattr(module, "_start_ingestion_run", lambda *a, **k: 7)
    monkeypatch.setattr(
        module,
        "_finish_ingestion_run",
        lambda conn, run_id, status, written, **kwargs: finished.append(
            {"status": status, "written": written, **kwargs}
        ),
    )
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)


class TestScrapeGameLogsSeasonTypeLoop:
    def _wire(self, monkeypatch, failing=()):
        calls = []
        upserts = []
        finished = []
        watermarks = {
            "Pre Season": None,
            "Regular Season": date(2025, 4, 13),
            "PlayIn": None,
            "Playoffs": None,
        }
        games = {
            "Pre Season": (PRESEASON_GAME_ID, "2024-10-10"),
            "Regular Season": ("0022401200", "2025-04-13"),
            "PlayIn": (PLAYIN_GAME_ID, "2025-04-15"),
            "Playoffs": (PLAYOFF_GAME_ID, "2025-04-20"),
        }

        def player_logs(season, date_from, season_type):
            calls.append(("player", season_type, date_from))
            if season_type in failing:
                raise ConnectionError("tarpit")
            return [_log_row(*games[season_type])]

        def team_logs(season, date_from, season_type):
            calls.append(("team", season_type, date_from))
            return _team_rows(*games[season_type])

        def league_logs(season, date_from, season_type):
            calls.append(("league", season_type, date_from))
            return []

        _stub_ingestion(monkeypatch, truth_layer, finished)
        monkeypatch.setattr(truth_layer, "_fetch_player_game_logs", player_logs)
        monkeypatch.setattr(truth_layer, "_fetch_team_game_logs", team_logs)
        monkeypatch.setattr(truth_layer, "_fetch_league_player_game_logs", league_logs)
        monkeypatch.setattr(
            truth_layer,
            "_latest_logged_game_date",
            lambda conn, season, season_type: watermarks[season_type],
        )
        monkeypatch.setattr(
            truth_layer,
            "_batch_upsert",
            lambda cur, sql, rows: upserts.extend(rows) or len(rows),
        )
        monkeypatch.setattr(truth_layer, "_sync_player_team_stints", lambda *a, **k: None)
        return calls, upserts, finished

    def test_every_season_type_is_fetched_with_its_own_watermark(self, monkeypatch):
        # arrange
        calls, _, _ = self._wire(monkeypatch)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2025, 5, 1))

        # assert
        player_calls = [(kind, t, d) for kind, t, d in calls if kind == "player"]
        assert player_calls == [
            ("player", "Regular Season",
             date(2025, 4, 13) - timedelta(days=GAME_LOG_CORRECTION_WINDOW_DAYS)),
            ("player", "PlayIn", season_start_date("2024-25")),
            ("player", "Playoffs", season_start_date("2024-25")),
        ]
        assert {t for kind, t, _ in calls if kind == "team"} == {
            "Regular Season", "PlayIn", "Playoffs",
        }

    def test_postseason_rows_are_written_with_their_season_type(self, monkeypatch):
        # arrange
        _, upserts, finished = self._wire(monkeypatch)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2025, 5, 1))

        # assert
        stored = {(row[1], row[3]) for row in upserts}
        assert (PLAYOFF_GAME_ID, "Playoffs") in stored
        assert (PLAYIN_GAME_ID, "PlayIn") in stored
        assert ("0022401200", "Regular Season") in stored
        assert finished[-1]["status"] == "succeeded"

    def test_one_failing_season_type_still_writes_the_others(self, monkeypatch):
        # arrange
        _, upserts, finished = self._wire(monkeypatch, failing=("Playoffs",))

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2025, 5, 1))

        # assert
        assert PLAYOFF_GAME_ID not in {row[1] for row in upserts}
        assert PLAYIN_GAME_ID in {row[1] for row in upserts}
        assert finished[-1]["status"] == "partial"
        assert "Playoffs" in finished[-1]["notes"]

    def test_every_season_type_failing_fails_the_run(self, monkeypatch):
        # arrange
        _, upserts, finished = self._wire(monkeypatch, failing=SEASON_TYPES_INGESTED)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2025, 5, 1))

        # assert
        assert upserts == []
        assert finished[-1]["status"] == "failed"

    def test_an_october_run_makes_no_postseason_requests(self, monkeypatch):
        # arrange
        calls, _, _ = self._wire(monkeypatch)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2024, 10, 30))

        # assert
        assert {t for _, t, _ in calls} == {"Pre Season", "Regular Season"}

    def test_an_october_run_writes_preseason_rows_labelled_pre_season(self, monkeypatch):
        # arrange
        calls, upserts, finished = self._wire(monkeypatch)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2024, 10, 12))

        # assert
        stored = {(row[1], row[3]) for row in upserts}
        assert (PRESEASON_GAME_ID, "Pre Season") in stored
        preseason_calls = [d for kind, t, d in calls if kind == "player" and t == "Pre Season"]
        assert preseason_calls == [season_start_date("2024-25")]
        assert finished[-1]["status"] == "succeeded"

    def test_a_december_run_makes_no_preseason_request(self, monkeypatch):
        # arrange
        calls, _, _ = self._wire(monkeypatch)

        # act
        truth_layer.scrape_game_logs(FakeConn(), "2024-25", today=date(2024, 12, 1))

        # assert
        assert "Pre Season" not in {t for _, t, _ in calls}


class TestScheduleTeamLogFallback:
    def test_the_fallback_reads_every_season_type(self, monkeypatch):
        # arrange
        seen = []
        monkeypatch.setattr(truth_layer.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(
            truth_layer,
            "_fetch_team_game_logs",
            lambda season, date_from, season_type: seen.append(season_type) or [],
        )

        # act
        truth_layer.fetch_all_season_type_team_logs("2024-25")

        # assert
        assert seen == list(SEASON_TYPES_INGESTED)


class TestBackfillGameLogsSeasonTypeLoop:
    def test_logs_for_every_season_type_and_postseason_schedule_rows_are_written(
        self, monkeypatch
    ):
        # arrange
        fetched = []
        upserts = []
        schedules = []
        finished = []
        games = {
            "Pre Season": (PRESEASON_GAME_ID, "2024-10-10"),
            "Regular Season": ("0022401200", "2025-04-13"),
            "PlayIn": (PLAYIN_GAME_ID, "2025-04-15"),
            "Playoffs": (PLAYOFF_GAME_ID, "2025-04-20"),
        }
        _stub_ingestion(monkeypatch, backfill, finished)
        monkeypatch.setattr(
            backfill, "_fetch_team_game_logs",
            lambda season, date_from, season_type: fetched.append(("team", season_type))
            or _team_rows(*games[season_type]),
        )
        monkeypatch.setattr(
            backfill, "_fetch_player_game_logs",
            lambda season, date_from, season_type: fetched.append(("player", season_type))
            or [_log_row(*games[season_type])],
        )
        monkeypatch.setattr(
            backfill, "_fetch_league_player_game_logs",
            lambda season, date_from, season_type: [],
        )
        monkeypatch.setattr(
            backfill, "_fetch_league_schedule",
            lambda season: [
                {**SEASON_GAME_ROW, "gameId": "0012400002"},
                {**SEASON_GAME_ROW, "gameId": PLAYOFF_GAME_ID},
            ],
        )
        monkeypatch.setattr(
            backfill, "_upsert_schedule_rows",
            lambda cur, rows: schedules.extend(rows) or len(rows),
        )
        monkeypatch.setattr(
            backfill, "_batch_upsert", lambda cur, sql, rows: upserts.extend(rows) or len(rows)
        )
        monkeypatch.setattr(backfill, "scrape_game_status", lambda *a, **k: 0)

        # act
        backfill.backfill_game_logs_season(FakeConn(), "2024-25")

        # assert
        assert [t for kind, t in fetched if kind == "player"] == list(SEASON_TYPES_INGESTED)
        assert {row[3] for row in upserts} == set(SEASON_TYPES_INGESTED)
        assert [r["season_type"] for r in schedules] == ["Pre Season", "Playoffs"]


class TestValidationCoverageGate:
    def test_schedule_coverage_is_checked_for_the_regular_season_only(self, monkeypatch):
        # arrange
        queries = []
        monkeypatch.setattr(backfill, "_scalar", lambda conn, sql, params=(): 1)
        monkeypatch.setattr(
            backfill, "_rows",
            lambda conn, sql, params=(): queries.append((sql, params)) or [],
        )

        # act
        backfill.validate_game_logs(FakeConn(), "2024-25", "2024-25")

        # assert
        coverage = [
            params for sql, params in queries
            if "FROM nba_schedule s" in sql and "NOT EXISTS" in sql
        ]
        assert coverage == [("2024-25", "Regular Season")]

    def test_the_two_team_rows_check_skips_preseason_games(self, monkeypatch):
        # arrange
        queries = []
        monkeypatch.setattr(backfill, "_scalar", lambda conn, sql, params=(): 1)
        monkeypatch.setattr(
            backfill, "_rows",
            lambda conn, sql, params=(): queries.append((sql, params)) or [],
        )

        # act
        backfill.validate_game_logs(FakeConn(), "2024-25", "2024-25")

        # assert
        two_sided = [
            params for sql, params in queries if "HAVING COUNT(*) <> 2" in sql
        ]
        assert two_sided == [("2024-25", "Pre Season")]


CELTICS = "1610612738"
LAKERS = "1610612747"
KNICKS = "1610612752"
SPURS = "1610612759"


def _sql(sql):
    return " ".join(sql.split())


class StintDb:
    # an in-memory player_game_logs + player_team_stints that enforces
    # idx_player_team_stints_one_open, so a write path that would open a second
    # stint fails here the way it fails in postgres.

    def __init__(self, logs, stints):
        self.logs = [dict(row) for row in logs]
        self.stints = [dict(row) for row in stints]
        self.writes = 0
        self.after_stint_read = None

    def rows(self):
        return sorted(
            (s["player"], s["team"], s["valid_from"], s["valid_to"]) for s in self.stints
        )

    def open_count(self, player):
        return sum(1 for s in self.stints if s["player"] == player and s["valid_to"] is None)

    def insert(self, player, team, valid_from, valid_to, source):
        if any(
            (s["player"], s["team"], s["valid_from"]) == (player, team, valid_from)
            for s in self.stints
        ):
            return
        if valid_to is None and self.open_count(player):
            raise psycopg2.errors.UniqueViolation(
                'duplicate key value violates unique constraint "idx_player_team_stints_one_open"'
            )
        self.stints.append(
            {"player": player, "team": team, "valid_from": valid_from,
             "valid_to": valid_to, "source": source}
        )


class StintCursor:
    def __init__(self, db):
        self.db = db
        self.result = []

    def execute(self, sql, params=None):
        text = _sql(sql)
        db = self.db
        self.result = []
        if text.startswith("SELECT pg_advisory_xact_lock"):
            self.result = [(None,)]
        elif text.startswith("SELECT DISTINCT nba_player_id FROM player_game_logs"):
            (season,) = params
            self.result = sorted(
                {(r["player"],) for r in db.logs if r["season"] == season and r["team"]}
            )
        elif text.startswith("SELECT nba_player_id, game_date, team_id, season_type"):
            (players,) = params
            self.result = [
                (r["player"], r["date"], r["team"], r["season_type"])
                for r in db.logs if r["player"] in players and r["team"]
            ]
        elif text.startswith("SELECT nba_player_id, team_id, valid_from, valid_to, source"):
            (players,) = params
            self.result = [
                (s["player"], s["team"], s["valid_from"], s["valid_to"], s["source"])
                for s in db.stints if s["player"] in players
            ]
            self._fire_hook()
        elif text.startswith("DELETE FROM player_team_stints"):
            (players,) = params
            db.stints = [s for s in db.stints if s["player"] not in players]
            db.writes += 1
        else:
            raise AssertionError(f"unexpected sql: {text}")

    def _fire_hook(self):
        hook, self.db.after_stint_read = self.db.after_stint_read, None
        if hook is not None:
            hook()

    def insert_rows(self, rows):
        for player, team, valid_from, valid_to, source in rows:
            self.db.insert(player, team, valid_from, valid_to, source)
        self.db.writes += 1
        return len(rows)

    def fetchall(self):
        return list(self.result)

    def close(self):
        pass


class StintConn:
    def __init__(self, db):
        self.db = db
        self.autocommit = True

    def cursor(self):
        return StintCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass


def _appearance(player, game_id, season, season_type, day, team):
    return {"player": player, "game_id": game_id, "season": season,
            "season_type": season_type, "date": day, "team": team}


# one player: Spurs in 2022-23, Celtics in 2023-24 (preseason first), Lakers in
# 2024-25. the 2023-24 backfill is what just landed.
TRAVELLER_LOGS = [
    _appearance("9", "0022200001", "2022-23", "Regular Season", date(2022, 10, 19), SPURS),
    _appearance("9", "0022200900", "2022-23", "Regular Season", date(2023, 4, 9), SPURS),
    _appearance("9", "0012300004", "2023-24", "Pre Season", date(2023, 10, 8), CELTICS),
    _appearance("9", "0022300010", "2023-24", "Regular Season", date(2023, 10, 25), CELTICS),
    _appearance("9", "0042300101", "2023-24", "Playoffs", date(2024, 4, 21), CELTICS),
    _appearance("9", "0022400005", "2024-25", "Regular Season", date(2024, 10, 22), LAKERS),
    _appearance("9", "0022401200", "2024-25", "Regular Season", date(2025, 4, 13), LAKERS),
]

TRAVELLER_STINTS = [
    {"player": "9", "team": SPURS, "valid_from": date(2022, 10, 19),
     "valid_to": None, "source": "playergamelogs"},
]


def _games(*spans):
    return [(day, team, season_type) for day, team, season_type in spans]


class TestDeriveStints:
    def test_a_traded_player_has_two_closed_stints_and_one_open(self):
        # arrange
        games = _games(
            (date(2024, 10, 22), CELTICS, "Regular Season"),
            (date(2024, 12, 1), CELTICS, "Regular Season"),
            (date(2024, 12, 20), LAKERS, "Regular Season"),
            (date(2025, 2, 1), LAKERS, "Regular Season"),
            (date(2025, 2, 10), KNICKS, "Regular Season"),
            (date(2025, 4, 25), KNICKS, "Playoffs"),
        )

        # act
        stints = derive_stints(games)

        # assert
        assert [s[:3] for s in stints] == [
            (CELTICS, date(2024, 10, 22), date(2024, 12, 19)),
            (LAKERS, date(2024, 12, 20), date(2025, 2, 9)),
            (KNICKS, date(2025, 2, 10), None),
        ]

    def test_the_answer_does_not_depend_on_the_order_games_arrive(self):
        # arrange
        games = _games(
            (date(2024, 10, 22), LAKERS, "Regular Season"),
            (date(2023, 10, 8), CELTICS, "Pre Season"),
            (date(2024, 4, 21), CELTICS, "Playoffs"),
            (date(2023, 10, 25), CELTICS, "Regular Season"),
        )

        # act
        shuffled = derive_stints(games)
        ordered = derive_stints(sorted(games))

        # assert
        assert shuffled == ordered
        assert [s[:3] for s in shuffled] == [
            (CELTICS, date(2023, 10, 8), date(2024, 10, 21)),
            (LAKERS, date(2024, 10, 22), None),
        ]

    def test_a_preseason_only_team_gets_a_closed_stint(self):
        # arrange: in camp with the spurs, cut, debuted for the knicks
        games = _games(
            (date(2023, 10, 8), SPURS, "Pre Season"),
            (date(2023, 10, 18), SPURS, "Pre Season"),
            (date(2023, 12, 1), KNICKS, "Regular Season"),
        )

        # act
        stints = derive_stints(games)

        # assert
        assert [s[:3] for s in stints] == [
            (SPURS, date(2023, 10, 8), date(2023, 10, 18)),
            (KNICKS, date(2023, 12, 1), None),
        ]

    def test_a_camp_team_he_never_played_for_is_not_left_open(self):
        # arrange
        games = _games(
            (date(2023, 4, 9), KNICKS, "Regular Season"),
            (date(2023, 10, 8), SPURS, "Pre Season"),
        )

        # act
        stints = derive_stints(games)

        # assert
        assert [s[:3] for s in stints] == [
            (KNICKS, date(2023, 4, 9), date(2023, 10, 7)),
            (SPURS, date(2023, 10, 8), date(2023, 10, 8)),
        ]

    def test_a_camp_team_on_his_current_roster_page_stays_open(self):
        # arrange
        games = _games((date(2026, 10, 5), SPURS, "Pre Season"))
        snapshots = [(SPURS, date(2026, 9, 28), ROSTER_SNAPSHOT_SOURCE)]

        # act
        stints = derive_stints(games, snapshots)

        # assert
        assert stints == [Stint(SPURS, date(2026, 10, 5), None, ROSTER_SNAPSHOT_SOURCE)]
        assert derive_stints(games, [(s.team_id, s.valid_from, s.source) for s in stints]) \
            == stints

    def test_a_newer_snapshot_on_another_team_closes_the_last_game_team(self):
        # arrange
        games = _games((date(2026, 4, 12), CELTICS, "Regular Season"))
        snapshots = [(LAKERS, date(2026, 7, 10), ROSTER_SNAPSHOT_SOURCE)]

        # act
        stints = derive_stints(games, snapshots)

        # assert
        assert stints == [
            Stint(CELTICS, date(2026, 4, 12), date(2026, 7, 9), "playergamelogs"),
            Stint(LAKERS, date(2026, 7, 10), None, ROSTER_SNAPSHOT_SOURCE),
        ]

    def test_a_snapshot_older_than_his_last_game_is_overruled_by_the_game(self):
        # arrange
        games = _games((date(2026, 1, 20), KNICKS, "Regular Season"))
        snapshots = [(LAKERS, date(2025, 10, 1), ROSTER_SNAPSHOT_SOURCE)]

        # act
        stints = derive_stints(games, snapshots)

        # assert
        assert [s[:3] for s in stints] == [(KNICKS, date(2026, 1, 20), None)]

    def test_a_snapshot_agreeing_with_his_last_team_adds_nothing(self):
        # act + assert
        assert derive_stints(
            _games((date(2026, 4, 12), CELTICS, "Regular Season")),
            [(CELTICS, date(2026, 9, 1), ROSTER_SNAPSHOT_SOURCE)],
        ) == [Stint(CELTICS, date(2026, 4, 12), None, "playergamelogs")]

    def test_no_history_yields_no_stints(self):
        # act + assert
        assert derive_stints([]) == []

    def test_an_absence_closure_keeps_the_trailing_stint_closed(self):
        # arrange
        games = _games(
            (date(2025, 1, 5), KNICKS, "Regular Season"),
            (date(2025, 4, 13), KNICKS, "Regular Season"),
        )

        # act
        stints = derive_stints(games, closed_through=date(2026, 9, 14))

        # assert
        assert stints == [
            Stint(KNICKS, date(2025, 1, 5), date(2026, 9, 14), "playergamelogs"),
        ]

    def test_a_game_after_the_closure_opens_a_new_segment(self):
        # arrange: closed for absence, then signed and played for the lakers
        games = _games(
            (date(2025, 4, 13), KNICKS, "Regular Season"),
            (date(2026, 10, 22), LAKERS, "Regular Season"),
        )

        # act
        stints = derive_stints(games, closed_through=date(2026, 9, 14))

        # assert
        assert [s[:3] for s in stints] == [
            (KNICKS, date(2025, 4, 13), date(2026, 10, 21)),
            (LAKERS, date(2026, 10, 22), None),
        ]

    def test_a_closure_earlier_than_his_last_game_is_ignored(self):
        # arrange
        games = _games(
            (date(2025, 4, 13), KNICKS, "Regular Season"),
            (date(2026, 10, 22), KNICKS, "Regular Season"),
        )

        # act
        stints = derive_stints(games, closed_through=date(2026, 9, 14))

        # assert
        assert [s[:3] for s in stints] == [(KNICKS, date(2025, 4, 13), None)]

    def test_an_absence_closure_also_holds_for_a_snapshot_stint(self):
        # arrange: moved to the lakers by a roster page, later on no roster
        games = _games((date(2026, 4, 12), CELTICS, "Regular Season"))
        snapshots = [(LAKERS, date(2026, 7, 10), ROSTER_SNAPSHOT_SOURCE)]

        # act
        stints = derive_stints(games, snapshots, closed_through=date(2026, 9, 14))

        # assert
        assert stints == [
            Stint(CELTICS, date(2026, 4, 12), date(2026, 7, 9), "playergamelogs"),
            Stint(LAKERS, date(2026, 7, 10), date(2026, 9, 14), ROSTER_SNAPSHOT_SOURCE),
        ]


class TestAbsenceClosure:
    def test_a_closed_latest_stint_with_nothing_open_is_an_absence_closure(self):
        # arrange
        existing = [
            Stint(SPURS, date(2023, 10, 1), date(2024, 6, 30), "playergamelogs"),
            Stint(KNICKS, date(2024, 7, 1), date(2026, 9, 14), "playergamelogs"),
        ]

        # act + assert
        assert absence_closure(existing) == date(2026, 9, 14)

    def test_any_open_stint_means_no_closure(self):
        # arrange
        existing = [
            Stint(SPURS, date(2023, 10, 1), date(2024, 6, 30), "playergamelogs"),
            Stint(KNICKS, date(2024, 7, 1), None, "playergamelogs"),
        ]

        # act + assert
        assert absence_closure(existing) is None

    def test_no_stints_means_no_closure(self):
        # act + assert
        assert absence_closure([]) is None


@pytest.fixture
def stint_batch_insert(monkeypatch):
    def batch_upsert(cur, sql, rows):
        if isinstance(cur, DryRunCursor):
            return database._batch_upsert(cur, sql, rows)
        return cur.insert_rows(rows)

    monkeypatch.setattr(truth_layer, "_batch_upsert", batch_upsert)


@pytest.mark.usefixtures("stint_batch_insert")
class TestStintSyncOutOfOrder:
    def test_a_history_backfill_racing_a_newer_sync_never_opens_a_second_stint(self):
        # arrange: the 2024-25 sync commits between the 2023-24 sync's read of
        # the stint table and its writes, as two backfill jobs did in prod.
        db = StintDb(TRAVELLER_LOGS, TRAVELLER_STINTS)
        db.after_stint_read = lambda: truth_layer._sync_player_team_stints(
            StintConn(db), "2024-25"
        )

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24")

        # assert
        assert db.open_count("9") == 1
        assert db.rows() == [
            ("9", CELTICS, date(2023, 10, 8), date(2024, 10, 21)),
            ("9", LAKERS, date(2024, 10, 22), None),
            ("9", SPURS, date(2022, 10, 19), date(2023, 10, 7)),
        ]

    def test_a_2023_preseason_game_arriving_after_2024_25_is_open_adds_history_only(self):
        # arrange
        logs = [r for r in TRAVELLER_LOGS if r["season"] == "2024-25"]
        db = StintDb(logs, [])
        truth_layer._sync_player_team_stints(StintConn(db), "2024-25")
        db.logs.append(TRAVELLER_LOGS[2])

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24")

        # assert
        assert db.rows() == [
            ("9", CELTICS, date(2023, 10, 8), date(2023, 10, 8)),
            ("9", LAKERS, date(2024, 10, 22), None),
        ]

    def test_running_the_sync_twice_leaves_the_same_rows_and_writes_nothing(self):
        # arrange
        db = StintDb(TRAVELLER_LOGS, TRAVELLER_STINTS)
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24")
        first = db.rows()
        writes = db.writes

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24")
        truth_layer._sync_player_team_stints(StintConn(db), "2024-25")

        # assert
        assert db.rows() == first
        assert db.writes == writes

    def test_a_newer_roster_snapshot_stint_survives_a_history_rebuild(self):
        # arrange
        snapshot = {"player": "9", "team": KNICKS, "valid_from": date(2025, 9, 1),
                    "valid_to": None, "source": ROSTER_SNAPSHOT_SOURCE}
        db = StintDb(TRAVELLER_LOGS, [snapshot])

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24")

        # assert
        assert ("9", KNICKS, date(2025, 9, 1), None) in db.rows()
        assert ("9", LAKERS, date(2024, 10, 22), date(2025, 8, 31)) in db.rows()
        assert db.open_count("9") == 1

    def test_an_absence_closed_stint_is_not_reopened_by_a_rebuild(self):
        # arrange
        logs = [r for r in TRAVELLER_LOGS if r["season"] == "2024-25"]
        closed = {"player": "9", "team": LAKERS, "valid_from": date(2024, 10, 22),
                  "valid_to": date(2026, 9, 14), "source": "playergamelogs"}
        db = StintDb(logs, [closed])

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2024-25")

        # assert
        assert db.rows() == [("9", LAKERS, date(2024, 10, 22), date(2026, 9, 14))]
        assert db.writes == 0

    def test_a_dry_run_writes_nothing(self):
        # arrange
        db = StintDb(TRAVELLER_LOGS, TRAVELLER_STINTS)

        # act
        truth_layer._sync_player_team_stints(StintConn(db), "2023-24", dry_run=True)

        # assert
        assert db.rows() == [("9", SPURS, date(2022, 10, 19), None)]
        assert db.writes == 0

    def test_the_connection_is_returned_in_autocommit(self):
        # arrange
        conn = StintConn(StintDb(TRAVELLER_LOGS, []))

        # act
        truth_layer._sync_player_team_stints(conn, "2023-24")

        # assert
        assert conn.autocommit is True
