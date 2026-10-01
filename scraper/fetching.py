import json
import logging
import time
from datetime import date, datetime
from functools import lru_cache
from typing import Callable, TypeVar
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from nba_api.stats.endpoints import (
    commonteamroster,
    leaguedashplayerstats,
    leaguedashteamstats,
    leaguegamelog,
    playergamelogs,
)

from config import (
    ADVANCED_RATINGS_FIRST_SEASON_START_YEAR,
    BACKFILL_MAX_ATTEMPTS,
    BACKFILL_REQUEST_DELAY_SECONDS,
    NBA_2K_API_URL,
    NBA_2K_MAX_ATTEMPTS,
    NBA_2K_PAGE_LIMIT,
    NBA_2K_REQUEST_DELAY_SECONDS,
    NBA_2K_RETRY_DELAY_SECONDS,
    NBA_WEB_GAMES_URL,
    NBA_WEB_PLAYERS_URL,
    NBA_WEB_TIMEOUT_SECONDS,
    SEASON,
    SEASON_TYPE_REGULAR,
    STATS_HEADERS,
    STATS_PROBE_TIMEOUT_SECONDS,
    STATS_PROBE_URL,
)
from parsing import (
    _normalize_name,
    _opt_float,
    extract_next_data,
    season_start_year,
    v2_inactive_is_unreliable,
)

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _is_retryable(exc: Exception) -> bool:
    # RequestException subclasses OSError, so this isinstance order matters.
    if isinstance(exc, requests.exceptions.RequestException):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None:
            return True
        return status == 429 or status >= 500
    if isinstance(exc, (ConnectionResetError, TimeoutError, OSError)):
        return True
    # nba_api hands back a decode error when Akamai returns an html error page
    return isinstance(exc, json.JSONDecodeError)


_FetchResult = TypeVar("_FetchResult")


def _fetch_with_retry(
    label: str,
    fetch: Callable[[], _FetchResult],
    max_attempts: int = BACKFILL_MAX_ATTEMPTS,
    initial_delay: float = BACKFILL_REQUEST_DELAY_SECONDS,
) -> _FetchResult:
    # re-raises the last exception so a caller can fail one season without
    # aborting the whole run.
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch()
        except Exception as exc:
            if attempt == max_attempts or not _is_retryable(exc):
                raise
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.0fs",
                label, attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{label}: retry loop exhausted")


def _fetch_cbs_positions() -> dict[str, str]:
    position_map: dict[str, str] = {}
    headers = {"User-Agent": BROWSER_USER_AGENT}
    for page in range(1, 15):
        try:
            resp = requests.get(
                "https://www.cbssports.com/nba/stats/player/scoring/nba/regular/"
                f"all-pos/all/?page={page}",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr.TableBase-bodyTr")
        if not rows:
            break

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue
            cell0 = cells[0]
            name_el = cell0.select_one("span.CellPlayerName--long a")
            pos_el = cell0.select_one("span.CellPlayerName-position")
            if name_el and pos_el:
                name = name_el.get_text(strip=True)
                pos = pos_el.get_text(strip=True)
                if name and pos in ("PG", "SG", "SF", "PF", "C"):
                    position_map[_normalize_name(name)] = pos

        time.sleep(0.5)

    return position_map


def _fetch_nba_positions() -> dict[str, str]:
    from nba_api.stats.endpoints import playerindex

    position_map: dict[str, str] = {}
    try:
        pi = playerindex.PlayerIndex(
            season=SEASON, league_id="00", timeout=60, headers=STATS_HEADERS
        )
        df = pi.get_data_frames()[0]
        for _, row in df.iterrows():
            pid = str(row.get("PERSON_ID", ""))
            pos = str(row.get("POSITION", "")).strip()
            if pid and pos:
                position_map[pid] = pos
    except Exception as e:
        logger.warning("could not fetch NBA playerindex positions: %s", e)
    return position_map


def fetch_player_stats(season: str = SEASON) -> object:
    return leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        season_type_all_star="Regular Season",
        timeout=60,
        headers=STATS_HEADERS,
    )


