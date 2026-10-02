"""score stored prediction runs against completed games.

    python score_runs.py                              # runs from the last 30 days
    python score_runs.py --since 2026-10-20 --channel production
    python score_runs.py --run-id 412 --run-id 413
    python score_runs.py --look dec1                  # the pre-registered look report

reads prediction_runs, player_game_predictions and the truth layer via
DATABASE_URL, scores with fnba_ml.scoring, and writes markdown plus a csv beside
it (MODEL.md 13.8.6). read-only: no write to the database.

a game counts as completed when team_game_logs holds both sides of it or
player_game_status holds any row for it. nba_schedule.game_status is not used:
it is free text from three sources ('Final', '7:30 pm ET', 'Postponed').

the 13.4 baselines are rebuilt from the truth layer as of each game's date:
status and box rows for every player in the scored games over a lookback window,
plus each predicted player's appearances since the artifact's training start for
the rate families seeded from models/<version>/ewma_state.parquet.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from fnba_ml.cli import add_common_args, setup_logging
from fnba_ml.config import (
    MODELS_DIR,
    PROSPECTIVE_2026_27,
    PROSPECTIVE_LOOKS,
    PROSPECTIVE_MODEL_VERSION,
    REPORTS_DIR,
    TRUTH_SEASON_TYPES,
)
from fnba_ml.scoring import (
    E5_STATS,
    PROBABILITIES,
    RATE_FAMILY_STATS,
    STATS,
    baseline_history_label,
    build_baselines,
    channel_of,
    compare_served_shadow,
    comparison_results,
    excluded_season_type_rows,
    falsification_observations,
    falsification_table,
    look_date,
    merge_baselines,
    render_look_report,
    scheduled_rows,
    score_runs,
    seeded_rate_baselines,
    snapshot_missing_families,
    split_endpoint_rows,
    summarise,
)
from fnba_ml.store import UNCOND_SUFFIX

log = logging.getLogger("score_runs")

DEFAULT_LOOKBACK_DAYS = 30

# a full prior season, so an october game's avail_rate_10, ewma_total and
# vacated prior see last season rather than nothing.
DEFAULT_HISTORY_DAYS = 400

LOOK_NAMES: tuple[str, ...] = tuple(name for name, *_ in PROSPECTIVE_LOOKS)
EWMA_STATE_FILE = "ewma_state.parquet"
METADATA_FILE = "metadata.json"
F7_F8_KEYS: tuple[str, ...] = ("stl_expanding_vs_h20_ewma", "rare_event_h20_vs_expanding")

# migration 015 adds these; selected only when information_schema shows them.
OPTIONAL_RUN_COLUMNS: tuple[str, ...] = ("channel", "information_as_of", "history_through")

RUN_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'prediction_runs'
  AND table_schema = ANY(current_schemas(false))
"""

# box-score presence, not game_status, which is multi-source free text.
COMPLETED_GAME_CONDITION = """
(
    (SELECT COUNT(*) FROM team_game_logs t WHERE t.nba_game_id = s.nba_game_id) >= 2
    OR EXISTS (SELECT 1 FROM player_game_status g WHERE g.nba_game_id = s.nba_game_id)
)
"""

RUNS_SQL = """
SELECT
    id, model_version, feature_version, predicted_at, forecast_cutoff_at, notes{optional}
FROM prediction_runs
WHERE status = 'complete'
  AND {selector}
ORDER BY id
"""

RUN_WINDOW_SELECTOR = "predicted_at >= %(since)s AND predicted_at < %(until)s"
RUN_ID_SELECTOR = "id = ANY(%(run_ids)s)"

PREDICTIONS_SQL = f"""
SELECT
    p.prediction_run_id AS run_id,
    p.nba_player_id,
    p.nba_game_id,
    p.game_date,
    p.stat,
    p.quantile,
    p.value,
    p.conditional
FROM player_game_predictions p
JOIN nba_schedule s ON s.nba_game_id = p.nba_game_id
WHERE p.prediction_run_id = ANY(%(run_ids)s)
  AND p.stat = ANY(%(stats)s)
  AND {COMPLETED_GAME_CONDITION}
"""

