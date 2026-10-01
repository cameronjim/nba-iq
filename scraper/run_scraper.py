import argparse
import logging
import sys
from typing import Callable

import psycopg2

from backfill import backfill_game_logs, backfill_history, validate_game_logs
from config import (
    BACKFILL_DEFAULT_FROM_SEASON,
    BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON,
    NBA_2K_DEFAULT_TEAM_TYPES,
    NBA_2K_TEAM_TYPES,
    SEASON,
)
# resolve_database_url is re-exported: check_migrations.py imports it, and the
# --dev/--prod rules must not come to mean two things in two files.
from database import TARGET_DEV, TARGET_PROD, get_db, resolve_database_url  # noqa: F401
from parsing import parse_team_types, season_range, season_start_year
from fetching import stats_nba_reachable
from odds import scrape_odds_snapshots
from ratings_2k import sync_2k_ratings
from roster_snapshot import scrape_roster_snapshot
from scrapes import scrape_injuries, scrape_players, scrape_scoreboard, scrape_teams
from truth_layer import scrape_game_logs, scrape_game_status, scrape_schedule

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NBA stats scraper")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--dev",
        dest="target",
        action="store_const",
        const=TARGET_DEV,
        help="write to the dev Neon branch (uses DATABASE_URL_DEV)",
    )
    target.add_argument(
        "--prod",
        dest="target",
        action="store_const",
        const=TARGET_PROD,
        help="write to the prod database (uses DATABASE_URL, the default)",
    )
    parser.set_defaults(target=TARGET_PROD)
    parser.add_argument(
        "--backfill-history",
        action="store_true",
        help="run the one-time historical season backfill instead of the normal scrape",
    )
    parser.add_argument(
        "--from",
        dest="from_season",
        default=BACKFILL_DEFAULT_FROM_SEASON,
        help=f"oldest season to attempt, e.g. 1979-80 (default {BACKFILL_DEFAULT_FROM_SEASON})",
    )
    parser.add_argument(
        "--to",
        dest="to_season",
        default=SEASON,
        help=f"newest season to attempt, e.g. 2025-26 (default {SEASON})",
    )
    parser.add_argument(
        "--sync-2k",
        dest="sync_2k",
        action="store_true",
        help="sync NBA 2K ratings instead of the normal scrape",
    )
    parser.add_argument(
        "--team-types",
        dest="team_types",
        default=NBA_2K_DEFAULT_TEAM_TYPES,
        help=(
            "comma-separated 2K roster types to sync: "
            f"{', '.join(NBA_2K_TEAM_TYPES)} (default {NBA_2K_DEFAULT_TEAM_TYPES})"
        ),
    )
    parser.add_argument(
        "--backfill-game-logs",
        dest="backfill_game_logs",
        action="store_true",
        help=(
            "run the one-time truth-layer backfill instead of the normal scrape; "
            f"honours --from/--to (default {BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON})"
        ),
    )
    parser.add_argument(
        "--validate-game-logs",
        dest="validate_game_logs",
        action="store_true",
        help="print a read-only truth-layer integrity report and exit",
    )
    parser.add_argument(
        "--sync-truth",
        dest="sync_truth",
        action="store_true",
        help="run ONLY the truth-layer phases for --season",
    )
    parser.add_argument(
        "--roster-snapshot",
        dest="roster_snapshot",
        action="store_true",
        help="write today's (player, team) assignments into player_team_stints",
    )
    parser.add_argument(
        "--snapshot-out",
        dest="snapshot_out",
        default=None,
        help="with --roster-snapshot, also write the assignments to this csv",
    )
    parser.add_argument(
        "--season",
        dest="season",
        default=SEASON,
        help=(
            f"season the truth-layer phases operate on, e.g. 2026-27 "
            f"(default {SEASON})"
        ),
    )
    parser.add_argument(
        "--injuries-only",
        dest="injuries_only",
        action="store_true",
        help="run ONLY the injury-report scrape, skipping every other phase",
    )
    parser.add_argument(
        "--odds-only",
        dest="odds_only",
        action="store_true",
        help="run ONLY the espn odds snapshot, skipping every other phase",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="log what would be written and write nothing (reads still run)",
    )
    return parser.parse_args(argv)


def _truth_layer_season_bounds(args: argparse.Namespace) -> tuple[str, str]:
    # --from/--to are shared with --backfill-history, whose default reaches back
    # to 1979-80; honouring that here would ask for 45 seasons of inactive lists.
    from_season = args.from_season
    if from_season == BACKFILL_DEFAULT_FROM_SEASON:
        from_season = BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON
    return from_season, args.to_season


