import argparse
import logging
import sys
from typing import Callable

import psycopg2

from backfill import backfill_game_logs, backfill_history, validate_game_logs
from config import (
    BACKFILL_DEFAULT_FROM_SEASON,
    BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON,
    BOX_SOURCE_AUTO,
    BOX_SOURCE_STATS,
    BOX_SOURCE_WEB,
    BOX_SOURCES,
    NBA_2K_DEFAULT_TEAM_TYPES,
    NBA_2K_TEAM_TYPES,
    SEASON,
    WEB_BOX_DETAILS_RUN_LIMIT,
    WEB_GAME_LOG_RUN_LIMIT,
)
# resolve_database_url is re-exported: check_migrations.py imports it, and the
# --dev/--prod rules must not come to mean two things in two files.
from database import TARGET_DEV, TARGET_PROD, get_db, resolve_database_url  # noqa: F401
from parsing import (
    format_processed_line,
    parse_season_types,
    parse_team_types,
    season_range,
    season_start_year,
)
from espn_injuries import scrape_espn_injuries
from fetching import stats_nba_reachable
from injury_reconcile import reconcile_player_injury_columns
from injury_report import scrape_official_injuries
from odds import scrape_odds_snapshots
from props import parse_props_markets, scrape_prop_odds
from ratings_2k import sync_2k_ratings
from roster_snapshot import scrape_roster_snapshot
from scrapes import scrape_injuries, scrape_players, scrape_scoreboard, scrape_teams
from truth_layer import (
    backfill_box_details,
    backfill_game_logs_from_web,
    discover_schedule,
    resolve_box_source,
    scrape_game_logs,
    scrape_game_status,
    scrape_schedule,
)

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
            "run the one-time truth-layer backfill instead of the normal scrape, "
            "regular season, play-in and playoffs; "
            f"honours --from/--to (default {BACKFILL_GAME_LOGS_DEFAULT_FROM_SEASON}). "
            "With --source web (or auto while stats.nba.com is down) it instead "
            "fills --season's scheduled games that have no logs, one nba.com "
            "box-score page per game, honouring --limit; it first runs "
            "--discover-schedule for any preseason or postseason type the "
            "schedule has no game of"
        ),
    )
    parser.add_argument(
        "--discover-schedule",
        dest="discover_schedule",
        action="store_true",
        help=(
            "find --season's preseason, play-in and playoff games on the "
            "nba.com date pages (then box-score pages for ids they missed) and "
            "upsert them into nba_schedule; honours --season-types and --dry-run"
        ),
    )
    parser.add_argument(
        "--season-types",
        dest="season_types",
        default=None,
        help=(
            "with --discover-schedule, comma-separated types to find: "
            "Pre Season, PlayIn, Playoffs (default all three)"
        ),
    )
    parser.add_argument(
        "--source",
        dest="source",
        choices=BOX_SOURCES,
        default=BOX_SOURCE_AUTO,
        help=(
            "box-score source for --backfill-box-details and --backfill-game-logs: "
            "stats (stats.nba.com), web (www.nba.com box-score pages) or auto, "
            "which probes stats.nba.com once and uses web when it fails "
            f"(default {BOX_SOURCE_AUTO})"
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
        "--backfill-box-details",
        dest="backfill_box_details",
        action="store_true",
        help=(
            "fill started, position, oreb/dreb/pf and dnp_reason from one "
            "box score per game for --season, oldest first; resumable, honours "
            "--source, --limit and --dry-run"
        ),
    )
    parser.add_argument(
        "--game-logs-web",
        dest="game_logs_web",
        action="store_true",
        help=(
            "run ONLY the nba.com box-score lane for --season: new game logs, "
            "team logs and status rows, then box details, then the stint sync; "
            f"at most {WEB_GAME_LOG_RUN_LIMIT} games per step unless --limit says otherwise"
        ),
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        help=(
            "with --backfill-box-details, a web --backfill-game-logs or "
            "--game-logs-web, stop "
            "after this many games (default: all remaining). About 300 games "
            "take 30 minutes from stats.nba.com at its 5s delay, about 600 from "
            "the nba.com pages at their 2s delay"
        ),
    )
    parser.add_argument(
        "--injuries-only",
        dest="injuries_only",
        action="store_true",
        help="run ONLY the injury scrapes (CBS, then ESPN, then the official report)",
    )
    parser.add_argument(
        "--espn-injuries-only",
        dest="espn_injuries_only",
        action="store_true",
        help="run ONLY the espn injury feed, skipping CBS and the official report",
    )
    parser.add_argument(
        "--official-injuries-only",
        dest="official_injuries_only",
        action="store_true",
        help="run ONLY the official nba injury report, skipping CBS",
    )
    parser.add_argument(
        "--reconcile-injuries-only",
        dest="reconcile_injuries_only",
        action="store_true",
        help="run ONLY the injury label reconcile over the stored report rows",
    )
    parser.add_argument(
        "--odds-only",
        dest="odds_only",
        action="store_true",
        help=(
            "run ONLY the odds lane: the espn odds snapshot, then the player "
            "prop snapshot when ODDS_API_KEY is set (at most once per 20 hours)"
        ),
    )
    parser.add_argument(
        "--props-only",
        dest="props_only",
        action="store_true",
        help=(
            "run ONLY the player prop snapshot, now, ignoring the 20-hour gap; "
            "spends quota even with --dry-run, since the api calls are reads"
        ),
    )
    parser.add_argument(
        "--props-markets",
        dest="props_markets",
        default=None,
        help=(
            "comma-separated prop markets for this run (pts,reb,ast,fg3m,pra,stl,"
            "blk,tov); default pts,pra. each market costs one api credit per game"
        ),
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


def _injury_phases(conn: psycopg2.extensions.connection, dry_run: bool) -> None:
    # the reconcile runs last and derives the players' labels from every source.
    _run_phase("injuries (cbs)", lambda: scrape_injuries(conn, dry_run=dry_run))
    _run_phase("injuries (espn)", lambda: scrape_espn_injuries(conn, dry_run=dry_run))
    _run_phase(
        "injuries (official)", lambda: scrape_official_injuries(conn, dry_run=dry_run)
    )
    _run_phase(
        "injuries (reconcile)",
        lambda: reconcile_player_injury_columns(conn, dry_run=dry_run),
    )


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
    _injury_phases(conn, dry_run)
    return schedule_ok


def _web_truth_phases(
    conn: psycopg2.extensions.connection,
    season: str,
    dry_run: bool,
    limit: int | None,
) -> None:
    # the nba.com pages answer from ci where stats.nba.com does not. the logs
    # step syncs stints itself whenever a game lands.
    _run_phase(
        "web game logs",
        lambda: backfill_game_logs_from_web(
            conn, season, dry_run=dry_run,
            limit=limit or WEB_GAME_LOG_RUN_LIMIT, discover=False,
        ),
    )
    _run_phase(
        "web box details",
        lambda: backfill_box_details(
            conn, season, dry_run=dry_run,
            limit=limit or WEB_BOX_DETAILS_RUN_LIMIT, source=BOX_SOURCE_WEB,
        ),
    )


def _odds_lane(
    conn: psycopg2.extensions.connection, dry_run: bool, markets: tuple[str, ...]
) -> None:
    # props last and isolated: a provider outage must never cost the espn snapshot.
    scrape_odds_snapshots(conn, dry_run=dry_run)
    _run_phase("prop odds snapshot", lambda: scrape_prop_odds(conn, dry_run=dry_run, markets=markets))


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    truth_from, truth_to = _truth_layer_season_bounds(args)

    try:
        props_markets = parse_props_markets(args.props_markets)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(2)

    try:
        season_start_year(args.season)
        season_types = parse_season_types(args.season_types)
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

    if args.limit is not None and args.limit < 1:
        logger.error("--limit must be at least 1")
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
            source = resolve_box_source(args.source)
            if source == BOX_SOURCE_WEB:
                processed = backfill_game_logs_from_web(
                    conn, args.season, dry_run=args.dry_run, limit=args.limit
                )
                print(format_processed_line(processed), flush=True)
            else:
                backfill_game_logs(conn, truth_from, truth_to, dry_run=args.dry_run)
        elif args.discover_schedule:
            discover_schedule(conn, args.season, season_types, dry_run=args.dry_run)
        elif args.validate_game_logs:
            validate_game_logs(conn, truth_from, truth_to)
        elif args.backfill_box_details:
            # an explicit --source stats still fails fast on a tarpitted ip
            # rather than burning the whole job on per-game retries.
            if args.source == BOX_SOURCE_STATS and not stats_nba_reachable():
                logger.error("stats.nba.com is unreachable: box-detail backfill skipped")
                sys.exit(1)
            processed = backfill_box_details(
                conn, args.season, dry_run=args.dry_run, limit=args.limit,
                source=args.source,
            )
            print(format_processed_line(processed), flush=True)
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
            _injury_phases(conn, args.dry_run)
        elif args.espn_injuries_only:
            scrape_espn_injuries(conn, dry_run=args.dry_run)
        elif args.official_injuries_only:
            scrape_official_injuries(conn, dry_run=args.dry_run)
        elif args.reconcile_injuries_only:
            reconcile_player_injury_columns(conn, dry_run=args.dry_run)
        elif args.game_logs_web:
            _web_truth_phases(conn, args.season, args.dry_run, args.limit)
        elif args.odds_only:
            _odds_lane(conn, args.dry_run, props_markets)
        elif args.props_only:
            scrape_prop_odds(
                conn, dry_run=args.dry_run, min_hours_between_runs=0,
                markets=props_markets,
            )
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
            _injury_phases(conn, args.dry_run)
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
                    # the probe above already resolved auto for this run.
                    lambda: scrape_game_status(
                        conn, args.season, dry_run=args.dry_run,
                        box_source=BOX_SOURCE_STATS,
                    ),
                )
            else:
                _web_truth_phases(conn, args.season, args.dry_run, args.limit)
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