TRUTH_SQL = f"""
WITH predicted AS (
    SELECT DISTINCT p.nba_player_id, p.nba_game_id
    FROM player_game_predictions p
    WHERE p.prediction_run_id = ANY(%(run_ids)s)
)
SELECT
    pr.nba_player_id,
    pr.nba_game_id,
    s.game_date,
    s.season_type,
    pgs.played,
    COALESCE(pgl.minutes, pgs.minutes) AS minutes,
    pgl.pts, pgl.reb, pgl.ast, pgl.stl, pgl.blk, pgl.tov,
    pgl.fg3m, pgl.fgm, pgl.fga, pgl.ftm, pgl.fta
FROM predicted pr
JOIN nba_schedule s ON s.nba_game_id = pr.nba_game_id
LEFT JOIN player_game_status pgs
    ON pgs.nba_player_id = pr.nba_player_id AND pgs.nba_game_id = pr.nba_game_id
LEFT JOIN player_game_logs pgl
    ON pgl.nba_player_id = pr.nba_player_id AND pgl.nba_game_id = pr.nba_game_id
WHERE s.season_type = ANY(%(season_types)s)
  AND {COMPLETED_GAME_CONDITION}
"""


_BOX_COLUMNS = ", ".join(f"pgl.{stat}" for stat in E5_STATS)

# every status row of every player in the scored games, over the lookback window.
HISTORY_SQL = f"""
WITH involved AS (
    SELECT DISTINCT g.nba_player_id
    FROM player_game_status g
    WHERE g.nba_game_id = ANY(%(game_ids)s)
    UNION
    SELECT UNNEST(%(player_ids)s::TEXT[])
)
SELECT
    pgs.nba_player_id,
    pgs.nba_game_id,
    pgs.team_id,
    s.game_date,
    s.season_type,
    pgs.played,
    pgs.listed_inactive,
    COALESCE(pgl.minutes, pgs.minutes) AS minutes,
    {_BOX_COLUMNS}
FROM player_game_status pgs
JOIN involved i ON i.nba_player_id = pgs.nba_player_id
JOIN nba_schedule s ON s.nba_game_id = pgs.nba_game_id
LEFT JOIN player_game_logs pgl
    ON pgl.nba_player_id = pgs.nba_player_id AND pgl.nba_game_id = pgs.nba_game_id
WHERE s.game_date >= %(start)s
  AND s.game_date <= %(end)s
"""

_RATE_COLUMNS = ", ".join(f"l.{stat}" for stat in RATE_FAMILY_STATS)

# appearances since the artifact's training start: the seed weight and the replay.
RATE_HISTORY_SQL = f"""
SELECT
    l.nba_player_id,
    l.game_date,
    l.season_type,
    l.minutes,
    {_RATE_COLUMNS}
FROM player_game_logs l
WHERE l.nba_player_id = ANY(%(player_ids)s)
  AND l.game_date >= %(start)s
  AND l.game_date <= %(end)s
  AND l.minutes > 0
"""


def scored_stat_names() -> list[str]:
    return [*PROBABILITIES, *STATS, *(f"{stat}{UNCOND_SUFFIX}" for stat in STATS)]


def _read_sql(sql: str, params: dict[str, object] | None = None) -> pd.DataFrame:
    import psycopg2  # noqa: PLC0415 - only needed on the database path

    from fnba_ml.data.postgres_source import load_database_url  # noqa: PLC0415

    with psycopg2.connect(load_database_url()) as conn:
        return pd.read_sql_query(sql, conn, params=params or {})


def load_runs(since: date, until: date, run_ids: list[int]) -> pd.DataFrame:
    present = set(_read_sql(RUN_COLUMNS_SQL)["column_name"])
    optional = "".join(f", {c}" for c in OPTIONAL_RUN_COLUMNS if c in present)
    if run_ids:
        sql = RUNS_SQL.format(optional=optional, selector=RUN_ID_SELECTOR)
        return _read_sql(sql, {"run_ids": run_ids})
    sql = RUNS_SQL.format(optional=optional, selector=RUN_WINDOW_SELECTOR)
    return _read_sql(sql, {"since": since, "until": until + timedelta(days=1)})


