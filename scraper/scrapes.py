import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Generic, Protocol, TypeVar
from zoneinfo import ZoneInfo

import psycopg2
from bs4 import BeautifulSoup, Tag

from config import SEASON, TEAM_META
from database import _batch_upsert, maybe_write_cursor
from fetching import (
    _fetch_cbs_positions,
    _fetch_espn_scoreboard,
    _fetch_nba_web_players,
    _fetch_nba_positions,
    fetch_advanced_team_stats,
    fetch_injury_page,
    fetch_player_stats,
    fetch_team_stats,
)
from parsing import (
    _normalize_name,
    _pct,
    _resolve_team_abbr,
    _safe_float,
    canonical_player_name,
    cbs_team_abbr,
    cleared_player_ids,
    clearances_for_report,
    normalize_injury_status,
    resolve_positions,
)
from rows import player_rows_from_nba_players_index

logger = logging.getLogger(__name__)

# a richer stored position (e.g. CBS's 'SF,PF') is never replaced by the index's
# coarse one; the headshot and name of an existing row are left alone too.
WEB_PLAYERS_UPSERT_SQL = """
    INSERT INTO players (nba_id, name, team, position, headshot_url)
    VALUES %s
    ON CONFLICT (nba_id) DO UPDATE SET
        team = EXCLUDED.team,
        position = COALESCE(NULLIF(players.position, ''), EXCLUDED.position),
        headshot_url = COALESCE(players.headshot_url, EXCLUDED.headshot_url),
        updated_at = NOW()
"""


def scrape_players_from_web(
    conn: psycopg2.extensions.connection, dry_run: bool = False
) -> int:
    # fallback for when stats.nba.com is unreachable: the nba.com players index
    # carries the current team of every rostered player, including offseason
    # movers and rookies, but no stats. free agents and retired players are
    # absent from it and keep their rows untouched.
    logger.info("fetching players from the nba.com players index...")
    try:
        rows = player_rows_from_nba_players_index(_fetch_nba_web_players())
    except Exception as e:  # noqa: BLE001 - nothing else to fall back to
        logger.error("nba.com players index failed: %s", e)
        return 0
    if not rows:
        logger.warning("nba.com players index had no rostered players")
        return 0

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        written = _batch_upsert(cur, WEB_PLAYERS_UPSERT_SQL, rows)
    finally:
        cur.close()
    logger.info(
        "upserted %d players from nba.com%s",
        written,
        " (dry run: nothing written)" if dry_run else "",
    )
    return written


