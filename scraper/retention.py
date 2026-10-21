import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    HEADLINE_RUN_NOTE,
    ODDS_PRUNE_BATCH_ROWS,
    ODDS_RETENTION_DAYS,
    ODDS_RETENTION_TABLES,
    PREDICTION_PRUNE_BATCH_RUNS,
    PREDICTION_RETENTION_DAY_TZ,
    PREDICTION_RETENTION_DAYS,
    PREDICTION_RETENTION_RUNS_PER_DAY,
    SHADOW_RETENTION_DAYS,
)
from database import _rows, maybe_write_cursor

logger = logging.getLogger(__name__)

PRODUCTION_CHANNEL = "production"
SHADOW_CHANNEL = "shadow"
COMPLETE_STATUS = "complete"
PREDICTIONS_TABLE = "player_game_predictions"

REASON_NEWEST = "newest complete run of its channel"
REASON_NEWEST_FULL = "newest complete production run with quantile rows"
REASON_LAST_OF_DAY = "last complete run of its day"
REASON_LAST_FULL_OF_DAY = "last complete run of its day with quantile rows"
REASON_EXPIRED = "older than the retention window"
REASON_SHADOW_EXPIRED = "shadow run older than the shadow window"
REASON_SUPERSEDED = "superseded by a later run the same day"

RUNS_SQL = """
SELECT r.id,
       r.channel,
       r.status,
       r.predicted_at,
       r.information_as_of,
       r.notes,
       r.pruned_at IS NOT NULL AS pruned,
       COALESCE(c.n_rows, 0) AS n_rows
FROM prediction_runs r
LEFT JOIN (
    SELECT prediction_run_id, COUNT(*) AS n_rows
    FROM player_game_predictions
    GROUP BY prediction_run_id
) c ON c.prediction_run_id = r.id
ORDER BY r.predicted_at, r.id
"""

DELETE_PREDICTIONS_SQL = """
DELETE FROM player_game_predictions
WHERE prediction_run_id = ANY(%s)
"""

MARK_PRUNED_SQL = """
UPDATE prediction_runs
SET pruned_at = %s
WHERE id = ANY(%s)
  AND pruned_at IS NULL
"""


@dataclass(frozen=True)
class RunInfo:
    id: int
    channel: str
    status: str
    predicted_at: datetime
    information_as_of: datetime | None
    n_rows: int
    headline_only: bool = False
    pruned: bool = False


@dataclass(frozen=True)
class RetentionPlan:
    keep: tuple[int, ...]
    prune: tuple[int, ...]
    rows_to_delete: int
    reasons: dict[int, str] = field(default_factory=dict)


def _order(run: RunInfo) -> tuple[datetime, int]:
    return (run.predicted_at, run.id)


def _newest(runs: Iterable[RunInfo]) -> RunInfo | None:
    return max(runs, key=_order, default=None)


def plan_prediction_retention(
    runs: list[RunInfo],
    now: datetime,
    keep_days: int,
    keep_runs_per_day: int,
    shadow_keep_days: int = SHADOW_RETENTION_DAYS,
    day_tz: str = PREDICTION_RETENTION_DAY_TZ,
) -> RetentionPlan:
    tz = ZoneInfo(day_tz)
    live = [run for run in runs if not run.pruned]
    complete = [run for run in live if run.status == COMPLETE_STATUS]
    reasons: dict[int, str] = {}

    def keep(run: RunInfo | None, reason: str) -> None:
        if run is not None and run.id not in reasons:
            reasons[run.id] = reason

    for channel in sorted({run.channel for run in complete}):
        keep(_newest(r for r in complete if r.channel == channel), REASON_NEWEST)
    # a headline-only run's readers take their quantiles from this one.
    keep(
        _newest(
            r for r in complete
            if r.channel == PRODUCTION_CHANNEL and not r.headline_only
        ),
        REASON_NEWEST_FULL,
    )

    def window_start(channel: str) -> datetime:
        days = min(keep_days, shadow_keep_days) if channel == SHADOW_CHANNEL else keep_days
        return now - timedelta(days=days)

    by_day: dict[tuple[date, str], list[RunInfo]] = defaultdict(list)
    for run in complete:
        if run.predicted_at >= window_start(run.channel):
            by_day[(run.predicted_at.astimezone(tz).date(), run.channel)].append(run)
    for day_runs in by_day.values():
        day_runs.sort(key=_order, reverse=True)
        latest = day_runs[:keep_runs_per_day]
        for run in latest:
            keep(run, REASON_LAST_OF_DAY)
        if all(run.headline_only for run in latest):
            keep(_newest(r for r in day_runs if not r.headline_only), REASON_LAST_FULL_OF_DAY)

    prune: list[RunInfo] = []
    for run in sorted(live, key=_order):
        if run.id in reasons:
            continue
        if run.predicted_at >= window_start(run.channel):
            reasons[run.id] = REASON_SUPERSEDED
        elif run.channel == SHADOW_CHANNEL:
            reasons[run.id] = REASON_SHADOW_EXPIRED
        else:
            reasons[run.id] = REASON_EXPIRED
        prune.append(run)

    pruned_ids = {run.id for run in prune}
    return RetentionPlan(
        keep=tuple(run.id for run in sorted(live, key=_order) if run.id not in pruned_ids),
        prune=tuple(run.id for run in prune),
        rows_to_delete=sum(run.n_rows for run in prune),
        reasons=reasons,
    )


