import logging
import os
import re
import sys
from collections.abc import Sequence
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

# prod is the default so an un-flagged run (the GitHub Actions cron) keeps its
# existing behaviour.
TARGET_PROD = "prod"
TARGET_DEV = "dev"

TARGET_ENV_VARS = {
    TARGET_PROD: "DATABASE_URL",
    TARGET_DEV: "DATABASE_URL_DEV",
}


def resolve_database_url(target: str) -> str:
    var = TARGET_ENV_VARS[target]
    url = os.getenv(var)
    if url:
        return url
    if target == TARGET_DEV:
        logger.error(
            "%s is not set. Add it to .env with your Neon dev branch connection "
            "string (Neon console -> Branches -> dev -> Connection string), or "
            "drop --dev to run against prod.",
            var,
        )
    else:
        logger.error("%s is not set in .env", var)
    sys.exit(1)


def get_db(target: str = TARGET_PROD) -> psycopg2.extensions.connection:
    url = resolve_database_url(target)
    conn = psycopg2.connect(url)
    conn.autocommit = True
    # a shell DATABASE_URL silently overrides .env, so "table does not exist" is
    # usually the wrong database rather than a missing migration. Host and db
    # name only, never credentials.
    try:
        parsed = urlparse(url)
        logger.info(
            "target=%s -> connected to %s/%s",
            target.upper(),
            parsed.hostname,
            (parsed.path or "").lstrip("/").split("?")[0] or "?",
        )
    except Exception:  # noqa: BLE001 - diagnostics must never break the run
        pass
    return conn


_WRITE_VERBS = frozenset(
    {"insert", "update", "delete", "create", "alter", "drop", "truncate", "merge"}
)


def is_write_statement(sql: str) -> bool:
    # errs toward "write" for a CTE-prefixed statement: a data-modifying CTE
    # reads as a SELECT by its first keyword, and misclassifying one would let
    # --dry-run write.
    stripped = (sql or "").strip()
    while stripped.startswith("--"):
        _, _, stripped = stripped.partition("\n")
        stripped = stripped.strip()
    if not stripped:
        return False

    first = stripped.split(None, 1)[0].lower()
    if first in _WRITE_VERBS:
        return True
    if first == "with":
        lowered = stripped.lower()
        return any(re.search(rf"\b{verb}\b", lowered) for verb in _WRITE_VERBS)
    return False


class DryRunCursor:
    # wrapping rather than branching at every call site keeps --dry-run on the
    # same write paths production uses. Reads still run: reporting what would be
    # written means querying the watermark and the keys already present.

    def __init__(self, cur: psycopg2.extensions.cursor) -> None:
        self._cur = cur
        self._last_skipped = False
        self.skipped_statements = 0
        self.skipped_rows = 0

    def execute(self, sql: str, params: object = None) -> None:
        if is_write_statement(sql):
            self._last_skipped = True
            self.skipped_statements += 1
            self.skipped_rows += 1
            return
        self._last_skipped = False
        self._cur.execute(sql, params)

    def execute_values(self, sql: str, rows: Sequence[tuple]) -> None:
        self._last_skipped = True
        self.skipped_statements += 1
        self.skipped_rows += len(rows)

    def fetchall(self) -> list:
        # a skipped write has no result set, so RETURNING reads as "nothing
        # matched" rather than blowing up the dry run.
        return [] if self._last_skipped else self._cur.fetchall()

    def fetchone(self) -> tuple | None:
        return None if self._last_skipped else self._cur.fetchone()

    @property
    def rowcount(self) -> int:
        return 0 if self._last_skipped else self._cur.rowcount

    def close(self) -> None:
        self._cur.close()


def maybe_write_cursor(
    cur: psycopg2.extensions.cursor, dry_run: bool
) -> psycopg2.extensions.cursor | DryRunCursor:
    return DryRunCursor(cur) if dry_run else cur


def _batch_upsert(
    cur: object, sql: str, rows: Sequence[tuple], template: str | None = None
) -> int:
    if not rows:
        return 0
    if isinstance(cur, DryRunCursor):
        cur.execute_values(sql, rows)
        return len(rows)
    execute_values(cur, sql, rows, template=template, page_size=500)
    return len(rows)


def _scalar(conn: psycopg2.extensions.connection, sql: str, params: tuple = ()) -> object:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _rows(conn: psycopg2.extensions.connection, sql: str, params: tuple = ()) -> list[tuple]:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def _start_ingestion_run(
    conn: psycopg2.extensions.connection,
    kind: str,
    watermark_from: object = None,
    watermark_to: object = None,
    dry_run: bool = False,
) -> int | None:
    # a None run id is stored as NULL, which is what a row with no traceable run
    # should carry.
    if dry_run:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO ingestion_runs (kind, watermark_from, watermark_to, status)
            VALUES (%s, %s, %s, 'running')
            RETURNING id
            """,
            (
                kind,
                None if watermark_from is None else str(watermark_from),
                None if watermark_to is None else str(watermark_to),
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        cur.close()


def _finish_ingestion_run(
    conn: psycopg2.extensions.connection,
    run_id: int | None,
    status: str,
    rows_written: int,
    notes: str | None = None,
    watermark_to: object = None,
) -> None:
    if run_id is None:
        return
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET finished_at = NOW(), status = %s, rows_written = %s,
                   notes = COALESCE(%s, notes),
                   watermark_to = COALESCE(%s, watermark_to)
             WHERE id = %s
            """,
            (
                status,
                rows_written,
                notes,
                None if watermark_to is None else str(watermark_to),
                run_id,
            ),
        )
    finally:
        cur.close()