def fetch_team_stats(season: str = SEASON) -> object:
    return leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        season_type_all_star="Regular Season",
        timeout=60,
        headers=STATS_HEADERS,
    )


def fetch_advanced_team_stats(season: str = SEASON) -> object:
    return leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
        season_type_all_star="Regular Season",
        timeout=60,
        headers=STATS_HEADERS,
    )


def _fetch_espn_scoreboard(date_str: str) -> list[dict]:
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        f"?dates={date_str}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("ESPN fetch failed for %s: %s", date_str, e)
        return []

    events = data.get("events", [])
    games = []

    for event in events:
        game_id = str(event.get("id", ""))
        status_obj = event.get("status", {})
        status_type = status_obj.get("type", {})
        status_name = status_type.get("name", "")

        if status_name == "STATUS_FINAL":
            status = "Final"
        elif status_name == "STATUS_IN_PROGRESS":
            status = "In Progress"
        else:
            status = status_type.get("detail", "Scheduled").strip() or "Scheduled"

        # ESPN stores event dates as UTC midnight, so the canonical game date is
        # the Eastern-time one.
        event_date_str = event.get("date", "")
        try:
            date_utc = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
            game_date = date_utc.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            game_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        is_pre_game = status_name not in ("STATUS_FINAL", "STATUS_IN_PROGRESS")
        try:
            home_score = int(home.get("score", 0)) if not is_pre_game else None
        except (TypeError, ValueError):
            home_score = None
        try:
            away_score = int(away.get("score", 0)) if not is_pre_game else None
        except (TypeError, ValueError):
            away_score = None

        arena = (competition.get("venue") or {}).get("fullName", "")

        games.append({
            "nba_game_id": game_id,
            "home_team": (home.get("team") or {}).get("displayName", "Unknown"),
            "away_team": (away.get("team") or {}).get("displayName", "Unknown"),
            "game_date": game_date,
            "home_score": home_score,
            "away_score": away_score,
            "status": status,
            "arena": arena,
        })

    return games


def fetch_injury_page() -> str:
    headers = {"User-Agent": BROWSER_USER_AGENT}
    resp = requests.get(
        "https://www.cbssports.com/nba/injuries/", headers=headers, timeout=30
    )
    resp.raise_for_status()
    return resp.text


def _fetch_season_player_rows(season: str) -> list[dict]:
    stats = _fetch_with_retry(
        f"player stats {season}", lambda: fetch_player_stats(season)
    )
    return stats.get_data_frames()[0].to_dict("records")


def _fetch_season_team_rows(season: str) -> list[dict]:
    stats = _fetch_with_retry(f"team stats {season}", lambda: fetch_team_stats(season))
    return stats.get_data_frames()[0].to_dict("records")


def _fetch_season_team_ratings(season: str) -> dict[str, dict[str, float | None]]:
    if season_start_year(season) < ADVANCED_RATINGS_FIRST_SEASON_START_YEAR:
        return {}

    try:
        stats = _fetch_with_retry(
            f"advanced team stats {season}", lambda: fetch_advanced_team_stats(season)
        )
    except Exception as e:
        # ratings are a nice-to-have; a season is still worth writing without them
        logger.warning("%s: advanced ratings unavailable (%s)", season, e)
        return {}

    ratings: dict[str, dict[str, float | None]] = {}
    for row in stats.get_data_frames()[0].to_dict("records"):
        ratings[str(row["TEAM_ID"])] = {
            "defensive_rating": _opt_float(row.get("DEF_RATING")),
            "offensive_rating": _opt_float(row.get("OFF_RATING")),
            "net_rating": _opt_float(row.get("NET_RATING")),
        }
    return ratings


