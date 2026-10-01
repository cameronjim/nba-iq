from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fnba_ml import config
from fnba_ml.models import P_PLAY, P_PLAY_CUTOFF
from fnba_ml.overrides import (
    DEFAULT_POLICY,
    DOUBTFUL_PROBABILITY,
    LEAGUE_QUESTIONABLE_PLAY_RATE,
    OFFICIAL_PRECEDENCE_HOURS,
    OUT_PROBABILITY,
    OVERRIDE_REASON,
    P_PLAY_MODEL,
    PROBABLE_MODEL_WEIGHT,
    PROBABLE_SHIFT,
    QUESTIONABLE_MODEL_WEIGHT,
    REPORT_MAX_AGE_HOURS,
    STATUS_CAPTURED_AT,
    STATUS_NORMALIZED,
    STATUS_SCOPE,
    STATUS_SOURCE,
    StatusPolicy,
    apply_status_overrides,
    latest_statuses,
    normalise_status,
    override_provenance_counts,
    override_summary,
    reason_for,
    resolve_statuses,
)

AS_OF = pd.Timestamp("2026-03-01 18:00")
GAME_DATE = pd.Timestamp("2026-03-01")


def predictions(model_probabilities: list[float], player_ids: list[str] | None = None
                ) -> pd.DataFrame:
    """a scored frame shaped exactly as predict.build_predictions emits one."""
    ids = player_ids or [str(2544 + i) for i in range(len(model_probabilities))]
    n = len(ids)
    conditional_pts = [24.0, 12.0, 8.0, 30.0, 4.0][:n]
    conditional_min = [34.0, 22.0, 15.0, 36.0, 9.0][:n]
    return pd.DataFrame({
        "PLAYER_ID": ids,
        "GAME_ID": ["0022500123"] * n,
        "GAME_DATE": [GAME_DATE] * n,
        P_PLAY: model_probabilities,
        P_PLAY_CUTOFF: [pd.Timestamp("2026-02-28")] * n,
        "E_MIN_COND": conditional_min,
        "E_MIN": [p * m for p, m in zip(model_probabilities, conditional_min)],
        "E_PTS_COND": conditional_pts,
        "E_PTS": [p * c for p, c in zip(model_probabilities, conditional_pts)],
        "Q10_PTS": [c - 8 for c in conditional_pts],
        "Q50_PTS": conditional_pts,
        "Q90_PTS": [c + 9 for c in conditional_pts],
    })