def scrape_players(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    stats_reachable: bool = True,
) -> None:
    if not stats_reachable:
        scrape_players_from_web(conn, dry_run=dry_run)
        return

    logger.info("fetching player stats...")

    logger.info("fetching player positions from CBS Sports...")
    cbs_positions = _fetch_cbs_positions()
    logger.info("got positions for %d players from CBS Sports", len(cbs_positions))

    logger.info("fetching fallback positions from NBA playerindex...")
    nba_positions = _fetch_nba_positions()
    logger.info("got fallback positions for %d players from NBA", len(nba_positions))

    time.sleep(2)

    try:
        stats = fetch_player_stats(SEASON)
        df = stats.get_data_frames()[0]
        logger.info("got %d players from NBA API", len(df))
    except Exception as e:
        logger.error("error fetching player stats: %s", e)
        scrape_players_from_web(conn, dry_run=dry_run)
        return

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    count = 0
    for _, row in df.iterrows():
        player_id = str(row["PLAYER_ID"])
        cur.execute(
            """
            INSERT INTO players (nba_id, name, team, position,
                                 points_per_game, rebounds_per_game, assists_per_game, steals_per_game, blocks_per_game,
                                 field_goal_percentage, three_point_percentage, free_throw_percentage, three_pointers_made,
                                 turnovers_per_game, minutes_per_game, games_played,
                                 headshot_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (nba_id) DO UPDATE SET
                name = EXCLUDED.name, team = EXCLUDED.team, position = EXCLUDED.position,
                points_per_game = EXCLUDED.points_per_game, rebounds_per_game = EXCLUDED.rebounds_per_game,
                assists_per_game = EXCLUDED.assists_per_game, steals_per_game = EXCLUDED.steals_per_game,
                blocks_per_game = EXCLUDED.blocks_per_game, field_goal_percentage = EXCLUDED.field_goal_percentage,
                three_point_percentage = EXCLUDED.three_point_percentage, free_throw_percentage = EXCLUDED.free_throw_percentage,
                three_pointers_made = EXCLUDED.three_pointers_made, turnovers_per_game = EXCLUDED.turnovers_per_game,
                minutes_per_game = EXCLUDED.minutes_per_game, games_played = EXCLUDED.games_played,
                headshot_url = EXCLUDED.headshot_url, updated_at = NOW()
            """,
            (
                player_id,
                row["PLAYER_NAME"],
                row.get("TEAM_ABBREVIATION", ""),
                resolve_positions(
                    cbs_positions.get(_normalize_name(row["PLAYER_NAME"]), ""),
                    nba_positions.get(player_id, ""),
                ),
                _safe_float(row.get("PTS")),
                _safe_float(row.get("REB")),
                _safe_float(row.get("AST")),
                _safe_float(row.get("STL")),
                _safe_float(row.get("BLK")),
                _pct(row.get("FG_PCT")),
                _pct(row.get("FG3_PCT")),
                _pct(row.get("FT_PCT")),
                _safe_float(row.get("FG3M")),
                _safe_float(row.get("TOV")),
                _safe_float(row.get("MIN")),
                int(row.get("GP", 0)),
                f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png",
            ),
        )
        count += 1

    cur.execute(
        "DELETE FROM my_roster WHERE player_id IN "
        "(SELECT id FROM players WHERE nba_id IS NULL)"
    )
    deleted_roster = cur.rowcount
    cur.execute("DELETE FROM players WHERE nba_id IS NULL")
    deleted = cur.rowcount
    if deleted > 0:
        logger.info(
            "cleaned up %d old seed players (%d roster entries)", deleted, deleted_roster
        )

    cur.close()
    logger.info("upserted %d players", count)


def scrape_teams(
    conn: psycopg2.extensions.connection, dry_run: bool = False
) -> None:
    logger.info("fetching team stats...")
    time.sleep(2)
    try:
        stats = fetch_team_stats(SEASON)
        df = stats.get_data_frames()[0]
        logger.info("got %d teams from NBA API", len(df))
    except Exception as e:
        logger.error("error fetching team stats: %s", e)
        return

    time.sleep(2)
    adv_ratings: dict[str, dict[str, float]] = {}
    try:
        adv_stats = fetch_advanced_team_stats(SEASON)
        adv_df = adv_stats.get_data_frames()[0]
        for _, row in adv_df.iterrows():
            tid = str(row["TEAM_ID"])
            adv_ratings[tid] = {
                "defensive_rating": _safe_float(row.get("DEF_RATING")),
                "offensive_rating": _safe_float(row.get("OFF_RATING")),
                "net_rating": _safe_float(row.get("NET_RATING")),
            }
        logger.info("got advanced ratings for %d teams", len(adv_ratings))
    except Exception as e:
        logger.warning("could not fetch advanced team stats: %s", e)

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    count = 0
    for _, row in df.iterrows():
        team_id = str(row["TEAM_ID"])
        team_name = row.get("TEAM_NAME", "")
        abbr = row.get("TEAM_ABBREVIATION") or _resolve_team_abbr(team_id, team_name)
        meta = TEAM_META.get(abbr, {})
        ratings = adv_ratings.get(team_id, {})

        if not abbr:
            logger.warning("could not resolve abbreviation for team %s (%s)", team_name, team_id)

        cur.execute(
            """
            INSERT INTO teams (nba_id, name, abbreviation, conference, division,
                               wins, losses,
                               points_per_game, rebounds_per_game, assists_per_game, steals_per_game, blocks_per_game,
                               field_goal_percentage, three_point_percentage, free_throw_percentage, turnovers_per_game,
                               defensive_rating, offensive_rating, net_rating,
                               logo_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (nba_id) DO UPDATE SET
                name = EXCLUDED.name, abbreviation = EXCLUDED.abbreviation,
                conference = EXCLUDED.conference, division = EXCLUDED.division,
                wins = EXCLUDED.wins, losses = EXCLUDED.losses,
                points_per_game = EXCLUDED.points_per_game, rebounds_per_game = EXCLUDED.rebounds_per_game,
                assists_per_game = EXCLUDED.assists_per_game, steals_per_game = EXCLUDED.steals_per_game,
                blocks_per_game = EXCLUDED.blocks_per_game, field_goal_percentage = EXCLUDED.field_goal_percentage,
                three_point_percentage = EXCLUDED.three_point_percentage, free_throw_percentage = EXCLUDED.free_throw_percentage,
                turnovers_per_game = EXCLUDED.turnovers_per_game, defensive_rating = EXCLUDED.defensive_rating,
                offensive_rating = EXCLUDED.offensive_rating, net_rating = EXCLUDED.net_rating,
                logo_url = EXCLUDED.logo_url, updated_at = NOW()
            """,
            (
                team_id,
                row.get("TEAM_NAME", meta.get("full_name", "")),
                abbr,
                meta.get("conference", ""),
                meta.get("division", ""),
                int(row.get("W", 0)),
                int(row.get("L", 0)),
                _safe_float(row.get("PTS")),
                _safe_float(row.get("REB")),
                _safe_float(row.get("AST")),
                _safe_float(row.get("STL")),
                _safe_float(row.get("BLK")),
                _pct(row.get("FG_PCT")),
                _pct(row.get("FG3_PCT")),
                _pct(row.get("FT_PCT")),
                _safe_float(row.get("TOV")),
                ratings.get("defensive_rating", 0.0),
                ratings.get("offensive_rating", 0.0),
                ratings.get("net_rating", 0.0),
                f"https://cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg",
            ),
        )
        count += 1

    cur.execute("DELETE FROM teams WHERE nba_id IS NULL")
    deleted = cur.rowcount
    if deleted > 0:
        logger.info("cleaned up %d old seed teams", deleted)

    cur.close()
    logger.info("upserted %d teams", count)


