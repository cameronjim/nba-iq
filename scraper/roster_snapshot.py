import csv
import logging
import time
from collections.abc import Mapping
from datetime import date

import psycopg2

from config import (
    ROSTER_SNAPSHOT_REQUEST_DELAY_SECONDS,
    ROSTER_SNAPSHOT_SOURCE,
    ROSTER_WEB_SOURCE,
    SEASON,
    TEAM_ID_TO_ABBR,
)
from database import (
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import _fetch_nba_web_players, _fetch_team_roster
from parsing import season_label, season_start_year
from rows import plan_roster_snapshot, roster_rows_from_nba_players_index

logger = logging.getLogger(__name__)

ROSTER_MAX_CONSECUTIVE_FAILURES = 2
ROSTER_FETCH_ATTEMPTS = 1
ROSTER_FETCH_TIMEOUT_SECONDS = 30


def fetch_roster_snapshot(
    season: str, delay_seconds: float = ROSTER_SNAPSHOT_REQUEST_DELAY_SECONDS
) -> tuple[dict[str, str], list[str]]:
    # a per-team failure is reported rather than raised: plan_roster_snapshot
    # never closes a stint on absence, so a missing team costs coverage and
    # cannot cost correctness.
    snapshot: dict[str, str] = {}
    failed: list[str] = []
    consecutive_failures = 0
    team_ids = sorted(TEAM_ID_TO_ABBR)
    for index, team_id in enumerate(team_ids):
        try:
            rows = _fetch_team_roster(
                team_id, season, ROSTER_FETCH_ATTEMPTS, ROSTER_FETCH_TIMEOUT_SECONDS
            )
        except Exception as e:  # noqa: BLE001 - one team must not end the phase
            failed.append(TEAM_ID_TO_ABBR[team_id])
            consecutive_failures += 1
            logger.warning(
                "roster snapshot: %s roster failed (%s)", TEAM_ID_TO_ABBR[team_id], e
            )
            if consecutive_failures >= ROSTER_MAX_CONSECUTIVE_FAILURES:
                failed.extend(TEAM_ID_TO_ABBR[t] for t in team_ids[index + 1:])
                logger.warning("roster snapshot: stats.nba.com unreachable, giving up")
                break
            time.sleep(delay_seconds * 2)
            continue
        consecutive_failures = 0

        for raw in rows:
            player_id = str(raw.get("PLAYER_ID") or "").strip()
            if not player_id:
                continue
            if player_id in snapshot and snapshot[player_id] != team_id:
                logger.warning(
                    "roster snapshot: player %s appears on both %s and %s; taking %s",
                    player_id, TEAM_ID_TO_ABBR.get(snapshot[player_id]),
                    TEAM_ID_TO_ABBR[team_id], TEAM_ID_TO_ABBR[team_id],
                )
            snapshot[player_id] = team_id

        done = index + 1
        logger.info(
            "roster snapshot: %d/%d teams (%d players so far)",
            done, len(team_ids), len(snapshot),
        )
        if done < len(team_ids):
            time.sleep(delay_seconds)
    return snapshot, failed


def fetch_web_roster_snapshot() -> dict[str, str]:
    return roster_rows_from_nba_players_index(_fetch_nba_web_players())


def _open_stints(conn: psycopg2.extensions.connection) -> dict[str, tuple[str, date]]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT nba_player_id, team_id, valid_from
              FROM player_team_stints
             WHERE valid_to IS NULL
            """
        )
        return {
            str(pid): (str(team_id), valid_from)
            for pid, team_id, valid_from in cur.fetchall()
        }
    finally:
        cur.close()


def _last_logged_team(
    conn: psycopg2.extensions.connection, season: str
) -> dict[str, str]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT ON (nba_player_id) nba_player_id, team_id
              FROM player_game_logs
             WHERE season = %s AND team_id IS NOT NULL
             ORDER BY nba_player_id, game_date DESC, nba_game_id DESC
            """,
            (season,),
        )
        return {str(pid): str(team_id) for pid, team_id in cur.fetchall()}
    finally:
        cur.close()


def write_roster_snapshot_csv(
    path: str,
    snapshot: Mapping[str, str],
    snapshot_date: date,
    season: str,
    source: str = ROSTER_SNAPSHOT_SOURCE,
) -> int:
    # a file, not a database write, so it happens under --dry-run too: the point
    # of a dry run is to see what the snapshot says before committing it.
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["nba_player_id", "team_id", "valid_from", "source", "season"])
        for player_id in sorted(snapshot):
            writer.writerow([
                player_id, snapshot[player_id], snapshot_date.isoformat(),
                source, season,
            ])
    return len(snapshot)


