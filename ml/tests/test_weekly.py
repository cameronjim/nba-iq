from __future__ import annotations

from datetime import date

import pytest

from fnba_ml.weekly import (
    COUNTING_STATS,
    GamePrediction,
    RatioTotal,
    availability_risk,
    clamp_probability,
    counting_totals,
    expected_games,
    in_range,
    ratio_total,
    ratio_totals,
    stat_value,
    weekly_projection,
)

MONDAY = date(2026, 2, 2)
SUNDAY = date(2026, 2, 8)


def game(day: date, prob: float = 1.0, **stats: float) -> GamePrediction:
    return GamePrediction(game_date=day, prob_active=prob, stats=stats)


class TestClampProbability:
    @pytest.mark.parametrize("value,expected", [(0.5, 0.5), (1.4, 1.0), (-0.2, 0.0)])
    def test_forces_into_the_unit_interval(self, value, expected):
        assert clamp_probability(value) == expected

    def test_missing_availability_reads_as_certain_not_as_ruled_out(self):
        assert clamp_probability(None) == 1.0


class TestStatValue:
    def test_reads_a_present_stat(self):
        assert stat_value(game(MONDAY, pts=22.5), "pts") == 22.5

    def test_a_missing_stat_contributes_nothing(self):
        assert stat_value(game(MONDAY, pts=22.5), "blk") == 0.0


class TestInRange:
    def test_is_inclusive_at_both_ends(self):
        rows = [game(MONDAY), game(date(2026, 2, 5)), game(SUNDAY)]

        assert len(in_range(rows, MONDAY, SUNDAY)) == 3

    def test_excludes_days_outside_the_week(self):
        rows = [game(date(2026, 2, 1)), game(date(2026, 2, 4)), game(date(2026, 2, 9))]

        kept = in_range(rows, MONDAY, SUNDAY)

        assert [row.game_date for row in kept] == [date(2026, 2, 4)]

    def test_an_inverted_range_selects_nothing(self):
        assert in_range([game(MONDAY)], SUNDAY, MONDAY) == []


class TestExpectedGames:
    def test_sums_availability_rather_than_counting_rows(self):
        rows = [game(MONDAY, 0.75), game(date(2026, 2, 4), 0.75), game(SUNDAY, 0.5)]

        assert expected_games(rows) == pytest.approx(2.0)

    def test_is_zero_for_an_empty_week(self):
        assert expected_games([]) == 0.0

    def test_clamps_malformed_probabilities(self):
        assert expected_games([game(MONDAY, 3.0)]) == 1.0


class TestCountingTotals:
    def test_sums_each_stat_across_the_week(self):
        rows = [game(MONDAY, pts=20, reb=5), game(SUNDAY, pts=14, reb=9)]

        totals = counting_totals(rows, ("pts", "reb"))

        assert totals == {"pts": 34.0, "reb": 14.0}

    def test_covers_every_configured_stat_even_when_unmodelled(self):
        totals = counting_totals([game(MONDAY, pts=20)])

        assert set(totals) == set(COUNTING_STATS)
        assert totals["blk"] == 0.0

    def test_an_empty_week_totals_zero_rather_than_erroring(self):
        assert counting_totals([], ("pts",)) == {"pts": 0.0}


class TestRatioTotals:
    def test_ratio_totals_are_not_averaged_percentages(self):
        # averaging would invent a shooter.
        rows = [game(MONDAY, fgm=2, fga=2), game(SUNDAY, fgm=9, fga=22)]

        result = ratio_total(rows, "fgm", "fga")

        assert result.made == 11.0
        assert result.attempted == 24.0
        assert result.pct == pytest.approx(11 / 24)
        assert result.pct != pytest.approx((1.0 + 9 / 22) / 2)

    def test_no_attempts_is_undefined_not_zero_percent(self):
        # 0/0 is "did not shoot", which is not the same as "missed everything"
        result = ratio_total([game(MONDAY, fgm=0, fga=0)], "fgm", "fga")

        assert result == RatioTotal(made=0.0, attempted=0.0, pct=None)

    def test_covers_both_configured_ratios(self):
        rows = [game(MONDAY, fgm=5, fga=10, ftm=3, fta=4)]

        ratios = ratio_totals(rows)

        assert set(ratios) == {"fg_pct", "ft_pct"}
        assert ratios["fg_pct"].pct == pytest.approx(0.5)
        assert ratios["ft_pct"].pct == pytest.approx(0.75)


class TestAvailabilityRisk:
    def test_is_the_complement_of_playing_every_game(self):
        rows = [game(MONDAY, 0.9), game(SUNDAY, 0.5)]

        assert availability_risk(rows) == pytest.approx(1 - 0.45)

    def test_rises_with_every_additional_game(self):
        one = availability_risk([game(MONDAY, 0.9)])
        three = availability_risk([game(MONDAY, 0.9), game(date(2026, 2, 4), 0.9), game(SUNDAY, 0.9)])

        assert three > one

    def test_a_certain_week_carries_no_risk(self):
        assert availability_risk([game(MONDAY, 1.0), game(SUNDAY, 1.0)]) == pytest.approx(0.0)

    def test_an_empty_week_carries_no_risk(self):
        assert availability_risk([]) == 0.0

    def test_is_an_upper_bound_when_absences_are_perfectly_dependent(self):
        rows = [game(MONDAY, 0.5), game(SUNDAY, 0.5)]
        one_shared_injury_risk = 0.5

        risk = availability_risk(rows)

        assert risk == pytest.approx(0.75)
        assert risk >= one_shared_injury_risk


class TestWeeklyProjection:
    def test_assembles_the_whole_week(self):
        rows = [
            game(date(2026, 2, 1), 0.9, pts=20, reb=6, fgm=8, fga=16, ftm=4, fta=4),
            game(MONDAY, 0.9, pts=24, reb=8, fgm=9, fga=18, ftm=6, fta=8),
            game(date(2026, 2, 5), 0.5, pts=10, reb=3, fgm=4, fga=11, ftm=2, fta=2),
            game(date(2026, 2, 12), 1.0, pts=30, reb=9, fgm=12, fga=20, ftm=6, fta=6),
        ]

        projection = weekly_projection(rows, MONDAY, SUNDAY)

        assert projection.games_scheduled == 2
        assert projection.expected_games == pytest.approx(1.4)
        assert projection.totals["pts"] == pytest.approx(34.0)
        assert projection.totals["reb"] == pytest.approx(11.0)
        assert projection.ratios["fg_pct"].pct == pytest.approx(13 / 29)
        assert projection.ratios["ft_pct"].pct == pytest.approx(8 / 10)
        assert projection.availability_risk == pytest.approx(1 - 0.45)

    def test_an_empty_week_projects_zeros_rather_than_raising(self):
        projection = weekly_projection([], MONDAY, SUNDAY)

        assert projection.games_scheduled == 0
        assert projection.expected_games == 0.0
        assert projection.availability_risk == 0.0
        assert projection.totals["pts"] == 0.0
        assert projection.ratios["fg_pct"].pct is None

    def test_as_dict_is_json_shaped(self):
        payload = weekly_projection([game(MONDAY, 0.8, pts=20)], MONDAY, SUNDAY).as_dict()

        assert payload["start"] == "2026-02-02"
        assert payload["end"] == "2026-02-08"
        assert payload["totals"]["pts"] == 20.0
        assert payload["ratios"]["fg_pct"] == {"made": 0.0, "attempted": 0.0, "pct": None}
