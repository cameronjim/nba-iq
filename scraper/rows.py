from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from config import (
    ABBR_TO_TEAM_ID,
    TEAM_ID_TO_ABBR,
    GAME_LOG_CORRECTION_WINDOW_DAYS,
)
from parsing import (
    _opt_int,
    _text_or_none,
    in_season,
    parse_game_date,
    parse_matchup,
    parse_minutes,
    season_start_date,
    season_type_from_game_id,
)

# tuple position of game_date in the two log row builders. Named rather than
# passed as bare 4s: an off-by-one here silently drops every row.
PLAYER_LOG_DATE_INDEX = 4
TEAM_LOG_DATE_INDEX = 4


def game_log_fetch_from(
    latest_logged_date: date | None,
    season: str,
    correction_window_days: int = GAME_LOG_CORRECTION_WINDOW_DAYS,
) -> date:
    # the watermark is the newest stored game date, walked back by the correction
    # window, and never past the season boundary.
    floor = season_start_date(season)
    if latest_logged_date is None:
        return floor
    return max(floor, latest_logged_date - timedelta(days=correction_window_days))


def split_rows_on_season_boundary(
    rows: Sequence[tuple], season: str, date_index: int
) -> tuple[list[tuple], list[tuple]]:
    # build_player_game_log_row stamps the REQUESTED season onto a row whose own
    # SEASON_YEAR is missing, so a stray row from another season would be stored
    # under this one and double-counted forever. Both halves are returned so the
    # caller can log what it refused.
    inside: list[tuple] = []
    outside: list[tuple] = []
    for row in rows:
        (inside if in_season(row[date_index], season) else outside).append(row)
    return inside, outside


def plan_stint_change(
    open_stint: tuple[str, date] | None,
    current_team_id: str,
    current_team_first_game_date: date,
    open_team_last_game_date: date | None,
) -> dict | None:
    # the previous stint ends on the last date he played for that team and the
    # new one starts on the first date he played for the new one, so the gap
    # between a trade and his debut belongs to neither.
    if open_stint is not None and open_stint[0] == current_team_id:
        return None

    change: dict = {
        "open_team_id": current_team_id,
        "open_valid_from": current_team_first_game_date,
        "close_team_id": None,
        "close_valid_from": None,
        "close_valid_to": None,
    }
    if open_stint is None:
        return change

    prev_team_id, prev_valid_from = open_stint
    close_to = open_team_last_game_date or prev_valid_from
    # a stint can never end before it began, nor on/after the next one starts
    close_to = max(close_to, prev_valid_from)
    close_to = min(close_to, max(prev_valid_from, current_team_first_game_date - timedelta(days=1)))
    change.update(
        close_team_id=prev_team_id,
        close_valid_from=prev_valid_from,
        close_valid_to=close_to,
    )
    return change


def plan_roster_snapshot(
    snapshot: Mapping[str, str],
    open_stints: Mapping[str, tuple[str, date]],
    snapshot_date: date,
) -> list[dict]:
    # the old stint closes the day before the snapshot, not on his last game for
    # that team: we observed that he is on the new roster today and never
    # observed when he left the old one.
    #
    # a player absent from every roster is NOT closed. Absence means unsigned or
    # one team's fetch failed, and those are indistinguishable here.
    changes: list[dict] = []
    for player_id in sorted(snapshot):
        team_id = snapshot[player_id]
        open_stint = open_stints.get(player_id)
        if open_stint is not None and open_stint[0] == team_id:
            continue

        change: dict = {
            "player_id": player_id,
            "open_team_id": team_id,
            "open_valid_from": snapshot_date,
            "close_team_id": None,
            "close_valid_from": None,
            "close_valid_to": None,
        }
        if open_stint is not None:
            prev_team_id, prev_valid_from = open_stint
            close_to = max(prev_valid_from, snapshot_date - timedelta(days=1))
            change.update(
                close_team_id=prev_team_id,
                close_valid_from=prev_valid_from,
                close_valid_to=close_to,
            )
        changes.append(change)
    return changes