def load_artifact_seed(
    models_dir: Path, version: str
) -> tuple[pd.DataFrame | None, str | None, str]:
    """(ewma_state, training start, reason) for the F7/F8 seed; reason is '' when usable."""
    directory = models_dir / version
    state_path, metadata_path = directory / EWMA_STATE_FILE, directory / METADATA_FILE
    if not state_path.exists() or not metadata_path.exists():
        return None, None, f"models/{version} has no {EWMA_STATE_FILE} or {METADATA_FILE}"
    snapshot = pd.read_parquet(state_path)
    missing = snapshot_missing_families(snapshot)
    if missing:
        return None, None, f"{EWMA_STATE_FILE} lacks {', '.join(missing)}"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    start = metadata.get("training_window", {}).get("start")
    if not start:
        return None, None, f"{METADATA_FILE} has no training_window.start for the seed weight"
    return snapshot, str(start), ""


def load_baselines(
    predictions: pd.DataFrame, args: argparse.Namespace
) -> tuple[pd.DataFrame, dict[str, str]]:
    """the as-of baseline frame for every predicted player-game, and what it lacks."""
    targets = predictions[["nba_player_id", "nba_game_id", "game_date"]].drop_duplicates(
        ["nba_player_id", "nba_game_id"]
    )
    dates = pd.to_datetime(targets["game_date"])
    first, last = dates.min().date(), dates.max().date()
    player_ids = sorted({str(p) for p in targets["nba_player_id"]})
    history = _read_sql(HISTORY_SQL, {
        "game_ids": sorted({str(g) for g in targets["nba_game_id"]}),
        "player_ids": player_ids,
        "start": first - timedelta(days=args.history_days),
        "end": last,
    })
    log.info("history rows: %d", len(history))
    baselines = build_baselines(targets, history)

    snapshot, start, reason = load_artifact_seed(args.models_dir, args.version)
    if snapshot is None or start is None:
        log.warning("F7/F8 not computable: %s", reason)
        return baselines, {key: reason for key in F7_F8_KEYS}
    appearances = _read_sql(RATE_HISTORY_SQL, {"player_ids": player_ids, "start": start, "end": last})
    log.info("rate-history rows: %d", len(appearances))
    rates = seeded_rate_baselines(targets, appearances, snapshot, start)
    return merge_baselines(baselines, rates), {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    today = date.today()
    parser.add_argument(
        "--since", type=date.fromisoformat, default=None,
        help=f"first predicted_at date of the runs to score (default: {DEFAULT_LOOKBACK_DAYS} "
             f"days ago, or the freeze date with --look)",
    )
    parser.add_argument(
        "--until", type=date.fromisoformat, default=None,
        help="last predicted_at date, inclusive (default: today, or the day before the look)",
    )
    parser.add_argument(
        "--look", choices=LOOK_NAMES, default=None,
        help="add the 13.5 falsification table for this look; scores only games before its date",
    )
    parser.add_argument(
        "--version", default=PROSPECTIVE_MODEL_VERSION,
        help=f"model version whose ewma_state seeds the F7/F8 rate families "
             f"(default: {PROSPECTIVE_MODEL_VERSION})",
    )
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument(
        "--history-days", type=int, default=DEFAULT_HISTORY_DAYS,
        help=f"days of truth-layer history before the first scored game "
             f"(default: {DEFAULT_HISTORY_DAYS})",
    )
    parser.add_argument(
        "--run-id", type=int, action="append", default=[], dest="run_ids",
        help="score this run; repeatable. overrides --since/--until",
    )
    parser.add_argument(
        "--channel", choices=("production", "shadow"), default=None,
        help="only runs on this channel (migration 015 column, else the shadow token in notes)",
    )
    parser.add_argument("--out-dir", type=Path, default=REPORTS_DIR / "scoring")
    parser.add_argument("--md", type=Path, default=None, help="default: <out-dir>/scoring_<today>.md")
    parser.add_argument("--csv", type=Path, default=None, help="default: <out-dir>/scoring_<today>_results.csv")
    args = parser.parse_args(argv)
    if args.look:
        cutoff = date.fromisoformat(look_date(args.look))
        args.since = args.since or date.fromisoformat(str(PROSPECTIVE_2026_27["frozen_at"]))
        args.until = args.until or cutoff - timedelta(days=1)
    args.since = args.since or today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    args.until = args.until or today
    stamp = f"{args.look}_{today.isoformat()}" if args.look else today.isoformat()
    args.md = args.md or args.out_dir / f"scoring_{stamp}.md"
    args.csv = args.csv or args.out_dir / f"scoring_{stamp}_results.csv"
    return args


def _nothing(reason: str) -> int:
    log.info("nothing to score: %s", reason)
    print(f"nothing to score: {reason}")
    return 0


def report_header(args: argparse.Namespace, runs: pd.DataFrame) -> str:
    selection = (
        f"run ids {', '.join(map(str, args.run_ids))}" if args.run_ids
        else f"runs predicted {args.since} to {args.until}"
    )
    return (
        f"# prediction scoring, {date.today().isoformat()}\n\n"
        f"- selection: {selection}; channel: {args.channel or 'all'}; "
        f"look: {args.look or 'none'}\n"
        f"- runs scored: {len(runs)}\n"
        f"- baseline appearance history: {baseline_history_label()}\n"
        f"- generated at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- results csv: {args.csv.name}\n\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    runs = load_runs(args.since, args.until, args.run_ids)
    if args.channel and not runs.empty:
        runs = runs[channel_of(runs) == args.channel]
    if runs.empty:
        return _nothing("no complete prediction runs match the selection")
    run_ids = [int(r) for r in runs["id"]]
    log.info("runs selected: %d", len(run_ids))

    predictions = _read_sql(PREDICTIONS_SQL, {"run_ids": run_ids, "stats": scored_stat_names()})
    if predictions.empty:
        return _nothing(f"{len(run_ids)} run(s) selected, but none of their games is complete yet")
    # preseason games are graded too (MODEL.md 20.5), so truth reads every type.
    truth = _read_sql(TRUTH_SQL, {"run_ids": run_ids, "season_types": list(TRUTH_SEASON_TYPES)})
    log.info("prediction rows: %d, truth rows: %d", len(predictions), len(truth))

    if args.look:
        cutoff = pd.Timestamp(look_date(args.look))
        predictions = predictions[pd.to_datetime(predictions["game_date"]) < cutoff]
        if predictions.empty:
            return _nothing(f"no completed game before the {args.look} cutoff {cutoff.date()}")

    baselines, unavailable = load_baselines(predictions, args)
    results = score_runs(predictions, runs, truth, baselines)
    graded, excluded, split = predictions, {}, None
    if args.look:
        # 13.3 is regular season only: every endpoint is rescored without the
        # other season types, whose cohort rows ride along as a separate split.
        graded, excluded = split_endpoint_rows(predictions, truth)
        split = excluded_season_type_rows(results)
        results = pd.concat([score_runs(graded, runs, truth, baselines), split], ignore_index=True)
    pairs, comparison = compare_served_shadow(graded, runs, truth)
    results = pd.concat([results, comparison_results(comparison)], ignore_index=True)
    markdown = report_header(args, runs) + summarise(results)
    if args.look:
        rows = scheduled_rows(results)
        observations = falsification_observations(results, comparison, unavailable=unavailable)
        table = falsification_table(observations, args.look, rows)
        markdown += "\n" + render_look_report(
            args.look, table, comparison, pairs, rows, excluded=excluded, split=split
        )

    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.csv, index=False)
    args.md.write_text(markdown, encoding="utf-8")
    print(f"scored {len(run_ids)} run(s); wrote {args.md} and {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
