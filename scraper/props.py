import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import psycopg2
import requests

from config import (
    NAME_TO_ABBR,
    PROPS_API_KEY_ENV,
    PROPS_DEFAULT_MARKETS,
    PROPS_INGESTION_KIND,
    PROPS_MARKET_MAP,
    PROPS_MIN_HOURS_BETWEEN_RUNS,
    PROPS_MONTHLY_BUDGET,
    PROPS_PROVIDER_THE_ODDS_API,
    PROPS_RESERVE_CREDITS,
    PROPS_WINDOW_DAYS,
    THE_ODDS_API_MAX_ATTEMPTS,
    THE_ODDS_API_REGIONS,
    THE_ODDS_API_RETRY_DELAY_SECONDS,
    THE_ODDS_API_SPORT_URL,
    THE_ODDS_API_TIMEOUT_SECONDS,
)
from database import (
    _batch_upsert,
    _finish_ingestion_run,
    _start_ingestion_run,
    maybe_write_cursor,
)
from fetching import _fetch_with_retry
from odds import EASTERN, _read_schedule_rows, map_event_to_nba_game, parse_american
from parsing import canonical_player_name
from scrapes import index_players_by_canonical_name

logger = logging.getLogger(__name__)

PROP_INSERT_SQL = """
INSERT INTO prop_odds_snapshots (provider, provider_event_id, nba_game_id, game_date,
                                 bookmaker, market, player_name, nba_player_id, line,
                                 over_price, under_price, provider_updated_at, source,
                                 ingestion_run_id)
VALUES %s
"""


class PropsProvider(Protocol):
    name: str
    # credits left in the provider's quota, as of the last response; None if unknown
    requests_remaining: int | None

    def fetch_events(self, start: datetime, end: datetime) -> list[dict]: ...

    def fetch_event_props(self, event_id: str) -> dict: ...

    def event_props_cost(self) -> int: ...


class TheOddsApiProvider:
    name = PROPS_PROVIDER_THE_ODDS_API

    def __init__(
        self,
        api_key: str,
        markets: Sequence[str] | None = None,
        regions: str = THE_ODDS_API_REGIONS,
        get: Callable[..., requests.Response] = requests.get,
    ) -> None:
        self._api_key = api_key
        self._markets = market_keys(markets or PROPS_DEFAULT_MARKETS)
        self._regions = regions
        self._get = get
        self.requests_remaining: int | None = None

    def _request(self, label: str, path: str, params: dict) -> object:
        def fetch() -> object:
            resp = self._get(
                f"{THE_ODDS_API_SPORT_URL}{path}",
                params={**params, "apiKey": self._api_key},
                timeout=THE_ODDS_API_TIMEOUT_SECONDS,
            )
            self._record_quota(resp.headers)
            resp.raise_for_status()
            return resp.json()

        return _fetch_with_retry(
            label, fetch,
            max_attempts=THE_ODDS_API_MAX_ATTEMPTS,
            initial_delay=THE_ODDS_API_RETRY_DELAY_SECONDS,
        )

    def _record_quota(self, headers: Mapping[str, str]) -> None:
        raw = headers.get("x-requests-remaining")
        if raw is None:
            return
        try:
            self.requests_remaining = int(float(raw))
        except ValueError:
            logger.warning("props: unreadable x-requests-remaining header %r", raw)

    def fetch_events(self, start: datetime, end: datetime) -> list[dict]:
        # the events listing is free; only the per-event odds calls spend quota.
        payload = self._request(
            "the odds api events", "/events",
            {"commenceTimeFrom": _iso_z(start), "commenceTimeTo": _iso_z(end)},
        )
        return list(payload) if isinstance(payload, list) else []

    def fetch_event_props(self, event_id: str) -> dict:
        payload = self._request(
            f"the odds api event {event_id}", f"/events/{event_id}/odds",
            {
                "markets": ",".join(self._markets),
                "regions": self._regions,
                "oddsFormat": "american",
            },
        )
        return payload if isinstance(payload, dict) else {}

    def event_props_cost(self) -> int:
        return len(self._markets) * len(self._regions.split(","))


