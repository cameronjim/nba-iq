"""score stored prediction runs against completed games.

    python score_runs.py                              # runs from the last 30 days
    python score_runs.py --since 2026-10-20 --channel production
    python score_runs.py --run-id 412 --run-id 413

reads prediction_runs, player_game_predictions and the truth layer via
DATABASE_URL, scores with fnba_ml.scoring, and writes markdown plus a csv beside
it (MODEL.md 13.8.6). read-only: no write to the database.

a game counts as completed when team_game_logs holds both sides of it or
player_game_status holds any row for it. nba_schedule.game_status is not used:
it is free text from three sources ('Final', '7:30 pm ET', 'Postponed').
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from fnba_ml.cli import add_common_args, setup_logging
from fnba_ml.config import REPORTS_DIR
from fnba_ml.scoring import PROBABILITIES, STATS, channel_of, score_runs, summarise
from fnba_ml.store import UNCOND_SUFFIX

log = logging.getLogger("score_runs")

DEFAULT_LOOKBACK_DAYS = 30

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
WHERE {COMPLETED_GAME_CONDITION}
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    today = date.today()
    parser.add_argument(
        "--since", type=date.fromisoformat, default=today - timedelta(days=DEFAULT_LOOKBACK_DAYS),
        help=f"first predicted_at date of the runs to score (default: {DEFAULT_LOOKBACK_DAYS} days ago)",
    )
    parser.add_argument(
        "--until", type=date.fromisoformat, default=today,
        help="last predicted_at date, inclusive (default: today)",
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
    stamp = today.isoformat()
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
        f"- selection: {selection}; channel: {args.channel or 'all'}\n"
        f"- runs scored: {len(runs)}\n"
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
    truth = _read_sql(TRUTH_SQL, {"run_ids": run_ids})
    log.info("prediction rows: %d, truth rows: %d", len(predictions), len(truth))

    results = score_runs(predictions, runs, truth)
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.csv, index=False)
    args.md.write_text(report_header(args, runs) + summarise(results), encoding="utf-8")
    print(f"scored {len(run_ids)} run(s); wrote {args.md} and {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
