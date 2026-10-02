import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    ABBR_TO_TEAM_ID,
    ESPN_INJURIES_INGESTION_KIND,
    ESPN_INJURIES_SOURCE,
    ESPN_INJURIES_URL,
    ESPN_INJURIES_WINDOW_DAYS,
    ESPN_LONG_TERM_MAX_REPORT_AGE_DAYS,
    ESPN_LONG_TERM_PATTERNS,
    NAME_TO_ABBR,
    TEAM_META,
)
from database import (
    _batch_upsert,
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import fetch_espn_injuries
from injury_report import INSERT_REPORT_SQL
from odds import normalize_espn_abbr
from parsing import normalize_injury_status
from scrapes import index_players_by_canonical_name, match_injury_rows

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_LONG_TERM = re.compile(ESPN_LONG_TERM_PATTERNS, re.IGNORECASE)
_FANTASY_STATUS = {
    "O": "out",
    "OUT": "out",
    "OFS": "out",
    "D": "doubtful",
    "Q": "questionable",
    "GTD": "questionable",
    "P": "probable",
}
# espn has no team_submitted notion, so that column stays NULL.
INSERT_ESPN_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)"


class EspnInjuryRow(NamedTuple):
    player_name: str
    team_abbr: str | None
    status_raw: str
    status_normalized: str
    reason: str | None
    comment: str
    return_date: date | None
    reported_at: datetime | None


def normalize_espn_status(
    status: str,
    fantasy_abbr: str | None,
    comment: str,
    reported_at: datetime | None = None,
    now: datetime | None = None,
) -> str:
    if _LONG_TERM.search(comment or "") and _note_is_fresh(reported_at, now):
        return "out"
    text = (status or "").strip()
    if text.lower() == "out":
        return "out"
    mapped = _FANTASY_STATUS.get((fantasy_abbr or "").strip().upper())
    if mapped:
        return mapped
    if text.lower() == "day-to-day":
        # day_to_day is a passthrough downstream, but espn lists it as a gtd.
        return "questionable"
    return normalize_injury_status(text)


def _note_is_fresh(reported_at: datetime | None, now: datetime | None) -> bool:
    if reported_at is None or now is None:
        return True
    return now - reported_at <= timedelta(days=ESPN_LONG_TERM_MAX_REPORT_AGE_DAYS)


