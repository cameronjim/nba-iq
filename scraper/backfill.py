import logging
import time
from collections.abc import Sequence

import psycopg2

from config import (
    BACKFILL_REQUEST_DELAY_SECONDS,
    NAME_TO_ABBR,
    SEASON_TYPE_REGULAR,
    SEASON_TYPES_INGESTED,
    TRUTH_LAYER_TABLES,
    VALIDATION_MAX_EXAMPLES,
    VALIDATION_POINTS_TOLERANCE,
)
from database import (
    _batch_upsert,
    _finish_ingestion_run,
    _rows,
    _scalar,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import (
    _fetch_league_player_game_logs,
    _fetch_league_schedule,
    _fetch_player_game_logs,
    _fetch_season_player_rows,
    _fetch_season_team_ratings,
    _fetch_season_team_rows,
    _fetch_team_game_logs,
)
from parsing import (
    BOX_SCORE_RULES,
    _opt_float,
    _opt_int,
    _opt_pct,
    season_range,
    season_start_date,
)
from rows import (
    PLAYER_LOG_DATE_INDEX,
    TEAM_LOG_DATE_INDEX,
    build_player_game_log_row,
    build_team_game_log_row,
    ingested_schedule_rows,
    schedule_rows_from_league_schedule,
    schedule_rows_from_team_logs,
    split_rows_on_season_boundary,
    supplement_player_log_rows,
)
from truth_layer import (
    PLAYER_GAME_LOG_UPSERT_SQL,
    TEAM_GAME_LOG_UPSERT_SQL,
    _upsert_schedule_rows,
    scrape_game_status,
)

logger = logging.getLogger(__name__)


def _existing_history_seasons(conn: psycopg2.extensions.connection) -> set[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT season FROM player_season_stats
        UNION
        SELECT season FROM team_season_stats
        """
    )
    seasons = {row[0] for row in cur.fetchall()}
    cur.close()
    return seasons


def _upsert_player_season(
    cur: psycopg2.extensions.cursor, season: str, row: dict
) -> None:
    cur.execute(
        """
        INSERT INTO player_season_stats (nba_player_id, player_name, season, team, games_played,
                                         minutes_per_game, points_per_game, rebounds_per_game,
                                         assists_per_game, steals_per_game, blocks_per_game,
                                         turnovers_per_game, field_goal_percentage,
                                         three_point_percentage, free_throw_percentage,
                                         three_pointers_made)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (nba_player_id, season) DO UPDATE SET
            player_name = EXCLUDED.player_name, team = EXCLUDED.team,
            games_played = EXCLUDED.games_played, minutes_per_game = EXCLUDED.minutes_per_game,
            points_per_game = EXCLUDED.points_per_game, rebounds_per_game = EXCLUDED.rebounds_per_game,
            assists_per_game = EXCLUDED.assists_per_game, steals_per_game = EXCLUDED.steals_per_game,
            blocks_per_game = EXCLUDED.blocks_per_game, turnovers_per_game = EXCLUDED.turnovers_per_game,
            field_goal_percentage = EXCLUDED.field_goal_percentage,
            three_point_percentage = EXCLUDED.three_point_percentage,
            free_throw_percentage = EXCLUDED.free_throw_percentage,
            three_pointers_made = EXCLUDED.three_pointers_made
        """,
        (
            str(row["PLAYER_ID"]),
            row["PLAYER_NAME"],
            season,
            # whatever abbreviation the API reported for that season (SEA, VAN,
            # NJN, ...), never a lookup through the current-team maps.
            row.get("TEAM_ABBREVIATION") or None,
            _opt_int(row.get("GP")),
            _opt_float(row.get("MIN")),
            _opt_float(row.get("PTS")),
            _opt_float(row.get("REB")),
            _opt_float(row.get("AST")),
            _opt_float(row.get("STL")),
            _opt_float(row.get("BLK")),
            _opt_float(row.get("TOV")),
            _opt_pct(row.get("FG_PCT")),
            _opt_pct(row.get("FG3_PCT")),
            _opt_pct(row.get("FT_PCT")),
            _opt_float(row.get("FG3M")),
        ),
    )


def _upsert_team_season(
    cur: psycopg2.extensions.cursor, season: str, row: dict, ratings: dict
) -> None:
    team_name = row.get("TEAM_NAME") or ""
    # NOT _resolve_team_abbr: team ids are permanent across relocations, so
    # resolving by id would label the 1995-96 SuperSonics as OKC.
    abbr = row.get("TEAM_ABBREVIATION") or NAME_TO_ABBR.get(team_name.strip().lower(), "")

    cur.execute(
        """
        INSERT INTO team_season_stats (nba_team_id, team_name, team_abbreviation, season,
                                       games_played, wins, losses,
                                       minutes_per_game, points_per_game, rebounds_per_game,
                                       assists_per_game, steals_per_game, blocks_per_game,
                                       turnovers_per_game, field_goal_percentage,
                                       three_point_percentage, free_throw_percentage,
                                       defensive_rating, offensive_rating, net_rating)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (nba_team_id, season) DO UPDATE SET
            team_name = EXCLUDED.team_name, team_abbreviation = EXCLUDED.team_abbreviation,
            games_played = EXCLUDED.games_played, wins = EXCLUDED.wins, losses = EXCLUDED.losses,
            minutes_per_game = EXCLUDED.minutes_per_game, points_per_game = EXCLUDED.points_per_game,
            rebounds_per_game = EXCLUDED.rebounds_per_game, assists_per_game = EXCLUDED.assists_per_game,
            steals_per_game = EXCLUDED.steals_per_game, blocks_per_game = EXCLUDED.blocks_per_game,
            turnovers_per_game = EXCLUDED.turnovers_per_game,
            field_goal_percentage = EXCLUDED.field_goal_percentage,
            three_point_percentage = EXCLUDED.three_point_percentage,
            free_throw_percentage = EXCLUDED.free_throw_percentage,
            defensive_rating = EXCLUDED.defensive_rating,
            offensive_rating = EXCLUDED.offensive_rating,
            net_rating = EXCLUDED.net_rating
        """,
        (
            str(row["TEAM_ID"]),
            team_name,
            abbr or None,
            season,
            _opt_int(row.get("GP")),
            _opt_int(row.get("W")),
            _opt_int(row.get("L")),
            _opt_float(row.get("MIN")),
            _opt_float(row.get("PTS")),
            _opt_float(row.get("REB")),
            _opt_float(row.get("AST")),
            _opt_float(row.get("STL")),
            _opt_float(row.get("BLK")),
            _opt_float(row.get("TOV")),
            _opt_pct(row.get("FG_PCT")),
            _opt_pct(row.get("FG3_PCT")),
            _opt_pct(row.get("FT_PCT")),
            ratings.get("defensive_rating"),
            ratings.get("offensive_rating"),
            ratings.get("net_rating"),
        ),
    )


def backfill_season(
    conn: psycopg2.extensions.connection, season: str
) -> tuple[int, int]:
    # an empty response means the NBA API has no data that far back, which is
    # reported as 0 rows rather than raised.
    player_rows = _fetch_season_player_rows(season)
    time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
    team_rows = _fetch_season_team_rows(season)
    time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
    ratings = _fetch_season_team_ratings(season)

    cur = conn.cursor()
    try:
        for row in player_rows:
            _upsert_player_season(cur, season, row)
        for row in team_rows:
            _upsert_team_season(cur, season, row, ratings.get(str(row["TEAM_ID"]), {}))
    finally:
        cur.close()

    return len(player_rows), len(team_rows)


def backfill_history(
    conn: psycopg2.extensions.connection,
    from_season: str,
    to_season: str,
) -> None:
    # opt-in only and never part of the cron: stats.nba.com is Akamai-blocked
    # from CI and throttles residential IPs, so this crawls deliberately and
    # tolerates per-season failure. Resumable: stored seasons are skipped.
    seasons = season_range(from_season, to_season)
    already_present = _existing_history_seasons(conn)

    logger.info(
        "backfilling %d candidate seasons (%s → %s), %d already present",
        len(seasons), seasons[0], seasons[-1], len(already_present),
    )

    written: list[str] = []
    empty: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for season in seasons:
        if season in already_present:
            skipped.append(season)
            logger.info("%s: already in database, skipping", season)
            continue

        try:
            players, teams = backfill_season(conn, season)
        except Exception as e:
            failed.append(season)
            logger.error("%s: failed, moving on (%s)", season, e)
            # a failure here is almost always throttling
            time.sleep(BACKFILL_REQUEST_DELAY_SECONDS * 4)
            continue

        if players == 0 and teams == 0:
            empty.append(season)
            logger.info("%s: no data returned by the NBA API", season)
        else:
            written.append(season)
            logger.info("%s: wrote %d players, %d teams", season, players, teams)

        time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)

    logger.info(
        "backfill summary: %d written, %d empty (no data), %d skipped, %d failed",
        len(written), len(empty), len(skipped), len(failed),
    )
    if written:
        logger.info("written: %s", ", ".join(written))
    if empty:
        logger.info("empty: %s", ", ".join(empty))
    if failed:
        logger.info("failed (re-run to retry): %s", ", ".join(failed))


def backfill_game_logs_season(
    conn: psycopg2.extensions.connection,
    season: str,
    dry_run: bool = False,
    delay_seconds: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> dict:
    # ordered cheapest-first: the league-wide calls cost one request each, only
    # the inactive lists cost one per game, and by then the schedule is already
    # banked. A killed run resumes because games with status rows are skipped.
    team_raw: list[dict] = []
    player_raw: list[dict] = []
    league_player_raw: list[dict] = []
    for index, season_type in enumerate(SEASON_TYPES_INGESTED):
        if index:
            time.sleep(delay_seconds)
        logger.info("%s: fetching %s team game logs...", season, season_type)
        team_raw.extend(_fetch_team_game_logs(season, None, season_type))
        time.sleep(delay_seconds)
        logger.info("%s: fetching %s player game logs...", season, season_type)
        player_raw.extend(_fetch_player_game_logs(season, None, season_type))

        try:
            time.sleep(delay_seconds)
            logger.info(
                "%s: fetching %s league player game logs (supplement)...",
                season, season_type,
            )
            league_player_raw.extend(
                _fetch_league_player_game_logs(season, None, season_type)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "%s: %s leaguegamelog player supplement failed (%s), zero-minute "
                "appearances may be missed", season, season_type, e,
            )

    # scheduleleaguev2 serves historical seasons too and, unlike the team game
    # log, knows the real home/away designation of neutral-site games.
    schedule_rows: list[dict] = []
    try:
        time.sleep(delay_seconds)
        schedule_rows = ingested_schedule_rows(
            schedule_rows_from_league_schedule(_fetch_league_schedule(season), season)
        )
        logger.info("%s: %d schedule row(s) from scheduleleaguev2", season, len(schedule_rows))
    except Exception as e:  # noqa: BLE001 - falling back is the handling
        logger.warning(
            "%s: scheduleleaguev2 unavailable (%s); reconstructing the schedule "
            "from the team game log", season, e,
        )
        schedule_rows = schedule_rows_from_team_logs(team_raw, season)

    run_id = _start_ingestion_run(
        conn,
        "game_logs_backfill",
        watermark_from=season,
        watermark_to=season,
        dry_run=dry_run,
    )

    player_rows = [
        row
        for row in (build_player_game_log_row(raw, season, run_id) for raw in player_raw)
        if row is not None
    ]
    supplements = supplement_player_log_rows(player_rows, league_player_raw, season, run_id)
    if supplements:
        logger.info(
            "%s: %d appearance(s) only leaguegamelog reported "
            "(zero-minute games playergamelogs omits)", season, len(supplements),
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
            "%s: %d player and %d team log row(s) fell outside the season window "
            "and were DROPPED", season, len(player_stray), len(team_stray),
        )

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        schedule_written = _upsert_schedule_rows(cur, schedule_rows)
        log_written = _batch_upsert(cur, PLAYER_GAME_LOG_UPSERT_SQL, player_rows)
        log_written += _batch_upsert(cur, TEAM_GAME_LOG_UPSERT_SQL, team_rows)
    finally:
        cur.close()

    logger.info(
        "%s: %d schedule row(s), %d player log(s), %d team log(s)",
        season, schedule_written, len(player_rows), len(team_rows),
    )
    _finish_ingestion_run(
        conn, run_id, "succeeded", schedule_written + log_written
    )

    # unbounded window and no per-run ceiling: the backfill's job is every game
    # the season has.
    status_written = scrape_game_status(
        conn,
        season=season,
        dry_run=dry_run,
        since=season_start_date(season),
        limit=None,
        delay_seconds=delay_seconds,
        run_kind="game_status_backfill",
    )

    return {
        "schedule": schedule_written,
        "player_logs": len(player_rows),
        "team_logs": len(team_rows),
        "status": status_written,
    }


def backfill_game_logs(
    conn: psycopg2.extensions.connection,
    from_season: str,
    to_season: str,
    dry_run: bool = False,
) -> None:
    # budget roughly 1,230 games per season at BACKFILL_REQUEST_DELAY_SECONDS
    # apiece for the inactive-list phase.
    seasons = season_range(from_season, to_season)
    logger.info(
        "truth-layer backfill: %d season(s), %s -> %s%s",
        len(seasons), seasons[0], seasons[-1], " (dry run)" if dry_run else "",
    )

    succeeded: list[str] = []
    failed: list[str] = []

    for season in seasons:
        try:
            counts = backfill_game_logs_season(conn, season, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001 - one season must not end the run
            failed.append(season)
            logger.error("%s: failed, moving on (%s)", season, e)
            time.sleep(BACKFILL_REQUEST_DELAY_SECONDS * 4)
            continue

        succeeded.append(season)
        logger.info(
            "%s: done — %d schedule, %d player logs, %d team logs, %d status",
            season, counts["schedule"], counts["player_logs"],
            counts["team_logs"], counts["status"],
        )

    logger.info(
        "truth-layer backfill summary: %d succeeded, %d failed",
        len(succeeded), len(failed),
    )
    if failed:
        logger.info("failed (re-run to retry): %s", ", ".join(failed))


# nba_schedule.postponed_status holds the NBA's postponedStatus, where 'N' means
# NOT postponed and is present on every future row: filtering on IS NULL alone
# would drop the entire future schedule.
NOT_POSTPONED_PREDICATE = "(s.postponed_status IS NULL OR s.postponed_status = 'N')"


def _report_examples(label: str, rows: Sequence[tuple]) -> None:
    if not rows:
        logger.info("    %-42s OK", label)
        return
    logger.warning("    %-42s %d offender(s)", label, len(rows))
    for row in rows[:VALIDATION_MAX_EXAMPLES]:
        logger.warning("        %s", ", ".join(str(v) for v in row))
    if len(rows) > VALIDATION_MAX_EXAMPLES:
        logger.warning("        ... and %d more", len(rows) - VALIDATION_MAX_EXAMPLES)


def validate_game_logs(
    conn: psycopg2.extensions.connection, from_season: str, to_season: str
) -> None:
    # writes nothing and takes no locks, so it is safe against prod mid-scrape.
    seasons = season_range(from_season, to_season)
    logger.info("truth-layer validation: %s -> %s", seasons[-1], seasons[0])

    for season in seasons:
        games = _scalar(
            conn,
            "SELECT COUNT(DISTINCT nba_game_id) FROM team_game_logs WHERE season = %s",
            (season,),
        )
        games_by_type = _rows(
            conn,
            """
            SELECT season_type, COUNT(DISTINCT nba_game_id)
              FROM team_game_logs
             WHERE season = %s
             GROUP BY season_type
             ORDER BY season_type
            """,
            (season,),
        )
        player_logs = _scalar(
            conn, "SELECT COUNT(*) FROM player_game_logs WHERE season = %s", (season,)
        )
        status_rows = _scalar(
            conn,
            """
            SELECT COUNT(*) FROM player_game_status s
             WHERE EXISTS (SELECT 1 FROM team_game_logs t
                            WHERE t.nba_game_id = s.nba_game_id AND t.season = %s)
            """,
            (season,),
        )

        logger.info("")
        logger.info(
            "%s: %s completed game(s), %s player log(s), %s status row(s)",
            season, games, player_logs, status_rows,
        )
        if not games:
            logger.info("    (nothing stored for this season)")
            continue
        logger.info(
            "    games by season type: %s",
            ", ".join(f"{season_type} {count}" for season_type, count in games_by_type),
        )

        _report_examples(
            "team rows per game == 2",
            _rows(
                conn,
                """
                SELECT nba_game_id, COUNT(*) FROM team_game_logs
                 WHERE season = %s GROUP BY nba_game_id HAVING COUNT(*) <> 2
                 ORDER BY nba_game_id
                """,
                (season,),
            ),
        )
        # a team-game the schedule row doesn't name is invisible to every
        # downstream join on (game, team).
        _report_examples(
            "every team log side is named on its schedule row",
            _rows(
                conn,
                """
                SELECT t.nba_game_id, t.game_date, t.team_abbr,
                       s.home_team_abbr, s.away_team_abbr
                  FROM team_game_logs t
                  JOIN nba_schedule s ON s.nba_game_id = t.nba_game_id
                 WHERE t.season = %s
                   AND t.team_id IS DISTINCT FROM s.home_team_id
                   AND t.team_id IS DISTINCT FROM s.away_team_id
                 ORDER BY t.game_date, t.nba_game_id
                """,
                (season,),
            ),
        )
        # the UNIQUE constraint makes this impossible; checked anyway, because a
        # constraint that was never applied to this database is not a constraint.
        _report_examples(
            "no duplicate (player, game) keys",
            _rows(
                conn,
                """
                SELECT nba_player_id, nba_game_id, COUNT(*) FROM player_game_logs
                 WHERE season = %s GROUP BY nba_player_id, nba_game_id HAVING COUNT(*) > 1
                """,
                (season,),
            ),
        )
        # fix for these: delete the games' status rows and re-run the backfill
        # once v3 answers for them.
        _report_examples(
            "no status rows sourced from v2 past its data cutoff (v2-suspect)",
            _rows(
                conn,
                """
                SELECT DISTINCT s.nba_game_id, s.source FROM player_game_status s
                 WHERE s.source LIKE '%%v2-suspect%%'
                   AND EXISTS (SELECT 1 FROM team_game_logs t
                                WHERE t.nba_game_id = s.nba_game_id AND t.season = %s)
                 ORDER BY s.nba_game_id
                """,
                (season,),
            ),
        )
        # a game where nobody was listed inactive is possible but rare; a cluster
        # of them is the signature of an empty-because-no-data source.
        _report_examples(
            "games whose status rows list zero inactive players (verify if many)",
            _rows(
                conn,
                """
                SELECT s.nba_game_id,
                       COUNT(*) FILTER (WHERE s.listed_inactive) AS inactives
                  FROM player_game_status s
                 WHERE EXISTS (SELECT 1 FROM team_game_logs t
                                WHERE t.nba_game_id = s.nba_game_id AND t.season = %s)
                 GROUP BY s.nba_game_id
                HAVING COUNT(*) FILTER (WHERE s.listed_inactive) = 0
                 ORDER BY s.nba_game_id
                """,
                (season,),
            ),
        )

        for name, left, right in BOX_SCORE_RULES:
            _report_examples(
                f"{left} <= {right}",
                _rows(
                    conn,
                    f"""
                    SELECT nba_player_id, nba_game_id, {left}, {right}
                      FROM player_game_logs
                     WHERE season = %s AND {left} IS NOT NULL AND {right} IS NOT NULL
                       AND {left} > {right}
                     ORDER BY nba_game_id
                    """,
                    (season,),
                ),
            )

        _report_examples(
            f"player pts sum == team pts (+/-{VALIDATION_POINTS_TOLERANCE})",
            _rows(
                conn,
                """
                SELECT t.nba_game_id, t.team_id, t.pts, COALESCE(p.player_pts, 0)
                  FROM team_game_logs t
                  LEFT JOIN (
                        SELECT nba_game_id, team_id, SUM(pts) AS player_pts
                          FROM player_game_logs
                         WHERE season = %s
                         GROUP BY nba_game_id, team_id
                       ) p
                    ON p.nba_game_id = t.nba_game_id AND p.team_id = t.team_id
                 WHERE t.season = %s AND t.pts IS NOT NULL
                   AND ABS(t.pts - COALESCE(p.player_pts, 0)) > %s
                 ORDER BY t.nba_game_id
                """,
                (season, season, VALIDATION_POINTS_TOLERANCE),
            ),
        )
        # regular season only: a scheduled "if necessary" playoff game that was
        # never played is not a missing log.
        _report_examples(
            "completed regular-season schedule games have logs",
            _rows(
                conn,
                f"""
                SELECT s.nba_game_id, s.game_date, s.home_team_abbr, s.away_team_abbr
                  FROM nba_schedule s
                 WHERE s.season = %s
                   AND s.season_type = %s
                   AND s.game_date < CURRENT_DATE
                   AND {NOT_POSTPONED_PREDICATE}
                   AND NOT EXISTS (SELECT 1 FROM team_game_logs t
                                    WHERE t.nba_game_id = s.nba_game_id)
                 ORDER BY s.game_date
                """,
                (season, SEASON_TYPE_REGULAR),
            ),
        )
        _report_examples(
            "completed games have status rows",
            _rows(
                conn,
                """
                SELECT DISTINCT t.nba_game_id, t.game_date
                  FROM team_game_logs t
                 WHERE t.season = %s
                   AND NOT EXISTS (SELECT 1 FROM player_game_status s
                                    WHERE s.nba_game_id = t.nba_game_id)
                 ORDER BY t.game_date
                """,
                (season,),
            ),
        )

    logger.info("")
    logger.info("storage:")
    for table in TRUTH_LAYER_TABLES:
        # to_regclass returns NULL instead of erroring when migration 013 has not
        # been applied to whichever database this is pointed at.
        size = _scalar(
            conn,
            "SELECT pg_size_pretty(pg_total_relation_size(to_regclass(%s)))",
            (table,),
        )
        logger.info("    %-24s %s", table, size or "not present")
