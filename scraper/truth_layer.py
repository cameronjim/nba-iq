import logging
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    BACKFILL_REQUEST_DELAY_SECONDS,
    GAME_STATUS_MAX_GAMES_PER_RUN,
    GAME_STATUS_RECENT_WINDOW_DAYS,
    NBA_WEB_PAGE_DELAY_SECONDS,
    NBA_WEB_SCHEDULE_DAYS_AHEAD,
    NBA_WEB_SCHEDULE_DAYS_BACK,
    SEASON,
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
)
from parsing import season_end_date, season_start_date
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
    plan_stint_change,
    player_ids_absent_from_box,
    schedule_rows_from_league_schedule,
    schedule_rows_from_nba_web,
    schedule_rows_from_team_logs,
    split_rows_on_season_boundary,
    stint_is_newer_than_game_log,
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
    conn: psycopg2.extensions.connection, season: str
) -> date | None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT MAX(game_date) FROM player_game_logs WHERE season = %s", (season,)
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


def _apply_box_details(
    cur: object,
    game_id: str,
    logged_player_ids: Sequence[str],
    run_id: int | None = None,
    pending_status_ids: Sequence[str] = (),
) -> dict[str, int]:
    # raises on a fetch failure or an empty box score, so the caller can count
    # the game as failed and leave it unstamped for the next run.
    payload = fetch_box_score_traditional(game_id)
    player_rows, team_rows = box_detail_rows_from_traditional(payload, game_id)
    if not player_rows:
        raise ValueError(f"{game_id}: box score has no player rows")

    player_tuples = [
        (
            r["nba_player_id"], r["nba_game_id"], r["started"], r["position"],
            r["oreb"], r["dreb"], r["pf"], r["dnp_reason"], BOX_DETAILS_SOURCE,
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
        player_rows, _existing_status_keys(cur, game_id, pending_status_ids), game_id
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
            (f"{BOX_DETAILS_SOURCE}:absent", game_id, absent),
        )
    return counts


def backfill_box_details(
    conn: psycopg2.extensions.connection,
    season: str,
    dry_run: bool = False,
    limit: int | None = None,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> int:
    # one request per game, oldest first. resumable: a game is selected only
    # while some player row still has details_fetched_at NULL, so a killed or
    # bounded run picks up where the last one stopped.
    games = _games_needing_box_details(conn, season, limit)
    if not games:
        logger.info("box details: nothing to do for %s", season)
        return 0

    logger.info("box details: %d game(s) to fetch for %s", len(games), season)
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
                counts = _apply_box_details(cur, game_id, logged_ids, run_id)
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
        f"{totals['absent']} logged player(s) absent from v3; "
        f"active_dnp_rows={totals['active_dnp']}"
    )
    _finish_ingestion_run(
        conn, run_id, "succeeded" if failed == 0 else "partial", written, notes=notes
    )
    logger.info(
        "box details: %s%s", notes, " (dry run: nothing written)" if dry_run else ""
    )
    return written


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
                team_rows = _fetch_team_game_logs(season, None)
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


def scrape_game_logs(
    conn: psycopg2.extensions.connection,
    season: str = SEASON,
    dry_run: bool = False,
) -> None:
    # an empty season is a normal outcome, not an error: between the schedule
    # landing and opening night every phase here no-ops cleanly.
    latest = _latest_logged_game_date(conn, season)
    date_from = game_log_fetch_from(latest, season)
    logger.info(
        "truth layer: syncing %s game logs from %s (watermark %s)",
        season,
        date_from.isoformat(),
        latest.isoformat() if latest else "none",
    )

    run_id = _start_ingestion_run(
        conn,
        "game_logs_incremental",
        watermark_from=date_from.isoformat(),
        dry_run=dry_run,
    )

    try:
        player_raw = _fetch_player_game_logs(season, date_from)
        time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
        team_raw = _fetch_team_game_logs(season, date_from)
    except Exception as e:  # noqa: BLE001 - one phase failing must not end the run
        logger.error("game logs: fetch failed (%s)", e)
        _finish_ingestion_run(conn, run_id, "failed", 0, notes=str(e)[:500])
        return

    league_player_raw: list[dict] = []
    try:
        time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
        league_player_raw = _fetch_league_player_game_logs(season, date_from)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "game logs: leaguegamelog player supplement failed (%s) — "
            "zero-minute appearances may be missed this run", e,
        )

    player_rows = [
        row
        for row in (build_player_game_log_row(raw, season, run_id) for raw in player_raw)
        if row is not None
    ]
    supplements = supplement_player_log_rows(player_rows, league_player_raw, season, run_id)
    if supplements:
        logger.info(
            "game logs: %d appearance(s) only leaguegamelog reported "
            "(zero-minute games playergamelogs omits)", len(supplements),
        )
        player_rows.extend(supplements)
    team_rows = [
        row
        for row in (build_team_game_log_row(raw, season, run_id) for raw in team_raw)
        if row is not None
    ]

    player_rows, player_stray = split_rows_on_season_boundary(
        player_rows, season, PLAYER_LOG_DATE_INDEX
    )
    team_rows, team_stray = split_rows_on_season_boundary(
        team_rows, season, TEAM_LOG_DATE_INDEX
    )
    if player_stray or team_stray:
        logger.warning(
            "game logs: %d player and %d team row(s) fell outside the %s window "
            "(%s..%s) and were DROPPED — the endpoint returned games from another "
            "season",
            len(player_stray), len(team_stray), season,
            season_start_date(season).isoformat(),
            season_end_date(season).isoformat(),
        )

    if not player_rows and not team_rows:
        logger.info(
            "game logs: %s has no game logs at or after %s yet — nothing to write",
            season, date_from.isoformat(),
        )

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        written = _batch_upsert(cur, PLAYER_GAME_LOG_UPSERT_SQL, player_rows)
        written += _batch_upsert(cur, TEAM_GAME_LOG_UPSERT_SQL, team_rows)
    finally:
        cur.close()

    newest = max((row[PLAYER_LOG_DATE_INDEX] for row in player_rows), default=latest)
    _finish_ingestion_run(
        conn,
        run_id,
        "succeeded",
        written,
        watermark_to=newest.isoformat() if newest else None,
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


def _stint_boundaries(
    conn: psycopg2.extensions.connection,
    player_id: str,
    new_team_id: str,
    open_stint: tuple[str, date] | None,
) -> dict:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT MIN(game_date) FROM player_game_logs
             WHERE nba_player_id = %s AND team_id = %s AND game_date >= %s
            """,
            (player_id, new_team_id, open_stint[1] if open_stint else date.min),
        )
        row = cur.fetchone()
        first_with_new = row[0] if row and row[0] else date.today()

        last_with_open: date | None = None
        if open_stint is not None:
            cur.execute(
                """
                SELECT MAX(game_date) FROM player_game_logs
                 WHERE nba_player_id = %s AND team_id = %s AND game_date >= %s
                """,
                (player_id, open_stint[0], open_stint[1]),
            )
            row = cur.fetchone()
            last_with_open = row[0] if row else None

        return {
            "first_with_new_team": first_with_new,
            "last_with_open_team": last_with_open,
        }
    finally:
        cur.close()


def _sync_player_team_stints(
    conn: psycopg2.extensions.connection, season: str, dry_run: bool = False
) -> None:
    # a season with no game logs yet yields no changes, which is why this can be
    # called unconditionally during the preseason. What it cannot do then is
    # notice an offseason trade; that is what the roster snapshot is for.
    cur = conn.cursor()
    try:
        # DISTINCT ON gives the newest game-log row per player, which is the team
        # he currently belongs to as far as the truth layer can observe.
        cur.execute(
            """
            SELECT DISTINCT ON (nba_player_id)
                   nba_player_id, team_id, game_date
              FROM player_game_logs
             WHERE season = %s AND team_id IS NOT NULL
             ORDER BY nba_player_id, game_date DESC, nba_game_id DESC
            """,
            (season,),
        )
        latest_by_player = {
            str(pid): (str(team_id), game_date) for pid, team_id, game_date in cur.fetchall()
        }

        cur.execute(
            """
            SELECT nba_player_id, team_id, valid_from
              FROM player_team_stints
             WHERE valid_to IS NULL
            """
        )
        open_by_player = {
            str(pid): (str(team_id), valid_from) for pid, team_id, valid_from in cur.fetchall()
        }
    finally:
        cur.close()

    changes: list[tuple[str, dict]] = []
    for player_id, (team_id, latest_date) in latest_by_player.items():
        open_stint = open_by_player.get(player_id)
        if open_stint is not None and open_stint[0] == team_id:
            continue
        if stint_is_newer_than_game_log(open_stint, latest_date):
            continue

        boundaries = _stint_boundaries(conn, player_id, team_id, open_stint)
        change = plan_stint_change(
            open_stint,
            team_id,
            boundaries["first_with_new_team"],
            boundaries["last_with_open_team"],
        )
        if change is not None:
            changes.append((player_id, change))

    if not changes:
        logger.info("stints: no team changes to record")
        return

    write_cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for player_id, change in changes:
            if change["close_team_id"] is not None:
                write_cur.execute(
                    """
                    UPDATE player_team_stints
                       SET valid_to = %s, updated_at = NOW()
                     WHERE nba_player_id = %s AND team_id = %s
                       AND valid_from = %s AND valid_to IS NULL
                    """,
                    (
                        change["close_valid_to"],
                        player_id,
                        change["close_team_id"],
                        change["close_valid_from"],
                    ),
                )
            write_cur.execute(
                """
                INSERT INTO player_team_stints (nba_player_id, team_id, valid_from, source)
                VALUES (%s, %s, %s, 'playergamelogs')
                ON CONFLICT (nba_player_id, team_id, valid_from) DO NOTHING
                """,
                (player_id, change["open_team_id"], change["open_valid_from"]),
            )
    finally:
        write_cur.close()

    logger.info(
        "stints: recorded %d team change(s)%s",
        len(changes),
        " (dry run: nothing written)" if dry_run else "",
    )
