"""weekly aggregation of per-game predictions into a fantasy week total.

pure functions over plain data. nothing here touches a database, a model, a
file or a network - the caller hands over a list of already-predicted games
and gets back the totals a manager actually plans against.

THIS MODULE IS THE ONLY IMPLEMENTATION of weekly aggregation. the backend
deliberately does not mirror it: a week total is an analysis artifact, not a
page render, and two implementations of the same arithmetic would drift.

two rules drive every function below:

1. PERCENTAGES ARE NEVER AVERAGED. a week's field goal percentage is the sum
   of makes over the sum of attempts. averaging per-game percentages weights a
   2-for-2 night the same as a 9-for-22 one, which is how a 50% shooter turns
   into a 62% shooter on paper. see `ratio_total`.

2. EXPECTATIONS ARE ALREADY UNCONDITIONAL. the per-game values this module
   sums are expectations over ALL outcomes including "did not play", so they
   are additive as they stand. multiplying them by prob_active again would
   discount availability twice. `prob_active` is carried separately, and is
   used only for the expected game count and the availability risk.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

# counting stats that add up across a week.
COUNTING_STATS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m")

# ratio stats, as (made, attempted) pairs. aggregated over totals, never as a
# mean of per-game percentages.
RATIO_STATS: dict[str, tuple[str, str]] = {
    "fg_pct": ("fgm", "fga"),
    "ft_pct": ("ftm", "fta"),
}


@dataclass(frozen=True)
class GamePrediction:
    """one predicted game for one player.

    stats holds UNCONDITIONAL expected values keyed by the same stat names the
    prediction table uses ('pts', 'reb', 'fgm', ...). a missing key counts as
    zero contribution rather than an error, because a run that models points
    but not blocks should still produce a points total.
    """

    game_date: date
    prob_active: float
    stats: Mapping[str, float]


@dataclass(frozen=True)
class WeeklyProjection:
    """what a player is projected to produce over one date range."""

    start: date
    end: date
    #: games on the schedule in the range, regardless of availability.
    games_scheduled: int
    #: sum of prob_active - the number of games expected to actually be played.
    expected_games: float
    #: summed counting stats, keyed as in COUNTING_STATS.
    totals: dict[str, float]
    #: made/attempted totals and the ratio over them, keyed as in RATIO_STATS.
    ratios: dict[str, "RatioTotal"]
    #: probability the player misses AT LEAST ONE game in the range.
    availability_risk: float

    def as_dict(self) -> dict[str, object]:
        """json-shaped view, for writing a report or an api payload."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "games_scheduled": self.games_scheduled,
            "expected_games": self.expected_games,
            "totals": dict(self.totals),
            "ratios": {name: ratio.as_dict() for name, ratio in self.ratios.items()},
            "availability_risk": self.availability_risk,
        }


@dataclass(frozen=True)
class RatioTotal:
    """a made/attempted pair and the ratio over the totals."""

    made: float
    attempted: float
    #: None when nothing was attempted - 0/0 is undefined, not 0%.
    pct: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {"made": self.made, "attempted": self.attempted, "pct": self.pct}


def clamp_probability(value: float | None) -> float:
    """a probability, forced into [0, 1]. None reads as 1.0.

    a missing prob_active means the run did not model availability for this
    game, not that the player is ruled out - treating it as 0 would silently
    zero out the whole week.
    """
    if value is None:
        return 1.0
    return min(1.0, max(0.0, float(value)))


def stat_value(row: GamePrediction, stat: str) -> float:
    """one stat off a row, with a missing or null value counting as zero."""
    value = row.stats.get(stat)
    return 0.0 if value is None else float(value)


def in_range(
    rows: Iterable[GamePrediction], start: date, end: date
) -> list[GamePrediction]:
    """rows whose game_date falls in [start, end], INCLUSIVE at both ends.

    a fantasy week is named by its first and last day and both are played, so
    a half-open range would quietly drop every Sunday.
    """
    if start > end:
        return []
    return [row for row in rows if start <= row.game_date <= end]


def expected_games(rows: Sequence[GamePrediction]) -> float:
    """expected number of games actually played: the sum of prob_active.

    this is a mean, not a count - 4 games at 75% each is 3.0 expected games,
    which is the number a matchup projection should use.
    """
    return sum(clamp_probability(row.prob_active) for row in rows)


def counting_totals(
    rows: Sequence[GamePrediction], stats: Sequence[str] = COUNTING_STATS
) -> dict[str, float]:
    """summed counting stats over the rows, one entry per requested stat."""
    return {stat: sum(stat_value(row, stat) for row in rows) for stat in stats}


def ratio_total(rows: Sequence[GamePrediction], made_key: str, att_key: str) -> RatioTotal:
    """makes over attempts, summed FIRST and divided ONCE.

    the whole reason this function exists is to make the wrong version -
    mean(per-game pct) - impossible to write by accident.
    """
    made = sum(stat_value(row, made_key) for row in rows)
    attempted = sum(stat_value(row, att_key) for row in rows)
    pct = made / attempted if attempted > 0 else None
    return RatioTotal(made=made, attempted=attempted, pct=pct)


def ratio_totals(
    rows: Sequence[GamePrediction],
    ratios: Mapping[str, tuple[str, str]] = RATIO_STATS,
) -> dict[str, RatioTotal]:
    """every configured ratio stat, aggregated over totals."""
    return {name: ratio_total(rows, made, att) for name, (made, att) in ratios.items()}


def availability_risk(rows: Sequence[GamePrediction]) -> float:
    """probability the player misses AT LEAST ONE game in the range.

    1 - product(prob_active), exact under independence. absences are
    positively correlated (a lingering injury), which raises P(plays every
    game) above the product, so this is an UPPER bound on the true risk.

    an empty range carries no risk at all, so it reports 0.0.
    """
    if not rows:
        return 0.0
    all_play = 1.0
    for row in rows:
        all_play *= clamp_probability(row.prob_active)
    return 1.0 - all_play


def weekly_projection(
    rows: Iterable[GamePrediction],
    start: date,
    end: date,
    stats: Sequence[str] = COUNTING_STATS,
    ratios: Mapping[str, tuple[str, str]] = RATIO_STATS,
) -> WeeklyProjection:
    """everything a manager needs for one player over one date range.

    an empty range is a legitimate answer (a bye week, a player between
    stints) and produces zeroed totals rather than an error.
    """
    window = in_range(rows, start, end)
    return WeeklyProjection(
        start=start,
        end=end,
        games_scheduled=len(window),
        expected_games=expected_games(window),
        totals=counting_totals(window, stats),
        ratios=ratio_totals(window, ratios),
        availability_risk=availability_risk(window),
    )
