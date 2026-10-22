import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date

import psycopg2

from config import SEASON, SEASON_TYPE_REGULAR, TEAM_ID_TO_ABBR
from database import _batch_upsert, maybe_write_cursor
from rows import headshot_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayerSeasonLine:
    games_played: int
    minutes_per_game: float
    points_per_game: float
    rebounds_per_game: float
    assists_per_game: float
    steals_per_game: float
    blocks_per_game: float
    turnovers_per_game: float
    three_pointers_made: float
    field_goal_percentage: float
    three_point_percentage: float
    free_throw_percentage: float
    last_team_abbr: str | None


# the stat columns of players, in upsert order after nba_id, name, team, position.
STAT_COLUMNS = (
    "points_per_game",
    "rebounds_per_game",
    "assists_per_game",
    "steals_per_game",
    "blocks_per_game",
    "field_goal_percentage",
    "three_point_percentage",
    "free_throw_percentage",
    "three_pointers_made",
    "turnovers_per_game",
    "minutes_per_game",
    "games_played",
)

UPSERT_COLUMNS = ("nba_id", "name", "team", "position", *STAT_COLUMNS, "headshot_url")

ZERO_LINE = PlayerSeasonLine(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None)

LOG_COLUMNS = (
    "nba_player_id", "season", "season_type", "game_date", "team_abbr", "minutes",
    "pts", "reb", "ast", "stl", "blk", "tov", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
)

_COUNTING = LOG_COLUMNS[5:]


def _number(val: object) -> float:
    return 0.0 if val is None else float(val)


def _per_game(total: float, games: int) -> float:
    return round(total / games, 1)


def _pct(made: float, attempted: float) -> float:
    return round(made / attempted * 100, 1) if attempted else 0.0


def season_averages(
    log_rows: Iterable[Mapping], season: str = SEASON
) -> dict[str, PlayerSeasonLine]:
    # regular-season appearances only, so a preseason or playoff line never
    # bleeds into the table; a dnp has no minutes and is not a game played.
    totals: dict[str, dict[str, float]] = {}
    games: dict[str, int] = {}
    last_team: dict[str, tuple[date, str | None]] = {}
    for row in log_rows:
        if row.get("season") != season or row.get("season_type") != SEASON_TYPE_REGULAR:
            continue
        if _number(row.get("minutes")) <= 0:
            continue
        player_id = str(row["nba_player_id"])
        sums = totals.setdefault(player_id, dict.fromkeys(_COUNTING, 0.0))
        for key in _COUNTING:
            sums[key] += _number(row.get(key))
        games[player_id] = games.get(player_id, 0) + 1
        game_date = row["game_date"]
        if player_id not in last_team or game_date >= last_team[player_id][0]:
            last_team[player_id] = (game_date, row.get("team_abbr") or None)

    lines: dict[str, PlayerSeasonLine] = {}
    for player_id, sums in totals.items():
        played = games[player_id]
        lines[player_id] = PlayerSeasonLine(
            games_played=played,
            minutes_per_game=_per_game(sums["minutes"], played),
            points_per_game=_per_game(sums["pts"], played),
            rebounds_per_game=_per_game(sums["reb"], played),
            assists_per_game=_per_game(sums["ast"], played),
            steals_per_game=_per_game(sums["stl"], played),
            blocks_per_game=_per_game(sums["blk"], played),
            turnovers_per_game=_per_game(sums["tov"], played),
            three_pointers_made=_per_game(sums["fg3m"], played),
            field_goal_percentage=_pct(sums["fgm"], sums["fga"]),
            three_point_percentage=_pct(sums["fg3m"], sums["fg3a"]),
            free_throw_percentage=_pct(sums["ftm"], sums["fta"]),
            last_team_abbr=last_team[player_id][1],
        )
    return lines


def plan_players_refresh(
    averages: Mapping[str, PlayerSeasonLine],
    roster: Mapping[str, str],
    identities: Mapping[str, tuple[str | None, str | None, str | None]],
    existing: Mapping[str, str | None],
    keep: Collection[str] = (),
) -> tuple[list[dict], list[str]]:
    # one row per rostered player (zeros before his first game) and per player
    # who left the roster after playing this season; everyone else in players
    # is deleted unless keep (a fantasy roster) still holds him. an empty
    # roster means the stint read failed, so nothing is deleted.
    upserts: list[dict] = []
    for player_id in sorted(set(roster) | set(averages)):
        line = averages.get(player_id, ZERO_LINE)
        team = roster.get(player_id) or line.last_team_abbr or existing.get(player_id)
        name, position, headshot = identities.get(player_id, (None, None, None))
        if not name and player_id not in existing:
            # a new row needs a name; he gets one after his first box score.
            continue
        row = {
            "nba_id": player_id,
            "name": name or "",
            "team": team,
            "position": position or "",
            "headshot_url": headshot or headshot_url(player_id),
        }
        row.update({column: getattr(line, column) for column in STAT_COLUMNS})
        upserts.append(row)

    if not roster:
        return upserts, []
    kept = {row["nba_id"] for row in upserts} | set(keep)
    deletes = sorted(player_id for player_id in existing if player_id not in kept)
    return upserts, deletes


