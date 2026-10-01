"""postgres data source.

reads the four tables the data-truth layer owns and returns the canonical
frames declared in :mod:`fnba_ml.data.schema`:

    player_game_logs, player_game_status, team_game_logs, nba_schedule

plus one piece of reference data the v2 teammate features need (``players.position``,
via :data:`PLAYER_POSITIONS_SQL`) and one serving-time query that is not part of
the dataset build: the latest injury designation per player, for the override
layer in :mod:`fnba_ml.overrides`.

NOT EXERCISED BY THE TEST SUITE. there is no test database in this repo and no
integration test opens a connection (AGENTS.md section 6). the SQL below is
written against the migration-013 contract and is the piece to re-read first if
that schema moves. ``psycopg2`` and ``python-dotenv`` are imported lazily so
the module - and its SQL - can be imported and inspected without either
package installed.

connection string comes from ``DATABASE_URL``. queries are parameterised; no
identifier or value is ever interpolated into a SQL string.
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from ..config import SEASON_TYPES, SEASONS
from .schema import (
    PLAYER_LOG_COLS,
    POSITION_COLS,
    SCHEDULE_COLS,
    STAT_COLS,
    STATUS_COLS,
    TEAM_LOG_COLS,
    TEAM_LOG_OPTIONAL_COLS,
    normalise_dates,
    normalise_ids,
    require_columns,
)

log = logging.getLogger(__name__)

PLAYER_LOGS_SQL = """
SELECT
    pgl.nba_player_id      AS "PLAYER_ID",
    pgl.nba_game_id        AS "GAME_ID",
    pgl.season             AS "SEASON",
    pgl.season_type        AS "SEASON_TYPE",
    pgl.game_date          AS "GAME_DATE",
    pgl.team_id            AS "TEAM_ID",
    pgl.team_abbr          AS "TEAM_ABBREVIATION",
    pgl.started            AS "STARTED",
    pgl.dnp_reason         AS "DNP_REASON",
    pgl.plus_minus         AS "PLUS_MINUS",
    pgl.minutes            AS "MIN",
    pgl.pts                AS "PTS",
    pgl.ast                AS "AST",
    pgl.fga                AS "FGA",
    pgl.fta                AS "FTA",
    pgl.reb                AS "REB",
    pgl.fg3m               AS "FG3M",
    pgl.fgm                AS "FGM",
    pgl.ftm                AS "FTM",
    pgl.tov                AS "TOV",
    pgl.stl                AS "STL",
    pgl.blk                AS "BLK"
FROM player_game_logs pgl
WHERE pgl.season = ANY(%(seasons)s)
  AND pgl.season_type = ANY(%(season_types)s)
ORDER BY pgl.nba_player_id, pgl.game_date
"""

# MIN/FGA/FTA/TOV are here for feature_version v2: they are the denominator of
# the usage-rate feature (share of the team's possessions per minute played).
# tgl.minutes is the team's TOTAL minutes for the game - 240 in regulation, more
# in overtime - which is why the usage formula divides it by 5 rather than
# hard-coding 48.
#
# FG3A joined for the P2 matchup family (2026-08-19): the "weak against threes"
# signal is the opponent's three-point ATTEMPTS per 100 possessions, and attempts
# rather than makes is the whole point - makes conflate the opponent's shooting luck
# with this team's willingness to concede the shot. Optional downstream
# (``data.schema.TEAM_LOG_OPTIONAL_COLS``); required here, because postgres has it.
#
# THE JOIN TO nba_schedule WAS REMOVED at the same time, and it is a robustness fix
# rather than a behaviour change on a complete database. ``team_game_logs`` carries
# its own ``season``, ``season_type`` and ``game_date``, so the join was reading two
# tables to get one table's own columns - and where ``nba_schedule`` is
# incompletely backfilled (the dev database holds 2024-25 onward only) an INNER join
# silently dropped every team-game of the earlier seasons. On a database where both
# tables are complete the two forms return identical rows; where they are not, only
# this form returns the rows that exist. The cutoff filter moves with it, from
# ``s.game_date`` to ``tgl.game_date``.
TEAM_LOGS_SQL = """
SELECT
    tgl.team_id            AS "TEAM_ID",
    tgl.nba_game_id        AS "GAME_ID",
    tgl.season             AS "SEASON",
    tgl.game_date          AS "GAME_DATE",
    tgl.pts                AS "PTS",
    tgl.minutes            AS "MIN",
    tgl.fga                AS "FGA",
    tgl.fta                AS "FTA",
    tgl.tov                AS "TOV",
    tgl.fg3a               AS "FG3A"
FROM team_game_logs tgl
WHERE tgl.season = ANY(%(seasons)s)
  AND tgl.season_type = ANY(%(season_types)s)
