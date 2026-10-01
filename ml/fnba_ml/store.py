"""writing a prediction run to the migration-014 tables.

two halves, split on purpose:

  :func:`build_prediction_rows`   pure. a scored frame in, a list of SQL-ready
                                  dicts out. no database, no clock, no
                                  environment. this is the half that is tested.
  :func:`write_predictions`       the transaction. one prediction_runs row plus
                                  every player row, committed together or not at
                                  all.

the split is not cosmetic. the interesting failure modes here - a crossed
quantile, a probability outside [0,1], a conditional estimate written as though
it were unconditional - are all shape errors in the rows, and none of them need
a database to find. AGENTS.md section 6 forbids a test database in this repo,
so anything that can only be checked against postgres cannot be checked at all;
everything that matters therefore lives on the pure side of the line.

APPEND-ONLY (migration 014). a run writes new rows and never updates old ones,
so there is no upsert here and no ON CONFLICT clause. a duplicate key means the
scored frame contained the same (player, game, stat, quantile) twice, which is a
bug in the caller - it should fail loudly, not resolve itself silently.

TRANSACTIONAL, because a half-written slate is worse than none: the serving path
reads the newest 'production' run with status 'complete', and a partial one would look
complete to it. the run row and its player rows commit together.

``psycopg2`` is imported lazily, inside the function that connects, so this
module - including its SQL - can be imported and inspected with no database
driver installed. no test in this repo opens a connection.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .config import horizon_label
from .intervals import QUANTILE_LEVELS, quantile_columns
from .models import P_PLAY
from .overrides import (
    OVERRIDE_REASON,
    OVERRIDE_REASON_CODES,
    P_PLAY_MODEL,
    STATUS_CAPTURED_AT,
)

log = logging.getLogger(__name__)

# the database's stat vocabulary, keyed by the package's SCREAMING_SNAKE
# internal names. the mapping exists because the two naming conventions are
# both load-bearing: the feature code was ported verbatim from the spike and
# uses NBA-style names, the database uses the snake_case the API serves.
STAT_NAMES: dict[str, str] = {
    "MIN": "minutes",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "FG3M": "fg3m",
    "FGM": "fgm",
    "FGA": "fga",
    "FTM": "ftm",
    "FTA": "fta",
}

# P(he plays at all). unconditional by construction and the only stat whose
# value is a probability rather than a quantity.
#
# 'prob_active' is the number the product uses: model output with the
# injury-report override already applied. 'prob_active_model' is what the model
# said before the layer touched it. both are written for every row, because the
# override layer's constants are hand-set (fnba_ml/overrides.py) and the only way
# to ever replace them with learned ones is to have both series on the record.
# adding a stat name is a vocabulary extension, which migration 014 designed for -
# the table is long-format precisely so this needs no schema change.
PROB_ACTIVE = "prob_active"
PROB_ACTIVE_MODEL = "prob_active_model"

# WHY THESE TWO ARE NUMBERS. player_game_predictions.value is NUMERIC NOT NULL,
# so a text reason cannot be stored as-is and the schema is not being widened for
# it. instead:
#   'status_override'     the reason, as a code from
#                         overrides.OVERRIDE_REASON_CODES (append-only, so a code
#                         never changes meaning).
#   'status_captured_at'  the report's captured_at as unix epoch SECONDS, which is
#                         lossless to the second and recovers to a timestamp with
#                         to_timestamp(value) in postgres.
# both rows are written ONLY for overridden player-games. an absent row means "the
# model's probability stands", which is a fact better recorded by absence than by
# a sentinel value that a later query has to know to exclude.
STATUS_OVERRIDE = "status_override"
STATUS_CAPTURED_AT_STAT = "status_captured_at"

# the schedule-level expectation, P(play) x the conditional estimate. a suffix
# rather than a `conditional` flag alone, because the uniqueness key in
# migration 014 is (run, player, game, stat, quantile) - two rows for the same
# stat differing only in conditionality would collide on it.
UNCOND_SUFFIX = "_uncond"

TABLE = "player_game_predictions"
RUNS_TABLE = "prediction_runs"

# migration 015. the serving path reads only 'production'; a shadow run is on the
# record for comparison and must never reach the app.
RUN_CHANNELS: tuple[str, ...] = ("production", "shadow")
PRODUCTION_CHANNEL = "production"

ROW_COLUMNS: tuple[str, ...] = (
    "nba_player_id",
    "nba_game_id",
    "game_date",
    "stat",
    "quantile",
    "value",
    "conditional",
)


def _as_date(value: object) -> date:
    ts = pd.Timestamp(value)  # type: ignore[arg-type]
    return ts.date()


def _finite(value: object) -> float | None:
    """a real number, or None for anything that is not one (NaN, NA, text)."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def build_prediction_rows(
    predictions: pd.DataFrame,
    targets: tuple[str, ...],
    levels: tuple[float, ...] = QUANTILE_LEVELS,
) -> list[dict[str, object]]:
    """a scored prediction frame -> the rows migration 014 wants.

    per scheduled player-game, in this order:

      prob_active            unconditional, quantile NULL, clamped to [0, 1].
                             POST-override: the number the product should use.
      prob_active_model      the model's own probability, same clamp. present
                             whenever the frame came through the override layer.
      status_override        reason code, overridden rows only
      status_captured_at     the report's captured_at in epoch seconds, overridden
                             rows only
      <stat>                 conditional expected value, quantile NULL
      <stat>_uncond          unconditional expected value, quantile NULL
      <stat> @ P10/P50/P90   conditional quantiles, sorted so they cannot cross

    the clamp on prob_active is a floor and a ceiling on a number that a
    downstream percentage sign takes at face value; a classifier that emits
    1.0000000002 must not become "100.00000002% to play".

    rows whose value is not finite are dropped rather than written as NULL:
    ``value`` is NOT NULL in the schema, and "the estimate did not exist" is a
    fact better recorded by an absent row than by a fabricated zero.
    """
    if predictions.empty:
        return []

    for column in ("PLAYER_ID", "GAME_ID", "GAME_DATE", P_PLAY):
        if column not in predictions.columns:
            raise ValueError(f"scored frame is missing {column!r}; cannot build prediction rows")

    rows: list[dict[str, object]] = []
    dropped = 0

    for record in predictions.to_dict("records"):
        player_id = str(record["PLAYER_ID"])
        game_id = str(record["GAME_ID"])
        game_date = _as_date(record["GAME_DATE"])

        def emit(stat: str, value: object, conditional: bool, quantile: float | None = None) -> None:
            nonlocal dropped
            number = _finite(value)
            if number is None:
                dropped += 1
                return
            rows.append({
                "nba_player_id": player_id,
                "nba_game_id": game_id,
                "game_date": game_date,
                "stat": stat,
                "quantile": quantile,
                "value": number,
                "conditional": conditional,
            })

        probability = _finite(record[P_PLAY])
        if probability is not None:
            emit(PROB_ACTIVE, min(max(probability, 0.0), 1.0), conditional=False)
        else:
            dropped += 1

        model_probability = _finite(record.get(P_PLAY_MODEL))
        if model_probability is not None:
            emit(PROB_ACTIVE_MODEL, min(max(model_probability, 0.0), 1.0), conditional=False)

        reason = record.get(OVERRIDE_REASON)
        if isinstance(reason, str) and reason:
            code = OVERRIDE_REASON_CODES.get(reason)
            if code is None:
                log.warning("override reason %r has no numeric code; not stored", reason)
            else:
                emit(STATUS_OVERRIDE, float(code), conditional=False)
            captured = record.get(STATUS_CAPTURED_AT)
            if captured is not None and not pd.isna(captured):
                emit(
                    STATUS_CAPTURED_AT_STAT,
                    float(pd.Timestamp(captured).timestamp()),
                    conditional=False,
                )

        for target in targets:
            stat = STAT_NAMES.get(target, target.lower())

            conditional_value = record.get(f"E_{target}_COND")
            if conditional_value is not None:
                emit(stat, conditional_value, conditional=True)

            unconditional_value = record.get(f"E_{target}")
            if unconditional_value is not None:
                emit(f"{stat}{UNCOND_SUFFIX}", unconditional_value, conditional=False)

            columns = quantile_columns(target, levels)
            present = [(level, columns[level]) for level in levels if columns[level] in record]
            if not present:
                continue
            # the second non-crossing enforcement, at the last point before the
            # numbers become permanent rows. sorting the values while keeping
            # the levels in order is what guarantees P10 <= P50 <= P90 even if
            # the frame arrived with them crossed.
            values = sorted(float(record[column]) for _, column in present)
            for (level, _), value in zip(sorted(present), values):
                emit(stat, value, conditional=True, quantile=round(float(level), 2))

    if dropped:
        log.warning("%d prediction values were not finite and were not written", dropped)
    return rows