def is_headline_note(notes: object) -> bool:
    return isinstance(notes, str) and HEADLINE_RUN_NOTE in notes


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def run_info_from_row(row: Sequence[object]) -> RunInfo:
    run_id, channel, status, predicted_at, information_as_of, notes, pruned, n_rows = row
    return RunInfo(
        id=int(run_id),
        channel=str(channel),
        status=str(status),
        predicted_at=_aware(predicted_at),
        information_as_of=_aware(information_as_of),
        n_rows=int(n_rows or 0),
        headline_only=is_headline_note(notes),
        pruned=bool(pruned),
    )


def chunked(ids: Sequence[int], size: int) -> list[list[int]]:
    return [list(ids[i:i + size]) for i in range(0, len(ids), size)]


def prune_predictions(
    conn: psycopg2.extensions.connection,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    runs = [run_info_from_row(row) for row in _rows(conn, RUNS_SQL)]
    plan = plan_prediction_retention(
        runs, now, PREDICTION_RETENTION_DAYS, PREDICTION_RETENTION_RUNS_PER_DAY
    )
    by_id = {run.id: run for run in runs}
    logger.info(
        "prediction retention%s: %d run(s) kept, %d to prune, %d row(s) to delete",
        " (dry run)" if dry_run else "", len(plan.keep), len(plan.prune),
        plan.rows_to_delete,
    )

    deleted = 0
    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        for batch in chunked(plan.prune, PREDICTION_PRUNE_BATCH_RUNS):
            for run_id in batch:
                run = by_id[run_id]
                logger.info(
                    "prune run %d (%s, %s): %d row(s), %s",
                    run_id, run.channel, run.predicted_at.isoformat(), run.n_rows,
                    plan.reasons[run_id],
                )
            cur.execute(DELETE_PREDICTIONS_SQL, (batch,))
            deleted += max(cur.rowcount, 0)
            cur.execute(MARK_PRUNED_SQL, (now, batch))
    finally:
        cur.close()

    return {
        "dry_run": dry_run,
        "runs_kept": len(plan.keep),
        "runs_pruned": len(plan.prune),
        "rows_planned": plan.rows_to_delete,
        "rows_deleted": deleted,
    }


def odds_cutoff(now: datetime, keep_days: int = ODDS_RETENTION_DAYS) -> datetime:
    return now - timedelta(days=keep_days)


def _retention_table(table: str) -> str:
    # the name is interpolated into sql, so only the configured tables pass.
    if table not in (PREDICTIONS_TABLE, *ODDS_RETENTION_TABLES):
        raise ValueError(f"{table!r} is not a retention table")
    return table


def prune_odds(
    conn: psycopg2.extensions.connection,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, dict[str, int]]:
    cutoff = odds_cutoff(now or datetime.now(timezone.utc))
    result: dict[str, dict[str, int]] = {}
    for table in ODDS_RETENTION_TABLES:
        name = _retention_table(table)
        planned = int(_rows(
            conn, f"SELECT COUNT(*) FROM {name} WHERE captured_at < %s", (cutoff,)
        )[0][0])
        logger.info(
            "%s retention%s: %d row(s) captured before %s",
            name, " (dry run)" if dry_run else "", planned, cutoff.isoformat(),
        )
        deleted = 0
        cur = maybe_write_cursor(conn.cursor(), dry_run)
        try:
            while True:
                cur.execute(
                    f"""
                    DELETE FROM {name}
                    WHERE id IN (
                        SELECT id
                        FROM {name}
                        WHERE captured_at < %s
                        ORDER BY id
                        LIMIT %s
                    )
                    """,
                    (cutoff, ODDS_PRUNE_BATCH_ROWS),
                )
                batch = max(cur.rowcount, 0)
                deleted += batch
                if batch < ODDS_PRUNE_BATCH_ROWS:
                    break
        finally:
            cur.close()
        result[name] = {"rows_planned": planned, "rows_deleted": deleted}
    return result


def vacuum_full(conn: psycopg2.extensions.connection) -> None:
    # a delete only frees space for reuse; the files shrink only on a rewrite.
    cur = conn.cursor()
    try:
        for table in (PREDICTIONS_TABLE, *ODDS_RETENTION_TABLES):
            name = _retention_table(table)
            logger.info("VACUUM (FULL, ANALYZE) %s", name)
            cur.execute(f"VACUUM (FULL, ANALYZE) {name}")
    finally:
        cur.close()
