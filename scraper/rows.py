from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import NamedTuple

from config import (
    ABBR_TO_TEAM_ID,
    TEAM_ID_TO_ABBR,
    GAME_LOG_CORRECTION_WINDOW_DAYS,
    PLAYIN_GAME_SUFFIXES,
    PLAYOFF_MAX_GAMES_PER_SERIES,
    PLAYOFF_SERIES_PER_ROUND,
    POSTSEASON_EARLIEST_MONTH,
    POSTSEASON_WINDOW_END,
    POSTSEASON_WINDOW_START,
    PRESEASON_MAX_GAME_NUMBER,
    PRESEASON_WINDOW_END,
    PRESEASON_WINDOW_START,
    SEASON_TYPE_PLAYIN,
    SEASON_TYPE_PLAYOFFS,
    SEASON_TYPE_PRESEASON,
    SEASON_TYPE_REGULAR,
    SEASON_TYPES_INGESTED,
    WEB_BOX_SCORE_SOURCE,
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

GAME_LOG_STINT_SOURCE = "playergamelogs"


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
    # games can exist, and the preseason only inside its window, so no run
    # spends a request on a type that cannot have new games.
    start_year = season_start_year(season)
    postseason_from = date(start_year + 1, POSTSEASON_EARLIEST_MONTH, 1)
    preseason_from = date(start_year, *PRESEASON_WINDOW_START)
    preseason_to = date(start_year, *PRESEASON_WINDOW_END)

    def wanted(season_type: str) -> bool:
        if season_type == SEASON_TYPE_REGULAR:
            return True
        if season_type == SEASON_TYPE_PRESEASON:
            return preseason_from <= today <= preseason_to
        return today >= postseason_from

    return tuple(season_type for season_type in season_types if wanted(season_type))


def discovery_dates(
    season: str, season_types: Collection[str], today: date
) -> list[date]:
    # every date a game of the requested types can fall on, oldest first, up to
    # today: later pages hold no final games to discover.
    start_year = season_start_year(season)
    windows: list[tuple[date, date]] = []
    if SEASON_TYPE_PRESEASON in season_types:
        windows.append(
            (date(start_year, *PRESEASON_WINDOW_START), date(start_year, *PRESEASON_WINDOW_END))
        )
    if SEASON_TYPE_PLAYIN in season_types or SEASON_TYPE_PLAYOFFS in season_types:
        windows.append(
            (
                date(start_year + 1, *POSTSEASON_WINDOW_START),
                date(start_year + 1, *POSTSEASON_WINDOW_END),
            )
        )
    dates: list[date] = []
    for first, last in windows:
        day = first
        while day <= min(last, today):
            dates.append(day)
            day += timedelta(days=1)
    return dates


def game_id_season_prefix(season: str) -> str:
    # characters 4 and 5 of a game id are the season's start year.
    return f"{season_start_year(season) % 100:02d}"


def enumerate_game_id_groups(season: str, season_type: str) -> list[list[str]]:
    # every id the league could have used for the type, in groups probed in
    # order: one per playoff series, one per play-in game, one for the preseason.
    yy = game_id_season_prefix(season)
    if season_type == SEASON_TYPE_PRESEASON:
        return [[f"001{yy}{n:05d}" for n in range(1, PRESEASON_MAX_GAME_NUMBER + 1)]]
    if season_type == SEASON_TYPE_PLAYIN:
        return [[f"005{yy}{suffix}"] for suffix in PLAYIN_GAME_SUFFIXES]
    if season_type == SEASON_TYPE_PLAYOFFS:
        return [
            [
                f"004{yy}00{round_number}{series}{game}"
                for game in range(1, PLAYOFF_MAX_GAMES_PER_SERIES + 1)
            ]
            for round_number, series_count in enumerate(PLAYOFF_SERIES_PER_ROUND, start=1)
            for series in range(series_count)
        ]
    return []


def discovered_rows_for_season(
    rows: Sequence[Mapping], season: str, season_types: Collection[str]
) -> list[dict]:
    # a date page lists every game that day; keep the requested types of this
    # season, labelled from the id so a june game always lands in its season.
    yy = game_id_season_prefix(season)
    kept: dict[str, dict] = {}
    for row in rows:
        game_id = str(row.get("nba_game_id") or "")
        if game_id[3:5] != yy or row.get("season_type") not in season_types:
            continue
        kept.setdefault(game_id, {**row, "season": season})
    return sorted(kept.values(), key=lambda r: (r["game_date"], r["nba_game_id"]))


def schedule_row_from_web_game(game: Mapping, season: str) -> dict | None:
    # a schedule row from one box-score page, for an id no date page listed.
    # gameEt carries a misleading Z suffix, but its date part is the eastern date.
    game_id = str(game.get("gameId") or "").strip()
    game_date = parse_game_date(game.get("gameEt"))
    if not game_id or not in_season(game_date, season):
        return None
    home = game.get("homeTeam") or {}
    away = game.get("awayTeam") or {}
    return {
        "nba_game_id": game_id,
        "season": season,
        "season_type": season_type_from_game_id(game_id),
        "game_date": game_date,
        "scheduled_at": _parse_utc(game.get("gameTimeUTC")),
        "home_team_id": str(home.get("teamId") or "") or None,
        "away_team_id": str(away.get("teamId") or "") or None,
        "home_team_abbr": home.get("teamTricode") or None,
        "away_team_abbr": away.get("teamTricode") or None,
        "game_status": game.get("gameStatusText") or None,
        "postponed_status": None,
        "source": WEB_BOX_SCORE_SOURCE,
    }


def ingested_schedule_rows(
    rows: Sequence[Mapping], season_types: Collection[str] = SEASON_TYPES_INGESTED
) -> list[Mapping]:
    # all-star rows have no logs or status rows behind them, so storing them
    # from a backfill would only light up the validation report.
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


class Stint(NamedTuple):
    team_id: str
    valid_from: date
    valid_to: date | None
    source: str


def absence_closure(existing: Iterable[Stint]) -> date | None:
    # only an absence closure leaves his latest stint closed with nothing open,
    # so the rebuild must keep that date rather than reopen the stint.
    stints = list(existing)
    if not stints or any(s.valid_to is None for s in stints):
        return None
    return max(stints, key=lambda s: (s.valid_from, s.valid_to)).valid_to


def derive_stints(
    appearances: Iterable[tuple[date, str, str]],
    snapshot_stints: Iterable[tuple[str, date, str]] = (),
    closed_through: date | None = None,
) -> list[Stint]:
    # rebuilt from his whole history, so ingestion order cannot change the answer.
    segments: list[list] = []
    for game_date, team_id, season_type in sorted(appearances, key=lambda a: (a[0], a[1])):
        preseason = season_type == SEASON_TYPE_PRESEASON
        if segments and segments[-1][0] == team_id:
            segments[-1][2] = game_date
            segments[-1][3] = segments[-1][3] and preseason
        else:
            segments.append([team_id, game_date, game_date, preseason])

    observations = sorted(snapshot_stints, key=lambda s: (s[1], s[0]))
    last_game = segments[-1][2] if segments else None
    # games are better evidence than a roster page, so only a newer one counts
    newer = [s for s in observations if last_game is None or s[1] > last_game]

    stints: list[Stint] = []
    for index, (team_id, first, last, preseason_only) in enumerate(segments):
        if index + 1 < len(segments):
            # a camp team he never played a real game for ends with camp
            valid_to = last if preseason_only else segments[index + 1][1] - timedelta(days=1)
            stints.append(Stint(team_id, first, valid_to, GAME_LOG_STINT_SOURCE))
        elif not preseason_only:
            stints.append(Stint(team_id, first, None, GAME_LOG_STINT_SOURCE))
        elif observations and observations[-1][0] == team_id:
            # the snapshot source is what keeps a camp stint open on the next rebuild
            stints.append(Stint(team_id, first, None, observations[-1][2]))
        else:
            stints.append(Stint(team_id, first, last, GAME_LOG_STINT_SOURCE))

    for team_id, observed_on, source in newer:
        current = stints[-1] if stints and stints[-1].valid_to is None else None
        if current is not None:
            if current.team_id == team_id:
                continue
            # observed on the new roster today, never observed leaving the old one
            stints[-1] = current._replace(
                valid_to=max(current.valid_from, observed_on - timedelta(days=1))
            )
        stints.append(Stint(team_id, observed_on, None, source))

    if stints and stints[-1].valid_to is None and closed_through is not None:
        # a game after the closure means he re-signed, so the closure is stale
        evidence = max(stints[-1].valid_from, last_game or stints[-1].valid_from)
        if closed_through >= evidence:
            stints[-1] = stints[-1]._replace(valid_to=closed_through)
    return stints


def roster_snapshot_is_complete(
    snapshot: Mapping[str, str], team_ids: Collection[str], min_players: int = 13
) -> bool:
    counts: dict[str, int] = {}
    for team_id in snapshot.values():
        counts[team_id] = counts.get(team_id, 0) + 1
    return bool(team_ids) and all(counts.get(t, 0) >= min_players for t in team_ids)


def plan_roster_snapshot(
    snapshot: Mapping[str, str],
    open_stints: Mapping[str, tuple[str, date]],
    snapshot_date: date,
    *,
    complete: bool = False,
) -> list[dict]:
    # the old stint closes the day before the snapshot, not on his last game for
    # that team: we observed that he is on the new roster today and never
    # observed when he left the old one.
    #
    # absence closes a stint only when the snapshot is complete; otherwise it
    # cannot be told apart from one team's fetch failing.
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

    if complete:
        for player_id in sorted(set(open_stints) - set(snapshot)):
            prev_team_id, prev_valid_from = open_stints[player_id]
            changes.append({
                "player_id": player_id,
                "open_team_id": None,
                "open_valid_from": None,
                "close_team_id": prev_team_id,
                "close_valid_from": prev_valid_from,
                "close_valid_to": max(prev_valid_from, snapshot_date - timedelta(days=1)),
            })
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


def _web_sides(game: Mapping) -> list[tuple[Mapping, bool, Mapping]]:
    # (team, is_home, opponent) per side. neutral-site games keep the page's
    # own home designation, as the schedule does.
    home = game.get("homeTeam") or {}
    away = game.get("awayTeam") or {}
    return [(home, True, away), (away, False, home)]


def _web_team_id(team: Mapping) -> str | None:
    return str(team.get("teamId") or "").strip() or None


def _web_minutes(stats: Mapping, comment: str | None) -> float | None:
    # a dnp arrives as "" today, but a zeroed duration with a comment is the
    # same player and must not count as an appearance.
    minutes = parse_minutes(stats.get("minutes"))
    if comment is not None and not minutes:
        return None
    return minutes


def _web_has_totals(stats: Mapping) -> bool:
    # an unplayed game carries a placeholder object instead of team totals.
    return "points" in stats and "minutes" in stats


def _web_player_as_traditional(raw: Mapping, team_id: str | None) -> dict:
    stats = raw.get("statistics") or {}
    comment = _text_or_none(raw.get("comment"))
    return {
        "personId": raw.get("personId"),
        "teamId": team_id,
        "position": raw.get("position"),
        "comment": comment,
        "minutes": _web_minutes(stats, comment),
        "reboundsOffensive": stats.get("reboundsOffensive"),
        "reboundsDefensive": stats.get("reboundsDefensive"),
        "foulsPersonal": stats.get("foulsPersonal"),
    }


def box_detail_rows_from_web(
    game: Mapping, game_id: str
) -> tuple[list[dict], list[dict]]:
    # the box-score page uses v3's camelCase names with the line nested under
    # statistics, so it is flattened into v3's shape and shares its rules.
    player_stats: list[dict] = []
    team_stats: list[dict] = []
    for team, _, _ in _web_sides(game):
        team_id = _web_team_id(team)
        for raw in team.get("players") or []:
            player_stats.append(_web_player_as_traditional(raw, team_id))
        totals = team.get("statistics") or {}
        if team_id and _web_has_totals(totals):
            team_stats.append(
                {
                    "teamId": team_id,
                    "reboundsOffensive": totals.get("reboundsOffensive"),
                    "reboundsDefensive": totals.get("reboundsDefensive"),
                    "foulsPersonal": totals.get("foulsPersonal"),
                }
            )
    return box_detail_rows_from_traditional(
        {"player_stats": player_stats, "team_stats": team_stats}, game_id
    )


WEB_GAME_STATUS_FINAL = 3


def web_game_is_final(game: Mapping) -> bool:
    # gameStatus is 1 scheduled, 2 live, 3 final; a live page has partial lines.
    try:
        return int(game.get("gameStatus") or 0) == WEB_GAME_STATUS_FINAL
    except (TypeError, ValueError):
        return False


def web_inactive_rows(game: Mapping) -> list[dict]:
    # the page's per-team inactive list, in the boxscoresummaryv3 shape that
    # normalize_inactive_rows reads.
    rows: list[dict] = []
    for team, _, _ in _web_sides(game):
        team_id = _web_team_id(team)
        for raw in team.get("inactives") or []:
            rows.append({"personId": raw.get("personId"), "teamId": team_id})
    return rows


def _web_line(stats: Mapping) -> tuple:
    # pts through plus_minus, in the order both log upserts expect.
    return (
        _opt_int(stats.get("points")),
        _opt_int(stats.get("reboundsTotal")),
        _opt_int(stats.get("assists")),
        _opt_int(stats.get("steals")),
        _opt_int(stats.get("blocks")),
        _opt_int(stats.get("turnovers")),
        _opt_int(stats.get("fieldGoalsMade")),
        _opt_int(stats.get("fieldGoalsAttempted")),
        _opt_int(stats.get("threePointersMade")),
        _opt_int(stats.get("threePointersAttempted")),
        _opt_int(stats.get("freeThrowsMade")),
        _opt_int(stats.get("freeThrowsAttempted")),
        _opt_int(stats.get("plusMinusPoints")),
    )


def game_log_rows_from_web(
    game: Mapping,
    game_id: str,
    season: str,
    season_type: str,
    game_date: date,
    run_id: int | None = None,
) -> tuple[list[tuple], list[tuple]]:
    # tuples in the build_player_game_log_row / build_team_game_log_row order.
    # only appearances get a player row, as with playergamelogs; dressed
    # non-appearances are the active-dnp status rows' job.
    player_rows: list[tuple] = []
    team_rows: list[tuple] = []
    for team, is_home, opponent in _web_sides(game):
        team_id = _web_team_id(team)
        if team_id is None:
            continue
        team_abbr = team.get("teamTricode") or None
        opponent_id = _web_team_id(opponent)

        for raw in team.get("players") or []:
            player_id = str(raw.get("personId") or "").strip()
            stats = raw.get("statistics") or {}
            minutes = _web_minutes(stats, _text_or_none(raw.get("comment")))
            if not player_id or minutes is None:
                continue
            player_rows.append(
                (
                    player_id, game_id, season, season_type, game_date,
                    team_id, team_abbr, opponent_id, is_home,
                    _text_or_none(raw.get("position")) is not None,
                    minutes,
                )
                + _web_line(stats)
                + (None, WEB_BOX_SCORE_SOURCE, run_id)
            )

        totals = team.get("statistics") or {}
        if not _web_has_totals(totals):
            continue
        team_rows.append(
            (
                team_id, game_id, season, season_type, game_date,
                team_abbr, opponent_id, is_home,
                parse_minutes(totals.get("minutes")),
            )
            + _web_line(totals)
            + (WEB_BOX_SCORE_SOURCE, run_id)
        )
    return player_rows, team_rows


# DND and NWT are injury and not-with-team designations, which the inactive
# list owns; the box score alone cannot say whether he was listed inactive.
INACTIVE_LIST_OWNED_PREFIXES = ("DND", "NWT")


def active_dnp_status_rows(
    box_player_rows: Sequence[Mapping],
    existing_status_keys: Collection[tuple[str, str]],
    game_id: str,
    source: str = BOX_DETAILS_SOURCE,
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
                "source": source,
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