def normalize_inactive_rows(rows: Sequence[Mapping]) -> list[dict]:
    # BoxScoreSummaryV3 reports personId/teamId, V2 reports PLAYER_ID/TEAM_ID.
    normalized: list[dict] = []
    for row in rows:
        player_id = row.get("personId", row.get("PLAYER_ID"))
        team_id = row.get("teamId", row.get("TEAM_ID"))
        if player_id in (None, ""):
            continue
        normalized.append(
            {
                "nba_player_id": str(player_id),
                "team_id": str(team_id) if team_id not in (None, "") else None,
            }
        )
    return normalized


def derive_game_status_rows(
    nba_game_id: str,
    played_rows: Sequence[Mapping],
    inactive_rows: Sequence[Mapping],
    source: str,
) -> list[dict]:
    # three populations merge into one row per rostered player: a game-log row
    # with no dnp_reason played; one WITH a dnp_reason dressed but did not play
    # (box scores set COMMENT only when a player did not appear); an
    # inactive-list entry did not play. A player in both the game log and the
    # inactive list keeps played and is still flagged listed_inactive, so the
    # contradiction stays visible.
    by_player: dict[str, dict] = {}

    for row in played_rows:
        player_id = str(row.get("nba_player_id") or "")
        if not player_id:
            continue
        dnp_reason = (row.get("dnp_reason") or "").strip() or None
        by_player[player_id] = {
            "nba_player_id": player_id,
            "nba_game_id": nba_game_id,
            "team_id": row.get("team_id"),
            "rostered": True,
            "listed_inactive": False,
            "started": row.get("started"),
            "played": dnp_reason is None,
            "dnp_reason": dnp_reason,
            "minutes": row.get("minutes"),
            "source": source,
        }

    for row in normalize_inactive_rows(inactive_rows):
        player_id = row["nba_player_id"]
        existing = by_player.get(player_id)
        if existing is not None:
            existing["listed_inactive"] = True
            continue
        by_player[player_id] = {
            "nba_player_id": player_id,
            "nba_game_id": nba_game_id,
            "team_id": row["team_id"],
            "rostered": True,
            "listed_inactive": True,
            "started": False,
            "played": False,
            "dnp_reason": None,
            "minutes": None,
            "source": source,
        }

    return list(by_player.values())


def schedule_rows_from_team_logs(
    team_rows: Sequence[Mapping], season: str
) -> list[dict]:
    # the fallback source: two rows per game, one per team, and only completed
    # games. Neutral-site games report an "@" MATCHUP for BOTH teams here, so a
    # claimed slot already held by another team takes the other slot instead:
    # the designation is then arbitrary but neither team vanishes from the row.
    by_game: dict[str, dict] = {}
    for row in team_rows:
        game_id = str(row.get("GAME_ID") or "").strip()
        game_date = parse_game_date(row.get("GAME_DATE"))
        if not game_id or game_date is None:
            continue

        is_home, opponent_abbr = parse_matchup(row.get("MATCHUP"))
        team_id = str(row.get("TEAM_ID") or "") or None
        team_abbr = (row.get("TEAM_ABBREVIATION") or "") or None

        entry = by_game.setdefault(
            game_id,
            {
                "nba_game_id": game_id,
                "season": season,
                "season_type": season_type_from_game_id(game_id),
                "game_date": game_date,
                "scheduled_at": None,
                "home_team_id": None,
                "away_team_id": None,
                "home_team_abbr": None,
                "away_team_abbr": None,
                "game_status": "Final",
                "postponed_status": None,
                "source": "leaguegamelog",
            },
        )
        if is_home is None:
            continue

        side, other = ("home", "away") if is_home else ("away", "home")
        if entry[f"{side}_team_id"] not in (None, team_id):
            side, other = other, side
        entry[f"{side}_team_id"] = team_id
        entry[f"{side}_team_abbr"] = team_abbr
        entry[f"{other}_team_abbr"] = entry[f"{other}_team_abbr"] or opponent_abbr

    return sorted(by_game.values(), key=lambda g: (g["game_date"], g["nba_game_id"]))