def _fetch_2k_page(team_type: str, cursor: str) -> dict:
    def fetch() -> dict:
        resp = requests.get(
            NBA_2K_API_URL,
            params={
                "limit": NBA_2K_PAGE_LIMIT,
                "cursor": cursor,
                "teamType": team_type,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    payload = _fetch_with_retry(
        f"2k {team_type} page (cursor {cursor})",
        fetch,
        max_attempts=NBA_2K_MAX_ATTEMPTS,
        initial_delay=NBA_2K_RETRY_DELAY_SECONDS,
    )

    if not payload.get("success"):
        raise RuntimeError(f"nba2kapi reported failure for teamType={team_type}")
    return payload


def _fetch_2k_players(team_type: str) -> list[dict]:
    players: list[dict] = []
    cursor = "0"

    while True:
        payload = _fetch_2k_page(team_type, cursor)
        players.extend(payload.get("data") or [])

        pagination = (payload.get("meta") or {}).get("pagination") or {}
        total = pagination.get("total")
        logger.info(
            "2k %s: fetched %d/%s cards",
            team_type, len(players), total if total is not None else "?",
        )

        if not pagination.get("hasMore"):
            break

        next_cursor = pagination.get("nextCursor")
        if next_cursor is None or str(next_cursor) == cursor:
            # a cursor that doesn't advance would page forever
            logger.warning(
                "2k %s: cursor stuck at %s, stopping early", team_type, cursor
            )
            break

        cursor = str(next_cursor)
        time.sleep(NBA_2K_REQUEST_DELAY_SECONDS)

    return players


def _fetch_player_game_logs(
    season: str, date_from: date | None, season_type: str = SEASON_TYPE_REGULAR
) -> list[dict]:
    def fetch() -> playergamelogs.PlayerGameLogs:
        return playergamelogs.PlayerGameLogs(
            season_nullable=season,
            season_type_nullable=season_type,
            date_from_nullable=date_from.strftime("%m/%d/%Y") if date_from else "",
            timeout=60,
            headers=STATS_HEADERS,
        )

    logs = _fetch_with_retry(f"player game logs {season}", fetch)
    return logs.get_data_frames()[0].to_dict("records")


def _fetch_league_player_game_logs(
    season: str, date_from: date | None, season_type: str = SEASON_TYPE_REGULAR
) -> list[dict]:
    # overlaps playergamelogs almost entirely, but reports the zero-minute
    # appearances it omits; see supplement_player_log_rows.
    def fetch() -> leaguegamelog.LeagueGameLog:
        return leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star=season_type,
            player_or_team_abbreviation="P",
            date_from_nullable=date_from.strftime("%m/%d/%Y") if date_from else "",
            timeout=60,
            headers=STATS_HEADERS,
        )

    logs = _fetch_with_retry(f"league player game logs {season}", fetch)
    return logs.get_data_frames()[0].to_dict("records")


def _fetch_team_game_logs(
    season: str, date_from: date | None, season_type: str = SEASON_TYPE_REGULAR
) -> list[dict]:
    def fetch() -> leaguegamelog.LeagueGameLog:
        return leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star=season_type,
            player_or_team_abbreviation="T",
            date_from_nullable=date_from.strftime("%m/%d/%Y") if date_from else "",
            timeout=60,
            headers=STATS_HEADERS,
        )

    logs = _fetch_with_retry(f"team game logs {season}", fetch)
    return logs.get_data_frames()[0].to_dict("records")


def _fetch_league_schedule(season: str) -> list[dict]:
    # imported lazily: this endpoint does not exist in older nba_api releases,
    # and the caller falls back to the team game log when it is missing.
    from nba_api.stats.endpoints import scheduleleaguev2

    def fetch() -> object:
        return scheduleleaguev2.ScheduleLeagueV2(
            season=season, timeout=60, headers=STATS_HEADERS
        )

    schedule = _fetch_with_retry(f"league schedule {season}", fetch)
    return schedule.season_games.get_data_frame().to_dict("records")


