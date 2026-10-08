import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    BACKFILL_REQUEST_DELAY_SECONDS,
    BOX_SOURCE_AUTO,
    BOX_SOURCE_STATS,
    BOX_SOURCE_WEB,
    BOX_SOURCES,
    GAME_STATUS_MAX_GAMES_PER_RUN,
    GAME_STATUS_RECENT_WINDOW_DAYS,
    ID_PROBE_MISS_LIMIT,
    NBA_WEB_PAGE_DELAY_SECONDS,
    NBA_WEB_SCHEDULE_DAYS_AHEAD,
    NBA_WEB_SCHEDULE_DAYS_BACK,
    ROSTER_SNAPSHOT_SOURCE,
    ROSTER_WEB_SOURCE,
    SEASON,
    SEASON_TYPES_DISCOVERED,
    SEASON_TYPES_INGESTED,
    WEB_BOX_SCORE_DELAY_SECONDS,
    WEB_BOX_SCORE_SOURCE,
)
from database import (
    _batch_update,
    _batch_upsert,
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import (
    _fetch_inactive_players,
    _fetch_league_player_game_logs,
    _fetch_league_schedule,
    _fetch_nba_web_games,
    _fetch_player_game_logs,
    _fetch_team_game_logs,
    fetch_box_score_traditional,
    fetch_box_score_web,
    stats_nba_reachable,
)
from parsing import season_end_date, season_start_date
from rows import (
    BOX_DETAILS_SOURCE,
    Stint,
    absence_closure,
    derive_stints,
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
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
    game_status_rows_from_web,
    web_game_is_final,
    player_ids_absent_from_box,
    schedule_rows_from_league_schedule,
    schedule_rows_from_nba_web,
    schedule_row_from_web_game,
    schedule_rows_from_team_logs,
    season_types_to_fetch,
    select_games_for_web_logs,
    split_rows_on_season_boundary,
    supplement_player_log_rows,
)

logger = logging.getLogger(__name__)

# fetched_at is left out of the insert column lists: it defaults to NOW() on
# insert and the DO UPDATE branches refresh it explicitly.

SCHEDULE_UPSERT_SQL = """
INSERT INTO nba_schedule (nba_game_id, season, season_type, game_date, scheduled_at,
                          home_team_id, away_team_id, home_team_abbr, away_team_abbr,
                          game_status, postponed_status, source)
VALUES %s
ON CONFLICT (nba_game_id) DO UPDATE SET
    season = EXCLUDED.season,
    season_type = EXCLUDED.season_type,
    game_date = EXCLUDED.game_date,
    -- COALESCE, not overwrite: the leaguegamelog fallback knows no tip-off time
    -- and must not erase one the schedule endpoint already supplied.
    scheduled_at = COALESCE(EXCLUDED.scheduled_at, nba_schedule.scheduled_at),
    home_team_id = COALESCE(EXCLUDED.home_team_id, nba_schedule.home_team_id),
    away_team_id = COALESCE(EXCLUDED.away_team_id, nba_schedule.away_team_id),
    home_team_abbr = COALESCE(EXCLUDED.home_team_abbr, nba_schedule.home_team_abbr),
    away_team_abbr = COALESCE(EXCLUDED.away_team_abbr, nba_schedule.away_team_abbr),
    game_status = COALESCE(EXCLUDED.game_status, nba_schedule.game_status),
    postponed_status = COALESCE(EXCLUDED.postponed_status, nba_schedule.postponed_status),
    source = EXCLUDED.source,
    fetched_at = NOW(),
    updated_at = NOW()
"""

PLAYER_GAME_LOG_UPSERT_SQL = """
INSERT INTO player_game_logs (nba_player_id, nba_game_id, season, season_type, game_date,
                              team_id, team_abbr, opponent_team_id, is_home, started,
                              minutes, pts, reb, ast, stl, blk, tov, fgm, fga, fg3m,
                              fg3a, ftm, fta, plus_minus, dnp_reason, source,
                              ingestion_run_id)
VALUES %s
ON CONFLICT (nba_player_id, nba_game_id) DO UPDATE SET
    season = EXCLUDED.season,
    season_type = EXCLUDED.season_type,
    game_date = EXCLUDED.game_date,
    team_id = COALESCE(EXCLUDED.team_id, player_game_logs.team_id),
    team_abbr = COALESCE(EXCLUDED.team_abbr, player_game_logs.team_abbr),
    opponent_team_id = COALESCE(EXCLUDED.opponent_team_id, player_game_logs.opponent_team_id),
    is_home = COALESCE(EXCLUDED.is_home, player_game_logs.is_home),
    -- started and dnp_reason only ever come from a per-game box score. The
    -- league-wide log sends NULL for both, and must not wipe what a box-score
    -- pass already established.
    started = COALESCE(EXCLUDED.started, player_game_logs.started),
    dnp_reason = COALESCE(EXCLUDED.dnp_reason, player_game_logs.dnp_reason),
    minutes = EXCLUDED.minutes,
    pts = EXCLUDED.pts, reb = EXCLUDED.reb, ast = EXCLUDED.ast,
    stl = EXCLUDED.stl, blk = EXCLUDED.blk, tov = EXCLUDED.tov,
    fgm = EXCLUDED.fgm, fga = EXCLUDED.fga,
    fg3m = EXCLUDED.fg3m, fg3a = EXCLUDED.fg3a,
    ftm = EXCLUDED.ftm, fta = EXCLUDED.fta,
    plus_minus = EXCLUDED.plus_minus,
    source = EXCLUDED.source,
    fetched_at = NOW(),
    ingestion_run_id = COALESCE(EXCLUDED.ingestion_run_id, player_game_logs.ingestion_run_id)
"""

TEAM_GAME_LOG_UPSERT_SQL = """
INSERT INTO team_game_logs (team_id, nba_game_id, season, season_type, game_date,
                            team_abbr, opponent_team_id, is_home, minutes, pts, reb,
                            ast, stl, blk, tov, fgm, fga, fg3m, fg3a, ftm, fta,
                            plus_minus, source, ingestion_run_id)
VALUES %s
ON CONFLICT (team_id, nba_game_id) DO UPDATE SET
    season = EXCLUDED.season,
    season_type = EXCLUDED.season_type,
    game_date = EXCLUDED.game_date,
    team_abbr = COALESCE(EXCLUDED.team_abbr, team_game_logs.team_abbr),
    opponent_team_id = COALESCE(EXCLUDED.opponent_team_id, team_game_logs.opponent_team_id),
    is_home = COALESCE(EXCLUDED.is_home, team_game_logs.is_home),
    minutes = EXCLUDED.minutes,
    pts = EXCLUDED.pts, reb = EXCLUDED.reb, ast = EXCLUDED.ast,
    stl = EXCLUDED.stl, blk = EXCLUDED.blk, tov = EXCLUDED.tov,
    fgm = EXCLUDED.fgm, fga = EXCLUDED.fga,
    fg3m = EXCLUDED.fg3m, fg3a = EXCLUDED.fg3a,
    ftm = EXCLUDED.ftm, fta = EXCLUDED.fta,
    plus_minus = EXCLUDED.plus_minus,
    source = EXCLUDED.source,
    fetched_at = NOW(),
    ingestion_run_id = COALESCE(EXCLUDED.ingestion_run_id, team_game_logs.ingestion_run_id)
"""

GAME_STATUS_UPSERT_SQL = """
INSERT INTO player_game_status (nba_player_id, nba_game_id, team_id, rostered,
                                listed_inactive, started, played, dnp_reason,
                                minutes, source, ingestion_run_id)
VALUES %s
ON CONFLICT (nba_player_id, nba_game_id) DO UPDATE SET
    team_id = COALESCE(EXCLUDED.team_id, player_game_status.team_id),
    rostered = EXCLUDED.rostered,
    -- COALESCE so a later pass that only has game logs cannot reset a known
    -- inactive flag back to "unknown".
    listed_inactive = COALESCE(EXCLUDED.listed_inactive, player_game_status.listed_inactive),
    started = COALESCE(EXCLUDED.started, player_game_status.started),
    played = EXCLUDED.played,
    dnp_reason = COALESCE(EXCLUDED.dnp_reason, player_game_status.dnp_reason),
    minutes = COALESCE(EXCLUDED.minutes, player_game_status.minutes),
    source = EXCLUDED.source,
    fetched_at = NOW(),
    ingestion_run_id = COALESCE(EXCLUDED.ingestion_run_id, player_game_status.ingestion_run_id)
"""


# the casts matter: a VALUES column that is NULL on every row is typed text,
# which postgres will not assign to a smallint or boolean column.
BOX_DETAIL_PLAYER_UPDATE_SQL = """
UPDATE player_game_logs AS p
   SET started = v.started::boolean,
       position = v.position::text,
       oreb = v.oreb::smallint,
       dreb = v.dreb::smallint,
       pf = v.pf::smallint,
       -- an inactive-list reason already stored wins over the box score's
       dnp_reason = COALESCE(p.dnp_reason, v.dnp_reason::text),
       details_source = v.details_source,
       details_fetched_at = NOW()
  FROM (VALUES %s) AS v (nba_player_id, nba_game_id, started, position, oreb,
                         dreb, pf, dnp_reason, details_source)
 WHERE p.nba_player_id = v.nba_player_id
   AND p.nba_game_id = v.nba_game_id
"""

BOX_DETAIL_TEAM_UPDATE_SQL = """
UPDATE team_game_logs AS t
   SET oreb = v.oreb::smallint,
       dreb = v.dreb::smallint,
       pf = v.pf::smallint
  FROM (VALUES %s) AS v (team_id, nba_game_id, oreb, dreb, pf)
 WHERE t.team_id = v.team_id
   AND t.nba_game_id = v.nba_game_id
"""

BOX_DETAIL_STATUS_UPDATE_SQL = """
UPDATE player_game_status AS s
   SET started = v.started::boolean
  FROM (VALUES %s) AS v (nba_player_id, nba_game_id, started)
 WHERE s.nba_player_id = v.nba_player_id
   AND s.nba_game_id = v.nba_game_id
"""

# DO NOTHING: an inactive-list or game-log row for the same key always wins.
ACTIVE_DNP_STATUS_INSERT_SQL = """
INSERT INTO player_game_status (nba_player_id, nba_game_id, team_id, rostered,
                                listed_inactive, started, played, dnp_reason,
                                minutes, source, ingestion_run_id)
VALUES %s
ON CONFLICT (nba_player_id, nba_game_id) DO NOTHING
"""

EXISTING_STATUS_IDS_SQL = """
SELECT nba_player_id
  FROM player_game_status
 WHERE nba_game_id = %s
"""

# stamps logged players v3 did not list without touching their stats
BOX_DETAIL_ABSENT_STAMP_SQL = """
UPDATE player_game_logs
   SET details_source = %s,
       details_fetched_at = NOW()
 WHERE nba_game_id = %s
   AND nba_player_id = ANY(%s)
   AND details_fetched_at IS NULL
"""


def _upsert_schedule_rows(cur: object, rows: Sequence[Mapping]) -> int:
    tuples = [
        (
            r["nba_game_id"], r["season"], r["season_type"], r["game_date"],
            r["scheduled_at"], r["home_team_id"], r["away_team_id"],
            r["home_team_abbr"], r["away_team_abbr"], r["game_status"],
            r["postponed_status"], r["source"],
        )
        for r in rows
    ]
    return _batch_upsert(cur, SCHEDULE_UPSERT_SQL, tuples)


def _upsert_game_status_rows(
    cur: object, rows: Sequence[Mapping], run_id: int | None
) -> int:
    tuples = [
        (
            r["nba_player_id"], r["nba_game_id"], r["team_id"], r["rostered"],
            r["listed_inactive"], r["started"], r["played"], r["dnp_reason"],
            r["minutes"], r["source"], run_id,
        )
        for r in rows
    ]
    return _batch_upsert(cur, GAME_STATUS_UPSERT_SQL, tuples)


def _latest_logged_game_date(
    conn: psycopg2.extensions.connection, season: str, season_type: str
) -> date | None:
    # one watermark per season type, so a type ingested for the first time
    # starts from the season boundary rather than from another type's games.
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT MAX(game_date) FROM player_game_logs "
            "WHERE season = %s AND season_type = %s",
            (season, season_type),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _games_needing_status(
    conn: psycopg2.extensions.connection,
    season: str,
    since: date | None,
    limit: int | None,
) -> list[str]:
    # driven off team_game_logs rather than nba_schedule so it only returns games
    # that actually finished.
    sql = [
        """
        SELECT DISTINCT t.nba_game_id, t.game_date
          FROM team_game_logs t
         WHERE t.season = %s
           AND NOT EXISTS (
                 SELECT 1 FROM player_game_status s
                  WHERE s.nba_game_id = t.nba_game_id
               )
        """
    ]
    params: list[object] = [season]
    if since is not None:
        sql.append("AND t.game_date >= %s")
        params.append(since)
    sql.append("ORDER BY t.game_date DESC, t.nba_game_id")
    if limit is not None:
        sql.append("LIMIT %s")
        params.append(limit)

    cur = conn.cursor()
    try:
        cur.execute(" ".join(sql), tuple(params))
        # game_date rides along so the inactive-list fetch can judge whether a
        # v2 fallback answer is trustworthy for that game's era.
        return [(str(row[0]), row[1]) for row in cur.fetchall()]
    finally:
        cur.close()


def _played_rows_for_games(
    conn: psycopg2.extensions.connection, game_ids: Sequence[str]
) -> dict[str, list[dict]]:
    if not game_ids:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT nba_game_id, nba_player_id, team_id, started, minutes, dnp_reason
              FROM player_game_logs
             WHERE nba_game_id = ANY(%s)
            """,
            (list(game_ids),),
        )
        grouped: dict[str, list[dict]] = {}
        for game_id, player_id, team_id, started, minutes, dnp_reason in cur.fetchall():
            grouped.setdefault(str(game_id), []).append(
                {
                    "nba_player_id": str(player_id),
                    "team_id": team_id,
                    "started": started,
                    "minutes": float(minutes) if minutes is not None else None,
                    "dnp_reason": dnp_reason,
                }
            )
        return grouped
    finally:
        cur.close()


def _games_needing_box_details(
    conn: psycopg2.extensions.connection, season: str, limit: int | None
) -> list[tuple[str, list[str]]]:
    # two team rows means the game finished and both logs landed; a game drops
    # out once every one of its player rows has been stamped.
    sql = [
        """
        SELECT s.nba_game_id
          FROM nba_schedule s
         WHERE s.season = %s
           AND (SELECT COUNT(*)
                  FROM team_game_logs t
                 WHERE t.nba_game_id = s.nba_game_id) = 2
           AND EXISTS (SELECT 1
                         FROM player_game_logs p
                        WHERE p.nba_game_id = s.nba_game_id
                          AND p.details_fetched_at IS NULL)
         ORDER BY s.game_date, s.nba_game_id
        """
    ]
    params: list[object] = [season]
    if limit is not None:
        sql.append("LIMIT %s")
        params.append(limit)

    cur = conn.cursor()
    try:
        cur.execute(" ".join(sql), tuple(params))
        game_ids = [str(row[0]) for row in cur.fetchall()]
        if not game_ids:
            return []
        cur.execute(
            """
            SELECT nba_game_id, nba_player_id
              FROM player_game_logs
             WHERE nba_game_id = ANY(%s)
               AND details_fetched_at IS NULL
            """,
            (game_ids,),
        )
        pending: dict[str, list[str]] = {gid: [] for gid in game_ids}
        for game_id, player_id in cur.fetchall():
            pending[str(game_id)].append(str(player_id))
        return [(gid, pending[gid]) for gid in game_ids]
    finally:
        cur.close()


def _existing_status_keys(
    cur: object, game_id: str, pending_ids: Sequence[str]
) -> set[tuple[str, str]]:
    # pending_ids covers rows written earlier on this cursor that a dry run
    # skipped, so a dry run does not count them as active-DNP inserts.
    cur.execute(EXISTING_STATUS_IDS_SQL, (game_id,))
    ids = {str(row[0]) for row in cur.fetchall()} | {str(pid) for pid in pending_ids}
    return {(pid, game_id) for pid in ids}


def resolve_box_source(
    requested: str, probe: Callable[[], bool] | None = None
) -> str:
    # auto spends one probe per run: stats.nba.com when it answers, else the
    # nba.com box-score pages, which answer from ci and a throttled home ip.
    if requested not in BOX_SOURCES:
        raise ValueError(f"unknown box-score source {requested!r}")
    if requested != BOX_SOURCE_AUTO:
        return requested
    if (probe or stats_nba_reachable)():
        return BOX_SOURCE_STATS
    logger.warning("box scores: stats.nba.com unreachable, using nba.com box-score pages")
    return BOX_SOURCE_WEB


def box_delay_seconds(source: str) -> float:
    if source == BOX_SOURCE_WEB:
        return WEB_BOX_SCORE_DELAY_SECONDS
    return BACKFILL_REQUEST_DELAY_SECONDS


def _fetch_box_detail_rows(
    game_id: str, source: str
) -> tuple[list[dict], list[dict], str]:
    if source == BOX_SOURCE_WEB:
        game = fetch_box_score_web(game_id)
        if not web_game_is_final(game):
            raise ValueError(f"{game_id}: nba.com does not report the game final")
        player_rows, team_rows = box_detail_rows_from_web(game, game_id)
        return player_rows, team_rows, WEB_BOX_SCORE_SOURCE
    payload = fetch_box_score_traditional(game_id)
    player_rows, team_rows = box_detail_rows_from_traditional(payload, game_id)
    return player_rows, team_rows, BOX_DETAILS_SOURCE


def _apply_box_details(
    cur: object,
    game_id: str,
    logged_player_ids: Sequence[str],
    run_id: int | None = None,
    pending_status_ids: Sequence[str] = (),
    source: str = BOX_SOURCE_STATS,
) -> dict[str, int]:
    # raises on a fetch failure or an empty box score, so the caller can count
    # the game as failed and leave it unstamped for the next run.
    player_rows, team_rows, details_source = _fetch_box_detail_rows(game_id, source)
    return _write_box_details(
        cur, game_id, player_rows, team_rows, logged_player_ids,
        run_id, pending_status_ids, details_source,
    )


def _write_box_details(
    cur: object,
    game_id: str,
    player_rows: Sequence[Mapping],
    team_rows: Sequence[Mapping],
    logged_player_ids: Sequence[str],
    run_id: int | None,
    pending_status_ids: Sequence[str],
    details_source: str,
) -> dict[str, int]:
    if not player_rows:
        raise ValueError(f"{game_id}: box score has no player rows")

    player_tuples = [
        (
            r["nba_player_id"], r["nba_game_id"], r["started"], r["position"],
            r["oreb"], r["dreb"], r["pf"], r["dnp_reason"], details_source,
        )
        for r in player_rows
    ]
    team_tuples = [
        (r["team_id"], r["nba_game_id"], r["oreb"], r["dreb"], r["pf"])
        for r in team_rows
    ]
    status_tuples = [
        (r["nba_player_id"], r["nba_game_id"], r["started"]) for r in player_rows
    ]
    absent = player_ids_absent_from_box(logged_player_ids, player_rows)
    active_dnp = active_dnp_status_rows(
        player_rows,
        _existing_status_keys(cur, game_id, pending_status_ids),
        game_id,
        source=details_source,
    )
    active_dnp_tuples = [
        (
            r["nba_player_id"], r["nba_game_id"], r["team_id"], r["rostered"],
            r["listed_inactive"], r["started"], r["played"], r["dnp_reason"],
            r["minutes"], r["source"], run_id,
        )
        for r in active_dnp
    ]

    counts = {
        "players": _batch_update(cur, BOX_DETAIL_PLAYER_UPDATE_SQL, player_tuples),
        "teams": _batch_update(cur, BOX_DETAIL_TEAM_UPDATE_SQL, team_tuples),
        "status": _batch_update(cur, BOX_DETAIL_STATUS_UPDATE_SQL, status_tuples),
        "absent": len(absent),
        # rowcount, so a key that appeared since the read is not counted
        "active_dnp": _batch_update(cur, ACTIVE_DNP_STATUS_INSERT_SQL, active_dnp_tuples),
    }
    if absent:
        logger.warning(
            "box details: %s lists no row for %d logged player(s); stats left as-is",
            game_id, len(absent),
        )
        cur.execute(
            BOX_DETAIL_ABSENT_STAMP_SQL,
            (f"{details_source}:absent", game_id, absent),
        )
    return counts


def backfill_box_details(
    conn: psycopg2.extensions.connection,
    season: str,
    dry_run: bool = False,
    limit: int | None = None,
    delay_seconds: float | None = None,
    source: str = BOX_SOURCE_AUTO,
) -> int:
    # one request per game, oldest first. resumable: a game is selected only
    # while some player row still has details_fetched_at NULL, so a killed or
    # bounded run picks up where the last one stopped.
    games = _games_needing_box_details(conn, season, limit)
    if not games:
        logger.info("box details: nothing to do for %s", season)
        return 0

    source = resolve_box_source(source)
    if delay_seconds is None:
        delay_seconds = box_delay_seconds(source)
    logger.info(
        "box details: %d game(s) to fetch for %s from %s", len(games), season, source
    )
    run_id = _start_ingestion_run(
        conn,
        "box_details_backfill",
        watermark_from=games[0][0],
        watermark_to=games[-1][0],
        dry_run=dry_run,
    )

    totals = {"players": 0, "teams": 0, "status": 0, "absent": 0, "active_dnp": 0}
    failed = 0
    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for index, (game_id, logged_ids) in enumerate(games):
            try:
                counts = _apply_box_details(
                    cur, game_id, logged_ids, run_id, source=source
                )
            except Exception as e:  # noqa: BLE001 - one game must not end the run
                failed += 1
                logger.warning("box details: %s failed (%s)", game_id, e)
                time.sleep(delay_seconds * 2)
                continue
            for key, value in counts.items():
                totals[key] += value

            done = index + 1
            if done % 25 == 0 or done == len(games):
                logger.info(
                    "box details: %d/%d games (%d failed, ~%.0f min left)",
                    done, len(games), failed,
                    (len(games) - done) * delay_seconds / 60,
                )
            if done < len(games):
                time.sleep(delay_seconds)
    finally:
        cur.close()

    written = (
        totals["players"] + totals["teams"] + totals["status"] + totals["active_dnp"]
    )
    notes = (
        f"{len(games) - failed} game(s), {failed} failed; {totals['players']} player, "
        f"{totals['teams']} team, {totals['status']} status row(s); "
        f"{totals['absent']} logged player(s) absent from the box score; "
        f"source={source}; "
        f"active_dnp_rows={totals['active_dnp']}"
    )
    _finish_ingestion_run(
        conn, run_id, "succeeded" if failed == 0 else "partial", written, notes=notes
    )
    logger.info(
        "box details: %s%s", notes, " (dry run: nothing written)" if dry_run else ""
    )
    return len(games) - failed


# a game with no team log scheduled on or before today; game_ready_for_logs
# then keeps the ones that are over. postponed games are excluded the way the
# validation report excludes them.
WEB_GAME_LOGS_NEEDED_SQL = """
SELECT s.nba_game_id, s.season_type, s.game_date, s.game_status, s.scheduled_at
  FROM nba_schedule s
 WHERE s.season = %s
   AND s.season_type = ANY(%s)
   AND (s.postponed_status IS NULL OR s.postponed_status = 'N')
   AND NOT EXISTS (SELECT 1
                     FROM team_game_logs t
                    WHERE t.nba_game_id = s.nba_game_id)
   AND s.game_date <= %s
 ORDER BY s.game_date, s.nba_game_id
"""


SCHEDULED_SEASON_TYPES_SQL = """
SELECT DISTINCT season_type
  FROM nba_schedule
 WHERE season = %s
"""


def _unscheduled_season_types(
    conn: psycopg2.extensions.connection, season: str, season_types: Sequence[str]
) -> list[str]:
    # discoverable types the schedule holds no game of, in canonical order.
    cur = conn.cursor()
    try:
        cur.execute(SCHEDULED_SEASON_TYPES_SQL, (season,))
        present = {str(row[0]) for row in cur.fetchall()}
    finally:
        cur.close()
    return [
        season_type for season_type in SEASON_TYPES_DISCOVERED
        if season_type in season_types and season_type not in present
    ]


def _games_needing_web_logs(
    conn: psycopg2.extensions.connection,
    season: str,
    season_types: Sequence[str],
    now: datetime,
    limit: int | None,
) -> list[tuple[str, str, date]]:
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    cur = conn.cursor()
    try:
        cur.execute(WEB_GAME_LOGS_NEEDED_SQL, (season, list(season_types), today))
        candidates = [
            (str(game_id), str(season_type), game_date, game_status, scheduled_at)
            for game_id, season_type, game_date, game_status, scheduled_at in cur.fetchall()
        ]
    finally:
        cur.close()
    return select_games_for_web_logs(candidates, now, limit)


def _apply_web_game(
    cur: object,
    game_id: str,
    season: str,
    season_type: str,
    game_date: date,
    run_id: int | None,
) -> dict[str, int]:
    # one page fills the logs, the status rows and the box details, so a game
    # stats.nba.com never served lands complete. raises so the caller can leave
    # the game unlogged for the next run.
    game = fetch_box_score_web(game_id)
    if not web_game_is_final(game):
        raise ValueError(f"{game_id}: nba.com does not report the game final")
    player_logs, team_logs = game_log_rows_from_web(
        game, game_id, season, season_type, game_date, run_id
    )
    if len(team_logs) != 2 or not player_logs:
        raise ValueError(
            f"{game_id}: page has {len(team_logs)} team line(s) and "
            f"{len(player_logs)} player line(s)"
        )
    box_players, box_teams = box_detail_rows_from_web(game, game_id)
    status_rows = game_status_rows_from_web(game, game_id)

    counts = {
        "player_logs": _batch_upsert(cur, PLAYER_GAME_LOG_UPSERT_SQL, player_logs),
        "team_logs": _batch_upsert(cur, TEAM_GAME_LOG_UPSERT_SQL, team_logs),
        "status": _upsert_game_status_rows(cur, status_rows, run_id),
    }
    box_counts = _write_box_details(
        cur, game_id, box_players, box_teams,
        [row[0] for row in player_logs], run_id,
        [row["nba_player_id"] for row in status_rows], WEB_BOX_SCORE_SOURCE,
    )
    counts["details"] = box_counts["players"]
    counts["active_dnp"] = box_counts["active_dnp"]
    return counts


def backfill_game_logs_from_web(
    conn: psycopg2.extensions.connection,
    season: str,
    season_types: Sequence[str] = SEASON_TYPES_INGESTED,
    dry_run: bool = False,
    limit: int | None = None,
    delay_seconds: float = WEB_BOX_SCORE_DELAY_SECONDS,
    today: date | None = None,
    now: datetime | None = None,
    discover: bool = True,
) -> int:
    # one nba.com page per scheduled game the league-wide log never covered,
    # oldest first. resumable: a game drops out once its team logs land.
    # the cron lanes pass discover=False: their schedule crawl already lands
    # every game days before tip, and discovery is minutes of page fetches.
    now = now or datetime.now(ZoneInfo("America/New_York"))
    today = today or now.astimezone(ZoneInfo("America/New_York")).date()
    undiscovered = (
        _unscheduled_season_types(conn, season, season_types) if discover else []
    )
    if undiscovered:
        logger.info(
            "web game logs: schedule has no %s game for %s; discovering them first",
            ", ".join(undiscovered), season,
        )
        discover_schedule(
            conn, season, undiscovered, dry_run=dry_run, today=today,
            delay_seconds=delay_seconds,
        )
        if dry_run:
            logger.info("web game logs: dry run stored no discovered game to fetch")
    games = _games_needing_web_logs(conn, season, season_types, now, limit)
    if not games:
        logger.info("web game logs: nothing to do for %s", season)
        return 0

    logger.info("web game logs: %d game(s) to fetch for %s", len(games), season)
    run_id = _start_ingestion_run(
        conn,
        "game_logs_web_backfill",
        watermark_from=games[0][2].isoformat(),
        watermark_to=games[-1][2].isoformat(),
        dry_run=dry_run,
    )

    totals = {"player_logs": 0, "team_logs": 0, "status": 0, "details": 0, "active_dnp": 0}
    failed = 0
    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for index, (game_id, season_type, game_date) in enumerate(games):
            try:
                counts = _apply_web_game(cur, game_id, season, season_type, game_date, run_id)
            except Exception as e:  # noqa: BLE001 - one game must not end the run
                failed += 1
                logger.warning("web game logs: %s failed (%s)", game_id, e)
                time.sleep(delay_seconds * 2)
                continue
            for key, value in counts.items():
                totals[key] += value

            done = index + 1
            if done % 25 == 0 or done == len(games):
                logger.info(
                    "web game logs: %d/%d games (%d failed, ~%.0f min left)",
                    done, len(games), failed,
                    (len(games) - done) * delay_seconds / 60,
                )
            if done < len(games):
                time.sleep(delay_seconds)
    finally:
        cur.close()

    processed = len(games) - failed
    notes = (
        f"{processed} game(s), {failed} failed; {totals['player_logs']} player and "
        f"{totals['team_logs']} team log row(s), {totals['status']} status row(s), "
        f"{totals['details']} detail row(s); active_dnp_rows={totals['active_dnp']}"
    )
    written = sum(totals.values())
    _finish_ingestion_run(
        conn, run_id, "succeeded" if failed == 0 else "partial", written, notes=notes
    )
    logger.info(
        "web game logs: %s%s", notes, " (dry run: nothing written)" if dry_run else ""
    )
    if processed:
        _sync_player_team_stints(conn, season, dry_run=dry_run)
    return processed


NBA_WEB_MAX_CONSECUTIVE_FAILURES = 3


def fetch_nba_web_schedule_rows(
    season: str,
    today: date | None = None,
    delay_seconds: float = NBA_WEB_PAGE_DELAY_SECONDS,
) -> list[dict]:
    # one page per eastern date. consecutive failures end the crawl early so an
    # unreachable site costs seconds, not one timeout per page.
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    days = range(-NBA_WEB_SCHEDULE_DAYS_BACK, NBA_WEB_SCHEDULE_DAYS_AHEAD + 1)
    rows: list[dict] = []
    failures = 0
    for index, offset in enumerate(days):
        game_date = today + timedelta(days=offset)
        try:
            page = _fetch_nba_web_games(game_date)
        except Exception as e:  # noqa: BLE001 - one date must not end the crawl
            failures += 1
            logger.warning("schedule: nba.com %s failed (%s)", game_date.isoformat(), e)
            if failures >= NBA_WEB_MAX_CONSECUTIVE_FAILURES:
                logger.warning("schedule: nba.com unreachable, giving up")
                break
        else:
            failures = 0
            rows.extend(schedule_rows_from_nba_web(page, game_date, season))
        if index < len(days) - 1:
            time.sleep(delay_seconds)
    return rows


def _crawl_discovery_dates(
    season: str, season_types: Sequence[str], today: date, delay_seconds: float
) -> tuple[list[dict], bool]:
    # (rows of the requested types, whether nba.com stayed reachable).
    dates = discovery_dates(season, season_types, today)
    rows: list[dict] = []
    reachable = True
    failures = 0
    for index, game_date in enumerate(dates):
        if index:
            time.sleep(delay_seconds)
        try:
            page = _fetch_nba_web_games(game_date)
        except Exception as e:  # noqa: BLE001 - one date must not end the crawl
            failures += 1
            logger.warning("discovery: nba.com %s failed (%s)", game_date.isoformat(), e)
            if failures >= NBA_WEB_MAX_CONSECUTIVE_FAILURES:
                logger.warning("discovery: nba.com unreachable, giving up")
                reachable = False
                break
            continue
        failures = 0
        rows.extend(schedule_rows_from_nba_web(page, game_date, season))
    logger.info(
        "discovery: %d date page(s) from %s to %s",
        len(dates),
        dates[0].isoformat() if dates else "-",
        dates[-1].isoformat() if dates else "-",
    )
    return discovered_rows_for_season(rows, season, season_types), reachable


def _probe_game_id(game_id: str, season: str) -> dict | None:
    # an id the league never used answers with an error or an unplayed page.
    try:
        game = fetch_box_score_web(game_id)
    except Exception as e:  # noqa: BLE001 - a miss is the expected answer
        logger.debug("discovery: %s has no page (%s)", game_id, e)
        return None
    if not web_game_is_final(game):
        return None
    return schedule_row_from_web_game(game, season)


def _probe_enumerated_ids(
    season: str,
    season_types: Sequence[str],
    known_ids: set[str],
    delay_seconds: float,
) -> list[dict]:
    groups = [
        (season_type, group)
        for season_type in season_types
        for group in enumerate_game_id_groups(season, season_type)
    ]
    unseen = sum(1 for _, group in groups for game_id in group if game_id not in known_ids)
    logger.info("discovery: %d enumerated id(s) were on no date page", unseen)

    rows: list[dict] = []
    probes = 0
    for season_type, group in groups:
        miss_limit = ID_PROBE_MISS_LIMIT.get(season_type, 1)
        misses = 0
        for game_id in group:
            if game_id in known_ids:
                misses = 0
                continue
            if probes:
                time.sleep(delay_seconds)
            probes += 1
            row = _probe_game_id(game_id, season)
            if row is None:
                misses += 1
                if misses >= miss_limit:
                    break
                continue
            misses = 0
            rows.append(row)
    logger.info("discovery: %d id probe(s) found %d more game(s)", probes, len(rows))
    return rows


def discover_schedule_rows(
    season: str,
    season_types: Sequence[str] = SEASON_TYPES_DISCOVERED,
    today: date | None = None,
    delay_seconds: float = WEB_BOX_SCORE_DELAY_SECONDS,
) -> list[dict]:
    # date pages first, then box-score probes for enumerated ids they missed;
    # a type with no past dates yet has nothing to find.
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    season_types = [
        season_type for season_type in season_types
        if discovery_dates(season, [season_type], today)
    ]
    if not season_types:
        logger.info("discovery: no requested season type has started for %s", season)
        return []

    rows, reachable = _crawl_discovery_dates(season, season_types, today, delay_seconds)
    if reachable:
        known_ids = {row["nba_game_id"] for row in rows}
        rows = discovered_rows_for_season(
            rows + _probe_enumerated_ids(season, season_types, known_ids, delay_seconds),
            season,
            season_types,
        )
    for season_type in season_types:
        typed = [row for row in rows if row["season_type"] == season_type]
        logger.info(
            "discovery: %s %s: %d game(s)%s",
            season, season_type, len(typed),
            f" from {typed[0]['game_date']} to {typed[-1]['game_date']}" if typed else "",
        )
    return rows


def discover_schedule(
    conn: psycopg2.extensions.connection,
    season: str,
    season_types: Sequence[str] = SEASON_TYPES_DISCOVERED,
    dry_run: bool = False,
    today: date | None = None,
    delay_seconds: float = WEB_BOX_SCORE_DELAY_SECONDS,
) -> int:
    run_id = _start_ingestion_run(
        conn, "schedule_discovery", watermark_from=season, watermark_to=season,
        dry_run=dry_run,
    )
    try:
        rows = discover_schedule_rows(season, season_types, today, delay_seconds)
        cur = maybe_write_cursor(conn.cursor(), dry_run)
        try:
            written = _upsert_schedule_rows(cur, rows)
        finally:
            cur.close()
    except Exception as e:
        _finish_ingestion_run(conn, run_id, "failed", 0, notes=str(e)[:500])
        raise

    notes = f"{len(rows)} game(s) for {', '.join(season_types)}"
    _finish_ingestion_run(conn, run_id, "succeeded", written, notes=notes)
    logger.info(
        "discovery: %d schedule row(s) upserted%s",
        written, " (dry run: nothing written)" if dry_run else "",
    )
    return written


def fetch_all_season_type_team_logs(
    season: str,
    date_from: date | None = None,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> list[dict]:
    rows: list[dict] = []
    for index, season_type in enumerate(SEASON_TYPES_INGESTED):
        if index:
            time.sleep(delay_seconds)
        rows.extend(_fetch_team_game_logs(season, date_from, season_type))
    return rows


def scrape_schedule(
    conn: psycopg2.extensions.connection,
    season: str = SEASON,
    dry_run: bool = False,
    stats_reachable: bool = True,
) -> bool:
    # returns False only when every source failed. ESPN is deliberately not a
    # source: its event ids join to no NBA game id, unlike nba.com's.
    logger.info("truth layer: syncing %s schedule...", season)
    run_id = _start_ingestion_run(
        conn, "schedule", watermark_from=season, watermark_to=season, dry_run=dry_run
    )

    rows: list[dict] = []
    try:
        if not stats_reachable:
            raise ConnectionError("stats.nba.com unreachable")
        rows = schedule_rows_from_league_schedule(_fetch_league_schedule(season), season)
        logger.info("schedule: %d game(s) from scheduleleaguev2", len(rows))
    except Exception as e:  # noqa: BLE001 - falling back is the handling
        logger.warning("scheduleleaguev2 unavailable (%s); trying nba.com", e)
        rows = fetch_nba_web_schedule_rows(season)
        if rows:
            logger.info("schedule: %d game(s) from nba.com", len(rows))
        elif not stats_reachable:
            logger.error("schedule: nba.com returned nothing and stats.nba.com is down")
            _finish_ingestion_run(
                conn, run_id, "failed", 0, notes="nba.com empty, stats.nba.com down"
            )
            return False
        else:
            logger.warning(
                "schedule: nba.com returned nothing; falling back to completed "
                "games from the team game log, so upcoming games will be missing"
            )
            try:
                team_rows = fetch_all_season_type_team_logs(season)
                rows = schedule_rows_from_team_logs(team_rows, season)
                logger.info("schedule: %d completed game(s) from leaguegamelog", len(rows))
            except Exception as fallback_error:  # noqa: BLE001
                logger.error("schedule: every source failed (%s)", fallback_error)
                _finish_ingestion_run(
                    conn, run_id, "failed", 0, notes=str(fallback_error)[:500]
                )
                return False

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        written = _upsert_schedule_rows(cur, rows)
    finally:
        cur.close()

    _finish_ingestion_run(conn, run_id, "succeeded", written)
    logger.info(
        "schedule: %d row(s) upserted%s",
        written,
        " (dry run: nothing written)" if dry_run else "",
    )
    return True


def _fetch_season_type_logs(
    season: str,
    season_type: str,
    date_from: date,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> tuple[list[dict], list[dict], list[dict]]:
    # (player, team, league player supplement). the supplement failing is not
    # fatal; either primary log failing raises for the caller to count.
    player_raw = _fetch_player_game_logs(season, date_from, season_type)
    time.sleep(delay_seconds)
    team_raw = _fetch_team_game_logs(season, date_from, season_type)

    league_player_raw: list[dict] = []
    try:
        time.sleep(delay_seconds)
        league_player_raw = _fetch_league_player_game_logs(season, date_from, season_type)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "game logs: %s leaguegamelog player supplement failed (%s), "
            "zero-minute appearances may be missed this run", season_type, e,
        )
    return player_raw, team_raw, league_player_raw


def scrape_game_logs(
    conn: psycopg2.extensions.connection,
    season: str = SEASON,
    dry_run: bool = False,
    today: date | None = None,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> None:
    # an empty season is a normal outcome, not an error: between the schedule
    # landing and opening night every phase here no-ops cleanly.
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    windows: list[tuple[str, date, date | None]] = []
    for season_type in season_types_to_fetch(season, today):
        latest = _latest_logged_game_date(conn, season, season_type)
        date_from = game_log_fetch_from(latest, season)
        windows.append((season_type, date_from, latest))
        logger.info(
            "truth layer: syncing %s %s game logs from %s (watermark %s)",
            season, season_type, date_from.isoformat(),
            latest.isoformat() if latest else "none",
        )

    run_id = _start_ingestion_run(
        conn,
        "game_logs_incremental",
        watermark_from=min(window[1] for window in windows).isoformat(),
        dry_run=dry_run,
    )

    player_rows: list[tuple] = []
    team_rows: list[tuple] = []
    failed: list[str] = []
    for index, (season_type, date_from, _) in enumerate(windows):
        if index:
            time.sleep(delay_seconds)
        try:
            player_raw, team_raw, league_player_raw = _fetch_season_type_logs(
                season, season_type, date_from, delay_seconds
            )
        except Exception as e:  # noqa: BLE001 - one season type must not end the run
            failed.append(season_type)
            logger.error("game logs: %s fetch failed (%s)", season_type, e)
            continue

        type_player_rows = [
            row
            for row in (
                build_player_game_log_row(raw, season, run_id) for raw in player_raw
            )
            if row is not None
        ]
        supplements = supplement_player_log_rows(
            type_player_rows, league_player_raw, season, run_id
        )
        if supplements:
            logger.info(
                "game logs: %d %s appearance(s) only leaguegamelog reported "
                "(zero-minute games playergamelogs omits)", len(supplements), season_type,
            )
            type_player_rows.extend(supplements)
        player_rows.extend(type_player_rows)
        team_rows.extend(
            row
            for row in (build_team_game_log_row(raw, season, run_id) for raw in team_raw)
            if row is not None
        )

    if len(failed) == len(windows):
        _finish_ingestion_run(
            conn, run_id, "failed", 0,
            notes=f"every season type failed: {', '.join(failed)}",
        )
        return

    player_rows, player_stray = split_rows_on_season_boundary(
        player_rows, season, PLAYER_LOG_DATE_INDEX
    )
    team_rows, team_stray = split_rows_on_season_boundary(
        team_rows, season, TEAM_LOG_DATE_INDEX
    )
    if player_stray or team_stray:
        logger.warning(
            "game logs: %d player and %d team row(s) fell outside the %s window "
            "(%s..%s) and were DROPPED: the endpoint returned games from another "
            "season",
            len(player_stray), len(team_stray), season,
            season_start_date(season).isoformat(),
            season_end_date(season).isoformat(),
        )

    if not player_rows and not team_rows:
        logger.info("game logs: %s has no new game logs yet, nothing to write", season)

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        written = _batch_upsert(cur, PLAYER_GAME_LOG_UPSERT_SQL, player_rows)
        written += _batch_upsert(cur, TEAM_GAME_LOG_UPSERT_SQL, team_rows)
    finally:
        cur.close()

    previous = max((w[2] for w in windows if w[2] is not None), default=None)
    newest = max((row[PLAYER_LOG_DATE_INDEX] for row in player_rows), default=previous)
    _finish_ingestion_run(
        conn,
        run_id,
        "partial" if failed else "succeeded",
        written,
        watermark_to=newest.isoformat() if newest else None,
        notes=f"failed season types: {', '.join(failed)}" if failed else None,
    )
    logger.info(
        "game logs: %d player row(s), %d team row(s) upserted%s",
        len(player_rows),
        len(team_rows),
        " (dry run: nothing written)" if dry_run else "",
    )

    _sync_player_team_stints(conn, season, dry_run=dry_run)


def scrape_game_status(
    conn: psycopg2.extensions.connection,
    season: str = SEASON,
    dry_run: bool = False,
    since: date | None = None,
    limit: int | None = GAME_STATUS_MAX_GAMES_PER_RUN,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
    run_kind: str = "game_status_incremental",
    box_source: str = BOX_SOURCE_AUTO,
) -> int:
    # one request per game, so the incremental path is bounded twice: to the
    # recent window and to a ceiling per run. A game is selected only if it has
    # no status rows at all, which makes a killed run resumable.
    if since is None:
        since = date.today() - timedelta(days=GAME_STATUS_RECENT_WINDOW_DAYS)

    games = _games_needing_status(conn, season, since, limit)
    if not games:
        logger.info("game status: nothing to do, every recent game has status rows")
        return 0

    logger.info("truth layer: deriving status for %d game(s)", len(games))
    box_source = resolve_box_source(box_source)
    run_id = _start_ingestion_run(
        conn,
        run_kind,
        watermark_from=since.isoformat() if since else None,
        dry_run=dry_run,
    )

    played_by_game = _played_rows_for_games(conn, [gid for gid, _ in games])
    written = 0
    failed = 0
    suspect = 0
    box_failed = 0
    active_dnp = 0

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for index, (game_id, game_date) in enumerate(games):
            try:
                inactive_raw, inactive_src = _fetch_inactive_players(game_id, game_date)
            except Exception as e:  # noqa: BLE001 - one game must not end the phase
                failed += 1
                logger.warning("game status: %s inactive list failed (%s)", game_id, e)
                # almost always throttling, so back off harder before the next
                time.sleep(delay_seconds * 2)
                continue

            if inactive_src == "v2-suspect":
                suspect += 1
                logger.warning(
                    "game status: %s inactive list came from BoxScoreSummaryV2 past its "
                    "data cutoff — rows tagged suspect; the validation report lists them",
                    game_id,
                )

            rows = derive_game_status_rows(
                game_id,
                played_by_game.get(game_id, []),
                inactive_raw,
                f"boxscoresummary{inactive_src}+playergamelogs",
            )
            written += _upsert_game_status_rows(cur, rows, run_id)

            # after the status upsert, so started lands on the rows just written.
            # a failure leaves the game unstamped for --backfill-box-details.
            time.sleep(delay_seconds)
            try:
                box_counts = _apply_box_details(
                    cur,
                    game_id,
                    [r["nba_player_id"] for r in played_by_game.get(game_id, [])],
                    run_id,
                    [r["nba_player_id"] for r in rows],
                    source=box_source,
                )
                active_dnp += box_counts["active_dnp"]
                written += box_counts["active_dnp"]
            except Exception as e:  # noqa: BLE001 - the status rows still stand
                box_failed += 1
                logger.warning("game status: %s box details failed (%s)", game_id, e)

            done = index + 1
            if done % 25 == 0 or done == len(games):
                remaining = len(games) - done
                # two requests per game: the inactive list and the box score
                eta_min = remaining * delay_seconds * 2 / 60
                logger.info(
                    "game status: %d/%d games (%d rows, %d failed, %d suspect, ~%.0f min left)",
                    done, len(games), written, failed, suspect, eta_min,
                )

            if index + 1 < len(games):
                time.sleep(delay_seconds)
    finally:
        cur.close()

    run_notes: list[str] = []
    if failed:
        run_notes.append(f"{failed} game(s) failed")
    if suspect:
        run_notes.append(f"{suspect} game(s) tagged v2-suspect")
    if box_failed:
        run_notes.append(f"{box_failed} game(s) missing box details")
    run_notes.append(f"active_dnp_rows={active_dnp}")
    _finish_ingestion_run(
        conn,
        run_id,
        "succeeded" if failed == 0 else "partial",
        written,
        notes="; ".join(run_notes),
    )
    logger.info(
        "game status: %d row(s) across %d game(s), %d failed, %d suspect%s",
        written,
        len(games) - failed,
        failed,
        suspect,
        " (dry run: nothing written)" if dry_run else "",
    )
    return written


STINT_SNAPSHOT_SOURCES = (ROSTER_SNAPSHOT_SOURCE, ROSTER_WEB_SOURCE)

# serializes stint rebuilds across concurrent backfill jobs on one database
STINT_SYNC_LOCK_KEY = 7_201_302

STINT_INSERT_SQL = """
    INSERT INTO player_team_stints (nba_player_id, team_id, valid_from, valid_to, source)
    VALUES %s
"""


def _sync_player_team_stints(
    conn: psycopg2.extensions.connection, season: str, dry_run: bool = False
) -> None:
    # one locked transaction, so a concurrent rebuild cannot act on a stale read
    previous_autocommit = conn.autocommit
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (STINT_SYNC_LOCK_KEY,))
        cur.execute(
            """
            SELECT DISTINCT nba_player_id
              FROM player_game_logs
             WHERE season = %s AND team_id IS NOT NULL
            """,
            (season,),
        )
        players = sorted(str(row[0]) for row in cur.fetchall())
        if not players:
            conn.commit()
            logger.info("stints: no team changes to record")
            return

        cur.execute(
            """
            SELECT nba_player_id, game_date, team_id, season_type
              FROM player_game_logs
             WHERE nba_player_id = ANY(%s) AND team_id IS NOT NULL
            """,
            (players,),
        )
        appearances: dict[str, list[tuple[date, str, str]]] = {}
        for pid, game_date, team_id, season_type in cur.fetchall():
            appearances.setdefault(str(pid), []).append((game_date, str(team_id), season_type))

        cur.execute(
            """
            SELECT nba_player_id, team_id, valid_from, valid_to, source
              FROM player_team_stints
             WHERE nba_player_id = ANY(%s)
            """,
            (players,),
        )
        existing: dict[str, set[Stint]] = {}
        for pid, team_id, valid_from, valid_to, source in cur.fetchall():
            existing.setdefault(str(pid), set()).add(
                Stint(str(team_id), valid_from, valid_to, source)
            )

        rewrites: dict[str, list[Stint]] = {}
        for player_id in players:
            current = existing.get(player_id, set())
            snapshots = [
                (s.team_id, s.valid_from, s.source)
                for s in current if s.source in STINT_SNAPSHOT_SOURCES
            ]
            derived = derive_stints(
                appearances.get(player_id, []), snapshots, absence_closure(current)
            )
            if set(derived) != current:
                rewrites[player_id] = derived

        write_cur = maybe_write_cursor(conn.cursor(), dry_run)
        try:
            if rewrites:
                write_cur.execute(
                    "DELETE FROM player_team_stints WHERE nba_player_id = ANY(%s)",
                    (sorted(rewrites),),
                )
                _batch_upsert(
                    write_cur,
                    STINT_INSERT_SQL,
                    [
                        (player_id, *stint)
                        for player_id, stints in sorted(rewrites.items())
                        for stint in stints
                    ],
                )
        finally:
            write_cur.close()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.autocommit = previous_autocommit

    if not rewrites:
        logger.info("stints: no team changes to record")
        return
    logger.info(
        "stints: rebuilt %d of %d player(s)%s",
        len(rewrites),
        len(players),
        " (dry run: nothing written)" if dry_run else "",
    )