def schedule_rows_from_league_schedule(
    raw_rows: Sequence[Mapping], season: str
) -> list[dict]:
    # scheduleleaguev2 publishes the full season in advance, including games with
    # no box score yet, which is what makes same-day prediction possible. Its
    # columns are camelCase rather than the stats endpoints' SHOUTY_CASE.
    rows: list[dict] = []
    for raw in raw_rows:
        game_id = str(raw.get("gameId") or "").strip()
        game_date = parse_game_date(raw.get("gameDate"))
        if not game_id or game_date is None:
            continue

        scheduled_at = raw.get("gameDateTimeUTC") or None
        if scheduled_at:
            try:
                scheduled_at = datetime.fromisoformat(
                    str(scheduled_at).replace("Z", "+00:00")
                )
            except ValueError:
                scheduled_at = None

        rows.append(
            {
                "nba_game_id": game_id,
                "season": str(raw.get("seasonYear") or season),
                "season_type": season_type_from_game_id(game_id),
                "game_date": game_date,
                "scheduled_at": scheduled_at,
                "home_team_id": str(raw.get("homeTeam_teamId") or "") or None,
                "away_team_id": str(raw.get("awayTeam_teamId") or "") or None,
                "home_team_abbr": raw.get("homeTeam_teamTricode") or None,
                "away_team_abbr": raw.get("awayTeam_teamTricode") or None,
                "game_status": raw.get("gameStatusText") or None,
                # the NBA's own postponedStatus, where 'N' means NOT postponed
                # and is present on every future row.
                "postponed_status": _text_or_none(raw.get("postponedStatus")),
                "source": "scheduleleaguev2",
            }
        )
    return rows


def _parse_utc(val: object) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None


def schedule_rows_from_nba_web(
    next_data: Mapping, game_date: date, season: str
) -> list[dict]:
    # same row shape as schedule_rows_from_league_schedule. game_date is the
    # requested page date, which is the eastern game date; gameTimeEastern is
    # unusable because it carries a misleading Z suffix.
    page_props = (next_data.get("props") or {}).get("pageProps") or {}
    modules = (page_props.get("gameCardFeed") or {}).get("modules") or []
    rows: list[dict] = []
    for module in modules:
        for card in (module or {}).get("cards") or []:
            data = (card or {}).get("cardData")
            if not isinstance(data, Mapping):
                continue
            game_id = str(data.get("gameId") or "").strip()
            if not game_id:
                continue
            home = data.get("homeTeam") or {}
            away = data.get("awayTeam") or {}
            rows.append(
                {
                    "nba_game_id": game_id,
                    "season": str(data.get("seasonYear") or season),
                    "season_type": season_type_from_game_id(game_id),
                    "game_date": game_date,
                    "scheduled_at": _parse_utc(data.get("gameTimeUtc")),
                    "home_team_id": str(home.get("teamId") or "") or None,
                    "away_team_id": str(away.get("teamId") or "") or None,
                    "home_team_abbr": home.get("teamTricode") or None,
                    "away_team_abbr": away.get("teamTricode") or None,
                    "game_status": data.get("gameStatusText") or None,
                    "postponed_status": None,
                    "source": "nba_web",
                }
            )
    return rows


def roster_rows_from_nba_players_index(next_data: Mapping) -> dict[str, str]:
    # player id -> team id, in the shape fetch_roster_snapshot builds. free
    # agents carry no team and are skipped, never closed.
    page_props = (next_data.get("props") or {}).get("pageProps") or {}
    snapshot: dict[str, str] = {}
    for raw in page_props.get("players") or []:
        player_id = str(raw.get("PERSON_ID") or "").strip()
        team_id = str(raw.get("TEAM_ID") or "").strip()
        if not player_id or team_id in ("", "0") or team_id not in TEAM_ID_TO_ABBR:
            continue
        snapshot[player_id] = team_id
    return snapshot