def market_keys(markets: Iterable[str]) -> list[str]:
    # normalized names (pts) to the provider's market keys (player_points).
    by_name = {name: key for key, name in PROPS_MARKET_MAP.items()}
    return [by_name[m] for m in markets]


def parse_props_markets(spec: str | None) -> tuple[str, ...]:
    # "pts,reb" to normalized names; None or blank means the default subset.
    if spec is None or not spec.strip():
        return PROPS_DEFAULT_MARKETS
    known = set(PROPS_MARKET_MAP.values())
    names = list(dict.fromkeys(m.strip().lower() for m in spec.split(",") if m.strip()))
    unknown = [m for m in names if m not in known]
    if unknown or not names:
        raise ValueError(
            f"unknown props market(s) {', '.join(unknown) or spec!r}; "
            f"choose from {', '.join(sorted(known))}"
        )
    return tuple(names)


def credits_for_snapshot(n_events: int, n_markets: int) -> int:
    # one credit per market per event; the events listing is free.
    return max(n_events, 0) * max(n_markets, 0)


def snapshot_allowed(
    remaining: int | None, projected: int, reserve: int = PROPS_RESERVE_CREDITS
) -> bool:
    # unknown quota is allowed; the per-call stop still applies.
    return remaining is None or remaining - projected >= reserve


def provider_from_env(markets: Sequence[str] = PROPS_DEFAULT_MARKETS) -> PropsProvider | None:
    api_key = (os.environ.get(PROPS_API_KEY_ENV) or "").strip()
    return TheOddsApiProvider(api_key, markets=markets) if api_key else None


def _iso_z(moment: datetime) -> str:
    # the api rejects fractional seconds and offsets other than Z.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def props_window(now: datetime, days: int = PROPS_WINDOW_DAYS) -> tuple[datetime, datetime]:
    # now through the end of the eastern day `days` out, matching the odds lane.
    last_day = now.astimezone(EASTERN).date() + timedelta(days=days)
    end = datetime(last_day.year, last_day.month, last_day.day, tzinfo=EASTERN) + timedelta(days=1)
    return now, end - timedelta(seconds=1)


def events_in_window(
    events: Iterable[Mapping], start: datetime, end: datetime
) -> list[Mapping]:
    # games already tipped are not props anyone can still bet, and the api's
    # time filter is not trusted to save quota on its own.
    kept = []
    for event in events:
        tip = parse_timestamp(event.get("commence_time"))
        if event.get("id") and tip is not None and start < tip <= end:
            kept.append(event)
    return kept


def props_run_due(
    last_started: datetime | None, now: datetime, min_hours: float
) -> bool:
    if last_started is None or min_hours <= 0:
        return True
    if last_started.tzinfo is None:
        last_started = last_started.replace(tzinfo=timezone.utc)
    return now - last_started >= timedelta(hours=min_hours)


def team_abbr_from_name(name: object) -> str | None:
    return NAME_TO_ABBR.get(str(name or "").strip().lower())


def map_prop_event(
    event: Mapping, schedule_rows: Iterable[Mapping]
) -> tuple[date | None, str | None]:
    # returns (eastern game date, nba game id or None).
    tip = parse_timestamp(event.get("commence_time"))
    if tip is None:
        return None, None
    game_date = tip.astimezone(EASTERN).date()
    home = team_abbr_from_name(event.get("home_team"))
    away = team_abbr_from_name(event.get("away_team"))
    return game_date, map_event_to_nba_game(game_date, home, away, schedule_rows)


def _side(name: object) -> str | None:
    text = str(name or "").strip().lower()
    return text if text in ("over", "under") else None


def _as_line(point: object) -> float | None:
    if isinstance(point, bool) or not isinstance(point, (int, float, str)):
        return None
    try:
        return float(point)
    except ValueError:
        return None