def _run_phase(name: str, phase: Callable[[], object]) -> bool:
    # each phase is independent: an outage during the game-log sync must not
    # cost the injury scrape that would have run after it. a phase that returns
    # False reports a failure it handled itself.
    try:
        return phase() is not False
    except Exception as e:  # noqa: BLE001 - independence is the whole point
        logger.error("%s failed, continuing (%s)", name, e)
        return False


def _truth_layer_phases(
    conn: psycopg2.extensions.connection, season: str, dry_run: bool
) -> bool:
    # dependency order. The injury report is not season-scoped: it is here
    # because player_injury_reports is a truth-layer table. returns whether the
    # schedule sync succeeded; the other phases are not fatal.
    schedule_ok = _run_phase(
        "schedule", lambda: scrape_schedule(conn, season, dry_run=dry_run)
    )
    _run_phase("game logs", lambda: scrape_game_logs(conn, season, dry_run=dry_run))
    _run_phase(
        "game status", lambda: scrape_game_status(conn, season, dry_run=dry_run)
    )
    _run_phase("injuries", lambda: scrape_injuries(conn, dry_run=dry_run))
    return schedule_ok


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    truth_from, truth_to = _truth_layer_season_bounds(args)

    try:
        season_start_year(args.season)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(2)

    if args.backfill_history:
        try:
            season_range(args.from_season, args.to_season)
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(2)

    if args.backfill_game_logs or args.validate_game_logs:
        try:
            season_range(truth_from, truth_to)
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(2)

    team_types: list[str] = []
    if args.sync_2k:
        try:
            team_types = parse_team_types(args.team_types)
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(2)

    if args.dry_run:
        logger.info("--dry-run: reads will run, writes will be counted and skipped")

    schedule_ok = True
    conn = get_db(args.target)
    try:
        if args.backfill_history:
            backfill_history(conn, args.from_season, args.to_season)
        elif args.backfill_game_logs:
            backfill_game_logs(conn, truth_from, truth_to, dry_run=args.dry_run)
        elif args.validate_game_logs:
            validate_game_logs(conn, truth_from, truth_to)
        elif args.sync_2k:
            sync_2k_ratings(conn, team_types)
        elif args.roster_snapshot:
            scrape_roster_snapshot(
                conn, args.season, dry_run=args.dry_run,
                snapshot_out=args.snapshot_out,
            )
        elif args.sync_truth:
            schedule_ok = _truth_layer_phases(conn, args.season, args.dry_run)
        elif args.injuries_only:
            scrape_injuries(conn, dry_run=args.dry_run)
        elif args.odds_only:
            scrape_odds_snapshots(conn, dry_run=args.dry_run)
        else:
            stats_reachable = stats_nba_reachable()
            if not stats_reachable:
                logger.warning(
                    "stats.nba.com is unreachable: skipping its phases, using nba.com"
                )
            scrape_players(
                conn, dry_run=args.dry_run, stats_reachable=stats_reachable
            )
            if stats_reachable:
                scrape_teams(conn, dry_run=args.dry_run)
            scrape_scoreboard(conn, dry_run=args.dry_run)
            scrape_injuries(conn, dry_run=args.dry_run)
            # truth layer runs last: the four scrapes above back user-visible
            # pages that must not be held hostage to it.
            # offseason trades, signings and rookies must land before predictions.
            _run_phase(
                "roster snapshot",
                lambda: scrape_roster_snapshot(
                    conn, args.season, dry_run=args.dry_run,
                    stats_reachable=stats_reachable,
                ),
            )
            schedule_ok = _run_phase(
                "schedule",
                lambda: scrape_schedule(
                    conn, args.season, dry_run=args.dry_run,
                    stats_reachable=stats_reachable,
                ),
            )
            if stats_reachable:
                _run_phase(
                    "game logs",
                    lambda: scrape_game_logs(conn, args.season, dry_run=args.dry_run),
                )
                _run_phase(
                    "game status",
                    lambda: scrape_game_status(conn, args.season, dry_run=args.dry_run),
                )
            # after the schedule sync, so new games map to an nba game id.
            _run_phase(
                "odds snapshot",
                lambda: scrape_odds_snapshots(conn, dry_run=args.dry_run),
            )
    finally:
        conn.close()

    logger.info("all done!")
    if not schedule_ok:
        # the one fatal phase: without a schedule the prediction cron has no games,
        # and a red workflow is the alert.
        logger.error("schedule sync failed from every source")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