def build_player_game_log_row(
    raw: Mapping, season: str, run_id: int | None, source: str = "playergamelogs"
) -> tuple | None:
    # also accepts a leaguegamelog player-mode record: the two endpoints share
    # every column read here except SEASON_YEAR, which falls back to the season
    # argument. Tuple order matches PLAYER_GAME_LOG_UPSERT_SQL.
    player_id = str(raw.get("PLAYER_ID") or "").strip()
    game_id = str(raw.get("GAME_ID") or "").strip()
    game_date = parse_game_date(raw.get("GAME_DATE"))
    if not player_id or not game_id or game_date is None:
        return None

    is_home, opponent_abbr = parse_matchup(raw.get("MATCHUP"))
    return (
        player_id,
        game_id,
        str(raw.get("SEASON_YEAR") or season),
        season_type_from_game_id(game_id),
        game_date,
        str(raw.get("TEAM_ID") or "") or None,
        raw.get("TEAM_ABBREVIATION") or None,
        ABBR_TO_TEAM_ID.get(opponent_abbr or ""),
        is_home,
        # started and dnp_reason: the league-wide log cannot report either, and
        # the upsert preserves whatever a box-score pass already stored.
        None,
        parse_minutes(raw.get("MIN")),
        _opt_int(raw.get("PTS")),
        _opt_int(raw.get("REB")),
        _opt_int(raw.get("AST")),
        _opt_int(raw.get("STL")),
        _opt_int(raw.get("BLK")),
        _opt_int(raw.get("TOV")),
        _opt_int(raw.get("FGM")),
        _opt_int(raw.get("FGA")),
        _opt_int(raw.get("FG3M")),
        _opt_int(raw.get("FG3A")),
        _opt_int(raw.get("FTM")),
        _opt_int(raw.get("FTA")),
        _opt_int(raw.get("PLUS_MINUS")),
        None,
        source,
        run_id,
    )


def supplement_player_log_rows(
    primary_rows: Sequence[tuple],
    league_raw: Sequence[Mapping],
    season: str,
    run_id: int | None,
) -> list[tuple]:
    # playergamelogs silently omits zero-minute appearances that leaguegamelog
    # reports. Only the missing (player, game) keys are taken: where both
    # endpoints answer, playergamelogs wins because its MIN has seconds.
    seen = {(row[0], row[1]) for row in primary_rows}
    supplements: list[tuple] = []
    for raw in league_raw:
        row = build_player_game_log_row(raw, season, run_id, source="leaguegamelog")
        if row is not None and (row[0], row[1]) not in seen:
            seen.add((row[0], row[1]))
            supplements.append(row)
    return supplements


def build_team_game_log_row(
    raw: Mapping, season: str, run_id: int | None
) -> tuple | None:
    team_id = str(raw.get("TEAM_ID") or "").strip()
    game_id = str(raw.get("GAME_ID") or "").strip()
    game_date = parse_game_date(raw.get("GAME_DATE"))
    if not team_id or not game_id or game_date is None:
        return None

    is_home, opponent_abbr = parse_matchup(raw.get("MATCHUP"))
    return (
        team_id,
        game_id,
        season,
        season_type_from_game_id(game_id),
        game_date,
        raw.get("TEAM_ABBREVIATION") or None,
        ABBR_TO_TEAM_ID.get(opponent_abbr or ""),
        is_home,
        parse_minutes(raw.get("MIN")),
        _opt_int(raw.get("PTS")),
        _opt_int(raw.get("REB")),
        _opt_int(raw.get("AST")),
        _opt_int(raw.get("STL")),
        _opt_int(raw.get("BLK")),
        _opt_int(raw.get("TOV")),
        _opt_int(raw.get("FGM")),
        _opt_int(raw.get("FGA")),
        _opt_int(raw.get("FG3M")),
        _opt_int(raw.get("FG3A")),
        _opt_int(raw.get("FTM")),
        _opt_int(raw.get("FTA")),
        _opt_int(raw.get("PLUS_MINUS")),
        "leaguegamelog",
        run_id,
    )
