from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import run_p3_bracket as p3  # noqa: E402
from fnba_ml import config  # noqa: E402
from fnba_ml.box_context import attach_v6_features  # noqa: E402
from fnba_ml.matchup import attach_v4_features  # noqa: E402
from fnba_ml.models import PerMinuteRate, conditional_estimate  # noqa: E402
from fnba_ml.promotion import ENDPOINT_AVAILABILITY, ENDPOINT_MINUTES  # noqa: E402

FIXTURE_ORIGINS = [
    ("F1 valid=2024-11-16..30", "2024-11-16", "2024-11-30"),
    ("F2 valid=2024-12", "2024-12-01", "2024-12-28"),
]


@pytest.fixture(scope="module")
def v4_frame(
    features_status: pd.DataFrame, team_logs: pd.DataFrame, box_details: pd.DataFrame
) -> pd.DataFrame:
    # the v4 and v6 families both attached, as build_dataset.py writes them
    return attach_v6_features(attach_v4_features(features_status, team_logs), box_details)


@pytest.fixture(scope="module")
def scores(v4_frame: pd.DataFrame) -> p3.BracketScores:
    return p3.score_brackets(v4_frame, FIXTURE_ORIGINS)


def _loss_frame(losses: np.ndarray, endpoint: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(losses)
    return pd.DataFrame({
        "origin": np.repeat(["O1", "O2", "O3"], n // 3),
        "endpoint": endpoint,
        "row_key": [f"r{i}" for i in range(n)],
        "GAME_DATE": pd.to_datetime("2025-01-01")
        + pd.to_timedelta(rng.integers(0, 28, n), unit="D"),
        "cohort": "ALL",
        "loss": losses,
    })


def test_the_p3_bar_is_written_down() -> None:
    # act + assert
    assert config.P3_PROMOTION_FLOOR == 0.01
    assert config.P3_COHORT_REGRESSION_TOLERANCE == 0.01
    assert config.P3_V5_GATED_ENDPOINTS == ("availability_brier", "minutes_mae")
    assert config.P3_RATE_GATED_ENDPOINTS == ("cond_pts_mae", "uncond_pts_mae")
    assert p3.rate_endpoint("PTS", True) in config.P3_RATE_GATED_ENDPOINTS
    assert p3.rate_endpoint("PTS", False) in config.P3_RATE_GATED_ENDPOINTS


def test_parse_args_defaults_to_the_v4_dataset_and_dev_origins() -> None:
    # act
    args = p3.parse_args([])

    # assert
    assert args.dataset.name == "dataset_v4.parquet"
    assert args.version == "p3"
    assert not args.allow_overwrite


def test_both_comparisons_score_identical_rows(scores: p3.BracketScores) -> None:
    # act
    for name, (incumbent, candidate) in scores.losses.items():
        a = incumbent.sort_values(["endpoint", "cohort", "origin", "row_key"])
        b = candidate.sort_values(["endpoint", "cohort", "origin", "row_key"])

        # assert
        assert a["row_key"].tolist() == b["row_key"].tolist(), name
        assert set(incumbent["origin"]) == {o for o, *_ in FIXTURE_ORIGINS}
    v5_endpoints = set(scores.losses[p3.COMPARISON_V5][0]["endpoint"])
    rate_endpoints = set(scores.losses[p3.COMPARISON_RATE][0]["endpoint"])
    assert v5_endpoints == {"availability_brier", "minutes_mae", "uncond_pts_mae"}
    assert rate_endpoints == {
        p3.rate_endpoint(t, c) for t in config.RATE_MODEL_TARGETS for c in (True, False)
    }


def test_conditional_rate_rows_are_appearances_and_unconditional_are_every_row(
    scores: p3.BracketScores, v4_frame: pd.DataFrame
) -> None:
    # arrange
    incumbent = scores.losses[p3.COMPARISON_RATE][0]
    _, vstart, vend = FIXTURE_ORIGINS[1]
    valid = v4_frame[(v4_frame["GAME_DATE"] >= vstart) & (v4_frame["GAME_DATE"] <= vend)]

    # act
    cond = incumbent[(incumbent["endpoint"] == "cond_pts_mae")
                     & (incumbent["cohort"] == "ALL")
                     & (incumbent["origin"] == FIXTURE_ORIGINS[1][0])]
    uncond = incumbent[(incumbent["endpoint"] == "uncond_pts_mae")
                       & (incumbent["cohort"] == "ALL")
                       & (incumbent["origin"] == FIXTURE_ORIGINS[1][0])]

    # assert
    assert len(cond) == int((valid["PLAYED"] == 1).sum())
    assert len(uncond) == len(valid)


def test_rate_losses_are_absolute_errors_on_the_right_rows() -> None:
    # arrange
    valid = pd.DataFrame({"PLAYED": [1, 0, 1], "PTS": [10.0, 0.0, 4.0]})
    estimates = {"PTS": (np.array([8.0, 6.0, 5.0]), np.array([7.0, 1.0, 3.0]))}

    # act
    losses = p3.rate_losses(valid, estimates)

    # assert
    cond_loss, cond_rows = losses["cond_pts_mae"]
    uncond_loss, uncond_rows = losses["uncond_pts_mae"]
    np.testing.assert_allclose(cond_loss[cond_rows], [2.0, 1.0])
    np.testing.assert_allclose(uncond_loss[uncond_rows], [3.0, 1.0, 1.0])


def test_the_incumbent_rate_loss_is_the_champion_composition(
    v4_frame: pd.DataFrame,
) -> None:
    # arrange
    _, vstart, vend = FIXTURE_ORIGINS[1]
    train, valid = p3.split(v4_frame, vstart, vend)
    valid = valid.reset_index(drop=True)
    cutoff = pd.Timestamp(valid["GAME_DATE"].min())
    feats = p3.feature_set_columns(v4_frame, config.SERVED_FEATURE_SET)
    scored = p3.fit_and_score(train, valid, feats, cutoff)
    train_app = train[(train["PLAYED"] == 1) & (train["MIN"] > 0)]
    rate = PerMinuteRate("AST").fit(train_app)
    expected = conditional_estimate(scored["MIN_PRED"], rate.predict(valid))

    # act
    cond, _ = p3.minutes_propagated_estimate(scored, rate.predict(valid))

    # assert
    np.testing.assert_allclose(cond, expected)


def test_a_synthetic_win_is_promoted_and_a_synthetic_loss_is_not() -> None:
    # arrange
    rng = np.random.default_rng(17)
    base = rng.gamma(2.0, 2.0, 900)
    gates = config.P3_V5_GATED_ENDPOINTS
    incumbent = pd.concat([
        _loss_frame(base, ENDPOINT_AVAILABILITY, 1),
        _loss_frame(base, ENDPOINT_MINUTES, 2),
    ], ignore_index=True)
    win = incumbent.assign(loss=incumbent["loss"] * 0.95)
    loss = incumbent.assign(loss=incumbent["loss"] * 1.05)
    tiny = incumbent.assign(loss=incumbent["loss"] * 0.997)

    # act
    win_verdict, _ = p3.decide_comparison(incumbent, win, gates)
    loss_verdict, _ = p3.decide_comparison(incumbent, loss, gates)
    tiny_verdict, _ = p3.decide_comparison(incumbent, tiny, gates)

    # assert
    assert win_verdict.promoted
    assert all(d.clears for d in win_verdict.decisions)
    assert not loss_verdict.promoted
    assert "NOT PROMOTED" in loss_verdict.reason
    # significant but under the 1% floor
    assert all(d.ci_excludes_zero for d in tiny_verdict.decisions)
    assert not tiny_verdict.promoted


def test_a_regressing_cohort_blocks_a_p3_win() -> None:
    # arrange
    rng = np.random.default_rng(17)
    base = rng.gamma(2.0, 2.0, 900)
    gates = config.P3_RATE_GATED_ENDPOINTS
    all_rows = _loss_frame(base, "cond_pts_mae", 3)
    cohort = all_rows.head(90).assign(cohort="star (>=30)")
    incumbent = pd.concat([all_rows, cohort], ignore_index=True)
    candidate = incumbent.assign(loss=incumbent["loss"] * 0.90)
    candidate.loc[candidate["cohort"] == "star (>=30)", "loss"] = (
        incumbent.loc[incumbent["cohort"] == "star (>=30)", "loss"] * 1.10
    )

    # act
    verdict, gated = p3.decide_comparison(incumbent, candidate, gates)

    # assert
    assert any(d.clears for d in verdict.decisions)
    assert not verdict.promoted
    assert "star (>=30)" in verdict.reason
    assert bool(gated.loc[gated["cohort"] == "star (>=30)", "regresses"].iloc[0])


def test_non_gated_endpoints_never_clear() -> None:
    # arrange
    rng = np.random.default_rng(17)
    base = rng.gamma(2.0, 2.0, 900)
    incumbent = _loss_frame(base, "cond_reb_mae", 4)

    # act
    verdict, _ = p3.decide_comparison(
        incumbent, incumbent.assign(loss=base * 0.8), config.P3_RATE_GATED_ENDPOINTS
    )

    # assert
    assert not verdict.decisions[0].is_gate
    assert not verdict.promoted


def test_main_writes_every_report_and_refuses_a_second_look(
    v4_frame: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # arrange
    dataset = tmp_path / "dataset_v4.parquet"
    v4_frame.to_parquet(dataset)
    monkeypatch.setattr(p3, "DEV_ORIGINS", FIXTURE_ORIGINS)
    argv = ["--dataset", str(dataset), "--reports-dir", str(tmp_path),
            "--version", "fx"]

    # act
    code = p3.main(argv)

    # assert
    assert code == 0
    for suffix in ("_decision.csv", "_cohorts.csv", "_per_origin.csv",
                   "_cohorts_all_endpoints.csv", "_clip_rates.csv", ".md"):
        assert (tmp_path / f"fx_p3{suffix}").is_file(), suffix
    decision = pd.read_csv(tmp_path / "fx_p3_decision.csv")
    assert set(decision["comparison"]) == {
        p3.COMPARISON_V5, p3.COMPARISON_RATE, p3.COMPARISON_V6, p3.COMPARISON_RATE_V6,
    }
    assert set(decision.loc[decision["gate"], "endpoint"]) == {
        *config.P3_V5_GATED_ENDPOINTS, *config.P3_RATE_GATED_ENDPOINTS,
    }
    decided = decision[~decision["binding"]]
    assert set(decided["comparison"]) == set(config.P3_DECIDED_COMPARISONS)
    assert not decided["promoted"].any()
    assert decided["verdict"].str.startswith("REFERENCE ONLY").all()
    markdown = (tmp_path / "fx_p3.md").read_text(encoding="utf-8")
    assert "coherence_endpoints: available" in markdown
    coherence = pd.read_csv(tmp_path / "fx_p3_coherence.csv")
    assert {"variant", "endpoint", "cohort", "n", "mae", "origin", "family"} <= set(coherence.columns)
    assert set(coherence["family"]) == {
        "champion", p3.COMPARISON_RATE, p3.COMPARISON_RATE_V6,
    }
    with pytest.raises(SystemExit, match="one look per version"):
        p3.main(argv)
