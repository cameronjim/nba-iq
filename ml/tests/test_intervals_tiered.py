from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fnba_ml.config import TIER_ORDER
from fnba_ml.eval_counts import (
    ENDPOINT_COVERAGE,
    ENDPOINT_WIDTH,
    VARIANT_POOLED,
    VARIANT_TIERED,
    interval_endpoints,
)
from fnba_ml.intervals import (
    MIN_TIER_ROWS,
    POOLED_KEY,
    QuantileOffsets,
    apply_quantiles_by_tier,
    attach_quantiles_by_tier,
    fallback_tiers,
    fit_residual_quantiles_by_tier,
    tiered_quantiles_as_dict,
    tiered_quantiles_from_dict,
)

STAR = "star (>=30)"
BENCH = "bench (10-20)"
FRINGE = "fringe (<10)"


def residual_frame(spreads: dict[str, tuple[float, int]], seed: int = 17) -> pd.DataFrame:
    """tier -> (residual sd, rows), with a constant point estimate of 10."""
    rng = np.random.default_rng(seed)
    parts = [
        pd.DataFrame({
            "MIN_TIER": tier,
            "PTS_HAT": 10.0,
            "PTS": 10.0 + rng.normal(0.0, sd, n),
        })
        for tier, (sd, n) in spreads.items()
    ]
    return pd.concat(parts, ignore_index=True)


def width(offsets: QuantileOffsets) -> float:
    return offsets.offsets[-1] - offsets.offsets[0]


def test_a_wide_tier_gets_wider_offsets_than_a_narrow_one():
    # arrange
    frame = residual_frame({STAR: (6.0, 400), FRINGE: (0.5, 400)})

    # act
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT")

    # assert
    assert width(by_tier[STAR]) > 5.0 * width(by_tier[FRINGE])
    assert width(by_tier[FRINGE]) < width(by_tier[POOLED_KEY]) < width(by_tier[STAR])


def test_a_tier_below_the_row_floor_falls_back_to_the_pooled_offsets():
    # arrange
    frame = residual_frame({STAR: (6.0, 400), BENCH: (1.0, MIN_TIER_ROWS - 1)})

    # act
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT")

    # assert
    assert by_tier[BENCH] is by_tier[POOLED_KEY]
    assert by_tier[STAR] is not by_tier[POOLED_KEY]
    assert set(TIER_ORDER) <= set(by_tier)
    assert BENCH in fallback_tiers(by_tier) and STAR not in fallback_tiers(by_tier)


def test_exactly_the_row_floor_is_enough_for_a_tier_of_its_own():
    # arrange
    frame = residual_frame({STAR: (6.0, 400), BENCH: (1.0, MIN_TIER_ROWS)})

    # act
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT")

    # assert
    assert by_tier[BENCH] is not by_tier[POOLED_KEY]
    assert by_tier[BENCH].n == MIN_TIER_ROWS


def test_tiered_quantiles_never_cross_even_from_unsorted_hand_offsets():
    # arrange
    bad = QuantileOffsets("PTS", (0.1, 0.5, 0.9), (3.0, 0.0, -3.0), n=500)
    pooled = QuantileOffsets("PTS", (0.1, 0.5, 0.9), (-1.0, 0.0, 1.0), n=1000)
    by_tier = {POOLED_KEY: pooled, STAR: bad}
    tiers = np.array([STAR, STAR, FRINGE, "never seen"], dtype=object)

    # act
    bands = apply_quantiles_by_tier(np.array([0.5, 20.0, 5.0, 5.0]), tiers, by_tier)

    # assert
    assert (bands[0.1] <= bands[0.5]).all()
    assert (bands[0.5] <= bands[0.9]).all()
    assert (bands[0.1] >= 0.0).all()
    np.testing.assert_allclose(bands[0.9][2:], [6.0, 6.0])


def test_fitted_tiered_quantiles_are_non_crossing_on_every_row():
    # arrange
    frame = residual_frame({STAR: (6.0, 400), BENCH: (2.0, 300), FRINGE: (0.5, 50)})
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT")

    # act
    out = attach_quantiles_by_tier(frame, frame["PTS_HAT"], by_tier)

    # assert
    assert (out["Q10_PTS"] <= out["Q50_PTS"]).all()
    assert (out["Q50_PTS"] <= out["Q90_PTS"]).all()


def test_the_tier_is_derived_from_the_tier_basis_when_the_column_is_absent():
    # arrange
    frame = residual_frame({STAR: (6.0, 400)}).drop(columns=["MIN_TIER"])
    frame["roll10_MIN"] = 34.0

    # act
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT")

    # assert
    assert by_tier[STAR] is not by_tier[POOLED_KEY]
    assert by_tier[STAR].n == 400