def parse_event_props(payload: Mapping, market_map: Mapping[str, str]) -> list[dict]:
    # one row per bookmaker x market x player x line, over and under paired.
    pairs: dict[tuple, dict] = {}
    unknown: set[str] = set()
    for bookmaker in payload.get("bookmakers") or []:
        book = str(bookmaker.get("key") or "")
        if not book:
            continue
        for market in bookmaker.get("markets") or []:
            key = str(market.get("key") or "")
            normalized = market_map.get(key)
            if normalized is None:
                unknown.add(key)
                continue
            updated = parse_timestamp(market.get("last_update")) or parse_timestamp(
                bookmaker.get("last_update")
            )
            for outcome in market.get("outcomes") or []:
                side = _side(outcome.get("name"))
                player = str(outcome.get("description") or "").strip()
                line = _as_line(outcome.get("point"))
                price = parse_american(outcome.get("price"))
                if side is None or not player or line is None or price is None:
                    continue
                row = pairs.setdefault((book, normalized, player, line), {
                    "bookmaker": book,
                    "market": normalized,
                    "player_name": player,
                    "line": line,
                    "over_price": None,
                    "under_price": None,
                    "provider_updated_at": updated,
                })
                if row[f"{side}_price"] is None:
                    row[f"{side}_price"] = price
    if unknown:
        logger.warning("props: skipped unknown market key(s): %s", ", ".join(sorted(unknown)))
    return list(pairs.values())


def match_prop_player(
    name: str,
    team_abbrs: Iterable[str | None],
    players_by_name: Mapping[str, Sequence[tuple[str, str]]],
) -> str | None:
    # props carry no team, so the event's two teams are the only disambiguator.
    candidates = list(players_by_name.get(canonical_player_name(name), ()))
    if len(candidates) > 1:
        teams = {t for t in team_abbrs if t}
        candidates = [c for c in candidates if c[1] in teams]
    return candidates[0][0] if len(candidates) == 1 else None


def plan_event_props(
    event: Mapping,
    payload: Mapping,
    market_map: Mapping[str, str],
    schedule_rows: list[Mapping],
    players_by_name: Mapping[str, Sequence[tuple[str, str]]],
) -> tuple[list[dict], str | None, set[str]]:
    # returns (rows, nba game id, unmatched player names).
    game_date, nba_game_id = map_prop_event(event, schedule_rows)
    if game_date is None:
        return [], None, set()
    teams = (
        team_abbr_from_name(event.get("home_team")),
        team_abbr_from_name(event.get("away_team")),
    )
    rows = []
    unmatched: set[str] = set()
    for row in parse_event_props(payload, market_map):
        nba_player_id = match_prop_player(row["player_name"], teams, players_by_name)
        if nba_player_id is None:
            unmatched.add(row["player_name"])
        rows.append({
            **row,
            "provider_event_id": str(event.get("id") or ""),
            "nba_game_id": nba_game_id,
            "game_date": game_date,
            "nba_player_id": nba_player_id,
        })
    return rows, nba_game_id, unmatched


def _prop_tuple(row: Mapping, provider: str, run_id: int | None) -> tuple:
    return (
        provider, row["provider_event_id"], row["nba_game_id"], row["game_date"],
        row["bookmaker"], row["market"], row["player_name"], row["nba_player_id"],
        row["line"], row["over_price"], row["under_price"],
        row["provider_updated_at"], provider, run_id,
    )