ORDER BY tgl.team_id, tgl.game_date
"""

# reference data, not a per-game fact: one row per player, the comma-joined
# position string the app's own ``players`` table already carries ("PG,SG").
#
# NOT bounded by the cutoff, and that is a deliberate exception worth stating.
# every other query here is filtered by game_date because a prediction run must
# not see games it could not have seen. a position is not an event: it is not
# dated, it does not change with the outcome of the game being predicted, and
# ``players`` is a mutable current-roster table with no history to filter on
# anyway. the residual risk is a player whose listed position was updated after a
# 2023 game, which mislabels a bucket rather than leaking an outcome.
#
# coverage is partial by construction - ``players`` holds the currently-tracked
# roster, so retired and released players have no row. 582 of the 895 players in
# the four-season universe match (~82% of scheduled rows). unmatched players get a
# null POS_GROUP and the positional half of the family is null for their rows.
PLAYER_POSITIONS_SQL = """
SELECT
    p.nba_id               AS "PLAYER_ID",
    p.position             AS "POSITION"
FROM players p
WHERE p.nba_id IS NOT NULL
"""

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
WHERE s.season = ANY(%(seasons)s)
  AND s.season_type = ANY(%(season_types)s)
ORDER BY s.game_date, s.nba_game_id
"""

# the universe comes from here whenever rows exist: rostered players per
# team-game, with the official inactive flag. this is the table that removes
# the approximation bias documented in REPORT.md section 5.
STATUS_SQL = """
SELECT
    st.nba_player_id       AS "PLAYER_ID",
    st.nba_game_id         AS "GAME_ID",
    st.team_id             AS "TEAM_ID",
    st.rostered            AS "ROSTERED",
    st.listed_inactive     AS "LISTED_INACTIVE",
    st.started             AS "STARTED",
    st.played              AS "PLAYED",
    st.dnp_reason          AS "DNP_REASON",
    st.minutes             AS "MIN",
    st.source              AS "STATUS_SOURCE"
FROM player_game_status st
JOIN nba_schedule s
  ON s.nba_game_id = st.nba_game_id
WHERE s.season = ANY(%(seasons)s)
  AND s.season_type = ANY(%(season_types)s)
ORDER BY st.nba_player_id, s.game_date
"""

# schedule rows only ever supply future context, so a prediction run may read
# them past the cutoff; every other table is bounded by the cutoff date.
CUTOFF_CLAUSE = " AND {col} < %(cutoff)s"

# ---------------------------------------------------------------------------
# the serving-time injury report, for fnba_ml.overrides.
# ---------------------------------------------------------------------------
# executed by daily_run.py's load_statuses through
# PostgresSource.load_latest_injury_statuses; the offline path reads a --statuses
# parquet/csv instead. there is no test database (AGENTS.md section 6), so the
# suite never runs it. written against migration 013's player_injury_reports
# contract and kept next to the rest of the SQL so it moves with the schema.
#
# THREE THINGS THIS QUERY GETS RIGHT, all of which are easy to get wrong:
#
#   1. `captured_at < %(as_of)s`, strictly. player_injury_reports is append-only
#      and accumulates in real time, so "the latest report" in an unbounded query
#      means "the latest report NOW" - which, in a backtest of the T-24h horizon,
#      is the report published twenty minutes before tipoff. the run's own
#      information boundary is the only correct filter, and it is the same rule
#      the training cutoff enforces one layer in.
#      report_as_of is deliberately NOT used for this: it is frequently NULL
#      (migration 013's own comment says so), and a filter on a nullable column
#      silently drops the rows it cannot judge.
#
#   2. DISTINCT ON (nba_player_id) with ORDER BY captured_at DESC takes the newest
#      admissible report per player and nothing else. a player can have dozens of
#      rows for one game - "questionable", then "out", then "available" - and only
#      the last one before the boundary is his status.
#
#   3. nba_game_id is NOT filtered on. it is NULL for a general designation, which
#      is what the CBS-style feed publishes and therefore most of the table; an
#      equality filter on it would return nothing, and an IS NULL filter would
#      throw away the game-specific reports that are strictly better information.
#      the newest report wins regardless of which kind it is.
#
#   4. a 7-day lower bound so the database does not ship the whole history. it is
#      wider than overrides.REPORT_MAX_AGE_HOURS on purpose: the python-side
#      expiry is the rule that decides, this only trims what cannot matter.
LATEST_INJURY_STATUS_SQL = """
SELECT DISTINCT ON (r.nba_player_id)
    r.nba_player_id    AS "nba_player_id",
    r.status_normalized AS "status_normalized",
    r.status_raw       AS "status_raw",
    r.captured_at      AS "captured_at",
    r.report_as_of     AS "report_as_of",
    r.source           AS "source"
FROM player_injury_reports r
WHERE r.captured_at < %(as_of)s
  AND r.captured_at >= %(as_of)s - INTERVAL '7 days'
ORDER BY r.nba_player_id, r.captured_at DESC
"""


def load_database_url() -> str:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415 - optional dependency

        load_dotenv()
    except ImportError:
        log.debug("python-dotenv not installed; reading DATABASE_URL from the environment")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set - the postgres source cannot connect. "
            "use --source parquet for offline runs."
        )
    return url