def scrape_scoreboard(
    conn: psycopg2.extensions.connection, dry_run: bool = False
) -> None:
    logger.info("fetching games from ESPN (2 days back → 7 days ahead)...")

    et = ZoneInfo("America/New_York")
    today = datetime.now(et)

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    total = 0

    for offset in range(-2, 8):
        day = today + timedelta(days=offset)
        date_str = day.strftime("%Y%m%d")
        label = day.strftime("%Y-%m-%d")

        games = _fetch_espn_scoreboard(date_str)
        for g in games:
            cur.execute(
                """
                INSERT INTO games (nba_game_id, home_team, away_team, game_date,
                                   home_score, away_score, status, arena, updated_at)
                VALUES (%(nba_game_id)s, %(home_team)s, %(away_team)s, %(game_date)s,
                        %(home_score)s, %(away_score)s, %(status)s, %(arena)s, NOW())
                ON CONFLICT (nba_game_id) DO UPDATE SET
                    home_team  = EXCLUDED.home_team,
                    away_team  = EXCLUDED.away_team,
                    game_date  = EXCLUDED.game_date,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    status     = EXCLUDED.status,
                    arena      = EXCLUDED.arena,
                    updated_at = NOW()
                """,
                g,
            )
        if games:
            logger.info("%s: %d games", label, len(games))
        total += len(games)

    cur.execute("DELETE FROM games WHERE nba_game_id IS NULL")
    deleted = cur.rowcount
    if deleted > 0:
        logger.info("cleaned up %d old seed games", deleted)

    cur.close()
    conn.commit()
    logger.info("total games upserted: %d", total)


@dataclass(frozen=True)
class CbsInjuryRow:
    player_name: str
    status: str
    injury: str
    updated: str
    team_abbr: str | None


class NamedInjuryRow(Protocol):
    @property
    def player_name(self) -> str: ...

    @property
    def team_abbr(self) -> str | None: ...


InjuryRowT = TypeVar("InjuryRowT", bound=NamedInjuryRow)


@dataclass
class InjuryNameMatch(Generic[InjuryRowT]):
    matched: list[tuple[str, InjuryRowT]] = field(default_factory=list)
    unmatched: list[InjuryRowT] = field(default_factory=list)
    ambiguous: list[InjuryRowT] = field(default_factory=list)


