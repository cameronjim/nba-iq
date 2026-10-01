import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date, datetime

from config import (
    CBS_TEAM_ABBR_ALIASES,
    GAME_ID_PREFIX_TO_SEASON_TYPE,
    NAME_TO_ABBR,
    NBA_2K_TEAM_TYPES,
    SEASON_TYPE_UNKNOWN,
    TEAM_ID_TO_ABBR,
    TEAM_META,
    V2_INACTIVE_UNRELIABLE_FROM,
)

_SEASON_PATTERN = re.compile(r"^(\d{4})-\d{2}$")
_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
_MATCHUP_PATTERN = re.compile(
    r"^\s*(?P<team>[A-Za-z]{2,4})\s+(?P<sep>vs\.?|@)\s+(?P<opp>[A-Za-z]{2,4})\s*$",
    re.IGNORECASE,
)
# v3 box scores report minutes as an ISO-8601 duration, e.g. "PT34M12.00S".
_MINUTES_ISO_PATTERN = re.compile(
    r"^PT(?:(?P<min>\d+(?:\.\d+)?)M)?(?:(?P<sec>\d+(?:\.\d+)?)S)?$", re.IGNORECASE
)

_BROAD_TO_SPECIFIC: dict[str, list[str]] = {
    "G": ["PG", "SG"],
    "G-F": ["SG", "SF"],
    "F-G": ["SG", "SF"],
    "F": ["SF", "PF"],
    "F-C": ["PF", "C"],
    "C-F": ["PF", "C"],
    "C": ["C"],
}

# longest phrase first: "out for season" must not be matched by "out", and
# "day-to-day" must not be matched by "day".
_INJURY_STATUS_BUCKETS: tuple[tuple[str, str], ...] = (
    ("out for season", "out"),
    ("season-ending", "out"),
    # questionable, not day_to_day: the ml override layer blends a gtd.
    ("game time decision", "questionable"),
    ("gtd", "questionable"),
    ("day-to-day", "day_to_day"),
    ("day to day", "day_to_day"),
    ("questionable", "questionable"),
    ("doubtful", "doubtful"),
    ("probable", "probable"),
    ("cleared", "cleared"),
    ("available", "available"),
    ("active", "available"),
    ("out", "out"),
)

# (rule name, the stat that must not exceed, the stat it must not exceed).
BOX_SCORE_RULES: tuple[tuple[str, str, str], ...] = (
    ("fgm_le_fga", "fgm", "fga"),
    ("fg3m_le_fg3a", "fg3m", "fg3a"),
    ("fg3m_le_fgm", "fg3m", "fgm"),
    ("ftm_le_fta", "ftm", "fta"),
)


def _pct(val: object) -> float:
    if val is None:
        return 0.0
    return round(float(val) * 100, 1)


def _safe_float(val: object) -> float:
    if val is None:
        return 0.0
    return round(float(val), 1)


def _is_missing(val: object) -> bool:
    # NaN included: psycopg2 stores it happily and a NUMERIC NaN then poisons
    # every AVG and ORDER BY over the column.
    if val is None:
        return True
    try:
        num = float(val)
    except (TypeError, ValueError):
        return True
    return num != num


def _opt_float(val: object) -> float | None:
    return None if _is_missing(val) else _safe_float(val)


def _opt_pct(val: object) -> float | None:
    return None if _is_missing(val) else _pct(val)


def _opt_int(val: object) -> int | None:
    return None if _is_missing(val) else int(float(val))


def _text_or_none(val: object) -> str | None:
    # 2K returns "" rather than omitting a field it has no value for.
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def _normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_name = ascii_name.lower().strip()
    ascii_name = re.sub(r"\b(jr\.?|sr\.?|iii|ii|iv)\b", "", ascii_name)
    ascii_name = ascii_name.replace(".", "").replace("'", "").replace("-", " ")
    ascii_name = re.sub(r"\s+", " ", ascii_name).strip()
    return ascii_name


_GENERATIONAL_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


def canonical_player_name(name: str) -> str:
    # curly apostrophes survive NFKD, and cbs prints them where nba.com does not.
    text = _normalize_name(str(name or "").replace("’", "'").replace("`", "'"))
    tokens = text.split(" ")
    # the first token is never a suffix, so a one-word name cannot vanish.
    kept = tokens[:1] + [t for t in tokens[1:] if t not in _GENERATIONAL_SUFFIXES]
    return " ".join(t for t in kept if t)


def cbs_team_abbr(raw: str) -> str | None:
    code = str(raw or "").strip().upper()
    code = CBS_TEAM_ABBR_ALIASES.get(code, code)
    return code if code in TEAM_META else None


def _resolve_team_abbr(team_id: str, team_name: str) -> str:
    abbr = TEAM_ID_TO_ABBR.get(str(team_id), "")
    if abbr:
        return abbr
    return NAME_TO_ABBR.get((team_name or "").strip().lower(), "")


def resolve_positions(cbs_pos: str, nba_broad_pos: str) -> str:
    specific_from_broad = _BROAD_TO_SPECIFIC.get(nba_broad_pos, [])

    if cbs_pos:
        positions = [cbs_pos]
        for p in specific_from_broad:
            if p != cbs_pos:
                positions.append(p)
        return ",".join(positions)

    if specific_from_broad:
        return ",".join(specific_from_broad)

    return ""