class PostgresSource:
    """canonical frames straight out of the data-truth tables."""

    name = "postgres"

    def __init__(
        self,
        seasons: list[str] | None = None,
        season_types: list[str] | None = None,
        cutoff: pd.Timestamp | None = None,
        database_url: str | None = None,
    ) -> None:
        self.seasons = list(seasons or SEASONS)
        self.season_types = list(season_types or SEASON_TYPES)
        self.cutoff = cutoff
        self._database_url = database_url

    # ------------------------------------------------------------------
    def _params(self) -> dict[str, object]:
        params: dict[str, object] = {
            "seasons": self.seasons,
            "season_types": self.season_types,
        }
        if self.cutoff is not None:
            params["cutoff"] = pd.Timestamp(self.cutoff).to_pydatetime()
        return params

    def _sql(self, base: str, cutoff_col: str | None) -> str:
        if self.cutoff is None or cutoff_col is None:
            return base
        head, _, tail = base.partition("ORDER BY")
        return f"{head.rstrip()}{CUTOFF_CLAUSE.format(col=cutoff_col)}\nORDER BY{tail}"

    def _read(self, sql: str) -> pd.DataFrame:
        import psycopg2  # noqa: PLC0415 - optional at import time, required at call time

        with psycopg2.connect(self._database_url or load_database_url()) as conn:
            return pd.read_sql_query(sql, conn, params=self._params())

    def _read_unparameterised(self, sql: str) -> pd.DataFrame:
        """for the reference-data queries that take no season/cutoff bounds.

        separate from :meth:`_read` so that "this query is deliberately not
        filtered by the cutoff" is visible at the call site rather than being an
        accident of a params dict whose placeholders happen to be unused.
        """
        import psycopg2  # noqa: PLC0415 - optional at import time, required at call time

        with psycopg2.connect(self._database_url or load_database_url()) as conn:
            return pd.read_sql_query(sql, conn)

    # ------------------------------------------------------------------
    def load_player_game_logs(self) -> pd.DataFrame:
        df = self._read(self._sql(PLAYER_LOGS_SQL, "pgl.game_date"))
        df["PLAYER_NAME"] = pd.NA
        for col in STAT_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        df = normalise_ids(normalise_dates(df))
        require_columns(df, PLAYER_LOG_COLS, "canonical player log")
        return df.reset_index(drop=True)

    def load_team_game_logs(self) -> pd.DataFrame:
        df = normalise_ids(normalise_dates(self._read(self._sql(TEAM_LOGS_SQL, "tgl.game_date"))))
        for col in ("PTS", "MIN", "FGA", "FTA", "TOV", *TEAM_LOG_OPTIONAL_COLS):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        require_columns(df, TEAM_LOG_COLS, "canonical team log")
        return df.reset_index(drop=True)

    def load_player_positions(self) -> pd.DataFrame | None:
        """one row per player: the comma-joined position string, or None.

        ``None`` would mean the table is empty; a partial match is normal and is
        handled downstream by leaving POS_GROUP null rather than guessing.
        """
        df = self._read_unparameterised(PLAYER_POSITIONS_SQL)
        if df.empty:
            log.warning("players table has no nba_id rows; positions will be null")
            return None
        df = normalise_ids(df[list(POSITION_COLS)])
        log.info("loaded %d player positions", len(df))
        return df.drop_duplicates("PLAYER_ID").reset_index(drop=True)

    def load_schedule(self) -> pd.DataFrame:
        df = normalise_ids(normalise_dates(self._read(self._sql(SCHEDULE_SQL, None))))
        require_columns(df, SCHEDULE_COLS, "canonical schedule")
        return df.reset_index(drop=True)

    def load_latest_injury_statuses(self, as_of: pd.Timestamp) -> pd.DataFrame:
        """the newest injury designation per player as known at ``as_of``.

        the frame :func:`fnba_ml.overrides.apply_status_overrides` expects:
        nba_player_id / status_normalized / captured_at, plus status_raw,
        report_as_of and source for auditing. ``as_of`` is required - there is no
        default of "now", because a backtest that forgets to pass its boundary
        would silently read the future and there would be nothing in the output to
        show it happened.

        NOT EXERCISED BY THE TEST SUITE; see the note on
        :data:`LATEST_INJURY_STATUS_SQL`.
        """
        import psycopg2  # noqa: PLC0415 - optional at import time, required at call time

        boundary = pd.Timestamp(as_of)
        if boundary.tzinfo is None:
            boundary = boundary.tz_localize("UTC")
        with psycopg2.connect(self._database_url or load_database_url()) as conn:
            frame = pd.read_sql_query(
                LATEST_INJURY_STATUS_SQL, conn,
                params={"as_of": boundary.to_pydatetime()},
            )
        frame["nba_player_id"] = frame["nba_player_id"].astype(str)
        log.info("loaded %d injury statuses known as of %s", len(frame), boundary)
        return frame

    def load_player_game_status(self) -> pd.DataFrame | None:
        df = self._read(self._sql(STATUS_SQL, "s.game_date"))
        if df.empty:
            log.warning(
                "player_game_status is empty for seasons %s - falling back to the "
                "BIASED game-log-presence approximation",
                self.seasons,
            )
            return None
        for col in STATUS_COLS:
            if col not in df.columns:
                df[col] = pd.NA
        return normalise_ids(df[list(STATUS_COLS)]).reset_index(drop=True)
