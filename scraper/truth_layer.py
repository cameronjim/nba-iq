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
)
from parsing import season_end_date, season_start_date
from rows import (
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
    build_player_game_log_row,
    build_team_game_log_row,
    derive_game_status_rows,
    game_log_fetch_from,
    plan_stint_change,
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
) -> bool:
    # returns False only when every source failed. ESPN is deliberately not a
    # source: its event ids join to no NBA game id, unlike nba.com's.
    logger.info("truth layer: syncing %s schedule...", season)
    run_id = _start_ingestion_run(
        conn, "schedule", watermark_from=season, watermark_to=season, dry_run=dry_run
    )

    rows: list[dict] = []
    try:
        rows = schedule_rows_from_league_schedule(_fetch_league_schedule(season), season)
        logger.info("schedule: %d game(s) from scheduleleaguev2", len(rows))
    except Exception as e:  # noqa: BLE001 - falling back is the handling
        logger.warning("scheduleleaguev2 unavailable (%s); trying nba.com", e)
        rows = fetch_nba_web_schedule_rows(season)
        if rows:
            logger.info("schedule: %d game(s) from nba.com", len(rows))
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

            done = index + 1
            if done % 25 == 0 or done == len(games):
                remaining = len(games) - done
                eta_min = remaining * delay_seconds / 60
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
    _finish_ingestion_run(
        conn,
        run_id,
        "succeeded" if failed == 0 else "partial",
        written,
        notes="; ".join(run_notes) if run_notes else None,
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
