"""validate play-by-play derived lineups against box-score minutes.

pbpstats reconstructs on-floor lineups from the cdn live play-by-play feed; this
script turns those into per-team stints and checks them against the stats.nba.com
box score (boxscoretraditionalv3), which is fetched from a different endpoint.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

import numpy as np
import pandas as pd

EXPERIMENT_DIR = Path(__file__).resolve().parent
DATA_DIR = EXPERIMENT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORT_PATH = EXPERIMENT_DIR / "REPORT.md"

log = logging.getLogger("pbp_validation")

SEASON_TYPE = "Regular Season"
REQUEST_DELAY_SECONDS = 5.0
MAX_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 60

REGULATION_PERIOD_SECONDS = 720.0
OVERTIME_PERIOD_SECONDS = 300.0
PLAYERS_ON_FLOOR = 5

# pre-registered pass criteria, fixed before the first run.
PLAYER_TOLERANCE_SECONDS = 30.0
MIN_SHARE_PLAYERS_WITHIN_TOLERANCE = 0.95
REQUIRED_SHARE_FIVE_MAN_STINTS = 1.0
TEAM_TOTAL_TOLERANCE_MINUTES = 0.5

EVENT_COLUMNS: tuple[str, ...] = (
    "game_id", "period", "event_order", "seconds_remaining", "team_id", "players",
)
STINT_COLUMNS: tuple[str, ...] = (
    "game_id", "team_id", "period", "stint_index", "start_elapsed", "end_elapsed",
    "start_clock", "end_clock", "duration_seconds", "n_players", "players",
)
BOX_COLUMNS: tuple[str, ...] = ("game_id", "team_id", "player_id", "box_seconds")


def period_length_seconds(period: int) -> float:
    return REGULATION_PERIOD_SECONDS if period <= 4 else OVERTIME_PERIOD_SECONDS


def expected_team_minutes(n_periods: int) -> float:
    """player-minutes per team for a game with n_periods: 240 + 25 per overtime."""
    overtimes = max(n_periods - 4, 0)
    return 5 * (48.0 + 5.0 * overtimes)


def format_clock(seconds_remaining: float) -> str:
    whole = int(round(seconds_remaining))
    return f"{whole // 60:02d}:{whole % 60:02d}"


def parse_box_minutes(value: object) -> float:
    """'41:45' or 'PT41M45.00S' -> seconds. blank or None -> nan (did not play)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    text = str(value).strip()
    if not text:
        return float("nan")
    if text.startswith("PT"):
        body = text[2:].rstrip("S")
        minutes_part, _, seconds_part = body.partition("M")
        return float(minutes_part or 0) * 60 + float(seconds_part or 0)
    minutes_part, _, seconds_part = text.partition(":")
    return float(minutes_part) * 60 + float(seconds_part or 0)


def build_stints(events: pd.DataFrame) -> pd.DataFrame:
    """collapse per-event lineups into per-team stints.

    a stint starts at the first event carrying a new lineup and ends where the next
    one starts (or at the period end). zero-duration runs are dropped because the
    live feed splits every substitution into an out event and an in event at the
    same clock, which leaves a momentary four-man lineup with no time attached.
    event order is kept as given, so a clock that runs backwards yields a negative
    duration rather than being silently sorted away.
    """
    rows: list[dict[str, object]] = []
    ordered = events.sort_values(["game_id", "team_id", "period", "event_order"])
    for (game_id, team_id, period), group in ordered.groupby(
        ["game_id", "team_id", "period"], sort=True
    ):
        length = period_length_seconds(int(period))
        runs: list[tuple[float, tuple[int, ...]]] = []
        for remaining, players in zip(group["seconds_remaining"], group["players"]):
            lineup = tuple(sorted(int(p) for p in players))
            if not runs or runs[-1][1] != lineup:
                runs.append((length - float(remaining), lineup))
        if runs:
            runs[0] = (0.0, runs[0][1])
        kept: list[tuple[float, float, tuple[int, ...]]] = []
        for i, (start, lineup) in enumerate(runs):
            end = runs[i + 1][0] if i + 1 < len(runs) else length
            if end - start == 0:
                continue
            if kept and kept[-1][2] == lineup and kept[-1][1] == start:
                kept[-1] = (kept[-1][0], end, lineup)
            else:
                kept.append((start, end, lineup))
        for index, (start, end, lineup) in enumerate(kept):
            rows.append({
                "game_id": str(game_id),
                "team_id": int(team_id),
                "period": int(period),
                "stint_index": index,
                "start_elapsed": start,
                "end_elapsed": end,
                "start_clock": format_clock(length - start),
                "end_clock": format_clock(length - end),
                "duration_seconds": end - start,
                "n_players": len(lineup),
                "players": list(lineup),
            })
    return pd.DataFrame(rows, columns=list(STINT_COLUMNS))


