import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

import psycopg2

from config import (
    CBS_INJURIES_SOURCE,
    INJURY_ABSENCE_CLEARS_SOURCES,
    INJURY_DISPLAY_LABELS,
    INJURY_DISPLAY_MAX_AGE_HOURS,
    INJURY_OUT_CLASS_STATUSES,
    INJURY_RECONCILE_LOOKBACK_DAYS,
    INJURY_SOURCE_PRECEDENCE,
)
from database import maybe_write_cursor
from parsing import normalize_injury_status

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_EXPECTED_RETURN = re.compile(
    r"^(?P<reason>.*?);\s*expected return (?P<date>\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_GAME_SEVERITY = {"doubtful": 3, "questionable": 2, "probable": 1}
_OUT_SEVERITY = 4


class InjuryReportRow(NamedTuple):
    nba_player_id: str
    nba_game_id: str | None
    captured_at: datetime
    status_normalized: str | None
    reason: str | None
    source: str
    game_date: date | None = None


class _SourceView(NamedTuple):
    rank: int
    as_of: datetime
    row: InjuryReportRow | None
    protected: bool


def _is_out_class(status: str | None) -> bool:
    return (status or "") in INJURY_OUT_CLASS_STATUSES


def _severity(status: str | None) -> int:
    if _is_out_class(status):
        return _OUT_SEVERITY
    return _GAME_SEVERITY.get(status or "", 0)


def _rank(source: str) -> int:
    return len(INJURY_SOURCE_PRECEDENCE) - INJURY_SOURCE_PRECEDENCE.index(source)


def _source_view(
    rows: list[InjuryReportRow], rank: int, now: datetime, today: date
) -> _SourceView | None:
    newest_general = max(
        (r for r in rows if not r.nba_game_id), key=lambda r: r.captured_at, default=None
    )
    newest_by_game: dict[str, InjuryReportRow] = {}
    for r in rows:
        if r.nba_game_id and (
            r.nba_game_id not in newest_by_game
            or r.captured_at > newest_by_game[r.nba_game_id].captured_at
        ):
            newest_by_game[r.nba_game_id] = r
    # a newer general report supersedes the source's older game rows.
    games = [
        r for r in newest_by_game.values()
        if r.game_date is not None and r.game_date >= today
        and (newest_general is None or r.captured_at >= newest_general.captured_at)
    ]
    candidates = games + ([newest_general] if newest_general else [])
    as_of = max((r.captured_at for r in candidates), default=None)
    if as_of is None:
        return None

    cutoff = now - timedelta(hours=INJURY_DISPLAY_MAX_AGE_HOURS)
    fresh = [
        r for r in candidates
        if _is_out_class(r.status_normalized) or r.captured_at >= cutoff
    ]
    if not fresh:
        return _SourceView(rank, as_of, None, False)
    chosen = max(
        fresh,
        key=lambda r: (
            _severity(r.status_normalized),
            r.nba_game_id is not None,
            -(r.game_date or date.max).toordinal(),
            r.captured_at,
        ),
    )
    protected = _is_out_class(chosen.status_normalized) or chosen.nba_game_id is not None
    return _SourceView(rank, as_of, chosen, protected)


def _winning_row(views: list[_SourceView]) -> InjuryReportRow | None:
    # an out or game-scoped view stands until a newer view of equal or higher
    # precedence; anything else yields to any newer view.
    standing = [
        v for v in views
        if v.row is not None
        and not any(
            w is not v and w.as_of > v.as_of and (w.rank >= v.rank or not v.protected)
            for w in views
        )
    ]
    if not standing:
        return None
    return max(standing, key=lambda v: (v.rank, v.as_of)).row


def _format_reason(reason: str | None) -> str | None:
    text = (reason or "").strip()
    match = _EXPECTED_RETURN.match(text)
    if not match:
        return text or None
    returns = date.fromisoformat(match.group("date"))
    head = match.group("reason").strip()
    expected = f"expected return {returns:%b} {returns.day}"
    return f"{head}, {expected}" if head and head != "None" else expected


def _cbs_detail(
    row: InjuryReportRow, label: str, cbs_rows: Mapping[str, tuple[str, str | None]]
) -> str | None:
    status_text, injury = cbs_rows.get(row.nba_player_id, ("", row.reason))
    status_text = (status_text or "").strip()
    injury = (injury or "").strip()
    # only fold cbs's wording in when it is the text that produced this row.
    if (
        not status_text
        or status_text.lower() == label.lower()
        or normalize_injury_status(status_text) != row.status_normalized
    ):
        return injury or None
    return ", ".join(p for p in (injury, status_text) if p)


def plan_player_injury_columns(
    reports: Iterable[InjuryReportRow],
    cbs_rows: Mapping[str, tuple[str, str | None]],
    now: datetime,
) -> dict[str, tuple[str, str | None]]:
    today = now.astimezone(ET).date()
    known = [r for r in reports if r.source in INJURY_SOURCE_PRECEDENCE]

    latest_by_source: dict[str, datetime] = {}
    by_player: dict[str, dict[str, list[InjuryReportRow]]] = {}
    for r in known:
        if r.source not in latest_by_source or r.captured_at > latest_by_source[r.source]:
            latest_by_source[r.source] = r.captured_at
        by_player.setdefault(r.nba_player_id, {}).setdefault(r.source, []).append(r)

    plan: dict[str, tuple[str, str | None]] = {}
    for nba_id, by_source in by_player.items():
        for source in INJURY_ABSENCE_CLEARS_SOURCES:
            rows = by_source.get(source)
            if rows and max(r.captured_at for r in rows) < latest_by_source[source]:
                rows.append(
                    InjuryReportRow(
                        nba_id, None, latest_by_source[source], "cleared", None, source
                    )
                )
        views = [
            view for source, rows in by_source.items()
            if (view := _source_view(rows, _rank(source), now, today)) is not None
        ]
        winner = _winning_row(views)
        label = INJURY_DISPLAY_LABELS.get((winner.status_normalized or "") if winner else "")
        if winner is None or label is None:
            continue
        detail = (
            _cbs_detail(winner, label, cbs_rows)
            if winner.source == CBS_INJURIES_SOURCE
            else _format_reason(winner.reason)
        )
        plan[nba_id] = (label, detail)
    return plan


def _load_reports(
    cur: object, now: datetime, today: date
) -> tuple[list[InjuryReportRow], dict[str, tuple[str, str | None]]]:
    cur.execute(
        """
        SELECT r.nba_player_id, r.nba_game_id, r.captured_at, r.status_normalized,
               r.reason, r.source, r.status_raw, s.game_date
        FROM player_injury_reports r
        LEFT JOIN nba_schedule s
          ON s.nba_game_id = r.nba_game_id
         AND s.game_date >= %s
        WHERE r.captured_at >= %s
        ORDER BY r.captured_at
        """,
        (today, now - timedelta(days=INJURY_RECONCILE_LOOKBACK_DAYS)),
    )
    reports: list[InjuryReportRow] = []
    cbs_rows: dict[str, tuple[str, str | None]] = {}
    for nba_id, game_id, captured_at, status, reason, source, status_raw, game_date in cur.fetchall():
        reports.append(
            InjuryReportRow(
                str(nba_id), str(game_id) if game_id else None, captured_at,
                status, reason, source, game_date,
            )
        )
        # ordered by capture time, so the last cbs row per player is his current one.
        if source == CBS_INJURIES_SOURCE:
            cbs_rows[str(nba_id)] = (status_raw or "", reason)
    return reports, cbs_rows


def reconcile_player_injury_columns(
    conn: psycopg2.extensions.connection,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    cur = maybe_write_cursor(conn.cursor(), dry_run)
    try:
        reports, cbs_rows = _load_reports(cur, now, now.astimezone(ET).date())
        plan = plan_player_injury_columns(reports, cbs_rows, now)
        cur.execute(
            "SELECT nba_id, injury_status, injury_detail FROM players WHERE nba_id IS NOT NULL"
        )
        current = {
            str(nba_id): (status, detail) for nba_id, status, detail in cur.fetchall()
        }

        updated = cleared = unchanged = 0
        for nba_id, (status, detail) in sorted(plan.items()):
            if nba_id not in current:
                continue
            if current[nba_id] == (status, detail):
                unchanged += 1
                continue
            cur.execute(
                """
                UPDATE players
                SET injury_status = %s, injury_detail = %s, updated_at = NOW()
                WHERE nba_id = %s
                """,
                (status, detail, nba_id),
            )
            updated += 1
        for nba_id, (status, detail) in sorted(current.items()):
            if nba_id in plan or (status is None and detail is None):
                continue
            cur.execute(
                """
                UPDATE players
                SET injury_status = NULL, injury_detail = NULL, updated_at = NOW()
                WHERE nba_id = %s
                """,
                (nba_id,),
            )
            cleared += 1
    finally:
        cur.close()

    logger.info(
        "injury reconcile: updated %d, cleared %d, unchanged %d%s",
        updated, cleared, unchanged, " (dry run: no rows written)" if dry_run else "",
    )
    return updated + cleared