def _read_last_run_started(cur: object) -> datetime | None:
    cur.execute(
        """
        SELECT MAX(started_at)
        FROM ingestion_runs
        WHERE kind = %s
          AND status = 'succeeded'
        """,
        (PROPS_INGESTION_KIND,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _read_player_index(cur: object) -> dict[str, list[tuple[str, str]]]:
    cur.execute(
        """
        SELECT nba_id, name, team
        FROM players
        WHERE nba_id IS NOT NULL
        """
    )
    return index_players_by_canonical_name(cur.fetchall())


def scrape_prop_odds(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    provider: PropsProvider | None = None,
    now: datetime | None = None,
    min_hours_between_runs: float = PROPS_MIN_HOURS_BETWEEN_RUNS,
    markets: Sequence[str] = PROPS_DEFAULT_MARKETS,
) -> bool:
    provider = provider or provider_from_env(markets)
    if provider is None:
        logger.info("props provider not configured (%s unset), skipping", PROPS_API_KEY_ENV)
        return True

    now = now or datetime.now(timezone.utc)
    start, end = props_window(now)
    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        last_started = _read_last_run_started(cur)
        if not props_run_due(last_started, now, min_hours_between_runs):
            logger.info(
                "props: last snapshot started %s, under %sh ago, skipping to save quota",
                last_started, min_hours_between_runs,
            )
            return True

        logger.info("props: snapshotting %s player props %s to %s...", provider.name, start, end)
        run_id = _start_ingestion_run(
            conn, PROPS_INGESTION_KIND,
            watermark_from=start.astimezone(EASTERN).date(),
            watermark_to=end.astimezone(EASTERN).date(),
            dry_run=dry_run,
        )
        try:
            events = events_in_window(provider.fetch_events(start, end), start, end)
        except Exception as e:  # noqa: BLE001 - a failed listing means nothing to snapshot
            logger.error("props: events fetch failed (%s)", e)
            _finish_ingestion_run(conn, run_id, "failed", 0, notes="events fetch failed")
            return False

        cost = provider.event_props_cost()
        projected = credits_for_snapshot(len(events), cost)
        logger.info(
            "props: %d event(s) x %d credit(s) = %d projected, %s of %d monthly credits remaining",
            len(events), cost, projected, provider.requests_remaining, PROPS_MONTHLY_BUDGET,
        )
        if not snapshot_allowed(provider.requests_remaining, projected):
            logger.error(
                "props: refusing snapshot, %s remaining minus %d projected is under the %d reserve",
                provider.requests_remaining, projected, PROPS_RESERVE_CREDITS,
            )
            _finish_ingestion_run(
                conn, run_id, "failed", 0,
                notes=f"refused: {provider.requests_remaining} credits remaining, "
                      f"{projected} projected, {PROPS_RESERVE_CREDITS} reserved",
            )
            return False

        lookup_from = start.astimezone(EASTERN).date() - timedelta(days=1)
        lookup_to = end.astimezone(EASTERN).date() + timedelta(days=1)
        schedule_rows = _read_schedule_rows(cur, lookup_from, lookup_to)
        players_by_name = _read_player_index(cur)

        rows: list[dict] = []
        unmapped = 0
        unmatched: set[str] = set()
        failed: list[str] = []
        skipped_for_quota = 0
        for event in events:
            event_id = str(event["id"])
            remaining = provider.requests_remaining
            if remaining is not None and remaining < cost:
                skipped_for_quota += 1
                continue
            try:
                payload = provider.fetch_event_props(event_id)
            except Exception as e:  # noqa: BLE001 - the other events are still usable
                logger.warning("props: odds for event %s failed (%s)", event_id, e)
                failed.append(event_id)
                continue
            event_rows, nba_game_id, event_unmatched = plan_event_props(
                event, payload, PROPS_MARKET_MAP, schedule_rows, players_by_name
            )
            if nba_game_id is None:
                unmapped += 1
                logger.warning(
                    "props: event %s (%s at %s) matched no nba_schedule game",
                    event_id, event.get("away_team"), event.get("home_team"),
                )
            unmatched |= event_unmatched
            rows.extend(event_rows)

        written = _batch_upsert(
            cur, PROP_INSERT_SQL, [_prop_tuple(r, provider.name, run_id) for r in rows]
        )
    finally:
        cur.close()

    if unmatched:
        logger.warning(
            "props: %d player name(s) matched no single player: %s",
            len(unmatched), ", ".join(sorted(unmatched)),
        )
    notes = "; ".join(
        part for part in (
            f"requests remaining {provider.requests_remaining}"
            if provider.requests_remaining is not None else "",
            f"{unmapped} unmapped event(s)" if unmapped else "",
            f"{len(unmatched)} unmatched player name(s)" if unmatched else "",
            f"odds fetch failed for {len(failed)} event(s)" if failed else "",
            f"quota too low, {skipped_for_quota} event(s) skipped" if skipped_for_quota else "",
        ) if part
    ) or None
    # every odds call failing is an outage, not a quiet night.
    status = "failed" if events and len(failed) == len(events) else "succeeded"
    _finish_ingestion_run(conn, run_id, status, written, notes=notes)
    logger.info(
        "props: %d row(s) from %d event(s), %d unmapped, %s request(s) remaining%s",
        written, len(events), unmapped, provider.requests_remaining,
        " (dry run: nothing written)" if dry_run else "",
    )
    return status == "succeeded"