def check_five_man(stints: pd.DataFrame) -> pd.DataFrame:
    """per game: stint count, how many do not have exactly five players."""
    flagged = stints.assign(bad=stints["n_players"] != PLAYERS_ON_FLOOR)
    out = flagged.groupby("game_id").agg(
        n_stints=("bad", "size"), bad_lineup_stints=("bad", "sum"),
    ).reset_index()
    out["bad_lineup_stints"] = out["bad_lineup_stints"].astype(int)
    out["five_man_ok"] = out["bad_lineup_stints"] == 0
    return out


def check_durations(stints: pd.DataFrame, expected: pd.DataFrame) -> pd.DataFrame:
    """per (game, team): negative stints and team minutes vs expected.

    team minutes are five times the summed stint clock, so a wrong lineup size does
    not leak into this check; that is check_five_man's job. ``expected`` carries
    game_id, team_id, expected_minutes.
    """
    agg = stints.assign(negative=stints["duration_seconds"] < 0).groupby(
        ["game_id", "team_id"]
    ).agg(
        clock_seconds=("duration_seconds", "sum"),
        negative_stints=("negative", "sum"),
        n_periods=("period", "max"),
    ).reset_index()
    agg["negative_stints"] = agg["negative_stints"].astype(int)
    agg["team_minutes"] = agg["clock_seconds"] * PLAYERS_ON_FLOOR / 60.0
    out = agg.merge(expected, on=["game_id", "team_id"], how="outer")
    out["team_minutes"] = out["team_minutes"].fillna(0.0)
    out["negative_stints"] = out["negative_stints"].fillna(0).astype(int)
    out["team_minutes_diff"] = out["team_minutes"] - out["expected_minutes"]
    out["team_total_ok"] = (
        out["team_minutes_diff"].abs() <= TEAM_TOTAL_TOLERANCE_MINUTES
    ) & (out["negative_stints"] == 0)
    return out


def player_floor_seconds(stints: pd.DataFrame) -> pd.DataFrame:
    exploded = stints[["game_id", "team_id", "duration_seconds", "players"]].explode(
        "players"
    )
    exploded = exploded.rename(columns={"players": "player_id"})
    exploded["player_id"] = exploded["player_id"].astype(int)
    return exploded.groupby(["game_id", "team_id", "player_id"], as_index=False)[
        "duration_seconds"
    ].sum().rename(columns={"duration_seconds": "pbp_seconds"})


def reconcile_players(stints: pd.DataFrame, box: pd.DataFrame) -> pd.DataFrame:
    """per player: on-floor seconds from stints vs box-score seconds.

    a player missing on one side counts as zero there, so a phantom stint player and
    a box-score player the lineups never saw both show up as large differences.
    """
    pbp = player_floor_seconds(stints)
    played = box[box["box_seconds"].fillna(0) > 0][list(BOX_COLUMNS)]
    out = pbp.merge(played, on=["game_id", "team_id", "player_id"], how="outer")
    out["pbp_seconds"] = out["pbp_seconds"].fillna(0.0)
    out["box_seconds"] = out["box_seconds"].fillna(0.0)
    out["diff_seconds"] = out["pbp_seconds"] - out["box_seconds"]
    out["abs_diff_seconds"] = out["diff_seconds"].abs()
    out["within_tolerance"] = out["abs_diff_seconds"] <= PLAYER_TOLERANCE_SECONDS
    return out.sort_values(["game_id", "team_id", "player_id"]).reset_index(drop=True)