# order matters: "injury status" has to claim its column before "injury" does.
_CBS_HEADER_KEYS: tuple[tuple[str, str], ...] = (
    ("player", "player"),
    ("updated", "updated"),
    ("injury status", "status"),
    ("injury", "injury"),
    ("position", "position"),
)
_CBS_REQUIRED_COLUMNS = ("player", "status")
# the four-column layout cbs served before the updated column appeared.
_CBS_LEGACY_COLUMNS = {"player": 0, "position": 1, "injury": 2, "status": 3}
_CBS_TEAM_HREF = re.compile(r"/nba/teams/([A-Za-z]+)/")


def map_cbs_injury_columns(headers: Sequence[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, header in enumerate(headers):
        text = header.strip().lower()
        for phrase, key in _CBS_HEADER_KEYS:
            if phrase in text and key not in columns:
                columns[key] = index
                break
    return columns


def _cbs_table_team(table: Tag) -> str | None:
    title = table.select_one(".TableBase-title")
    link = title.select_one('a[href*="/nba/teams/"]') if title else None
    match = _CBS_TEAM_HREF.search(link.get("href", "")) if link else None
    return cbs_team_abbr(match.group(1)) if match else None


def _cell_text(cells: Sequence[Tag], columns: Mapping[str, int], key: str) -> str:
    index = columns.get(key)
    if index is None or index >= len(cells):
        return ""
    return cells[index].get_text(" ", strip=True)


def _parse_cbs_injury_rows(html: str) -> list[CbsInjuryRow]:
    return _parse_cbs_injury_tables(html)[0]


def _parse_cbs_injury_tables(html: str) -> tuple[list[CbsInjuryRow], int]:
    """every row the page yields, plus the number of tables skipped for a bad header."""
    soup = BeautifulSoup(html, "html.parser")
    parsed: list[CbsInjuryRow] = []
    skipped_tables = 0
    for table in soup.select("div.TableBase"):
        headers = [th.get_text(" ", strip=True) for th in table.select("th")]
        if headers:
            columns = map_cbs_injury_columns(headers)
            missing = [key for key in _CBS_REQUIRED_COLUMNS if key not in columns]
            if missing:
                # a layout change has to be loud: silently reading the wrong
                # column is how every status became an injury name.
                logger.warning(
                    "cbs injury table is missing %s column(s), skipping it; headers: %s",
                    ", ".join(missing),
                    headers,
                )
                skipped_tables += 1
                continue
            min_cells = max(columns.values()) + 1
        else:
            columns = _CBS_LEGACY_COLUMNS
            min_cells = len(_CBS_LEGACY_COLUMNS)

        team_abbr = _cbs_table_team(table)
        for row in table.select("tr.TableBase-bodyTr"):
            cells = row.select("td")
            # without headers only the exact legacy width is safe to read by position.
            if len(cells) < min_cells or (not headers and len(cells) != min_cells):
                continue

            player_cell = cells[columns["player"]]
            name_el = player_cell.select_one("span.CellPlayerName--long a") or player_cell.select_one("a")
            player_name = name_el.get_text(strip=True) if name_el else ""
            if not player_name:
                continue
            parsed.append(
                CbsInjuryRow(
                    player_name=player_name,
                    status=_cell_text(cells, columns, "status") or "Day-To-Day",
                    injury=_cell_text(cells, columns, "injury") or "Unknown",
                    updated=_cell_text(cells, columns, "updated"),
                    team_abbr=team_abbr,
                )
            )
    return parsed, skipped_tables


def index_players_by_canonical_name(
    players: Iterable[tuple[object, object, object]],
) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    for nba_id, name, team in players:
        if not nba_id or not name:
            continue
        key = canonical_player_name(str(name))
        index.setdefault(key, []).append((str(nba_id), str(team or "").strip().upper()))
    return index


def match_injury_rows(
    rows: Iterable[InjuryRowT],
    players_by_name: Mapping[str, Sequence[tuple[str, str]]],
) -> InjuryNameMatch[InjuryRowT]:
    result: InjuryNameMatch[InjuryRowT] = InjuryNameMatch()
    seen: set[str] = set()
    for row in rows:
        candidates = list(players_by_name.get(canonical_player_name(row.player_name), ()))
        if len(candidates) > 1 and row.team_abbr:
            on_team = [c for c in candidates if c[1] == row.team_abbr]
            candidates = on_team if len(on_team) == 1 else candidates
        if not candidates:
            result.unmatched.append(row)
            continue
        if len(candidates) > 1:
            result.ambiguous.append(row)
            continue
        nba_id = candidates[0][0]
        # one report row per player even if the source lists him twice.
        if nba_id in seen:
            continue
        seen.add(nba_id)
        result.matched.append((nba_id, row))
    return result


def scrape_injuries(
    conn: psycopg2.extensions.connection, dry_run: bool = False
) -> None:
    logger.info("fetching injury report from CBS Sports...")
    time.sleep(1)
    try:
        html = fetch_injury_page()
    except Exception as e:  # noqa: BLE001 - any failure means the page is unusable
        logger.warning("error fetching injuries, leaving statuses untouched: %s", e)
        return

    parsed, skipped_tables = _parse_cbs_injury_tables(html)
    # a failed or empty scrape must never read as "everyone is healthy".
    if not parsed:
        logger.warning("injury page parsed to zero rows, leaving statuses untouched")
        return

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    cur.execute("SELECT nba_id, name FROM players WHERE injury_status IS NOT NULL")
    previous_rows = [(str(nba_id), str(name or "")) for nba_id, name in cur.fetchall() if nba_id]
    previously_listed = [nba_id for nba_id, _ in previous_rows]
    # cbs publishes names only, so they are resolved to ids here with a read,
    # which also lets a dry run report the same clearances production writes.
    cur.execute("SELECT nba_id, name, team FROM players WHERE nba_id IS NOT NULL")
    match = match_injury_rows(parsed, index_players_by_canonical_name(cur.fetchall()))
    if match.ambiguous:
        logger.warning(
            "cbs injuries: %d row(s) matched several players and were skipped: %s",
            len(match.ambiguous),
            ", ".join(f"{r.player_name} ({r.team_abbr or '?'})" for r in match.ambiguous),
        )
    if match.unmatched:
        logger.info(
            "cbs injuries: %d row(s) matched no player: %s",
            len(match.unmatched),
            ", ".join(r.player_name for r in match.unmatched),
        )
    if not match.matched:
        logger.warning("injury page matched zero players, leaving statuses untouched")
        cur.close()
        return
    currently_listed = [nba_id for nba_id, _ in match.matched]

    cur.execute("UPDATE players SET injury_status = NULL, injury_detail = NULL")

    for nba_id, row in match.matched:
        logger.debug(
            "cbs injury %s (%s): %s, updated %s",
            row.player_name, nba_id, row.status, row.updated,
        )
        cur.execute(
            """
            UPDATE players SET injury_status = %s, injury_detail = %s,
                updated_at = NOW()
            WHERE nba_id = %s
            """,
            (row.status, row.injury, nba_id),
        )
        # append-only history, never an upsert: "what did we know at 6am" is
        # the question the model asks, and overwriting destroys the answer.
        cur.execute(
            """
            INSERT INTO player_injury_reports (nba_player_id, captured_at,
                                               status_raw, status_normalized,
                                               reason, source)
            VALUES (%s, NOW(), %s, %s, %s, 'cbssports')
            """,
            (nba_id, row.status, normalize_injury_status(row.status), row.injury),
        )
    count = logged = len(match.matched)

    # a recovered player just disappears from the page, so his clearance has to be
    # written explicitly or his last 'out' row stands forever. a partial page is
    # not a recovery: a skipped table or an unmatched name means absence from the
    # matched set says nothing about the player.
    cleared = clearances_for_report(
        previous_rows, currently_listed,
        unmatched_names=[r.player_name for r in match.unmatched],
        complete=skipped_tables == 0,
    )
    if skipped_tables:
        logger.warning(
            "cbs injuries: %d table(s) skipped, so no clearances were written this run",
            skipped_tables,
        )
    for nba_id in cleared:
        cur.execute(
            """
            INSERT INTO player_injury_reports (nba_player_id, captured_at,
                                               status_raw, status_normalized,
                                               reason, source)
            VALUES (%s, NOW(), 'cleared', 'cleared', NULL, 'cbssports')
            """,
            (nba_id,),
        )

    cur.close()
    logger.info(
        "updated %d player injuries%s, logged %d report row(s), cleared %d player(s)",
        count,
        " (dry run: no rows written)" if dry_run else "",
        logged,
        len(cleared),
    )
