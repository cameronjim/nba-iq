import io
import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pdfplumber
import psycopg2
import requests

from config import (
    ABBR_TO_TEAM_ID,
    NAME_TO_ABBR,
    NBA_INJURY_REPORT_BASE_URL,
    NBA_INJURY_REPORT_PROBE_DELAY_SECONDS,
    NBA_INJURY_REPORT_SLOT_MINUTES,
    NBA_INJURY_REPORT_SOURCE,
    NBA_INJURY_REPORT_TIMEOUT_SECONDS,
    TEAM_ID_TO_ABBR,
)
from database import (
    _batch_upsert,
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import BROWSER_USER_AGENT
from parsing import _normalize_name, normalize_injury_status

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# older seasons published hourly ("_05PM"), current ones every 15 minutes ("_05_00PM").
_FILENAME_PATTERN = re.compile(
    r"Injury-Report_(?P<date>\d{4}-\d{2}-\d{2})_(?P<hour>\d{2})(?:_(?P<minute>\d{2}))?"
    r"(?P<ampm>AM|PM)\.pdf",
    re.IGNORECASE,
)
_DATE_PREFIX = re.compile(r"^(?P<month>\d{2})/(?P<day>\d{2})/(?P<year>\d{4})(?:\s+|$)")
_TIME_PREFIX = re.compile(r"^(?P<time>\d{1,2}:\d{2})\s*\(ET\)(?:\s+|$)")
_MATCHUP_PREFIX = re.compile(r"^(?P<matchup>[A-Z]{2,4}@[A-Z]{2,4})(?:\s+|$)")
_PLAYER_LINE = re.compile(
    r"^(?P<name>[^,;/]+?,\s*[^,;/]+?)\s+"
    r"(?P<status>Out|Doubtful|Questionable|Probable|Available)(?:\s+(?P<reason>.+))?$"
)
_NOISE_LINE = re.compile(r"^(?:Injury Report:|Page \d+ of \d+$|Game Date\s+Game Time)")
# a wrapped reason cell is split around the player's line, and only its first
# line starts with one of these, so this is how a fragment is told head from tail.
_REASON_HEAD = re.compile(
    r"^(?:Injury/Illness|G League|Not With Team|Personal Reasons?|League Suspension"
    r"|Concussion Protocol|Health and Safety|Rest)\b"
)
NOT_YET_SUBMITTED = "NOT YET SUBMITTED"
_TEAM_NAMES = sorted(NAME_TO_ABBR.items(), key=lambda item: -len(item[0]))
_UNMATCHED_LOG_EXAMPLES = 10

INSERT_REPORT_SQL = """
    INSERT INTO player_injury_reports (nba_player_id, nba_game_id, captured_at,
                                       report_as_of, status_raw, status_normalized,
                                       reason, source, team_id, report_url,
                                       team_submitted)
    VALUES %s
"""
INSERT_REPORT_TEMPLATE = "(%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, TRUE)"


def report_url_for_slot(slot_et: datetime) -> str:
    # built by hand because %p and %I follow the process locale.
    hour12 = slot_et.hour % 12 or 12
    ampm = "AM" if slot_et.hour < 12 else "PM"
    return (
        f"{NBA_INJURY_REPORT_BASE_URL}Injury-Report_{slot_et:%Y-%m-%d}_"
        f"{hour12:02d}_{slot_et.minute:02d}{ampm}.pdf"
    )


def candidate_report_urls(now: datetime) -> list[str]:
    now_et = now.astimezone(ET)
    floored = now_et.minute - now_et.minute % NBA_INJURY_REPORT_SLOT_MINUTES
    slot = now_et.replace(minute=floored, second=0, microsecond=0)
    urls: list[str] = []
    while slot.date() == now_et.date():
        urls.append(report_url_for_slot(slot))
        slot -= timedelta(minutes=NBA_INJURY_REPORT_SLOT_MINUTES)
    return urls


def parse_report_published_at(url_or_name: str) -> datetime | None:
    match = _FILENAME_PATTERN.search(url_or_name or "")
    if not match:
        return None
    hour = int(match.group("hour"))
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if match.group("ampm").upper() == "PM" else 0)
    minute = int(match.group("minute") or 0)
    day = date.fromisoformat(match.group("date"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
    return local.astimezone(timezone.utc)


def last_first_to_first_last(name: str) -> str:
    last, sep, first = (name or "").partition(",")
    if not sep:
        return " ".join(last.split())
    return " ".join(f"{first} {last}".split())


def parse_report_matchup(matchup: str | None) -> tuple[str | None, str | None]:
    away, sep, home = (matchup or "").strip().upper().partition("@")
    if not sep or not away or not home:
        return None, None
    return away, home


def _take_team(text: str) -> tuple[str | None, str]:
    lowered = text.lower()
    for name, abbr in _TEAM_NAMES:
        if lowered.startswith(name) and (len(text) == len(name) or text[len(name)] == " "):
            return abbr, text[len(name):].strip()
    return None, text


def _report_lines(pages: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for raw in (page or "").splitlines():
            line = " ".join(raw.split())
            if line and not _NOISE_LINE.match(line):
                lines.append(line)
    return lines


def _append_reason(row: dict, fragment: str) -> None:
    row["reason"] = f"{row['reason']} {fragment}" if row["reason"] else fragment


def parse_injury_report_text(pages: Sequence[str], published_at: datetime) -> list[dict]:
    # columns to the left of the player are printed only when they change, and
    # a page break neither repeats them nor ends a wrapped reason, so the pages
    # are read as one continuous stream.
    rows: list[dict] = []
    game_date: date | None = None
    game_time: str | None = None
    matchup: str | None = None
    team_abbr: str | None = None
    pending_head: list[str] = []
    last_player: dict | None = None

    def base_row() -> dict:
        return {
            "game_date": game_date,
            "game_time_et": game_time,
            "matchup": matchup,
            "team_abbr": team_abbr,
            "report_as_of": published_at,
            "player_name_last_first": None,
            "player_name": None,
            "status_raw": None,
            "status_normalized": None,
            "reason": None,
            "not_yet_submitted": False,
        }

    for line in _report_lines(pages):
        rest = line
        date_match = _DATE_PREFIX.match(rest)
        if date_match:
            game_date = date(
                int(date_match.group("year")),
                int(date_match.group("month")),
                int(date_match.group("day")),
            )
            rest = rest[date_match.end():]
        time_match = _TIME_PREFIX.match(rest)
        if time_match:
            game_time = time_match.group("time")
            rest = rest[time_match.end():]
        matchup_match = _MATCHUP_PREFIX.match(rest)
        if matchup_match:
            matchup = matchup_match.group("matchup")
            rest = rest[matchup_match.end():]
        team, rest = _take_team(rest)
        if team:
            team_abbr = team

        if rest.upper() == NOT_YET_SUBMITTED:
            row = base_row()
            row["not_yet_submitted"] = True
            rows.append(row)
            continue

        player_match = None if _REASON_HEAD.match(rest) else _PLAYER_LINE.match(rest)
        if player_match:
            row = base_row()
            last_first = " ".join(player_match.group("name").split())
            status = player_match.group("status")
            reason_parts = pending_head + (
                [player_match.group("reason")] if player_match.group("reason") else []
            )
            row.update(
                player_name_last_first=last_first,
                player_name=last_first_to_first_last(last_first),
                status_raw=status,
                status_normalized=normalize_injury_status(status),
                reason=" ".join(reason_parts) or None,
            )
            pending_head = []
            rows.append(row)
            last_player = row
            continue

        if not rest:
            continue
        if _REASON_HEAD.match(rest):
            if pending_head and last_player is not None:
                _append_reason(last_player, " ".join(pending_head))
            pending_head = [rest]
        elif pending_head:
            pending_head.append(rest)
        elif last_player is not None:
            _append_reason(last_player, rest)
        else:
            logger.debug("official injury report: unattributed line %r", rest)

    if pending_head and last_player is not None:
        _append_reason(last_player, " ".join(pending_head))
    return rows


def index_players(
    players: Iterable[tuple[object, object, object]],
) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for nba_id, name, team in players:
        if not nba_id or not name:
            continue
        key = (_normalize_name(str(name)), str(team or "").strip().upper())
        index.setdefault(key, []).append(str(nba_id))
    return index


@dataclass
class ReportMatch:
    matched: list[dict] = field(default_factory=list)
    unmatched_players: list[dict] = field(default_factory=list)
    unmatched_games: int = 0
    not_submitted: list[dict] = field(default_factory=list)


def match_report_rows(
    rows: Iterable[Mapping],
    players_by_name_team: Mapping[tuple[str, str], Sequence[str]],
    schedule_rows: Iterable[Mapping],
) -> ReportMatch:
    by_name: dict[str, set[str]] = {}
    for (name, _team), ids in players_by_name_team.items():
        by_name.setdefault(name, set()).update(ids)
    games: dict[tuple[date, str, str], str] = {}
    for game in schedule_rows:
        away = str(game.get("away_team_abbr") or "").upper()
        home = str(game.get("home_team_abbr") or "").upper()
        if game.get("game_date") and away and home:
            games[(game["game_date"], away, home)] = str(game["nba_game_id"])

    result = ReportMatch()
    for row in rows:
        away, home = parse_report_matchup(row.get("matchup"))
        nba_game_id = games.get((row.get("game_date"), away, home)) if away else None
        team_abbr = row.get("team_abbr")
        resolved = {
            **row,
            "nba_game_id": nba_game_id,
            "team_id": ABBR_TO_TEAM_ID.get(team_abbr or ""),
        }
        if row.get("not_yet_submitted"):
            result.not_submitted.append(resolved)
            continue

        name = _normalize_name(row.get("player_name") or "")
        candidates = set(players_by_name_team.get((name, team_abbr or ""), ()))
        if not candidates:
            # a traded player can be listed under his new team before the
            # players table catches up, so a unique name is still trusted.
            candidates = by_name.get(name, set())
        if len(candidates) != 1:
            result.unmatched_players.append(resolved)
            continue
        resolved["nba_player_id"] = next(iter(candidates))
        if nba_game_id is None:
            result.unmatched_games += 1
        result.matched.append(resolved)
    return result


def official_clearances(
    previous_rows: Iterable[Mapping], current: ReportMatch
) -> list[dict]:
    # a team that filed but has no one left to list does not appear at all, so a
    # game that is still on the report with the team not flagged counts as filed.
    # a game that has dropped off entirely (tipped off, postponed) clears nobody.
    latest: dict[tuple[str, str], datetime] = {}
    previous = [r for r in previous_rows if r.get("nba_game_id") and r.get("team_abbr")]
    for r in previous:
        key = (str(r["nba_game_id"]), str(r["team_abbr"]))
        if key not in latest or r["report_as_of"] > latest[key]:
            latest[key] = r["report_as_of"]

    current_games = {
        str(r["nba_game_id"])
        for r in [*current.matched, *current.not_submitted]
        if r.get("nba_game_id")
    }
    unfiled = {
        (str(r["nba_game_id"]), str(r["team_abbr"]))
        for r in current.not_submitted
        if r.get("nba_game_id") and r.get("team_abbr")
    }
    listed = {
        (str(r["nba_player_id"]), str(r["nba_game_id"]))
        for r in current.matched
        if r.get("nba_game_id")
    }

    cleared: dict[tuple[str, str], dict] = {}
    for r in previous:
        game_id, team = str(r["nba_game_id"]), str(r["team_abbr"])
        player_id = str(r["nba_player_id"])
        if r["report_as_of"] != latest[(game_id, team)]:
            continue
        if r.get("status_normalized") == "cleared":
            continue
        if game_id not in current_games or (game_id, team) in unfiled:
            continue
        if (player_id, game_id) in listed:
            continue
        cleared[(player_id, game_id)] = {
            "nba_player_id": player_id,
            "nba_game_id": game_id,
            "team_abbr": team,
        }
    return [cleared[key] for key in sorted(cleared)]


def fetch_latest_report(now: datetime) -> tuple[str, bytes] | None:
    headers = {"User-Agent": BROWSER_USER_AGENT}
    for url in candidate_report_urls(now):
        try:
            resp = requests.get(
                url, headers=headers, timeout=NBA_INJURY_REPORT_TIMEOUT_SECONDS
            )
        except requests.RequestException as e:
            logger.warning("official injury report: %s unreachable (%s)", url, e)
            return None
        if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
            return url, resp.content
        time.sleep(NBA_INJURY_REPORT_PROBE_DELAY_SECONDS)
    return None


def extract_pdf_pages(pdf_bytes: bytes) -> list[str]:
    # the default tolerance glues words together ("ClevelandCavaliers").
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text(x_tolerance=1.5) or "" for page in pdf.pages]


def _schedule_window(
    conn: psycopg2.extensions.connection, today_et: date
) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT nba_game_id, game_date, home_team_abbr, away_team_abbr
            FROM nba_schedule
            WHERE game_date BETWEEN %s AND %s
            """,
            (today_et - timedelta(days=1), today_et + timedelta(days=2)),
        )
        return [
            {
                "nba_game_id": game_id,
                "game_date": game_date,
                "home_team_abbr": home,
                "away_team_abbr": away,
            }
            for game_id, game_date, home, away in cur.fetchall()
        ]
    finally:
        cur.close()


def _previous_official_rows(
    cur: object, game_ids: list[str], report_as_of: datetime
) -> list[dict]:
    if not game_ids:
        return []
    cur.execute(
        """
        SELECT nba_player_id, nba_game_id, team_id, status_normalized, report_as_of
        FROM player_injury_reports
        WHERE source = %s
          AND nba_game_id = ANY(%s)
          AND report_as_of < %s
        """,
        (NBA_INJURY_REPORT_SOURCE, game_ids, report_as_of),
    )
    return [
        {
            "nba_player_id": player_id,
            "nba_game_id": game_id,
            "team_abbr": TEAM_ID_TO_ABBR.get(str(team_id or "")),
            "status_normalized": status,
            "report_as_of": as_of,
        }
        for player_id, game_id, team_id, status, as_of in cur.fetchall()
    ]


def _already_ingested(cur: object, report_url: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM player_injury_reports
        WHERE source = %s
          AND report_url = %s
        LIMIT 1
        """,
        (NBA_INJURY_REPORT_SOURCE, report_url),
    )
    return bool(cur.fetchall())


def _log_unmatched(match: ReportMatch) -> None:
    if not match.unmatched_players:
        return
    examples = ", ".join(
        f"{r.get('player_name')} ({r.get('team_abbr')})"
        for r in match.unmatched_players[:_UNMATCHED_LOG_EXAMPLES]
    )
    logger.warning(
        "official injury report: %d row(s) matched no unique player and were skipped: %s",
        len(match.unmatched_players),
        examples,
    )


def scrape_official_injuries(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    logger.info("fetching the official nba injury report...")
    run_id = _start_ingestion_run(conn, "injuries_official", dry_run=dry_run)
    try:
        return _ingest_official_report(conn, run_id, dry_run, now)
    except Exception:
        # autocommit has kept whatever landed, so the run must not read as running.
        _finish_ingestion_run(conn, run_id, "failed", 0, notes="raised")
        raise


def _ingest_official_report(
    conn: psycopg2.extensions.connection,
    run_id: int | None,
    dry_run: bool,
    now: datetime,
) -> bool:
    today_et = now.astimezone(ET).date()
    schedule = _schedule_window(conn, today_et)
    if not any(g["game_date"] in (today_et, today_et + timedelta(days=1)) for g in schedule):
        logger.info("official injury report: no games scheduled today or tomorrow, skipping")
        _finish_ingestion_run(conn, run_id, "succeeded", 0, notes="no games scheduled")
        return True

    fetched = fetch_latest_report(now)
    if fetched is None:
        logger.warning("official injury report: no report published yet today")
        _finish_ingestion_run(conn, run_id, "failed", 0, notes="no report found")
        return False
    report_url, pdf_bytes = fetched
    published_at = parse_report_published_at(report_url)
    if published_at is None:
        _finish_ingestion_run(conn, run_id, "failed", 0, notes=f"bad url {report_url}")
        return False

    rows = parse_injury_report_text(extract_pdf_pages(pdf_bytes), published_at)
    if not rows:
        # an unparseable report must never read as "nobody is injured".
        logger.warning("official injury report: %s parsed to zero rows", report_url)
        _finish_ingestion_run(conn, run_id, "failed", 0, notes=f"zero rows from {report_url}")
        return False

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    cur.execute("SELECT nba_id, name, team FROM players WHERE nba_id IS NOT NULL")
    match = match_report_rows(rows, index_players(cur.fetchall()), schedule)
    _log_unmatched(match)

    game_ids = sorted(
        {
            str(r["nba_game_id"])
            for r in [*match.matched, *match.not_submitted]
            if r["nba_game_id"]
        }
    )
    duplicate = _already_ingested(cur, report_url)
    cleared = (
        [] if duplicate
        else official_clearances(_previous_official_rows(cur, game_ids, published_at), match)
    )

    written = 0
    if not duplicate:
        written += _batch_upsert(
            cur,
            INSERT_REPORT_SQL,
            [
                (
                    r["nba_player_id"], r["nba_game_id"], published_at, r["status_raw"],
                    r["status_normalized"], r["reason"], NBA_INJURY_REPORT_SOURCE,
                    r["team_id"], report_url,
                )
                for r in match.matched
            ],
            template=INSERT_REPORT_TEMPLATE,
        )
        written += _batch_upsert(
            cur,
            INSERT_REPORT_SQL,
            [
                (
                    c["nba_player_id"], c["nba_game_id"], published_at, "cleared",
                    "cleared", None, NBA_INJURY_REPORT_SOURCE,
                    ABBR_TO_TEAM_ID.get(c["team_abbr"]), report_url,
                )
                for c in cleared
            ],
            template=INSERT_REPORT_TEMPLATE,
        )

    # earliest game written last, so a player listed for two games shows the nearer one.
    ordered = sorted(match.matched, key=lambda r: r["game_date"] or date.max, reverse=True)
    for r in ordered:
        cur.execute(
            """
            UPDATE players
            SET injury_status = %s, injury_detail = %s, updated_at = NOW()
            WHERE nba_id = %s
            """,
            (r["status_raw"], r["reason"], r["nba_player_id"]),
        )
    listed_ids = {r["nba_player_id"] for r in match.matched}
    for c in cleared:
        if c["nba_player_id"] in listed_ids:
            continue
        cur.execute(
            """
            UPDATE players
            SET injury_status = NULL, injury_detail = NULL, updated_at = NOW()
            WHERE nba_id = %s
            """,
            (c["nba_player_id"],),
        )
    cur.close()

    unfiled_teams = len({(r["matchup"], r["team_abbr"]) for r in match.not_submitted})
    notes = (
        f"url={report_url} matched={len(match.matched)} "
        f"unmatched_players={len(match.unmatched_players)} "
        f"unmatched_games={match.unmatched_games} not_yet_submitted_teams={unfiled_teams} "
        f"cleared={len(cleared)}{' duplicate_report' if duplicate else ''}"
    )
    logger.info(
        "official injury report %s: %s%s",
        report_url,
        notes,
        " (dry run: no rows written)" if dry_run else "",
    )
    _finish_ingestion_run(
        conn, run_id, "succeeded", written, notes=notes, watermark_to=published_at
    )
    return True
