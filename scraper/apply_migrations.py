import argparse
import logging
import os
import sys

import psycopg2

# so `python scraper/apply_migrations.py` works from the repo root, not only
# from inside scraper/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_migrations import (  # noqa: E402
    MIGRATIONS_DIR,
    _record,
    _recorded_migrations,
    classify,
    file_checksum,
    migration_files,
)
from run_scraper import TARGET_DEV, TARGET_PROD, resolve_database_url  # noqa: E402

logger = logging.getLogger(__name__)


def plan_migrations(
    classification: dict[str, list[str]], only: list[str] | None = None
) -> list[str]:
    unapplied = sorted(classification["unapplied"])
    if not only:
        return unapplied
    not_pending = sorted(set(only) - set(unapplied))
    if not_pending:
        raise ValueError(
            "--only names file(s) that are not unapplied: " + ", ".join(not_pending)
        )
    return [name for name in unapplied if name in set(only)]


def refuses_on_mismatch(
    classification: dict[str, list[str]], allow_mismatch: bool
) -> bool:
    return bool(classification["mismatched"]) and not allow_mismatch


def _count_recorded(conn: psycopg2.extensions.connection) -> int:
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM schema_migrations")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        cur.close()


def _apply_one(conn: psycopg2.extensions.connection, filename: str, checksum: str) -> None:
    with open(os.path.join(MIGRATIONS_DIR, filename), encoding="utf-8") as handle:
        sql = handle.read()
    cur = conn.cursor()
    try:
        cur.execute(sql)
    finally:
        cur.close()
    # recorded inside the same transaction so a file is never applied but unrecorded.
    _record(conn, filename, checksum)
    conn.commit()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="apply unapplied migrations")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--dev",
        dest="target",
        action="store_const",
        const=TARGET_DEV,
        help="apply to the dev Neon branch (uses DATABASE_URL_DEV)",
    )
    target.add_argument(
        "--prod",
        dest="target",
        action="store_const",
        const=TARGET_PROD,
        help="apply to the prod database (uses DATABASE_URL, the default)",
    )
    parser.set_defaults(target=TARGET_PROD)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="print the plan and write nothing (the default)",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="actually run the planned migrations",
    )
    parser.set_defaults(apply=False)
    parser.add_argument(
        "--only",
        dest="only",
        metavar="FILENAME",
        action="append",
        default=[],
        help="restrict the plan to this unapplied file; repeatable",
    )
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="skip files whose recorded checksum no longer matches instead of refusing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    files = migration_files()
    if not files:
        logger.error("no migrations found in %s", MIGRATIONS_DIR)
        return 1
    on_disk = {
        name: file_checksum(os.path.join(MIGRATIONS_DIR, name)) for name in files
    }

    conn = psycopg2.connect(resolve_database_url(args.target))
    try:
        conn.autocommit = True
        recorded = _recorded_migrations(conn)
        if recorded is None:
            logger.error(
                "schema_migrations does not exist on this database. Apply "
                "013_truth_layer.sql by hand and backfill with "
                "check_migrations.py --record before using this tool."
            )
            return 1

        result = classify(on_disk, recorded)
        for name in result["orphaned"]:
            logger.warning("    NO SUCH FILE %s (recorded, but not on disk)", name)

        if refuses_on_mismatch(result, args.allow_mismatch):
            for name in result["mismatched"]:
                logger.error("    CHECKSUM     %s (file edited since it was applied)", name)
            logger.error(
                "refusing to proceed: %d recorded migration(s) changed on disk. "
                "Pass --allow-mismatch to skip them.",
                len(result["mismatched"]),
            )
            return 1
        for name in result["mismatched"]:
            logger.warning("    SKIPPED      %s (checksum mismatch, --allow-mismatch)", name)

        try:
            plan = plan_migrations(result, args.only)
        except ValueError as exc:
            logger.error("%s", exc)
            return 1

        before = _count_recorded(conn)
        logger.info(
            "target=%s, schema_migrations rows before: %d", args.target.upper(), before
        )
        if not plan:
            logger.info("nothing to apply")
            return 0
        logger.info("plan (%d file(s), in order):", len(plan))
        for name in plan:
            logger.info("    %s", name)

        if not args.apply:
            logger.info("dry run: nothing applied. Pass --apply to run the plan.")
            return 0

        conn.autocommit = False
        for name in plan:
            logger.info("applying %s", name)
            try:
                _apply_one(conn, name, on_disk[name])
            except psycopg2.Error as exc:
                conn.rollback()
                logger.error(
                    "%s failed and was rolled back: %s", name, str(exc).strip()
                )
                remaining = plan[plan.index(name) + 1:]
                if remaining:
                    logger.error("left unapplied: %s", ", ".join(remaining))
                conn.autocommit = True
                logger.info(
                    "schema_migrations rows after: %d (was %d)",
                    _count_recorded(conn), before,
                )
                return 1
            logger.info("applied and recorded %s", name)

        conn.autocommit = True
        after = _count_recorded(conn)
        logger.info("schema_migrations rows after: %d (was %d)", after, before)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
