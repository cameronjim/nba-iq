"""build a prospective feature dataset for a season that has not been played."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import (  # noqa: E402
    add_common_args,
    default_dataset_path,
    load_dataset,
    setup_logging,
)
from fnba_ml.config import (  # noqa: E402
    DATA_DIR,
    FEATURE_COLS,
    FEATURE_VERSION,
    PROSPECTIVE_COLD_START_THROUGH,
    is_cold_start,
)
from fnba_ml.prospective import (  # noqa: E402
    SOURCE_PROSPECTIVE,
    build_prospective_features,
    history_from_dataset,
    load_postseason_sidecar,
    prospective_universe,
)

log = logging.getLogger("project_preseason")

SCHEDULE_SQL = """
SELECT
    s.nba_game_id          AS "GAME_ID",
    s.season               AS "SEASON",
    s.season_type          AS "SEASON_TYPE",
    s.game_date            AS "GAME_DATE",
    s.scheduled_at         AS "SCHEDULED_AT",
    s.home_team_id         AS "HOME_TEAM_ID",
    s.away_team_id         AS "AWAY_TEAM_ID",
    s.game_status          AS "GAME_STATUS"
FROM nba_schedule s
WHERE s.season = %(season)s
  AND s.season_type = %(season_type)s
ORDER BY s.game_date, s.nba_game_id
"""

STINTS_SQL = """
SELECT nba_player_id, team_id
  FROM player_team_stints
 WHERE valid_to IS NULL
"""

POSITIONS_SQL = """
SELECT p.nba_id AS "PLAYER_ID", p.position AS "POSITION"
  FROM players p
 WHERE p.nba_id IS NOT NULL
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path(),
                        help="the built dataset supplying the played history")
    parser.add_argument("--schedule-season", required=True,
                        help="the season whose schedule to project, e.g. 2026-27")
    parser.add_argument("--season-type", default="Regular Season")
    parser.add_argument("--start", required=True, help="first game date to project")
    parser.add_argument("--end", required=True, help="last game date to project")
    parser.add_argument("--rosters", type=Path, default=None,
                        help="csv of roster assignments (nba_player_id, team_id)")
    parser.add_argument("--schedule", type=Path, default=None,
                        help="csv of the schedule, instead of reading the database")
    parser.add_argument("--out", type=Path,
                        default=DATA_DIR / "preseason.parquet")
    return parser.parse_args(argv)


def _read_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    import psycopg2  # noqa: PLC0415 - only needed on the database path

    from fnba_ml.data.postgres_source import load_database_url  # noqa: PLC0415

    with psycopg2.connect(load_database_url()) as conn:
        return pd.read_sql_query(sql, conn, params=params or {})


def load_schedule(args: argparse.Namespace) -> pd.DataFrame:
    if args.schedule is not None:
        frame = pd.read_csv(args.schedule)
    else:
        frame = _read_sql(
            SCHEDULE_SQL,
            {"season": args.schedule_season, "season_type": args.season_type},
        )
    if frame.empty:
        raise SystemExit(
            f"no {args.season_type} schedule rows for {args.schedule_season}. "
            f"run the scraper's schedule sync for that season first."
        )
    log.info("schedule: %d games for %s", len(frame), args.schedule_season)
    return frame


def load_rosters(args: argparse.Namespace) -> pd.DataFrame:
    if args.rosters is not None:
        frame = pd.read_csv(args.rosters, dtype=str)
        log.info("rosters: %d assignments from %s", len(frame), args.rosters)
        return frame
    frame = _read_sql(STINTS_SQL)
    if frame.empty:
        raise SystemExit(
            "player_team_stints has no open stints, so there is nothing to say "
            "which players are on which team. run the scraper's --roster-snapshot, "
            "or pass its --snapshot-out csv to --rosters."
        )
    log.info("rosters: %d open stints from player_team_stints", len(frame))
    return frame


def load_positions() -> pd.DataFrame | None:
    try:
        frame = _read_sql(POSITIONS_SQL)
    except Exception as exc:  # noqa: BLE001 - positions are optional reference data
        log.warning("could not read positions (%s); POS_GROUP will be null", exc)
        return None
    return frame if len(frame) else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    schedule = load_schedule(args)
    rosters = load_rosters(args)
    positions = None if args.schedule is not None else load_positions()

    dataset = load_dataset(args.dataset)
    history = history_from_dataset(dataset)
    postseason = load_postseason_sidecar(args.dataset)
    log.info(
        "history: %d played-universe rows, %s .. %s",
        len(history), history["GAME_DATE"].min().date(), history["GAME_DATE"].max().date(),
    )

    future = prospective_universe(
        schedule, rosters, args.start, args.end, positions=positions
    )
    features = build_prospective_features(history, future, postseason)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    features.to_parquet(args.out, index=False)

    known = set(history["PLAYER_ID"])
    projected = set(features["PLAYER_ID"])
    no_history = sorted(projected - known)
    cold = is_cold_start(features["GAME_DATE"])

    print("--- PRESEASON DATASET ---")
    print(f"season           : {args.schedule_season} ({args.season_type})")
    print(f"window           : {args.start} .. {args.end}")
    print(f"universe source  : {SOURCE_PROSPECTIVE}")
    print(f"feature version  : {FEATURE_VERSION}  ({len(FEATURE_COLS)} columns)")
    print(f"game dates       : {features['GAME_DATE'].nunique()}  "
          f"(one feature build each)")
    print(f"games            : {features['GAME_ID'].nunique():,}")
    print(f"rows             : {len(features):,}")
    print(f"players          : {len(projected):,}")
    print(f"cold_start rows  : {int(cold.sum()):,} of {len(features):,} "
          f"(GAME_DATE <= {PROSPECTIVE_COLD_START_THROUGH})")
    print(f"no NBA history   : {len(no_history):,} players "
          f"({int(features['PLAYER_ID'].isin(no_history).sum()):,} rows)")
    print(f"insufficient_history: {int(features['insufficient_history'].sum()):,} rows "
          f"({features['insufficient_history'].mean():.1%})")
    print(f"has_history      : {int(features['has_history'].sum()):,} rows "
          f"({features['has_history'].mean():.1%})")
    print("\nrows per minutes tier (unknown = no prior rolling minutes at all):")
    print(features["MIN_TIER"].value_counts().to_string())
    print(f"\nsaved            -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