# a richer stored position (cbs's 'SF,PF') is never replaced by a box-score
# slot, a name is never blanked, and the injury columns belong to the reconcile.
PLAYERS_REFRESH_UPSERT_SQL = f"""
    INSERT INTO players ({", ".join(UPSERT_COLUMNS)})
    VALUES %s
    ON CONFLICT (nba_id) DO UPDATE SET
        name = COALESCE(NULLIF(EXCLUDED.name, ''), players.name),
        team = EXCLUDED.team,
        position = COALESCE(NULLIF(players.position, ''), EXCLUDED.position),
        {", ".join(f"{column} = EXCLUDED.{column}" for column in STAT_COLUMNS)},
        headshot_url = EXCLUDED.headshot_url,
        updated_at = NOW()
"""

PLAYERS_DELETE_SQL = """
    DELETE FROM players
     WHERE nba_id = ANY(%s)
"""

SEASON_LOGS_SQL = f"""
    SELECT {", ".join(LOG_COLUMNS)}
      FROM player_game_logs
     WHERE season = %s
       AND season_type = %s
"""

OPEN_STINTS_SQL = """
    SELECT s.nba_player_id, s.team_id, t.abbreviation
      FROM player_team_stints s
      LEFT JOIN teams t
        ON t.nba_id = s.team_id
     WHERE s.valid_to IS NULL
"""

IDENTITIES_SQL = """
    SELECT nba_player_id, name, position, headshot_url
      FROM player_identities
"""

EXISTING_PLAYERS_SQL = """
    SELECT nba_id, name, team, position
      FROM players
     WHERE nba_id IS NOT NULL
"""

FANTASY_ROSTER_PLAYERS_SQL = """
    SELECT DISTINCT p.nba_id
      FROM my_roster r
      JOIN players p
        ON p.id = r.player_id
     WHERE p.nba_id IS NOT NULL
"""


def _read(
    conn: psycopg2.extensions.connection, sql: str, params: tuple = ()
) -> list[tuple]:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def refresh_players_from_logs(
    conn: psycopg2.extensions.connection, season: str = SEASON, dry_run: bool = False
) -> dict:
    logs = [
        dict(zip(LOG_COLUMNS, row))
        for row in _read(conn, SEASON_LOGS_SQL, (season, SEASON_TYPE_REGULAR))
    ]
    averages = season_averages(logs, season)

    roster: dict[str, str] = {}
    for player_id, team_id, abbr in _read(conn, OPEN_STINTS_SQL):
        team_abbr = abbr or TEAM_ID_TO_ABBR.get(str(team_id))
        if team_abbr:
            roster[str(player_id)] = team_abbr

    # the players table's own names stand in until a box score lists him.
    existing: dict[str, str | None] = {}
    identities: dict[str, tuple[str | None, str | None, str | None]] = {}
    for nba_id, name, team, position in _read(conn, EXISTING_PLAYERS_SQL):
        existing[str(nba_id)] = team
        identities[str(nba_id)] = (name, position, None)
    for player_id, name, position, headshot in _read(conn, IDENTITIES_SQL):
        identities[str(player_id)] = (name, position, headshot)
    keep = {str(row[0]) for row in _read(conn, FANTASY_ROSTER_PLAYERS_SQL)}

    upserts, deletes = plan_players_refresh(averages, roster, identities, existing, keep)

    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        written = _batch_upsert(
            cur,
            PLAYERS_REFRESH_UPSERT_SQL,
            [tuple(row[column] for column in UPSERT_COLUMNS) for row in upserts],
        )
        if deletes:
            cur.execute(PLAYERS_DELETE_SQL, (deletes,))
    finally:
        cur.close()

    if not roster:
        logger.warning("players refresh: no open stints, so no player was deleted")
    logger.info(
        "players refresh for %s: %d upserted (%d rostered, %d with games), %d deleted%s",
        season, written, len(roster), len(averages), len(deletes),
        " (dry run: nothing written)" if dry_run else "",
    )
    return {
        "season": season,
        "rostered": len(roster),
        "with_games": len(averages),
        "upserted": written,
        "deleted": len(deletes),
    }
