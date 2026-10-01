from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest

import predict
from fnba_ml.config import BASE_FEATURE_COLS, FEATURE_COLS, PRODUCTION_TARGETS
from fnba_ml.intervals import QUANTILE_LEVELS, QuantileOffsets
from fnba_ml.models import BASE_MODEL_KIND, P_PLAY, AvailabilityModel, MinutesModel
from fnba_ml.overrides import (
    DEFAULT_POLICY,
    StatusPolicy,
    apply_status_overrides,
    latest_statuses,
)
from fnba_ml.scenarios import (
    enumerate_scenarios,
    pivotal_players,
    score_with_scenarios,
)
from predict import build_predictions, rebuild_context

CUTOFF = pd.Timestamp("2024-12-01")
E_MIN_COND = "E_MIN_COND"

# a questionable designation served at exactly p = 0.5, whatever the model says.
HALF_POLICY = StatusPolicy(questionable_model_weight=0.0, questionable_prior=0.5)


@pytest.fixture(scope="module")
def probe(features_status):
    """a small artifact trained on the synthetic fixture to CUTOFF."""
    frame = features_status.sort_values("GAME_DATE").reset_index(drop=True)
    train = frame[frame["GAME_DATE"] < CUTOFF]
    base = AvailabilityModel(kind=BASE_MODEL_KIND).fit(train, BASE_FEATURE_COLS, CUTOFF)
    model = AvailabilityModel().fit(train, FEATURE_COLS, CUTOFF)
    minutes = MinutesModel().fit(train[train["PLAYED"] == 1], FEATURE_COLS, CUTOFF)
    quantiles = {
        target: QuantileOffsets(target, QUANTILE_LEVELS, (-4.0, 0.0, 4.0), n=100).as_dict()
        for target in ("MIN", *PRODUCTION_TARGETS)
    }
    metadata = {
        "model_version": "probe",
        "feature_version": "v3",
        "production": {
            "rate_fallbacks": {t: 0.3 for t in PRODUCTION_TARGETS},
            "quantiles": quantiles,
        },
    }
    first_day = frame.loc[frame["GAME_DATE"] >= CUTOFF, "GAME_DATE"].min()
    slate = frame[frame["GAME_DATE"] == first_day].reset_index(drop=True)
    return {
        "frame": frame, "slate": slate, "base": base, "model": model,
        "minutes": minutes, "metadata": metadata,
        "as_of": pd.Timestamp(first_day),
    }


def _team_game(slate: pd.DataFrame, index: int = 0) -> pd.DataFrame:
    keys = slate[["GAME_ID", "TEAM_ID"]].drop_duplicates().iloc[index]
    rows = slate[(slate["GAME_ID"] == keys["GAME_ID"]) & (slate["TEAM_ID"] == keys["TEAM_ID"])]
    return rows.sort_values("tm_MIN", ascending=False)