def _utc(value: datetime) -> datetime:
    """an aware UTC datetime; a naive one is taken to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_run_record(
    metadata: dict[str, object],
    predicted_at: datetime,
    forecast_cutoff_at: datetime,
    code_sha: str | None = None,
    status: str = "complete",
    notes: str | None = None,
    horizon: str | None = None,
    channel: str = PRODUCTION_CHANNEL,
    information_as_of: datetime | None = None,
    history_through: date | None = None,
) -> dict[str, object]:
    """the prediction_runs row: which model, trained how far, knowing what.

    four instants, never the same fact:

      ``predicted_at``        when the run was generated (wall clock).
      ``information_as_of``   the latest instant of non-game information, i.e.
                              injury reports, the run was allowed to see.
      ``history_through``     the last game date whose outcomes were in the
                              feature frame.
      ``forecast_cutoff_at``  the LATEST instant any input was allowed to see:
                              max(information_as_of, the start of the first
                              scored day). the caller passes the start of that
                              day; the later of the two is stored, so the column
                              is a true boundary and never claims less
                              visibility than the run actually had.

    ``trained_through`` is the last game date inside the training window. a
    backtest re-run today has today's ``predicted_at``, last season's
    ``forecast_cutoff_at``, and a ``trained_through`` earlier than both.

    ``channel`` is one of :data:`RUN_CHANNELS`. only 'production' is served.

    ``horizon`` is one of config.HORIZONS and is prepended to ``notes`` as
    ``horizon=<name> (<offset>)``. it goes in notes rather than a column because
    migration 014 has no horizon field and does not need one: notes is free text
    on an append-only row, so the label is as immutable as a column would be, and
    the same run row stays readable by a consumer that has never heard of
    horizons. a run without a horizon is a run whose timing was not recorded,
    which is worth being able to see.
    """
    if channel not in RUN_CHANNELS:
        raise ValueError(
            f"unknown prediction run channel {channel!r}; expected one of {RUN_CHANNELS}"
        )
    cutoff = _utc(forecast_cutoff_at)
    information = _utc(information_as_of) if information_as_of is not None else None
    if information is not None and information > cutoff:
        cutoff = information
    window = metadata.get("training_window") or {}
    trained_through = window.get("end") if isinstance(window, dict) else None
    if horizon:
        notes = "; ".join(filter(None, [f"horizon={horizon_label(horizon)}", notes]))
    return {
        "model_version": str(metadata.get("model_version") or "unknown"),
        "feature_version": str(metadata.get("feature_version") or "unknown"),
        "code_sha": code_sha or metadata.get("git_commit"),
        "trained_through": trained_through,
        "predicted_at": predicted_at,
        "forecast_cutoff_at": cutoff,
        "artifact_checksum": metadata.get("artifact_checksum"),
        "status": status,
        "notes": notes,
        "channel": channel,
        "information_as_of": information,
        "history_through": history_through,
    }


INSERT_RUN_SQL = f"""
INSERT INTO {RUNS_TABLE} (
    model_version, feature_version, code_sha, trained_through,
    predicted_at, forecast_cutoff_at, artifact_checksum, status, notes,
    channel, information_as_of, history_through
) VALUES (
    %(model_version)s, %(feature_version)s, %(code_sha)s, %(trained_through)s,
    %(predicted_at)s, %(forecast_cutoff_at)s, %(artifact_checksum)s, %(status)s, %(notes)s,
    %(channel)s, %(information_as_of)s, %(history_through)s
)
RETURNING id
"""

INSERT_ROWS_SQL = f"""
INSERT INTO {TABLE} (
    prediction_run_id, nba_player_id, nba_game_id, game_date,
    stat, quantile, value, conditional
) VALUES %s
"""


def _row_tuple(run_id: int, row: dict[str, object]) -> tuple[object, ...]:
    return (run_id, *(row[column] for column in ROW_COLUMNS))


def write_predictions(
    rows: list[dict[str, object]],
    run_record: dict[str, object],
    database_url: str | None = None,
    page_size: int = 1000,
) -> int:
    """insert the run and its rows in one transaction; return the run id.

    refuses an empty row set: an empty run would be indistinguishable from a
    complete one to the serving query, which reads the newest 'complete' run and
    would then find nothing for every player.
    """
    if not rows:
        raise ValueError("refusing to write a prediction run with no prediction rows")

    import psycopg2  # noqa: PLC0415 - optional at import time, required at call time
    from psycopg2.extras import execute_values  # noqa: PLC0415

    from .data.postgres_source import load_database_url  # noqa: PLC0415

    conn = psycopg2.connect(database_url or load_database_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_RUN_SQL, run_record)
                run_id = int(cur.fetchone()[0])
                execute_values(
                    cur,
                    INSERT_ROWS_SQL,
                    [_row_tuple(run_id, row) for row in rows],
                    page_size=page_size,
                )
    finally:
        conn.close()

    log.info("wrote prediction run %d with %d rows", run_id, len(rows))
    return run_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