def summarize_games(
    five_man: pd.DataFrame, durations: pd.DataFrame, players: pd.DataFrame,
) -> pd.DataFrame:
    """one row per game with every check and whether any failed."""
    team = durations.groupby("game_id").agg(
        negative_stints=("negative_stints", "sum"),
        max_team_minutes_diff=("team_minutes_diff", lambda s: float(s.abs().max())),
        team_total_ok=("team_total_ok", "all"),
        expected_minutes=("expected_minutes", "max"),
    ).reset_index()
    per_player = players.groupby("game_id").agg(
        n_players=("player_id", "size"),
        players_within=("within_tolerance", "sum"),
        mean_abs_diff_seconds=("abs_diff_seconds", "mean"),
        max_abs_diff_seconds=("abs_diff_seconds", "max"),
    ).reset_index()
    per_player["players_within"] = per_player["players_within"].astype(int)
    out = five_man.merge(team, on="game_id", how="outer").merge(
        per_player, on="game_id", how="outer",
    )
    out["players_ok"] = out["players_within"] == out["n_players"]
    out["game_ok"] = (
        out["five_man_ok"].fillna(False).astype(bool)
        & out["team_total_ok"].fillna(False).astype(bool)
        & out["players_ok"].fillna(False).astype(bool)
    )
    return out.sort_values("game_id").reset_index(drop=True)


@dataclass(frozen=True)
class Verdict:
    n_games: int
    games_failing: int
    n_stints: int
    five_man_share: float
    n_players: int
    player_share_within: float
    player_mean_abs_diff: float
    team_rows: int
    team_rows_within: int
    unreconstructed_games: int

    @property
    def players_pass(self) -> bool:
        return self.player_share_within >= MIN_SHARE_PLAYERS_WITHIN_TOLERANCE

    @property
    def five_man_pass(self) -> bool:
        return self.five_man_share >= REQUIRED_SHARE_FIVE_MAN_STINTS

    @property
    def team_pass(self) -> bool:
        return self.team_rows > 0 and self.team_rows_within == self.team_rows

    @property
    def passed(self) -> bool:
        return (
            self.n_games > 0 and self.unreconstructed_games == 0
            and self.players_pass and self.five_man_pass and self.team_pass
        )


def compute_verdict(
    stints: pd.DataFrame, durations: pd.DataFrame, players: pd.DataFrame,
    games: pd.DataFrame, unreconstructed_games: int = 0,
) -> Verdict:
    n_stints = int(len(stints))
    five = int((stints["n_players"] == PLAYERS_ON_FLOOR).sum())
    n_players = int(len(players))
    return Verdict(
        n_games=int(len(games)) + unreconstructed_games,
        games_failing=int((~games["game_ok"]).sum()) + unreconstructed_games,
        n_stints=n_stints,
        five_man_share=five / n_stints if n_stints else 0.0,
        n_players=n_players,
        player_share_within=(
            float(players["within_tolerance"].mean()) if n_players else 0.0
        ),
        player_mean_abs_diff=(
            float(players["abs_diff_seconds"].mean()) if n_players else float("nan")
        ),
        team_rows=int(len(durations)),
        team_rows_within=int(durations["team_total_ok"].sum()),
        unreconstructed_games=unreconstructed_games,
    )


_Result = TypeVar("_Result")


def _is_retryable(exc: Exception) -> bool:
    import requests

    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(exc.response, "status_code", None)
        return status is None or status == 429 or status >= 500
    if isinstance(exc, requests.exceptions.RequestException):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError, json.JSONDecodeError))


def _is_network_error(exc: Exception) -> bool:
    import requests

    return isinstance(exc, (requests.exceptions.RequestException, ConnectionError,
                            TimeoutError))