def _report(players: list[str], status: str, captured_at: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({
        "nba_player_id": [str(p) for p in players],
        "status_normalized": [status] * len(players),
        "captured_at": [captured_at.isoformat()] * len(players),
    })


def _forced_run(probe, rows: pd.DataFrame, statuses, policy, forced) -> pd.DataFrame:
    rebuilt, _ = rebuild_context(
        rows, probe["base"], statuses, probe["as_of"], policy,
        forced_probabilities=forced,
    )
    scored = build_predictions(rebuilt, probe["model"], probe["minutes"], probe["metadata"])
    return apply_status_overrides(scored, statuses, policy, as_of=probe["as_of"])


def _score(probe, statuses, policy=DEFAULT_POLICY):
    return score_with_scenarios(
        probe["slate"], probe["base"], probe["model"], probe["minutes"],
        probe["metadata"], statuses, probe["as_of"], policy,
        rebuild=rebuild_context, score=build_predictions,
    )


def _write_artifact(probe, tmp_path):
    version = tmp_path / "models" / "probe"
    version.mkdir(parents=True)
    joblib.dump(probe["model"], version / "availability_model.joblib")
    joblib.dump(probe["minutes"], version / "minutes_model.joblib")
    joblib.dump(probe["base"], version / "base_availability_model.joblib")
    (version / "metadata.json").write_text(json.dumps(probe["metadata"]))
    dataset = tmp_path / "dataset.parquet"
    probe["frame"].to_parquet(dataset, index=False)
    return tmp_path / "models", dataset


def test_no_questionable_player_leaves_the_output_byte_identical(probe, tmp_path):
    # arrange
    models_dir, dataset = _write_artifact(probe, tmp_path)
    ruled_out = _team_game(probe["slate"]).iloc[0]["PLAYER_ID"]
    statuses = tmp_path / "statuses.csv"
    _report([ruled_out], "out", probe["as_of"] - pd.Timedelta(hours=2)).to_csv(
        statuses, index=False
    )
    common = [
        "--version", "probe", "--models-dir", str(models_dir), "--dataset", str(dataset),
        "--run-at", str(CUTOFF.date()), "--statuses", str(statuses),
        "--statuses-as-of", probe["as_of"].isoformat(), "--horizon", "none",
    ]
    default_out = tmp_path / "default.parquet"
    scenario_out = tmp_path / "scenarios.parquet"

    # act
    assert predict.main([*common, "--out", str(default_out)]) == 0
    assert predict.main([*common, "--out", str(scenario_out), "--scenarios"]) == 0

    # assert
    assert default_out.read_bytes() == scenario_out.read_bytes()
    audit = pd.read_parquet(predict.scenario_audit_path(scenario_out))
    assert audit.empty
    assert not predict.scenario_audit_path(default_out).exists()


def test_a_backups_minutes_are_the_average_of_the_two_forced_worlds(probe):
    # arrange
    rows = _team_game(probe["slate"])
    star, backup = str(rows.iloc[0]["PLAYER_ID"]), str(rows.iloc[1]["PLAYER_ID"])
    statuses = _report([star], "questionable", probe["as_of"] - pd.Timedelta(hours=2))
    plays = _forced_run(probe, rows, statuses, HALF_POLICY, {star: 1.0})
    sits = _forced_run(probe, rows, statuses, HALF_POLICY, {star: 0.02})
    m_plays = float(plays.loc[plays["PLAYER_ID"].astype(str) == backup, E_MIN_COND].iloc[0])
    m_sits = float(sits.loc[sits["PLAYER_ID"].astype(str) == backup, E_MIN_COND].iloc[0])

    # act
    mixed, audit = _score(probe, statuses, HALF_POLICY)

    # assert
    served = mixed.loc[mixed["PLAYER_ID"].astype(str) == backup, E_MIN_COND].iloc[0]
    # the two worlds must actually differ, or the identity below proves nothing.
    assert m_sits != pytest.approx(m_plays)
    assert served == pytest.approx(0.5 * m_plays + 0.5 * m_sits)
    assert audit["PLAYER_ID"].astype(str).tolist() == [star]
    assert audit["N_SCENARIOS"].iloc[0] == 2


def test_the_star_keeps_his_blended_p_play_and_his_plays_world_minutes(probe):
    # arrange
    rows = _team_game(probe["slate"])
    star = str(rows.iloc[0]["PLAYER_ID"])
    statuses = _report([star], "questionable", probe["as_of"] - pd.Timedelta(hours=2))
    plays = _forced_run(probe, rows, statuses, HALF_POLICY, {star: 1.0})
    single = _forced_run(probe, probe["slate"], statuses, HALF_POLICY, None)

    # act
    mixed, _ = _score(probe, statuses, HALF_POLICY)

    # assert
    own = mixed[mixed["PLAYER_ID"].astype(str) == star].iloc[0]
    assert own[P_PLAY] == pytest.approx(0.5)
    assert own[P_PLAY] == pytest.approx(
        single.loc[single["PLAYER_ID"].astype(str) == star, P_PLAY].iloc[0]
    )
    assert own[E_MIN_COND] == pytest.approx(
        plays.loc[plays["PLAYER_ID"].astype(str) == star, E_MIN_COND].iloc[0]
    )
    assert own["E_MIN"] == pytest.approx(0.5 * own[E_MIN_COND])


def test_two_pivotal_players_make_four_worlds_whose_weights_sum_to_one(probe):
    # arrange
    pivotal = pd.DataFrame({"PLAYER_ID": ["a", "b"], "p": [0.7, 0.4]})
    rows = _team_game(probe["slate"])
    stars = [str(p) for p in rows["PLAYER_ID"].iloc[:2]]
    statuses = _report(stars, "questionable", probe["as_of"] - pd.Timedelta(hours=2))

    # act
    scenarios = enumerate_scenarios(pivotal)
    _, audit = _score(probe, statuses)

    # assert
    assert len(scenarios) == 4
    assert sum(w for _, w in scenarios) == pytest.approx(1.0)
    assert dict((tuple(a.values()), w) for a, w in scenarios)[(1, 0)] == pytest.approx(0.42)
    assert sorted(audit["PLAYER_ID"].astype(str)) == sorted(stars)
    assert (audit["N_SCENARIOS"] == 4).all()
    weights = json.loads(audit["SCENARIO_WEIGHTS"].iloc[0])
    assert sum(w["weight"] for w in weights) == pytest.approx(1.0, abs=1e-5)


def test_quantiles_do_not_cross_after_mixing(probe):
    # arrange
    stars = []
    for index in range(4):
        rows = _team_game(probe["slate"], index)
        stars += [str(p) for p in rows["PLAYER_ID"].iloc[:2]]
    statuses = _report(stars, "questionable", probe["as_of"] - pd.Timedelta(hours=2))

    # act
    mixed, audit = _score(probe, statuses)

    # assert
    assert audit[["GAME_ID", "TEAM_ID"]].drop_duplicates().shape[0] == 4
    for target in ("MIN", *PRODUCTION_TARGETS):
        q10, q50, q90 = (mixed[f"Q{level}_{target}"].to_numpy() for level in ("10", "50", "90"))
        assert (q10 <= q50 + 1e-12).all() and (q50 <= q90 + 1e-12).all()
    assert (mixed["E_FGM"] <= mixed["E_FGA"] + 1e-12).all()
    assert (mixed["E_MIN"] <= mixed[E_MIN_COND] + 1e-12).all()


def test_a_doubtful_bench_player_is_not_pivotal(probe):
    # arrange
    rows = _team_game(probe["slate"])
    bench = rows[rows["tm_MIN"] < 20.0].sort_values("tm_USG").iloc[0]
    latest = latest_statuses(
        _report([bench["PLAYER_ID"]], "doubtful", probe["as_of"] - pd.Timedelta(hours=2)),
        probe["as_of"],
    )
    upcoming = probe["slate"][["GAME_ID", "TEAM_ID", "PLAYER_ID"]].assign(P_PLAY_MODEL=0.9)
    star_latest = latest_statuses(
        _report([rows.iloc[0]["PLAYER_ID"]], "doubtful", probe["as_of"] - pd.Timedelta(hours=2)),
        probe["as_of"],
    )

    # act
    bench_pivotal = pivotal_players(upcoming, latest, probe["slate"])
    star_pivotal = pivotal_players(upcoming, star_latest, probe["slate"])

    # assert
    assert bench_pivotal.empty
    assert star_pivotal["PLAYER_ID"].tolist() == [str(rows.iloc[0]["PLAYER_ID"])]
    assert star_pivotal["p"].iloc[0] == pytest.approx(0.10)


def test_a_report_captured_after_the_boundary_cannot_make_anyone_pivotal(probe):
    # arrange
    rows = _team_game(probe["slate"])
    star = str(rows.iloc[0]["PLAYER_ID"])
    late = _report([star], "questionable", probe["as_of"] + pd.Timedelta(minutes=5))
    baseline = _forced_run(probe, probe["slate"], None, DEFAULT_POLICY, None)

    # act
    mixed, audit = _score(probe, late)

    # assert
    assert audit.empty
    pd.testing.assert_frame_equal(mixed, baseline)


def test_forced_probabilities_reach_the_teammate_sums_and_default_to_off(probe):
    # arrange
    rows = _team_game(probe["slate"])
    star = str(rows.iloc[0]["PLAYER_ID"])

    # act
    default, audit = rebuild_context(rows, probe["base"], None, probe["as_of"])
    forced, forced_audit = rebuild_context(
        rows, probe["base"], None, probe["as_of"], forced_probabilities={star: 0.0}
    )

    # assert
    assert "CONTEXT_FORCED" not in audit.columns
    assert forced_audit["CONTEXT_FORCED"].sum() == 1
    star_p = float(audit.loc[audit["PLAYER_ID"].astype(str) == star, "P_CONTEXT"].iloc[0])
    gained = forced["exp_vacated_minutes"].to_numpy() - default["exp_vacated_minutes"].to_numpy()
    others = (rows["PLAYER_ID"].astype(str) != star).to_numpy()
    assert gained[others] == pytest.approx(np.full(others.sum(), star_p * rows.iloc[0]["tm_MIN"]))
    assert gained[~others] == pytest.approx([0.0])