def _fetch_inactive_players(game_id: str, game_date: date | None) -> tuple[list[dict], str]:
    # v3 first, v2 as the fallback for older games. A successful v3 answer is
    # trusted as-is past the v2 cutoff, INCLUDING an empty list: falling through
    # would let "v2 has no data" masquerade as "nobody was inactive". A v2 answer
    # past the cutoff is tagged suspect so the rows stay identifiable.
    from nba_api.stats.endpoints import boxscoresummaryv2, boxscoresummaryv3

    def fetch_v3() -> object:
        return boxscoresummaryv3.BoxScoreSummaryV3(
            game_id=game_id, timeout=60, headers=STATS_HEADERS
        )

    def fetch_v2() -> object:
        return boxscoresummaryv2.BoxScoreSummaryV2(
            game_id=game_id, timeout=60, headers=STATS_HEADERS
        )

    v2_unreliable = v2_inactive_is_unreliable(game_date)

    try:
        summary = _fetch_with_retry(f"box score summary v3 {game_id}", fetch_v3)
        rows = summary.inactive_players.get_data_frame().to_dict("records")
        if rows or v2_unreliable:
            return rows, "v3"
    except Exception as e:  # noqa: BLE001 - v2 is the whole point of the fallback
        logger.debug("v3 summary failed for %s (%s), trying v2", game_id, e)

    time.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
    summary = _fetch_with_retry(f"box score summary v2 {game_id}", fetch_v2)
    rows = summary.inactive_players.get_data_frame().to_dict("records")
    return rows, "v2-suspect" if v2_unreliable else "v2"


def fetch_box_score_traditional(game_id: str) -> dict[str, list[dict]]:
    # the caller owns the delay between games; this only retries one game.
    from nba_api.stats.endpoints import boxscoretraditionalv3

    def fetch() -> object:
        return boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id, timeout=60, headers=STATS_HEADERS
        )

    box = _fetch_with_retry(f"box score traditional v3 {game_id}", fetch)
    return {
        "player_stats": box.player_stats.get_data_frame().to_dict("records"),
        "team_stats": box.team_stats.get_data_frame().to_dict("records"),
    }


def _fetch_team_roster(
    team_id: str,
    season: str,
    max_attempts: int = BACKFILL_MAX_ATTEMPTS,
    timeout: int = 60,
) -> list[dict]:
    def fetch() -> commonteamroster.CommonTeamRoster:
        return commonteamroster.CommonTeamRoster(
            team_id=team_id, season=season, timeout=timeout,
            headers=STATS_HEADERS,
        )

    roster = _fetch_with_retry(
        f"team roster {team_id} {season}", fetch, max_attempts=max_attempts
    )
    return roster.get_data_frames()[0].to_dict("records")


def _fetch_nba_web_page(label: str, url: str, params: dict | None = None) -> dict:
    # nba.com pages are reachable from CI when stats.nba.com is not; a desktop
    # user agent is required.
    def fetch() -> dict:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": BROWSER_USER_AGENT},
            timeout=NBA_WEB_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return extract_next_data(resp.text)

    return _fetch_with_retry(label, fetch, max_attempts=2, initial_delay=3.0)


def _fetch_nba_web_games(game_date: date) -> dict:
    return _fetch_nba_web_page(
        f"nba.com games {game_date.isoformat()}",
        NBA_WEB_GAMES_URL,
        {"date": game_date.isoformat()},
    )


@lru_cache(maxsize=1)
def _fetch_nba_web_players() -> dict:
    # cached so the players table and the roster snapshot share one request.
    return _fetch_nba_web_page("nba.com players index", NBA_WEB_PLAYERS_URL)


def stats_nba_reachable() -> bool:
    # one cheap request: stats.nba.com either answers quickly or never does
    # (datacenter ips are tarpitted), so there is no point retrying.
    try:
        resp = requests.get(
            STATS_PROBE_URL,
            params={"LeagueID": "00", "Season": SEASON, "TeamID": "1610612738"},
            headers=STATS_HEADERS,
            timeout=STATS_PROBE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning("stats.nba.com unreachable (%s)", e)
        return False
    return True