def scrape_roster_snapshot(
    conn: psycopg2.extensions.connection,
    season: str = SEASON,
    dry_run: bool = False,
    snapshot_date: date | None = None,
    reference_season: str | None = None,
    delay_seconds: float = ROSTER_SNAPSHOT_REQUEST_DELAY_SECONDS,
    snapshot_out: str | None = None,
) -> int:
    # the offseason patch for the game-log-derived stint table, which can only
    # learn that a player moved once he has played for the new team. Idempotent:
    # a player whose open stint already names his current team is no change.
    snapshot_date = snapshot_date or date.today()
    reference_season = reference_season or season_label(season_start_year(season) - 1)

    logger.info(
        "truth layer: roster snapshot for %s as of %s",
        season, snapshot_date.isoformat(),
    )
    run_id = _start_ingestion_run(
        conn,
        "roster_snapshot",
        watermark_from=season,
        watermark_to=snapshot_date.isoformat(),
        dry_run=dry_run,
    )

    snapshot, failed = fetch_roster_snapshot(season, delay_seconds)
    source = ROSTER_SNAPSHOT_SOURCE
    if failed and season == SEASON:
        # the index only describes the current season. it covers all 30 teams in
        # one page, so it replaces an incomplete CommonTeamRoster pass.
        try:
            web_snapshot = fetch_web_roster_snapshot()
        except Exception as e:  # noqa: BLE001 - keep whatever the stats path got
            logger.warning("roster snapshot: nba.com players index failed (%s)", e)
            web_snapshot = {}
        if web_snapshot:
            logger.info(
                "roster snapshot: %d players from the nba.com players index",
                len(web_snapshot),
            )
            snapshot, failed, source = web_snapshot, [], ROSTER_WEB_SOURCE
    if not snapshot:
        logger.error("roster snapshot: every source failed; nothing to do")
        _finish_ingestion_run(
            conn, run_id, "failed", 0, notes="all roster sources failed"
        )
        return 0

    if snapshot_out:
        written_csv = write_roster_snapshot_csv(
            snapshot_out, snapshot, snapshot_date, season, source
        )
        logger.info("roster snapshot: %d row(s) -> %s", written_csv, snapshot_out)

    open_stints = _open_stints(conn)
    changes = plan_roster_snapshot(snapshot, open_stints, snapshot_date)

    # movement is measured against the end-of-season game-log team rather than
    # the stint table, which can be empty in a fresh environment.
    previous = _last_logged_team(conn, reference_season)
    shared = [pid for pid in snapshot if pid in previous]
    moved = [pid for pid in shared if snapshot[pid] != previous[pid]]
    new_to_league = [pid for pid in snapshot if pid not in previous]
    gone = [pid for pid in previous if pid not in snapshot]

    write_cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for change in changes:
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
                        change["player_id"],
                        change["close_team_id"],
                        change["close_valid_from"],
                    ),
                )
            write_cur.execute(
                """
                INSERT INTO player_team_stints (nba_player_id, team_id, valid_from, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (nba_player_id, team_id, valid_from) DO NOTHING
                """,
                (
                    change["player_id"],
                    change["open_team_id"],
                    change["open_valid_from"],
                    source,
                ),
            )
    finally:
        write_cur.close()

    notes = f"{len(changes)} stint change(s)"
    if failed:
        notes += f"; {len(failed)} team(s) failed: {','.join(failed)}"
    _finish_ingestion_run(
        conn, run_id, "partial" if failed else "succeeded", len(changes), notes=notes
    )

    logger.info(
        "roster snapshot: %d players across %d team(s) from %s%s",
        len(snapshot), len(set(snapshot.values())), source,
        f" ({len(failed)} failed: {', '.join(failed)})" if failed else "",
    )
    logger.info(
        "roster snapshot: %d stint change(s) to write (%d closing an open stint)%s",
        len(changes),
        sum(1 for c in changes if c["close_team_id"] is not None),
        " (dry run: nothing written)" if dry_run else "",
    )
    logger.info(
        "offseason movement vs %s end-of-season teams: %d of %d returning players "
        "changed team (%.1f%%); %d players new to the truth layer; %d %s players "
        "not on any current roster",
        reference_season, len(moved), len(shared),
        100.0 * len(moved) / max(len(shared), 1),
        len(new_to_league), len(gone), reference_season,
    )
    return len(changes)