def season_start_year(season: str) -> int:
    match = _SEASON_PATTERN.match(season.strip())
    if not match:
        raise ValueError(f"season must look like 1996-97, got {season!r}")
    return int(match.group(1))


def season_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_range(from_season: str, to_season: str) -> list[str]:
    # newest first, so a run throttled to death still leaves the seasons users
    # actually look at behind. Bounds may be given in either order.
    a = season_start_year(from_season)
    b = season_start_year(to_season)
    lo, hi = min(a, b), max(a, b)
    return [season_label(year) for year in range(hi, lo - 1, -1)]


def season_start_date(season: str) -> date:
    # July 1 and June 30 are bounds, not dates: together they partition the
    # calendar into non-overlapping season windows with no seam.
    return date(season_start_year(season), 7, 1)


def season_end_date(season: str) -> date:
    return date(season_start_year(season) + 1, 6, 30)


def in_season(game_date: date | None, season: str) -> bool:
    if game_date is None:
        return False
    return season_start_date(season) <= game_date <= season_end_date(season)


def parse_minutes(val: object) -> float | None:
    # the endpoints report minutes three ways: a number (34.2), "MM:SS", and an
    # ISO-8601 duration. None, never 0.0, for anything unparseable: a player
    # with no minutes reported did not play zero minutes.
    if val is None:
        return None

    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        num = float(val)
        return None if num != num else round(num, 2)

    text = str(val).strip()
    if not text:
        return None

    iso = _MINUTES_ISO_PATTERN.match(text)
    if iso:
        minutes = float(iso.group("min") or 0)
        seconds = float(iso.group("sec") or 0)
        return round(minutes + seconds / 60, 2)

    if ":" in text:
        head, _, tail = text.partition(":")
        try:
            return round(float(head or 0) + float(tail or 0) / 60, 2)
        except ValueError:
            return None

    try:
        return round(float(text), 2)
    except ValueError:
        return None


def parse_game_date(val: object) -> date | None:
    # any time component is discarded rather than converted: these are already
    # canonical Eastern-time game dates, and a timezone shift would move late
    # games onto the wrong night.
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val

    text = str(val).strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text.split("T", 1)[0])
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_matchup(matchup: object) -> tuple[bool | None, str | None]:
    # the league game log carries no explicit home flag, so the MATCHUP string
    # is the only place is_home exists. (None, None) rather than a guess.
    match = _MATCHUP_PATTERN.match(str(matchup or ""))
    if not match:
        return None, None
    is_home = match.group("sep").startswith("@") is False
    return is_home, match.group("opp").upper()


def season_type_from_game_id(game_id: object) -> str:
    # NBA encodes the season type in the first three characters of a game id.
    text = str(game_id or "").strip()
    return GAME_ID_PREFIX_TO_SEASON_TYPE.get(text[:3], SEASON_TYPE_UNKNOWN)


def v2_inactive_is_unreliable(game_date: date | None) -> bool:
    return game_date is None or game_date >= V2_INACTIVE_UNRELIABLE_FROM


def normalize_injury_status(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return "unknown"
    for phrase, bucket in _INJURY_STATUS_BUCKETS:
        if phrase in text:
            return bucket
    return "unknown"


def clearances_for_report(
    previous_rows: Iterable[tuple[str, str]],
    currently_listed: Iterable[str],
    unmatched_names: Iterable[str] = (),
    complete: bool = True,
) -> list[str]:
    """players to clear: previously listed, absent now, and provably not just unparsed.

    an incomplete report (a table skipped for a bad header) clears nobody. a
    previously listed player whose name is on the page but could not be matched
    is not cleared either: the page still lists him, we just lost the link.
    """
    if not complete:
        return []
    unmatched = {canonical_player_name(name) for name in unmatched_names if name}
    previous_ids = [
        nba_id for nba_id, name in previous_rows
        if canonical_player_name(name or "") not in unmatched
    ]
    return cleared_player_ids(previous_ids, currently_listed)


def cleared_player_ids(
    previously_listed: Iterable[str], currently_listed: Iterable[str]
) -> list[str]:
    current = {str(player_id) for player_id in currently_listed}
    # an empty page is an outage, not a league with no injuries.
    if not current:
        return []
    previous = {str(player_id) for player_id in previously_listed if player_id}
    return sorted(previous - current)


def box_score_violations(row: Mapping) -> list[str]:
    violations: list[str] = []
    for name, left, right in BOX_SCORE_RULES:
        lhs, rhs = row.get(left), row.get(right)
        if lhs is None or rhs is None:
            continue
        if float(lhs) > float(rhs):
            violations.append(name)
    return violations


def parse_team_types(raw: str) -> list[str]:
    requested = [part.strip().lower() for part in (raw or "").split(",")]
    requested = [part for part in requested if part]
    valid = ", ".join(NBA_2K_TEAM_TYPES)

    if not requested:
        raise ValueError(f"--team-types needs at least one of: {valid}")

    unknown = [part for part in requested if part not in NBA_2K_TEAM_TYPES]
    if unknown:
        raise ValueError(
            f"unknown team type(s): {', '.join(unknown)} (choose from {valid})"
        )

    ordered: list[str] = []
    for part in requested:
        if part not in ordered:
            ordered.append(part)
    return ordered


def extract_next_data(html: str) -> dict:
    match = _NEXT_DATA_PATTERN.search(html)
    if not match:
        raise ValueError("page has no __NEXT_DATA__ payload")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("__NEXT_DATA__ payload is not an object")
    return data
