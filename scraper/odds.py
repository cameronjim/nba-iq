import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    ODDS_INGESTION_KIND,
    ODDS_SNAPSHOT_SOURCE,
    ODDS_WINDOW_DAYS,
)
from database import (
    _batch_upsert,
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import fetch_espn_scoreboard_events

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

MAPPED_BY_DATE_ABBR = "date_abbr"

# espn abbreviation -> nba tricode; anything not listed is already the same.
ESPN_TO_NBA_ABBR = {
    "GS": "GSW",
    "NY": "NYK",
    "SA": "SAS",
    "NO": "NOP",
    "UTAH": "UTA",
    "WSH": "WAS",
    "PHO": "PHX",
    "BK": "BKN",
    "BRK": "BKN",
}

_AMERICAN_RE = re.compile(r"^[+-]?\d+$")
_DETAILS_RE = re.compile(r"^([A-Z]{2,4})\s+(-?\d+(?:\.\d+)?)$")

ODDS_INSERT_SQL = """
INSERT INTO odds_snapshots (espn_event_id, nba_game_id, game_date, provider, market,
                            selection, line, price, price_observed,
                            provider_updated_at, source, ingestion_run_id)
VALUES %s
"""

EVENT_MAP_UPSERT_SQL = """
INSERT INTO espn_event_map (espn_event_id, nba_game_id, game_date, home_team_abbr,
                            away_team_abbr, mapped_by)
VALUES %s
ON CONFLICT (espn_event_id) DO UPDATE SET
    nba_game_id = EXCLUDED.nba_game_id,
    game_date = EXCLUDED.game_date,
    home_team_abbr = EXCLUDED.home_team_abbr,
    away_team_abbr = EXCLUDED.away_team_abbr,
    mapped_by = EXCLUDED.mapped_by
"""


def parse_american(odds: object) -> int | None:
    if odds is None:
        return None
    text = str(odds).strip()
    if text.upper() == "EVEN":
        return 100
    if not _AMERICAN_RE.match(text):
        return None
    return int(text)


def parse_line(line: object) -> float | None:
    if line is None:
        return None
    # total lines arrive as "o224.5" / "u224.5"
    text = re.sub(r"^[ou]", "", str(line).strip(), flags=re.IGNORECASE)
    try:
        return float(text)
    except ValueError:
        return None


def parse_spread_details(
    details: object, home_abbr: str, away_abbr: str
) -> float | None:
    if not isinstance(details, str):
        return None
    text = details.strip()
    if text.upper() == "EVEN":
        return 0.0
    match = _DETAILS_RE.match(text)
    if not match:
        return None
    abbr, line_text = match.groups()
    line = float(line_text)
    if abbr == home_abbr:
        return line
    if abbr == away_abbr:
        return _negate(line)
    return None


def normalize_espn_abbr(abbr: str | None) -> str | None:
    if not abbr:
        return None
    upper = abbr.strip().upper()
    return ESPN_TO_NBA_ABBR.get(upper, upper)


def event_date_et(event: Mapping) -> date | None:
    # espn stores event dates in utc, so a 10pm et tip is the next utc day.
    raw = event.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(EASTERN).date()
    except ValueError:
        return None


def event_teams(event: Mapping) -> tuple[str, str] | None:
    competition = (event.get("competitions") or [{}])[0] or {}
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None
    home_abbr = (home.get("team") or {}).get("abbreviation") or ""
    away_abbr = (away.get("team") or {}).get("abbreviation") or ""
    return home_abbr, away_abbr


def is_pregame(event: Mapping) -> bool:
    status = ((event.get("status") or {}).get("type") or {}).get("name")
    return status == "STATUS_SCHEDULED"


def _negate(value: float) -> float:
    # avoids storing -0.0 for a pick'em
    return -value if value else 0.0


def _close(node: Mapping, side: str, field: str) -> object:
    return ((node.get(side) or {}).get("close") or {}).get(field)


def _row(market: str, selection: str, line: float | None, price: int | None) -> dict:
    return {
        "market": market,
        "selection": selection,
        "line": line,
        "price": price,
        "price_observed": price is not None,
    }


def _spread_rows(odds: Mapping, home_abbr: str, away_abbr: str) -> list[dict]:
    point_spread = odds.get("pointSpread") or {}
    line = parse_line(_close(point_spread, "home", "line"))
    if line is None and isinstance(odds.get("spread"), (int, float)):
        line = float(odds["spread"])
    if line is None:
        line = parse_spread_details(odds.get("details"), home_abbr, away_abbr)
    if line is None:
        return []
    return [
        _row("spread", "home", line, parse_american(_close(point_spread, "home", "odds"))),
        _row(
            "spread", "away", _negate(line),
            parse_american(_close(point_spread, "away", "odds")),
        ),
    ]


def _total_rows(odds: Mapping) -> list[dict]:
    total = odds.get("total") or {}
    line = parse_line(_close(total, "over", "line"))
    if line is None and isinstance(odds.get("overUnder"), (int, float)):
        line = float(odds["overUnder"])
    if line is None:
        return []
    return [
        _row("total", "over", line, parse_american(_close(total, "over", "odds"))),
        _row("total", "under", line, parse_american(_close(total, "under", "odds"))),
    ]


def _moneyline_price(odds: Mapping, side: str) -> int | None:
    price = parse_american(_close(odds.get("moneyline") or {}, side, "odds"))
    if price is not None:
        return price
    fallback = (odds.get(f"{side}TeamOdds") or {}).get("moneyLine")
    if isinstance(fallback, (int, float)) and not isinstance(fallback, bool):
        return int(fallback)
    return None


def _moneyline_rows(odds: Mapping) -> list[dict]:
    home = _moneyline_price(odds, "home")
    away = _moneyline_price(odds, "away")
    if home is None and away is None:
        return []
    return [
        _row("moneyline", "home", None, home),
        _row("moneyline", "away", None, away),
    ]


def parse_event_odds(event: Mapping) -> list[dict]:
    # one row per (market, selection); an event with no odds node yields none.
    teams = event_teams(event)
    game_date = event_date_et(event)
    competition = (event.get("competitions") or [{}])[0] or {}
    odds = (competition.get("odds") or [None])[0]
    if teams is None or game_date is None or not isinstance(odds, Mapping):
        return []

    home_abbr, away_abbr = teams
    base = {
        "espn_event_id": str(event.get("id") or ""),
        "game_date": game_date,
        "provider": (odds.get("provider") or {}).get("name") or "",
        # the scoreboard odds node carries no update timestamp
        "provider_updated_at": None,
    }
    if not base["espn_event_id"]:
        return []
    rows = (
        _spread_rows(odds, home_abbr, away_abbr)
        + _total_rows(odds)
        + _moneyline_rows(odds)
    )
    return [{**base, **row} for row in rows]


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def map_event_to_nba_game(
    event_date: date,
    home_abbr: str | None,
    away_abbr: str | None,
    schedule_rows: Iterable[Mapping],
) -> str | None:
    # an ambiguous match maps to nothing rather than to a guess.
    home = normalize_espn_abbr(home_abbr)
    away = normalize_espn_abbr(away_abbr)
    if not home or not away:
        return None
    matches = {
        str(row["nba_game_id"])
        for row in schedule_rows
        if _as_date(row.get("game_date")) == event_date
        and (row.get("home_team_abbr") or "").upper() == home
        and (row.get("away_team_abbr") or "").upper() == away
    }
    return matches.pop() if len(matches) == 1 else None


def plan_odds_snapshot(
    events: Iterable[Mapping],
    schedule_rows: list[Mapping],
    known_map: Mapping[str, str],
) -> tuple[list[dict], list[dict], int]:
    # returns (snapshot rows, new event mappings, unmapped event count).
    snapshot_rows: list[dict] = []
    new_mappings: list[dict] = []
    unmapped = 0
    for event in events:
        if not is_pregame(event):
            continue
        rows = parse_event_odds(event)
        if not rows:
            continue
        espn_event_id = rows[0]["espn_event_id"]
        game_date = rows[0]["game_date"]
        home_abbr, away_abbr = event_teams(event) or ("", "")
        nba_game_id = known_map.get(espn_event_id)
        if nba_game_id is None:
            nba_game_id = map_event_to_nba_game(
                game_date, home_abbr, away_abbr, schedule_rows
            )
            if nba_game_id is not None:
                new_mappings.append({
                    "espn_event_id": espn_event_id,
                    "nba_game_id": nba_game_id,
                    "game_date": game_date,
                    "home_team_abbr": normalize_espn_abbr(home_abbr),
                    "away_team_abbr": normalize_espn_abbr(away_abbr),
                    "mapped_by": MAPPED_BY_DATE_ABBR,
                })
        if nba_game_id is None:
            unmapped += 1
            logger.warning(
                "odds: espn event %s (%s %s@%s) matched no nba_schedule game",
                espn_event_id, game_date, away_abbr, home_abbr,
            )
        snapshot_rows.extend({**row, "nba_game_id": nba_game_id} for row in rows)
    return snapshot_rows, new_mappings, unmapped


def _snapshot_tuple(row: Mapping, run_id: int | None) -> tuple:
    return (
        row["espn_event_id"], row["nba_game_id"], row["game_date"], row["provider"],
        row["market"], row["selection"], row["line"], row["price"],
        row["price_observed"], row["provider_updated_at"], ODDS_SNAPSHOT_SOURCE, run_id,
    )


def _mapping_tuple(row: Mapping) -> tuple:
    return (
        row["espn_event_id"], row["nba_game_id"], row["game_date"],
        row["home_team_abbr"], row["away_team_abbr"], row["mapped_by"],
    )


def _read_schedule_rows(cur: object, start: date, end: date) -> list[dict]:
    cur.execute(
        """
        SELECT nba_game_id, game_date, home_team_abbr, away_team_abbr
        FROM nba_schedule
        WHERE game_date BETWEEN %s AND %s
        """,
        (start, end),
    )
    return [
        {
            "nba_game_id": nba_game_id,
            "game_date": game_date,
            "home_team_abbr": home,
            "away_team_abbr": away,
        }
        for nba_game_id, game_date, home, away in cur.fetchall()
    ]


def _read_known_map(cur: object, start: date, end: date) -> dict[str, str]:
    cur.execute(
        """
        SELECT espn_event_id, nba_game_id
        FROM espn_event_map
        WHERE game_date BETWEEN %s AND %s
        """,
        (start, end),
    )
    return {str(espn_id): str(nba_id) for espn_id, nba_id in cur.fetchall()}


def _fetch_window(start: date, end: date) -> tuple[list[dict], list[str]]:
    # returns (events, failed days); a day that fails costs only that day.
    events: dict[str, dict] = {}
    failed: list[str] = []
    day = start
    while day <= end:
        try:
            for event in fetch_espn_scoreboard_events(day):
                events.setdefault(str(event.get("id") or ""), event)
        except Exception as e:  # noqa: BLE001 - the other days are still usable
            logger.warning("odds: espn scoreboard %s failed (%s)", day, e)
            failed.append(day.isoformat())
        day += timedelta(days=1)
    events.pop("", None)
    return list(events.values()), failed


def scrape_odds_snapshots(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    today: date | None = None,
) -> bool:
    start = today or datetime.now(EASTERN).date()
    end = start + timedelta(days=ODDS_WINDOW_DAYS)
    logger.info("odds: snapshotting espn scoreboard %s to %s...", start, end)
    run_id = _start_ingestion_run(
        conn, ODDS_INGESTION_KIND, watermark_from=start, watermark_to=end,
        dry_run=dry_run,
    )

    events, failed_days = _fetch_window(start, end)
    if len(failed_days) == (end - start).days + 1:
        logger.error("odds: every espn scoreboard request failed")
        _finish_ingestion_run(conn, run_id, "failed", 0, notes="espn fetch failed")
        return False

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        # a day of slack either side: espn's dates filter is not strictly et.
        lookup_from, lookup_to = start - timedelta(days=1), end + timedelta(days=1)
        schedule_rows = _read_schedule_rows(cur, lookup_from, lookup_to)
        known_map = _read_known_map(cur, lookup_from, lookup_to)
        rows, mappings, unmapped = plan_odds_snapshot(events, schedule_rows, known_map)
        _batch_upsert(cur, EVENT_MAP_UPSERT_SQL, [_mapping_tuple(m) for m in mappings])
        written = _batch_upsert(
            cur, ODDS_INSERT_SQL, [_snapshot_tuple(r, run_id) for r in rows]
        )
    finally:
        cur.close()

    notes = "; ".join(
        part for part in (
            f"{unmapped} unmapped event(s)" if unmapped else "",
            f"fetch failed for {', '.join(failed_days)}" if failed_days else "",
        ) if part
    ) or None
    _finish_ingestion_run(conn, run_id, "succeeded", written, notes=notes)
    logger.info(
        "odds: %d row(s) from %d event(s), %d new mapping(s), %d unmapped%s",
        written, len(events), len(mappings), unmapped,
        " (dry run: nothing written)" if dry_run else "",
    )
    return True