def test_the_tiered_mapping_round_trips_through_the_metadata_dict():
    # arrange
    frame = residual_frame({STAR: (6.0, 400), BENCH: (2.0, 300), FRINGE: (0.5, 50)})
    by_tier = fit_residual_quantiles_by_tier(frame, "PTS", "PTS_HAT",
                                             window=("2025-01-01", "2025-01-29"))

    # act
    payload = json.loads(json.dumps(tiered_quantiles_as_dict(by_tier)))
    restored = tiered_quantiles_from_dict(payload)

    # assert
    assert set(restored) == set(by_tier)
    assert payload["tier_basis"] == "roll10_MIN"
    assert payload["min_tier_rows"] == MIN_TIER_ROWS
    assert set(payload["fallback"]) == set(fallback_tiers(by_tier))
    for tier, offsets in by_tier.items():
        assert restored[tier].as_dict() == offsets.as_dict()
    assert restored[FRINGE] is restored[POOLED_KEY]
    assert restored[STAR] is not restored[POOLED_KEY]


def test_interval_endpoints_by_hand():
    # arrange
    pooled = QuantileOffsets("PTS", (0.1, 0.5, 0.9), (-1.0, 0.0, 1.0), n=1000)
    star = QuantileOffsets("PTS", (0.1, 0.5, 0.9), (-3.0, 0.0, 3.0), n=400)
    tiered = {POOLED_KEY: pooled, STAR: star, FRINGE: pooled}
    frame = pd.DataFrame({
        "MIN_TIER": [STAR, STAR, FRINGE, FRINGE],
        "PTS_HAT": [10.0, 10.0, 0.5, 2.0],
        # star rows miss by 2 and by 4, fringe rows by 0.5 and by 2
        "PTS": [12.0, 6.0, 1.0, 0.0],
    })

    # act
    out = interval_endpoints(frame, {"PTS": "PTS_HAT"}, {"PTS": pooled}, {"PTS": tiered})

    # assert
    get = out.set_index(["variant", "endpoint", "cohort"])["value"]
    assert get[(VARIANT_POOLED, ENDPOINT_COVERAGE, POOLED_KEY)] == pytest.approx(0.25)
    assert get[(VARIANT_TIERED, ENDPOINT_COVERAGE, POOLED_KEY)] == pytest.approx(0.5)
    assert get[(VARIANT_TIERED, ENDPOINT_COVERAGE, STAR)] == pytest.approx(0.5)
    assert get[(VARIANT_POOLED, ENDPOINT_COVERAGE, STAR)] == pytest.approx(0.0)
    # the fringe point 0.5 is floored at zero on the low side, so its width is 1.5
    assert get[(VARIANT_POOLED, ENDPOINT_WIDTH, FRINGE)] == pytest.approx((1.5 + 2.0) / 2)
    assert get[(VARIANT_TIERED, ENDPOINT_WIDTH, STAR)] == pytest.approx(6.0)
    assert get[(VARIANT_TIERED, ENDPOINT_WIDTH, POOLED_KEY)] == pytest.approx(
        (6.0 + 6.0 + 1.5 + 2.0) / 4
    )
    assert out.set_index(["variant", "endpoint", "cohort"]).loc[
        (VARIANT_POOLED, ENDPOINT_COVERAGE, POOLED_KEY), "n"
    ] == 4


def test_train_writes_no_tiered_block_unless_asked():
    # arrange
    import train  # noqa: PLC0415

    # act
    default = train.parse_args([])
    asked = train.parse_args(["--tiered-quantiles"])

    # assert
    assert default.tiered_quantiles is False
    assert asked.tiered_quantiles is True


def test_holdout_quantiles_by_tier_mirrors_the_pooled_holdout(features_status):
    # arrange
    import train  # noqa: PLC0415
    from fnba_ml.features import available_features  # noqa: PLC0415

    feats = available_features(features_status)
    cutoff = pd.Timestamp("2024-12-29")

    # act
    pooled = train.holdout_quantiles(features_status, feats, cutoff, 28)
    tiered = train.holdout_quantiles_by_tier(features_status, feats, cutoff, 28)

    # assert
    assert set(tiered) == set(pooled)
    for target, payload in tiered.items():
        assert payload["pooled"] == pooled[target].as_dict()
        restored = tiered_quantiles_from_dict(json.loads(json.dumps(payload)))
        assert set(TIER_ORDER) <= set(restored)


def test_the_report_pipeline_runs_end_to_end_on_the_fixture(features_status):
    # arrange
    import report_counts  # noqa: PLC0415

    # act
    counts, intervals, choices = report_counts.run(
        features_status, ("BLK",), ("MIN", "BLK")
    )

    # assert
    assert set(counts["origin"]) == {"O1 valid=2024-12"}
    assert set(intervals["variant"]) == {VARIANT_POOLED, VARIANT_TIERED}
    assert set(intervals["endpoint"]) == {ENDPOINT_COVERAGE, ENDPOINT_WIDTH}
    assert (intervals["value"] >= 0.0).all()
    assert list(choices["stat"]) == ["BLK"]
    assert not report_counts.origin_mean(counts).empty