def _parse_reported_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_return_date(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _group_team_abbr(group: Mapping, athlete: Mapping) -> str | None:
    # the group is the current team; athlete.team lags behind offseason moves.
    abbr = NAME_TO_ABBR.get(str(group.get("displayName") or "").strip().lower())
    if abbr:
        return abbr
    fallback = normalize_espn_abbr((athlete.get("team") or {}).get("abbreviation"))
    return fallback if fallback in TEAM_META else None


def parse_espn_injuries(payload: Mapping, now: datetime | None = None) -> list[EspnInjuryRow]:
    rows: list[EspnInjuryRow] = []
    for group in payload.get("injuries") or []:
        for item in group.get("injuries") or []:
            athlete = item.get("athlete") or {}
            name = str(athlete.get("displayName") or "").strip()
            if not name:
                continue
            details = item.get("details") or {}
            fantasy_abbr = (details.get("fantasyStatus") or {}).get("abbreviation")
            status = str(item.get("status") or "").strip()
            comment = str(item.get("shortComment") or "").strip()
            reason = details.get("type") or (item.get("type") or {}).get("description")
            reported_at = _parse_reported_at(item.get("date"))
            rows.append(
                EspnInjuryRow(
                    player_name=name,
                    team_abbr=_group_team_abbr(group, athlete),
                    status_raw=status,
                    status_normalized=normalize_espn_status(
                        status, fantasy_abbr, comment, reported_at, now
                    ),
                    reason=str(reason) if reason else None,
                    comment=comment,
                    return_date=_parse_return_date(details.get("returnDate")),
                    reported_at=reported_at,
                )
            )
    return rows


def _status_row(
    nba_id: str, row: EspnInjuryRow, nba_game_id: str | None, team_id: str | None,
    status: str, reason: str | None,
) -> dict:
    return {
        "nba_player_id": nba_id,
        "nba_game_id": nba_game_id,
        "team_id": team_id,
        "report_as_of": row.reported_at,
        "status_raw": row.status_raw,
        "status_normalized": status,
        "reason": reason,
    }


def game_scoped_statuses(
    rows: Iterable[tuple[str, EspnInjuryRow]],
    schedule_games: Iterable[Mapping],
    as_of: date,
) -> list[dict]:
    horizon = as_of + timedelta(days=ESPN_INJURIES_WINDOW_DAYS)
    games_by_team: dict[str, list[tuple[date, str, str | None]]] = {}
    for game in schedule_games:
        game_date = game.get("game_date")
        if not game_date or not as_of <= game_date <= horizon:
            continue
        for side in ("home", "away"):
            abbr = str(game.get(f"{side}_team_abbr") or "").upper()
            if not abbr:
                continue
            team_id = game.get(f"{side}_team_id") or ABBR_TO_TEAM_ID.get(abbr)
            games_by_team.setdefault(abbr, []).append(
                (game_date, str(game["nba_game_id"]), str(team_id) if team_id else None)
            )

    out: list[dict] = []
    for nba_id, row in rows:
        if row.return_date is None or row.status_normalized == "out":
            out.append(
                _status_row(
                    nba_id, row, None, ABBR_TO_TEAM_ID.get(row.team_abbr or ""),
                    row.status_normalized, row.reason,
                )
            )
        if row.return_date is None:
            continue
        reason = f"{row.reason}; expected return {row.return_date}"
        for game_date, game_id, team_id in sorted(games_by_team.get(row.team_abbr or "", [])):
            if game_date < row.return_date:
                out.append(_status_row(nba_id, row, game_id, team_id, "out", reason))
    return out


def _upcoming_schedule(
    conn: psycopg2.extensions.connection, today_et: date
) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT nba_game_id, game_date, home_team_abbr, away_team_abbr,
                   home_team_id, away_team_id
            FROM nba_schedule
            WHERE game_date BETWEEN %s AND %s
              AND (postponed_status IS NULL OR postponed_status = 'N')
            """,
            (today_et, today_et + timedelta(days=ESPN_INJURIES_WINDOW_DAYS)),
        )
        return [
            {
                "nba_game_id": game_id,
                "game_date": game_date,
                "home_team_abbr": home,
                "away_team_abbr": away,
                "home_team_id": home_id,
                "away_team_id": away_id,
            }
            for game_id, game_date, home, away, home_id, away_id in cur.fetchall()
        ]
    finally:
        cur.close()


def scrape_espn_injuries(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    logger.info("fetching the espn injury feed...")
    run_id = _start_ingestion_run(conn, ESPN_INJURIES_INGESTION_KIND, dry_run=dry_run)
    try:
        return _ingest_espn_injuries(conn, run_id, dry_run, now)
    except Exception:
        _finish_ingestion_run(conn, run_id, "failed", 0, notes="raised")
        raise


def _ingest_espn_injuries(
    conn: psycopg2.extensions.connection,
    run_id: int | None,
    dry_run: bool,
    now: datetime,
) -> int:
    parsed = parse_espn_injuries(fetch_espn_injuries(), now)
    if not parsed:
        # an empty feed must never read as "nobody is injured".
        logger.warning("espn injuries: feed parsed to zero rows, writing nothing")
        _finish_ingestion_run(conn, run_id, "failed", 0, notes="zero rows")
        return 0

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        cur.execute("SELECT nba_id, name, team FROM players WHERE nba_id IS NOT NULL")
        match = match_injury_rows(parsed, index_players_by_canonical_name(cur.fetchall()))
        if match.ambiguous:
            logger.warning(
                "espn injuries: %d row(s) matched several players and were skipped: %s",
                len(match.ambiguous),
                ", ".join(f"{r.player_name} ({r.team_abbr or '?'})" for r in match.ambiguous),
            )
        if match.unmatched:
            logger.info(
                "espn injuries: %d row(s) matched no player: %s",
                len(match.unmatched),
                ", ".join(r.player_name for r in match.unmatched),
            )
        if not match.matched:
            logger.warning("espn injuries: feed matched zero players, writing nothing")
            _finish_ingestion_run(conn, run_id, "failed", 0, notes="zero matched")
            return 0

        today_et = now.astimezone(ET).date()
        statuses = game_scoped_statuses(
            match.matched, _upcoming_schedule(conn, today_et), today_et
        )
        written = _batch_upsert(
            cur,
            INSERT_REPORT_SQL,
            [
                (
                    s["nba_player_id"], s["nba_game_id"], now, s["report_as_of"],
                    s["status_raw"], s["status_normalized"], s["reason"],
                    ESPN_INJURIES_SOURCE, s["team_id"], ESPN_INJURIES_URL,
                )
                for s in statuses
            ],
            template=INSERT_ESPN_TEMPLATE,
        )
    finally:
        cur.close()

    game_rows = sum(1 for s in statuses if s["nba_game_id"])
    notes = (
        f"parsed={len(parsed)} matched={len(match.matched)} "
        f"unmatched={len(match.unmatched)} ambiguous={len(match.ambiguous)} "
        f"general_rows={len(statuses) - game_rows} game_rows={game_rows}"
    )
    logger.info(
        "espn injuries: %s%s", notes, " (dry run: no rows written)" if dry_run else ""
    )
    _finish_ingestion_run(conn, run_id, "succeeded", written, notes=notes)
    return written
