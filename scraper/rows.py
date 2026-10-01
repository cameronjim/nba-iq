from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta

from config import (
    ABBR_TO_TEAM_ID,
    TEAM_ID_TO_ABBR,
    GAME_LOG_CORRECTION_WINDOW_DAYS,
    POSTSEASON_EARLIEST_MONTH,
    SEASON_TYPE_REGULAR,
    SEASON_TYPES_INGESTED,
)
from parsing import (
    _opt_int,
    _text_or_none,
    in_season,
    parse_game_date,
    parse_matchup,
    parse_minutes,
    resolve_positions,
    season_start_date,
    season_start_year,
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


def season_types_to_fetch(
    season: str,
    today: date,
    season_types: Sequence[str] = SEASON_TYPES_INGESTED,
) -> tuple[str, ...]:
    # the regular season is always fetched; a postseason type only once its
    # games can exist, so an october run does not spend two empty requests each.
    postseason_from = date(season_start_year(season) + 1, POSTSEASON_EARLIEST_MONTH, 1)
    return tuple(
        season_type
        for season_type in season_types
        if season_type == SEASON_TYPE_REGULAR or today >= postseason_from
    )


def ingested_schedule_rows(
    rows: Sequence[Mapping], season_types: Collection[str] = SEASON_TYPES_INGESTED
) -> list[Mapping]:
    # preseason and all-star rows have no logs or status rows behind them, so
    # storing them from a backfill would only light up the validation report.
    return [row for row in rows if row["season_type"] in season_types]


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


def stint_is_newer_than_game_log(
    open_stint: tuple[str, date] | None, latest_game_date: date
) -> bool:
    # a roster snapshot observed a move newer than any game we hold; the game
    # log is stale for him until his debut and must not reopen the old team.
    return open_stint is not None and open_stint[1] > latest_game_date


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


def player_rows_from_nba_players_index(next_data: Mapping) -> list[tuple]:
    # (nba_id, name, team abbr, position, headshot_url) per rostered player.
    # players without a current team are skipped: there is nothing to label.
    page_props = (next_data.get("props") or {}).get("pageProps") or {}
    rows: list[tuple] = []
    for raw in page_props.get("players") or []:
        player_id = str(raw.get("PERSON_ID") or "").strip()
        team_abbr = TEAM_ID_TO_ABBR.get(str(raw.get("TEAM_ID") or "").strip())
        name = " ".join(
            part
            for part in (
                str(raw.get("PLAYER_FIRST_NAME") or "").strip(),
                str(raw.get("PLAYER_LAST_NAME") or "").strip(),
            )
            if part
        )
        if not player_id or not team_abbr or not name:
            continue
        rows.append((
            player_id,
            name,
            team_abbr,
            resolve_positions("", str(raw.get("POSITION") or "").strip()),
            f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png",
        ))
    return rows


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


BOX_DETAILS_SOURCE = "boxscoretraditionalv3"


def box_detail_rows_from_traditional(
    payload: Mapping, game_id: str
) -> tuple[list[dict], list[dict]]:
    # v3 sets position for the five starters only, so a non-empty position is
    # the starter flag. A DNP row reports zeros for every stat; those are
    # stored as NULL, because a player who did not play did not foul zero times.
    player_rows: list[dict] = []
    for raw in payload.get("player_stats") or []:
        player_id = str(raw.get("personId") or "").strip()
        if not player_id:
            continue
        position = _text_or_none(raw.get("position"))
        minutes = parse_minutes(raw.get("minutes"))
        appeared = minutes is not None
        player_rows.append(
            {
                "nba_player_id": player_id,
                "nba_game_id": game_id,
                "team_id": str(raw.get("teamId") or "").strip() or None,
                "started": position is not None,
                "position": position,
                "minutes": minutes,
                "oreb": _opt_int(raw.get("reboundsOffensive")) if appeared else None,
                "dreb": _opt_int(raw.get("reboundsDefensive")) if appeared else None,
                "pf": _opt_int(raw.get("foulsPersonal")) if appeared else None,
                "dnp_reason": _text_or_none(raw.get("comment")),
            }
        )

    team_rows: list[dict] = []
    for raw in payload.get("team_stats") or []:
        team_id = str(raw.get("teamId") or "").strip()
        if not team_id:
            continue
        team_rows.append(
            {
                "team_id": team_id,
                "nba_game_id": game_id,
                "oreb": _opt_int(raw.get("reboundsOffensive")),
                "dreb": _opt_int(raw.get("reboundsDefensive")),
                "pf": _opt_int(raw.get("foulsPersonal")),
            }
        )
    return player_rows, team_rows


# DND and NWT are injury and not-with-team designations, which the inactive
# list owns; the box score alone cannot say whether he was listed inactive.
INACTIVE_LIST_OWNED_PREFIXES = ("DND", "NWT")


def active_dnp_status_rows(
    box_player_rows: Sequence[Mapping],
    existing_status_keys: Collection[tuple[str, str]],
    game_id: str,
) -> list[dict]:
    # a player who dressed and never entered has no game-log row and is not on
    # the inactive list, so without this he has no status row at all.
    rows: list[dict] = []
    seen: set[str] = set()
    for row in box_player_rows:
        player_id = row["nba_player_id"]
        if row.get("minutes") is not None or player_id in seen:
            continue
        if (player_id, game_id) in existing_status_keys:
            continue
        seen.add(player_id)
        reason = (row.get("dnp_reason") or "").strip() or None
        owned = reason is not None and reason.upper().startswith(
            INACTIVE_LIST_OWNED_PREFIXES
        )
        rows.append(
            {
                "nba_player_id": player_id,
                "nba_game_id": game_id,
                "team_id": row.get("team_id"),
                "rostered": True,
                "listed_inactive": None if owned else False,
                "started": False,
                "played": False,
                "dnp_reason": reason,
                "minutes": None,
                "source": BOX_DETAILS_SOURCE,
            }
        )
    return rows


def merge_dnp_reason(existing: str | None, incoming: str | None) -> str | None:
    # mirrors the COALESCE in the box-detail update: a reason already stored
    # wins, so a later pass can fill a gap but never rewrite one.
    return existing if existing is not None else incoming


def player_ids_absent_from_box(
    logged_player_ids: Sequence[str], box_player_rows: Sequence[Mapping]
) -> list[str]:
    # logged players v3 does not list keep their stats; they are only stamped
    # as visited so the resumable backfill does not select the game forever.
    in_box = {row["nba_player_id"] for row in box_player_rows}
    return sorted({pid for pid in logged_player_ids if pid not in in_box})