def statuses(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """(player id, status, captured_at) -> the frame the layer expects."""
    return pd.DataFrame(
        rows, columns=["nba_player_id", "status_normalized", "captured_at"]
    )


def applied(frame: pd.DataFrame, player_id: str) -> pd.Series:
    return frame[frame["PLAYER_ID"] == player_id].iloc[0]


@pytest.mark.parametrize("status", ["out", "suspended", "g_league"])
def test_unavailable_statuses_floor_the_probability(status):
    """not 0.0: an official 'out' is occasionally reversed."""
    frame = predictions([0.93])
    report = statuses([("2544", status, "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert row[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert row[P_PLAY] == pytest.approx(0.02)
    assert row[OVERRIDE_REASON] == reason_for(status)


def test_doubtful_replaces_the_model_outright():
    frame = predictions([0.88])
    report = statuses([("2544", "doubtful", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(DOUBTFUL_PROBABILITY)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.10)


def test_questionable_blends_the_model_with_the_league_prior():
    frame = predictions([0.90, 0.30])
    report = statuses([
        ("2544", "questionable", "2026-03-01 12:00"),
        ("2545", "questionable", "2026-03-01 12:00"),
    ])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.6 * 0.90 + 0.4 * 0.60)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.78)
    assert applied(out, "2545")[P_PLAY] == pytest.approx(0.6 * 0.30 + 0.4 * 0.60)
    assert applied(out, "2545")[P_PLAY] == pytest.approx(0.42)
    assert applied(out, "2544")[P_PLAY] < 0.90
    assert applied(out, "2545")[P_PLAY] > 0.30


def test_the_questionable_blend_weights_sum_to_one():
    assert QUESTIONABLE_MODEL_WEIGHT + (1.0 - QUESTIONABLE_MODEL_WEIGHT) == 1.0
    assert QUESTIONABLE_MODEL_WEIGHT == pytest.approx(0.6)
    assert LEAGUE_QUESTIONABLE_PLAY_RATE == pytest.approx(0.60)
    assert DEFAULT_POLICY.probability(
        "questionable", LEAGUE_QUESTIONABLE_PLAY_RATE
    ) == pytest.approx(LEAGUE_QUESTIONABLE_PLAY_RATE)


@pytest.mark.parametrize("model_probability", [0.05, 0.40, 0.75, 0.95, 0.999, 1.0])
def test_probable_is_a_floor_and_never_a_haircut(model_probability):
    """a team saying 'expected to play' must never be able to LOWER a projection."""
    frame = predictions([model_probability])
    report = statuses([("2544", "probable", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    result = applied(out, "2544")[P_PLAY]
    assert result >= model_probability - 1e-12
    assert result == pytest.approx(
        min(max(model_probability,
                PROBABLE_MODEL_WEIGHT * model_probability + PROBABLE_SHIFT), 1.0)
    )
    assert 0.0 <= result <= 1.0


def test_probable_lifts_a_bench_player_by_the_documented_amount():
    frame = predictions([0.40])
    report = statuses([("2544", "probable", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.49)


@pytest.mark.parametrize("status", ["available", "day_to_day", "unknown", "", "nonsense"])
def test_passthrough_statuses_leave_the_model_alone(status):
    frame = predictions([0.71])
    report = statuses([("2544", status, "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert row[P_PLAY] == pytest.approx(0.71)
    assert row[OVERRIDE_REASON] is None
    assert row["E_PTS"] == pytest.approx(0.71 * 24.0)


def test_an_unlisted_player_is_untouched():
    frame = predictions([0.66, 0.44])
    report = statuses([("999999", "out", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert out[P_PLAY].tolist() == pytest.approx([0.66, 0.44])
    assert out[OVERRIDE_REASON].isna().all()


def test_source_wording_is_normalised_onto_the_vocabulary():
    frame = predictions([0.80, 0.80, 0.80])
    report = statuses([
        ("2544", "Game Time Decision", "2026-03-01 12:00"),
        ("2545", "OUT FOR SEASON", "2026-03-01 12:00"),
        ("2546", "G-League", "2026-03-01 12:00"),
    ])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert normalise_status("Game Time Decision") == "questionable"
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.6 * 0.80 + 0.4 * 0.60)
    assert applied(out, "2545")[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert applied(out, "2546")[P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_the_unconditional_stats_are_recomputed_from_the_overridden_probability():
    frame = predictions([0.93])
    report = statuses([("2544", "out", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert row["E_PTS"] == pytest.approx(OUT_PROBABILITY * 24.0)
    assert row["E_MIN"] == pytest.approx(OUT_PROBABILITY * 34.0)
    assert row["E_PTS"] == pytest.approx(row[P_PLAY] * row["E_PTS_COND"])
    assert row["E_MIN"] == pytest.approx(row[P_PLAY] * row["E_MIN_COND"])


def test_the_conditional_stats_and_quantiles_are_untouched():
    """being less likely to play does not change how good the night would be."""
    frame = predictions([0.93])
    report = statuses([("2544", "doubtful", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert row["E_PTS_COND"] == pytest.approx(24.0)
    assert row["E_MIN_COND"] == pytest.approx(34.0)
    assert (row["Q10_PTS"], row["Q50_PTS"], row["Q90_PTS"]) == (16.0, 24.0, 33.0)


def test_the_model_probability_survives_on_every_row():
    frame = predictions([0.93, 0.55])
    report = statuses([("2544", "out", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert out[P_PLAY_MODEL].tolist() == pytest.approx([0.93, 0.55])
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert applied(out, "2545")[P_PLAY] == pytest.approx(0.55)


def test_the_report_timestamp_is_carried_onto_the_overridden_row():
    frame = predictions([0.93])
    report = statuses([("2544", "out", "2026-03-01 12:34")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert pd.Timestamp(row[STATUS_CAPTURED_AT]) == pd.Timestamp("2026-03-01 12:34")
    assert row[STATUS_NORMALIZED] == "out"


def test_a_report_captured_after_the_boundary_does_not_apply():
    """THE LEAKAGE CASE. a T-24h backtest must not read the T-60m report."""
    frame = predictions([0.93])
    report = statuses([("2544", "out", "2026-03-01 19:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    row = applied(out, "2544")
    assert row[P_PLAY] == pytest.approx(0.93)
    assert row[OVERRIDE_REASON] is None
    assert row["E_PTS"] == pytest.approx(0.93 * 24.0)


def test_a_report_captured_exactly_at_the_boundary_does_not_apply():
    """the boundary is exclusive, matching the training cutoff's own convention."""
    frame = predictions([0.93])
    report = statuses([("2544", "out", str(AS_OF))])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)


def test_the_newest_admissible_report_wins():
    report = statuses([
        ("2544", "questionable", "2026-03-01 09:00"),
        ("2544", "out", "2026-03-01 11:00"),
        ("2544", "available", "2026-03-01 13:00"),
        ("2544", "out", "2026-03-01 21:00"),
    ])
    latest = latest_statuses(report, AS_OF)
    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)
    assert len(latest) == 1
    assert latest.iloc[0]["captured_at"] == pd.Timestamp("2026-03-01 13:00")
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)


def test_without_a_boundary_every_report_is_admissible():
    report = statuses([("2544", "out", "2099-01-01 00:00")])
    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, None)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_a_four_day_old_out_report_has_expired():
    report = statuses([("2544", "out", "2026-02-25 18:00")])

    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)

    assert REPORT_MAX_AGE_HOURS == 72.0
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)
    assert pd.isna(applied(out, "2544")[OVERRIDE_REASON])


def test_a_one_day_old_out_report_still_applies():
    report = statuses([("2544", "out", "2026-02-28 18:00")])

    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)

    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert applied(out, "2544")[OVERRIDE_REASON] == reason_for("out")


def test_a_newer_clearance_supersedes_an_older_out():
    report = statuses([
        ("2544", "out", "2026-03-01 06:00"),
        ("2544", "cleared", "2026-03-01 12:00"),
    ])

    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)

    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)
    assert pd.isna(applied(out, "2544")[OVERRIDE_REASON])
    assert pd.isna(applied(out, "2544")[STATUS_NORMALIZED])


def test_max_age_none_disables_expiry():
    report = statuses([("2544", "out", "2026-02-01 18:00")])

    out = apply_status_overrides(
        predictions([0.93]), report, DEFAULT_POLICY, AS_OF, max_age_hours=None,
    )

    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_without_a_boundary_no_report_expires():
    report = statuses([("2544", "out", "2020-01-01 00:00")])

    latest = latest_statuses(report, None)
    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, None)

    assert len(latest) == 1
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_timezone_aware_reports_compare_against_a_naive_boundary():
    """postgres hands back TIMESTAMPTZ; a csv has no zone."""
    report = statuses([("2544", "out", "2026-03-01T12:00:00+00:00")])
    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_an_unparseable_timestamp_drops_the_report():
    report = statuses([("2544", "out", "not a date")])
    out = apply_status_overrides(predictions([0.93]), report, DEFAULT_POLICY, AS_OF)
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)


def test_no_statuses_frame_is_an_identity_on_every_value():
    frame = predictions([0.93, 0.55])
    out = apply_status_overrides(frame, None, DEFAULT_POLICY, AS_OF)
    for column in (P_PLAY, "E_PTS", "E_PTS_COND", "E_MIN", "E_MIN_COND"):
        assert out[column].tolist() == pytest.approx(frame[column].tolist())
    assert out[OVERRIDE_REASON].isna().all()
    assert out[P_PLAY_MODEL].tolist() == pytest.approx(frame[P_PLAY].tolist())


def test_an_empty_statuses_frame_is_an_identity_on_every_value():
    frame = predictions([0.93])
    out = apply_status_overrides(frame, statuses([]), DEFAULT_POLICY, AS_OF)
    assert out[P_PLAY].tolist() == pytest.approx([0.93])
    assert out[OVERRIDE_REASON].isna().all()


def test_the_input_frame_is_not_mutated():
    frame = predictions([0.93])
    before = frame.copy()
    apply_status_overrides(frame, statuses([("2544", "out", "2026-03-01 12:00")]),
                           DEFAULT_POLICY, AS_OF)
    pd.testing.assert_frame_equal(frame, before)


def test_a_statuses_frame_missing_a_column_is_refused():
    report = statuses([("2544", "out", "2026-03-01 12:00")]).drop(columns=["captured_at"])
    with pytest.raises(ValueError, match="captured_at"):
        apply_status_overrides(predictions([0.9]), report, DEFAULT_POLICY, AS_OF)


def test_a_frame_without_probabilities_is_refused():
    with pytest.raises(ValueError, match=P_PLAY):
        apply_status_overrides(predictions([0.9]).drop(columns=[P_PLAY]), None)


def test_integer_player_ids_still_match_text_report_ids():
    """the silent-no-match failure: TEXT nba_player_id against int64 PLAYER_ID."""
    frame = predictions([0.93])
    frame["PLAYER_ID"] = np.array([2544], dtype="int64")
    report = statuses([("2544", "out", "2026-03-01 12:00")])
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)
    assert out.iloc[0][P_PLAY] == pytest.approx(OUT_PROBABILITY)


def test_the_policy_is_substitutable_without_touching_the_module():
    policy = StatusPolicy(questionable_prior=0.80, questionable_model_weight=0.5)
    out = apply_status_overrides(
        predictions([0.40]),
        statuses([("2544", "questionable", "2026-03-01 12:00")]),
        policy, AS_OF,
    )
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.5 * 0.40 + 0.5 * 0.80)
    assert policy.as_dict()["questionable_prior"] == 0.80


def test_the_policy_returns_none_for_anything_it_does_not_govern():
    assert DEFAULT_POLICY.probability("available", 0.5) is None
    assert DEFAULT_POLICY.probability("day_to_day", 0.5) is None
    assert DEFAULT_POLICY.probability("", 0.5) is None
    assert DEFAULT_POLICY.probability("out", 0.5) == pytest.approx(OUT_PROBABILITY)


def test_the_summary_reports_the_shift_per_reason():
    frame = predictions([0.93, 0.88, 0.30])
    report = statuses([
        ("2544", "out", "2026-03-01 12:00"),
        ("2545", "doubtful", "2026-03-01 12:00"),
        ("2546", "probable", "2026-03-01 12:00"),
    ])
    summary = override_summary(apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF))
    by_reason = summary.set_index("reason")
    assert set(by_reason.index) == {"status_out", "status_doubtful", "status_probable"}
    assert by_reason.loc["status_out", "mean_model_p"] == pytest.approx(0.93)
    assert by_reason.loc["status_out", "mean_override_p"] == pytest.approx(OUT_PROBABILITY)
    assert int(by_reason["rows"].sum()) == 3


def test_the_summary_is_empty_when_nothing_was_overridden():
    summary = override_summary(apply_status_overrides(predictions([0.9]), None))
    assert summary.empty


# ---- game-scoped resolution and unavailable expiry (MODEL.md 20.2) ----

GAME_A = "0022500123"
GAME_B = "0022500124"
OFFICIAL = "nba_official"
CBS = "cbssports"
SWITCHES_ON = {"expire_unavailable": False, "game_scoped": True}


def scoped_statuses(rows: list[tuple[str, str, str, str | None, str]]) -> pd.DataFrame:
    """(player id, status, captured_at, nba_game_id, source) -> a statuses frame."""
    return pd.DataFrame(
        rows,
        columns=["nba_player_id", "status_normalized", "captured_at", "nba_game_id",
                 "source"],
    )


def two_game_predictions() -> pd.DataFrame:
    """one player scheduled in two games, as a multi-day window scores him."""
    frame = predictions([0.93, 0.93], player_ids=["2544", "2544"])
    frame["GAME_ID"] = [GAME_A, GAME_B]
    return frame


def row_for_game(frame: pd.DataFrame, game_id: str) -> pd.Series:
    return frame[frame["GAME_ID"] == game_id].iloc[0]


def pre_switch_latest_statuses(
    statuses: pd.DataFrame, as_of: pd.Timestamp | None, max_age_hours: float | None
) -> pd.DataFrame:
    """the resolution as it was before the switches existed, kept verbatim as a pin."""
    frame = statuses.copy()
    frame["nba_player_id"] = frame["nba_player_id"].astype(str)
    frame[STATUS_NORMALIZED] = frame["status_normalized"].map(normalise_status)
    frame["captured_at"] = pd.to_datetime(
        frame["captured_at"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    frame = frame[frame["captured_at"].notna()]
    if as_of is not None:
        boundary = pd.Timestamp(as_of)
        frame = frame[frame["captured_at"] < boundary]
        if max_age_hours is not None:
            frame = frame[
                frame["captured_at"] >= boundary - pd.Timedelta(hours=max_age_hours)
            ]
    return (
        frame.sort_values("captured_at")
        .drop_duplicates("nba_player_id", keep="last")
        .reset_index(drop=True)
    )


PIN_REPORTS = [
    ("2544", "questionable", "2026-03-01 09:00", None, CBS),
    ("2544", "out", "2026-03-01 11:00", GAME_B, OFFICIAL),
    ("2545", "out", "2026-02-25 18:00", None, CBS),
    ("2546", "doubtful", "2026-03-01 10:00", GAME_A, OFFICIAL),
    ("2546", "probable", "2026-03-01 14:00", None, CBS),
    ("2547", "out", "2026-03-01 06:00", None, CBS),
    ("2547", "cleared", "2026-03-01 12:00", None, CBS),
    ("2548", "suspended", "2026-03-01 19:00", None, CBS),
]


def test_the_switch_defaults_are_the_frozen_behaviour():
    # act + assert
    assert config.EXPIRE_UNAVAILABLE_STATUSES is True
    assert config.GAME_SCOPED_STATUS_RESOLUTION is False
    assert OFFICIAL_PRECEDENCE_HOURS == 6.0


def test_default_resolution_matches_the_pre_switch_algorithm():
    # arrange
    report = scoped_statuses(PIN_REPORTS)

    # act
    latest = latest_statuses(report, AS_OF)
    expected = pre_switch_latest_statuses(report, AS_OF, REPORT_MAX_AGE_HOURS)

    # assert
    columns = ["nba_player_id", STATUS_NORMALIZED, "captured_at"]
    pd.testing.assert_frame_equal(latest[columns], expected[columns])


def test_default_overrides_are_pinned_on_a_mixed_fixture():
    # arrange
    frame = predictions([0.93, 0.80, 0.70, 0.60, 0.50],
                        player_ids=["2544", "2545", "2546", "2547", "2548"])
    report = scoped_statuses(PIN_REPORTS)

    # act
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF)

    # assert
    assert out[P_PLAY].tolist() == pytest.approx(
        [OUT_PROBABILITY, 0.80, 0.85 * 0.70 + 0.15, 0.60, 0.50]
    )
    assert out[OVERRIDE_REASON].tolist() == [
        "status_out", None, "status_probable", None, None,
    ]
    assert out[STATUS_SCOPE].tolist() == ["game", None, "general", None, None]
    assert out[STATUS_SOURCE].tolist() == [OFFICIAL, None, CBS, None, None]


def test_switches_off_ignore_the_game_a_report_is_for():
    # arrange
    report = scoped_statuses([("2544", "out", "2026-03-01 12:00", GAME_A, OFFICIAL)])

    # act
    out = apply_status_overrides(two_game_predictions(), report, DEFAULT_POLICY, AS_OF)

    # assert
    assert out[P_PLAY].tolist() == pytest.approx([OUT_PROBABILITY, OUT_PROBABILITY])


@pytest.mark.parametrize("status", ["out", "suspended", "g_league"])
def test_a_four_day_old_unavailable_status_still_applies_with_the_switch_on(status):
    # arrange
    report = scoped_statuses([("2544", status, "2026-02-25 18:00", None, CBS)])

    # act
    out = apply_status_overrides(
        predictions([0.93]), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert applied(out, "2544")[OVERRIDE_REASON] == reason_for(status)


@pytest.mark.parametrize("status", ["questionable", "doubtful", "probable"])
def test_a_four_day_old_game_designation_expires_with_the_switch_on(status):
    # arrange
    report = scoped_statuses([("2544", status, "2026-02-25 18:00", None, CBS)])

    # act
    out = apply_status_overrides(
        predictions([0.93]), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.93)
    assert pd.isna(applied(out, "2544")[OVERRIDE_REASON])


def test_an_expired_questionable_does_not_let_an_older_out_resurface():
    # arrange
    report = scoped_statuses([
        ("2544", "out", "2026-02-24 18:00", None, CBS),
        ("2544", "questionable", "2026-02-25 18:00", None, CBS),
    ])

    # act
    on = apply_status_overrides(
        predictions([0.93]), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )
    expiry_only = apply_status_overrides(
        predictions([0.93]), report, DEFAULT_POLICY, AS_OF, expire_unavailable=False
    )

    # assert
    assert applied(on, "2544")[P_PLAY] == pytest.approx(0.93)
    assert applied(expiry_only, "2544")[P_PLAY] == pytest.approx(0.93)


def test_a_game_scoped_out_does_not_touch_the_players_other_game():
    # arrange
    report = scoped_statuses([("2544", "out", "2026-03-01 12:00", GAME_A, OFFICIAL)])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert row_for_game(out, GAME_A)[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert row_for_game(out, GAME_B)[P_PLAY] == pytest.approx(0.93)
    assert pd.isna(row_for_game(out, GAME_B)[OVERRIDE_REASON])
    assert row_for_game(out, GAME_B)["E_PTS"] == pytest.approx(
        row_for_game(two_game_predictions(), GAME_B)["E_PTS"]
    )


def test_a_general_out_applies_to_every_game():
    # arrange
    report = scoped_statuses([("2544", "out", "2026-03-01 12:00", None, CBS)])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert out[P_PLAY].tolist() == pytest.approx([OUT_PROBABILITY, OUT_PROBABILITY])
    assert out[STATUS_SCOPE].tolist() == ["general", "general"]


def test_a_game_report_is_preferred_over_a_newer_general_one_for_that_game_only():
    # arrange
    report = scoped_statuses([
        ("2544", "doubtful", "2026-03-01 10:00", GAME_A, OFFICIAL),
        ("2544", "out", "2026-03-01 12:00", None, CBS),
    ])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert row_for_game(out, GAME_A)[P_PLAY] == pytest.approx(DOUBTFUL_PROBABILITY)
    assert row_for_game(out, GAME_A)[STATUS_SCOPE] == "game"
    assert row_for_game(out, GAME_B)[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert row_for_game(out, GAME_B)[STATUS_SCOPE] == "general"


@pytest.mark.parametrize("official_game", [GAME_A, None])
def test_an_official_out_beats_a_cbs_questionable_newer_within_six_hours(official_game):
    # arrange
    report = scoped_statuses([
        ("2544", "out", "2026-03-01 08:00", official_game, OFFICIAL),
        ("2544", "questionable", "2026-03-01 14:00", None, CBS),
    ])
    frame = predictions([0.93])

    # act
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON)

    # assert
    assert applied(out, "2544")[P_PLAY] == pytest.approx(OUT_PROBABILITY)
    assert applied(out, "2544")[STATUS_SOURCE] == OFFICIAL


@pytest.mark.parametrize("official_game", [GAME_A, None])
def test_an_official_out_loses_to_a_cbs_report_seven_hours_newer(official_game):
    # arrange
    report = scoped_statuses([
        ("2544", "out", "2026-03-01 08:00", official_game, OFFICIAL),
        ("2544", "questionable", "2026-03-01 15:00", None, CBS),
    ])
    frame = predictions([0.93])

    # act
    out = apply_status_overrides(frame, report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON)

    # assert
    assert applied(out, "2544")[P_PLAY] == pytest.approx(0.6 * 0.93 + 0.4 * 0.60)
    assert applied(out, "2544")[STATUS_SOURCE] == CBS
    assert applied(out, "2544")[STATUS_SCOPE] == "general"


def test_the_audit_columns_record_the_deciding_scope_and_source():
    # arrange
    report = scoped_statuses([("2544", "out", "2026-03-01 12:00", GAME_A, OFFICIAL)])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert row_for_game(out, GAME_A)[STATUS_SCOPE] == "game"
    assert row_for_game(out, GAME_A)[STATUS_SOURCE] == OFFICIAL
    assert pd.isna(row_for_game(out, GAME_B)[STATUS_SCOPE])
    assert pd.isna(row_for_game(out, GAME_B)[STATUS_SOURCE])
    assert override_provenance_counts(out) == {
        "scope": {"game": 1}, "source": {OFFICIAL: 1},
    }


def test_absent_game_and_source_columns_read_as_general_and_unknown():
    # arrange
    report = statuses([("2544", "out", "2026-03-01 12:00")])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert out[P_PLAY].tolist() == pytest.approx([OUT_PROBABILITY, OUT_PROBABILITY])
    assert out[STATUS_SCOPE].tolist() == ["general", "general"]
    assert out[STATUS_SOURCE].tolist() == ["unknown", "unknown"]


@pytest.mark.parametrize("game_id, source", [(GAME_A, OFFICIAL), (None, CBS)])
@pytest.mark.parametrize("captured_at", [str(AS_OF), "2026-03-01 19:00"])
def test_the_boundary_refuses_reports_at_or_after_as_of_in_every_scope(
    game_id, source, captured_at
):
    # arrange
    report = scoped_statuses([("2544", "out", captured_at, game_id, source)])

    # act
    out = apply_status_overrides(
        two_game_predictions(), report, DEFAULT_POLICY, AS_OF, **SWITCHES_ON
    )

    # assert
    assert out[P_PLAY].tolist() == pytest.approx([0.93, 0.93])
    assert out[OVERRIDE_REASON].isna().all()


def test_resolve_statuses_returns_one_row_per_player_and_game():
    # arrange
    report = scoped_statuses([
        ("2544", "out", "2026-03-01 12:00", GAME_A, OFFICIAL),
        ("2545", "questionable", "2026-03-01 12:00", None, CBS),
    ])

    # act
    resolved = resolve_statuses(
        report, AS_OF, pd.Series([GAME_A, GAME_B]), game_scoped=True
    )

    # assert
    keys = sorted(zip(resolved["nba_player_id"], resolved["GAME_ID"]))
    assert keys == [("2544", GAME_A), ("2545", GAME_A), ("2545", GAME_B)]