class Throttle:
    """enforces a minimum gap between network calls and retries with backoff."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self._last_call = 0.0

    def call(self, label: str, fetch: Callable[[], _Result]) -> _Result:
        backoff = INITIAL_BACKOFF_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = self.delay_seconds - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                return fetch()
            except Exception as exc:
                if attempt == MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                log.warning("%s failed (attempt %d/%d): %s, retrying in %.0fs",
                            label, attempt, MAX_ATTEMPTS, exc, backoff)
                time.sleep(backoff + random.uniform(0, 2))
                backoff *= 2
        raise RuntimeError(f"{label}: retry loop exhausted")


def discover_games(season: str, throttle: Throttle) -> pd.DataFrame:
    """every completed game this season: game_id, game_date, team_id, team_minutes."""
    from nba_api.stats.endpoints import leaguegamelog

    endpoint = throttle.call(f"leaguegamelog {season}", lambda: leaguegamelog.LeagueGameLog(
        season=season, season_type_all_star=SEASON_TYPE,
        player_or_team_abbreviation="T", timeout=REQUEST_TIMEOUT_SECONDS,
    ))
    raw = endpoint.get_data_frames()[0]
    return pd.DataFrame({
        "game_id": raw["GAME_ID"].astype(str),
        "game_date": raw["GAME_DATE"].astype(str),
        "team_id": raw["TEAM_ID"].astype(int),
        "matchup": raw["MATCHUP"].astype(str),
        "expected_minutes": pd.to_numeric(raw["MIN"], errors="coerce").astype(float),
    })


def sample_game_ids(games: pd.DataFrame, n: int, seed: int) -> list[str]:
    ids = sorted(games["game_id"].unique())
    if n >= len(ids):
        return ids
    return sorted(random.Random(seed).sample(ids, n))


def fetch_event_lineups(game_id: str, throttle: Throttle) -> pd.DataFrame:
    from pbpstats.client import Client

    client = Client({"Possessions": {"source": "web", "data_provider": "live"}})
    game = throttle.call(f"pbpstats {game_id}", lambda: client.Game(game_id))
    rows: list[dict[str, object]] = []
    order = 0
    for possession in game.possessions.items:
        for event in possession.events:
            for team_id, players in event.current_players.items():
                rows.append({
                    "game_id": game_id,
                    "period": int(event.period),
                    "event_order": order,
                    "seconds_remaining": float(event.seconds_remaining),
                    "team_id": int(team_id),
                    "players": [int(p) for p in players],
                })
            order += 1
    return pd.DataFrame(rows, columns=list(EVENT_COLUMNS))


def fetch_box_minutes(game_id: str, throttle: Throttle) -> pd.DataFrame:
    from nba_api.stats.endpoints import boxscoretraditionalv3

    endpoint = throttle.call(f"boxscoretraditionalv3 {game_id}", lambda: (
        boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id, timeout=REQUEST_TIMEOUT_SECONDS,
        )
    ))
    raw = endpoint.get_data_frames()[0]
    return pd.DataFrame({
        "game_id": game_id,
        "team_id": raw["teamId"].astype(int),
        "player_id": raw["personId"].astype(int),
        "box_seconds": raw["minutes"].map(parse_box_minutes),
    })


def _cached(path: Path, use_cache: bool, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    if use_cache and path.exists():
        return pd.read_parquet(path)
    frame = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def _fmt_game_table(games: pd.DataFrame, meta: pd.DataFrame) -> str:
    labels = meta.drop_duplicates("game_id").set_index("game_id")
    lines = [
        "| game | date | matchup | periods | stints | non-5 stints | neg stints "
        "| max team diff (min) | players | within 30s | mean abs diff (s) "
        "| max abs diff (s) | ok |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in games.itertuples():
        info = labels.loc[row.game_id] if row.game_id in labels.index else None
        n_periods = 4 + int(round((row.expected_minutes - 240) / 25))
        lines.append(
            f"| {row.game_id} | {info['game_date'] if info is not None else ''} "
            f"| {info['matchup'] if info is not None else ''} | {n_periods} "
            f"| {int(row.n_stints)} | {int(row.bad_lineup_stints)} "
            f"| {int(row.negative_stints)} | {row.max_team_minutes_diff:.2f} "
            f"| {int(row.n_players)} | {int(row.players_within)} "
            f"| {row.mean_abs_diff_seconds:.1f} | {row.max_abs_diff_seconds:.1f} "
            f"| {'yes' if row.game_ok else '**no**'} |"
        )
    return "\n".join(lines)


def criteria_markdown() -> str:
    return "\n".join([
        "## Pass criteria (stated before results)",
        "",
        "Fixed in `validate.py` before the first run. The backfill is justified only "
        "if all three hold on the sample:",
        "",
        f"1. **Players**: at least {MIN_SHARE_PLAYERS_WITHIN_TOLERANCE:.0%} of "
        "player-games have summed on-floor seconds within "
        f"{PLAYER_TOLERANCE_SECONDS:.0f} seconds of box-score minutes.",
        f"2. **Lineups**: {REQUIRED_SHARE_FIVE_MAN_STINTS:.0%} of stints (positive "
        "duration) have exactly five players per team.",
        "3. **Team totals**: every team-game sums to 240 + 25 per OT player-minutes "
        f"within {TEAM_TOTAL_TOLERANCE_MINUTES} minutes, with no negative stint.",
        "",
        "A game pbpstats cannot reconstruct counts as failing. A game that cannot be "
        "downloaded at all is listed and excluded.",
    ])


def method_markdown() -> str:
    return "\n".join([
        "## Method",
        "",
        "- Lineups: `pbpstats` 1.3.11, `Possessions` resource, `live` data provider "
        "(cdn live play-by-play JSON). The `stats_nba` provider is dead: it calls "
        "`playbyplayv2`, which no longer returns `resultSets`.",
        "- Reference minutes: stats.nba.com `boxscoretraditionalv3` (`minutes` as "
        "`mm:ss`), a separate endpoint from the lineup source.",
        "- Expected team minutes: `leaguegamelog` team `MIN` (240, 265, 290, ...).",
        "- Stints: consecutive events with the same five on the floor for one team. "
        "The live feed records each substitution as an out event and an in event at "
        "the same clock, so the momentary four-man lineup between them has zero "
        "duration and is dropped. Event order is preserved, so a clock running "
        "backwards would show as a negative stint.",
        "- Team minutes are 5 x summed stint clock; lineup size is judged only by "
        "the five-man check.",
        "- Players: summed seconds over stints vs box seconds, outer-joined, so a "
        "phantom lineup player and an unseen box-score player both count as misses.",
        f"- Sampling: seeded random draw of completed {SEASON_TYPE} games.",
    ])


def render_report(
    season: str, seed: int, command: str, verdict: Verdict | None,
    games: pd.DataFrame | None, meta: pd.DataFrame | None,
    players: pd.DataFrame | None, failures: list[tuple[str, str]],
) -> str:
    parts = [
        "# PBP lineup validation",
        "",
        "Should play-by-play derived lineups be backfilled? This experiment checks "
        "reconstructed stints against box-score minutes on a sample of games.",
        "",
        criteria_markdown(),
        "",
        method_markdown(),
        "",
        "## Results",
        "",
    ]
    if verdict is None or games is None or meta is None or players is None:
        parts += ["Not yet run.", "", run_instructions_markdown()]
        return "\n".join(parts) + "\n"
    parts += [
        f"Season {season}, seed {seed}. Command: `{command}`",
        "",
        _fmt_game_table(games, meta),
        "",
    ]
    if failures:
        parts += ["Games that could not be fetched or reconstructed:", ""]
        parts += [f"- `{gid}`: {reason}" for gid, reason in failures]
        parts.append("")
    worst = players.sort_values("abs_diff_seconds", ascending=False).head(10)
    parts += [
        "Largest player discrepancies:",
        "",
        "| game | team | player | pbp s | box s | diff s |",
        "|---|---|---|---|---|---|",
    ]
    parts += [
        f"| {r.game_id} | {r.team_id} | {r.player_id} | {r.pbp_seconds:.1f} "
        f"| {r.box_seconds:.1f} | {r.diff_seconds:+.1f} |"
        for r in worst.itertuples()
    ]
    parts += [
        "",
        "## Verdict",
        "",
        "| criterion | threshold | observed | pass |",
        "|---|---|---|---|",
        f"| players within {PLAYER_TOLERANCE_SECONDS:.0f}s | >= "
        f"{MIN_SHARE_PLAYERS_WITHIN_TOLERANCE:.0%} | {verdict.player_share_within:.1%} "
        f"({verdict.n_players} player-games, mean abs diff "
        f"{verdict.player_mean_abs_diff:.1f}s) | {_yes(verdict.players_pass)} |",
        f"| five-man stints | {REQUIRED_SHARE_FIVE_MAN_STINTS:.0%} "
        f"| {verdict.five_man_share:.2%} of {verdict.n_stints} | "
        f"{_yes(verdict.five_man_pass)} |",
        f"| team totals within {TEAM_TOTAL_TOLERANCE_MINUTES} min | all "
        f"| {verdict.team_rows_within}/{verdict.team_rows} team-games | "
        f"{_yes(verdict.team_pass)} |",
        f"| games failing any check | (reported) | {verdict.games_failing}/"
        f"{verdict.n_games} | |",
        "",
        f"**Overall: {'PASS' if verdict.passed else 'FAIL'}.**",
        "",
    ]
    return "\n".join(parts) + "\n"


def _yes(flag: bool) -> str:
    return "yes" if flag else "**no**"


def run_instructions_markdown() -> str:
    return "\n".join([
        "### How to run",
        "",
        "```powershell",
        "python -m uv pip install --python ml/.venv/Scripts/python.exe "
        "-r ml/experiments/pbp_validation/requirements.txt",
        "ml/.venv/Scripts/python.exe ml/experiments/pbp_validation/validate.py "
        "--games 20 --season 2025-26",
        "```",
        "",
        "From a GitHub Actions job (ubuntu-latest, python 3.14), if stats.nba.com "
        "refuses this machine:",
        "",
        "```yaml",
        "- uses: actions/setup-python@v5",
        "  with: { python-version: '3.14' }",
        "- run: pip install -r ml/requirements.txt "
        "-r ml/experiments/pbp_validation/requirements.txt",
        "- run: python ml/experiments/pbp_validation/validate.py --games 20 "
        "--season 2025-26",
        "- uses: actions/upload-artifact@v4",
        "  with: { name: pbp-validation, path: ml/experiments/pbp_validation/REPORT.md }",
        "```",
    ])


def run(args: argparse.Namespace) -> int:
    throttle = Throttle(args.delay)
    command = "validate.py " + " ".join(sys.argv[1:])
    try:
        meta = discover_games(args.season, throttle)
    except Exception as exc:
        log.error("could not reach stats.nba.com for the game log: %s", exc)
        args.report.write_text(
            render_report(args.season, args.seed, command, None, None, None, None, []),
            encoding="utf-8",
        )
        return 2
    game_ids = sample_game_ids(meta, args.games, args.seed)
    log.info("sampled %d of %d games", len(game_ids), meta["game_id"].nunique())

    all_stints: list[pd.DataFrame] = []
    all_box: list[pd.DataFrame] = []
    fetch_failures: list[tuple[str, str]] = []
    unreconstructed: list[tuple[str, str]] = []
    for game_id in game_ids:
        try:
            box = _cached(CACHE_DIR / f"{game_id}_box.parquet", not args.no_cache,
                          lambda: fetch_box_minutes(game_id, throttle))
        except Exception as exc:
            fetch_failures.append((game_id, f"box score fetch: {type(exc).__name__}: {exc}"))
            continue
        try:
            events = _cached(CACHE_DIR / f"{game_id}_events.parquet", not args.no_cache,
                             lambda: fetch_event_lineups(game_id, throttle))
        except Exception as exc:
            if _is_network_error(exc):
                fetch_failures.append((game_id, f"pbp fetch: {type(exc).__name__}: {exc}"))
            else:
                unreconstructed.append((game_id, f"pbpstats: {type(exc).__name__}: {exc}"))
            continue
        stints = build_stints(events)
        if stints.empty:
            unreconstructed.append((game_id, "no stints produced"))
            continue
        all_stints.append(stints)
        all_box.append(box)
        log.info("%s: %d stints", game_id, len(stints))

    if not all_stints:
        log.error("no game reconstructed")
        args.report.write_text(
            render_report(args.season, args.seed, command, None, None, None, None,
                          fetch_failures + unreconstructed),
            encoding="utf-8",
        )
        return 2

    stints = pd.concat(all_stints, ignore_index=True)
    box = pd.concat(all_box, ignore_index=True)
    expected = meta[meta["game_id"].isin(stints["game_id"].unique())][
        ["game_id", "team_id", "expected_minutes"]
    ]
    five_man = check_five_man(stints)
    durations = check_durations(stints, expected)
    players = reconcile_players(stints, box)
    games = summarize_games(five_man, durations, players)
    verdict = compute_verdict(stints, durations, players, games, len(unreconstructed))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stints.to_parquet(DATA_DIR / f"stints_{args.season}.parquet", index=False)
    players.to_parquet(DATA_DIR / f"player_reconciliation_{args.season}.parquet",
                       index=False)
    args.report.write_text(
        render_report(args.season, args.seed, command, verdict, games, meta, players,
                      fetch_failures + unreconstructed),
        encoding="utf-8",
    )
    log.info("verdict: %s (%d/%d games failing)",
             "PASS" if verdict.passed else "FAIL", verdict.games_failing, verdict.n_games)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS,
                        help="minimum seconds between network calls")
    parser.add_argument("--report", type=Path, default=REPORT_PATH,
                        help="where to write the markdown summary")
    parser.add_argument("--no-cache", action="store_true",
                        help="re-download games already cached under data/cache")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
