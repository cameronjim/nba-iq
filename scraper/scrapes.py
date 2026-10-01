import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
from bs4 import BeautifulSoup

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


def scrape_injuries(
    conn: psycopg2.extensions.connection, dry_run: bool = False
) -> None:
    logger.info("fetching injury report from CBS Sports...")
    time.sleep(1)
    try:
        html = fetch_injury_page()
    except Exception as e:
        logger.error("error fetching injuries: %s", e)
        return

    soup = BeautifulSoup(html, "html.parser")

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    cur.execute("UPDATE players SET injury_status = NULL, injury_detail = NULL")

    count = 0
    logged = 0
    tables = soup.select("div.TableBase")
    for table in tables:
        rows = table.select("tr.TableBase-bodyTr")
        for row in rows:
            cells = row.select("td")
            if len(cells) < 4:
                continue

            name_el = cells[0].select_one("span.CellPlayerName--long a") or cells[0].select_one("a")
            player_name = name_el.get_text(strip=True) if name_el else ""
            injury_detail = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            injury_status = cells[3].get_text(strip=True) if len(cells) > 3 else "Day-To-Day"

            if not player_name:
                continue

            status = injury_status or "Day-To-Day"
            detail = injury_detail or "Unknown"
            # RETURNING nba_id because CBS publishes names only, and the players
            # table is the only place the name -> NBA id mapping exists.
            cur.execute(
                """
                UPDATE players SET injury_status = %s, injury_detail = %s,
                    updated_at = NOW()
                WHERE LOWER(name) = LOWER(%s)
                RETURNING nba_id
                """,
                (status, detail, player_name),
            )
            matched = cur.fetchall()
            if matched:
                count += len(matched)

            # append-only history, never an upsert: "what did we know at 6am" is
            # the question the model asks, and overwriting destroys the answer.
            for (nba_id,) in matched:
                if not nba_id:
                    continue
                logged += 1
                cur.execute(
                    """
                    INSERT INTO player_injury_reports (nba_player_id, captured_at,
                                                       status_raw, status_normalized,
                                                       reason, source)
                    VALUES (%s, NOW(), %s, %s, %s, 'cbssports')
                    """,
                    (str(nba_id), status, normalize_injury_status(status), detail),
                )

    cur.close()
    logger.info(
        "updated %d player injuries%s, logged %d report row(s)",
        count,
        " (dry run: no rows written)" if dry_run else "",
        logged,
    )
