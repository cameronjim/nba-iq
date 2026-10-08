from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

import predict
import train
from fnba_ml import config, registry
from fnba_ml.cli import feature_set_version

CUTOFF = "2024-12-01"


@pytest.fixture(scope="module")
def v1_artifact(features_status: pd.DataFrame, tmp_path_factory) -> dict[str, Path]:
    """one v1 fit on the fixture, shared by the tests that read it."""
    root = tmp_path_factory.mktemp("v1")
    dataset = root / "dataset.parquet"
    features_status.to_parquet(dataset, index=False)
    models_dir = root / "models"
    code = train.main([
        "--dataset", str(dataset),
        "--feature-set", "v1",
        "--version", "testver",
        "--cutoff", CUTOFF,
        "--models-dir", str(models_dir),
    ])
    assert code == 0
    return {"dataset": dataset, "models_dir": models_dir, "dir": models_dir / "testver-v1"}


class TestArgs:
    def test_the_default_feature_set_is_the_served_one(self) -> None:
        # act
        args = train.parse_args([])

        # assert
        assert args.feature_set == config.SERVED_FEATURE_SET == "v3-honest"

    def test_v1_is_accepted(self) -> None:
        # act + assert
        assert train.parse_args(["--feature-set", "v1"]).feature_set == "v1"

    def test_an_unfrozen_feature_set_is_refused(self) -> None:
        # act + assert
        with pytest.raises(SystemExit):
            train.parse_args(["--feature-set", "v2-oracle"])


class TestArtifactNaming:
    def test_the_served_set_keeps_the_bare_version(self) -> None:
        # act + assert
        assert feature_set_version("20260818", config.SERVED_FEATURE_SET) == "20260818"

    def test_v1_gets_a_suffixed_directory(self) -> None:
        # act + assert
        assert feature_set_version("20260818", "v1") == "20260818-v1"


class TestCompanionCutoff:
    def test_a_shadow_inherits_the_served_cutoff(self, tmp_path: Path) -> None:
        # arrange
        served = tmp_path / "20990101"
        served.mkdir()
        (served / train.META_FILE).write_text(
            json.dumps({"training_window": {"cutoff": "2026-04-13"}}), encoding="utf-8"
        )
        args = train.parse_args(["--feature-set", "v1", "--models-dir", str(tmp_path)])
        frame = pd.DataFrame({"GAME_DATE": pd.to_datetime(["2026-06-01"])})

        # act
        cutoff, source = train.resolve_training_cutoff(args, frame, "20990101")

        # assert
        assert cutoff.date() == pd.Timestamp("2026-04-13").date()
        assert "20990101" in source

    def test_an_explicit_cutoff_wins(self, tmp_path: Path) -> None:
        # arrange
        args = train.parse_args([
            "--feature-set", "v1", "--cutoff", "2025-01-01", "--models-dir", str(tmp_path),
        ])
        frame = pd.DataFrame({"GAME_DATE": pd.to_datetime(["2026-06-01"])})

        # act
        cutoff, source = train.resolve_training_cutoff(args, frame, "20990101")

        # assert
        assert cutoff.date() == pd.Timestamp("2025-01-01").date()
        assert source == "--cutoff"

    def test_the_v3_artifact_cutoff_is_what_a_real_v7_fit_inherits(self) -> None:
        # act
        cutoff = train.companion_cutoff(train.WINDOW_REFERENCE_VERSION, config.MODELS_DIR)

        # assert
        assert cutoff is not None
        assert str(cutoff.date()) == "2026-04-13"


class TestV1Fit:
    def test_metadata_records_the_feature_set_and_36_columns(self, v1_artifact) -> None:
        # act
        metadata = json.loads((v1_artifact["dir"] / train.META_FILE).read_text("utf-8"))

        # assert
        assert metadata["feature_set"] == "v1"
        assert metadata["model_version"] == "testver-v1"
        assert metadata["feature_cols"] == config.BASE_FEATURE_COLS
        assert len(metadata["feature_cols"]) == 36
        assert metadata["training_window"]["cutoff"] == CUTOFF
        assert metadata["context"]["skipped"] is True
        assert "base_artifact_checksum" not in metadata

    def test_no_base_model_is_written(self, v1_artifact) -> None:
        # act
        files = {p.name for p in v1_artifact["dir"].iterdir()}

        # assert
        assert train.BASE_MODEL_FILE not in files
        assert {train.MODEL_FILE, train.MINUTES_FILE, train.EWMA_FILE} <= files

    def test_both_models_see_no_teammate_column(self, v1_artifact) -> None:
        # act
        model, minutes, base, _ = predict.load_version("testver-v1", v1_artifact["models_dir"])

        # assert
        assert base is None
        for fitted in (model, minutes):
            assert fitted.feature_cols == config.BASE_FEATURE_COLS
            assert not set(fitted.feature_cols) & set(config.TEAMMATE_FEATURE_COLS)

    def test_the_registry_entry_records_the_feature_set_and_verifies(self, v1_artifact) -> None:
        # arrange
        models_dir = v1_artifact["models_dir"]

        # act
        entry = registry.find("testver-v1", models_dir / "registry.json")

        # assert
        assert entry is not None
        assert entry["feature_set"] == "v1"
        assert entry["n_features"] == 36
        assert registry.verify_artifacts("testver-v1", models_dir) == []


class TestV1Predict:
    def test_predict_skips_rebuild_context_and_scores(
        self, v1_artifact, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # arrange
        out = tmp_path / "predictions.parquet"
        caplog.set_level(logging.INFO, logger="predict")

        # act
        code = predict.main([
            "--dataset", str(v1_artifact["dataset"]),
            "--version", "testver-v1",
            "--models-dir", str(v1_artifact["models_dir"]),
            "--out", str(out),
            "--channel", "shadow",
            "--statuses-as-of", "2024-12-01T12:00:00Z",
        ])

        # assert
        assert code == 0
        assert "skipping rebuild_context" in caplog.text
        assert "context rebuilt from base p" not in caplog.text
        predictions = pd.read_parquet(out)
        assert len(predictions) > 0
        assert set(predictions["MODEL_VERSION"]) == {"testver-v1"}


class TestPreseasonPriorPredict:
    def test_only_preseason_rows_move_and_the_rest_match_the_off_run(
        self, v1_artifact, tmp_path: Path
    ) -> None:
        # arrange
        frame = pd.read_parquet(v1_artifact["dataset"])
        upcoming = frame[frame["GAME_DATE"] >= pd.Timestamp(CUTOFF)]
        preseason_game = upcoming["GAME_ID"].iloc[0]
        frame["SEASON_TYPE"] = frame["GAME_ID"].eq(preseason_game).map(
            {True: "Pre Season", False: "Regular Season"}
        )
        dataset = tmp_path / "dataset.parquet"
        frame.to_parquet(dataset, index=False)
        common = [
            "--dataset", str(dataset), "--version", "testver-v1",
            "--models-dir", str(v1_artifact["models_dir"]), "--channel", "shadow",
            "--statuses-as-of", "2024-12-01T12:00:00Z",
        ]

        # act
        off_code = predict.main([*common, "--out", str(tmp_path / "off.parquet")])
        on_code = predict.main([
            *common, "--out", str(tmp_path / "on.parquet"), "--preseason-prior", "on",
        ])

        # assert
        assert off_code == on_code == 0
        off = pd.read_parquet(tmp_path / "off.parquet")
        on = pd.read_parquet(tmp_path / "on.parquet")
        moved = on["PRESEASON_PRIOR_APPLIED"].to_numpy(dtype=bool)
        assert moved.any() and not moved.all()
        assert set(on.loc[moved, "GAME_ID"]) == {preseason_game}
        pd.testing.assert_frame_equal(
            on.loc[~moved, off.columns], off.loc[~moved], check_exact=True
        )
        expected = on.loc[moved, "PRESEASON_PRIOR_TIER"].map(config.PRESEASON_MINUTES_PRIOR)
        assert on.loc[moved, "E_MIN_COND"].to_numpy() == pytest.approx(expected.to_numpy())
        assert on.loc[moved, "E_MIN"].to_numpy() == pytest.approx(
            (on.loc[moved, "P_PLAY"] * on.loc[moved, "E_MIN_COND"]).to_numpy()
        )


@pytest.fixture(scope="module")
def v7_artifact(
    features_status: pd.DataFrame, team_logs: pd.DataFrame, box_details: pd.DataFrame,
    preseason_logs: pd.DataFrame, tmp_path_factory,
) -> dict[str, Path]:
    """one v7-preseason-role fit on the fixture dataset with every family attached."""
    from fnba_ml.box_context import attach_v6_features
    from fnba_ml.matchup import attach_v4_features
    from fnba_ml.preseason_role import attach_preseason_role_features

    root = tmp_path_factory.mktemp("v7")
    frame = attach_v6_features(attach_v4_features(features_status, team_logs), box_details)
    frame = attach_preseason_role_features(frame, preseason_logs)
    dataset = root / "dataset.parquet"
    frame.to_parquet(dataset, index=False)
    models_dir = root / "models"
    code = train.main([
        "--dataset", str(dataset),
        "--feature-set", config.PROSPECTIVE_FEATURE_SET,
        "--version", "testver-v7",
        "--cutoff", CUTOFF,
        "--models-dir", str(models_dir),
    ])
    assert code == 0
    return {"dataset": dataset, "models_dir": models_dir, "dir": models_dir / "testver-v7"}


class TestV7Args:
    def test_the_served_v7_set_is_trainable(self) -> None:
        # act
        args = train.parse_args(["--feature-set", "v7-preseason-role"])

        # assert
        assert args.feature_set == config.PROSPECTIVE_FEATURE_SET

    def test_the_v7_artifact_keeps_the_version_it_is_given(self) -> None:
        # act + assert
        assert feature_set_version("20261008-v7", "v7-preseason-role") == "20261008-v7"

    def test_a_v7_fit_inherits_the_v3_shadow_cutoff(self, tmp_path: Path) -> None:
        # arrange
        reference = tmp_path / train.WINDOW_REFERENCE_VERSION
        reference.mkdir()
        (reference / train.META_FILE).write_text(
            json.dumps({"training_window": {"cutoff": "2026-04-13"}}), encoding="utf-8"
        )
        args = train.parse_args([
            "--feature-set", "v7-preseason-role", "--models-dir", str(tmp_path),
        ])
        frame = pd.DataFrame({"GAME_DATE": pd.to_datetime(["2026-06-01"])})

        # act
        cutoff, source = train.resolve_training_cutoff(args, frame, "20261008-v7")

        # assert
        assert train.WINDOW_REFERENCE_VERSION == "20260818"
        assert cutoff.date() == pd.Timestamp("2026-04-13").date()
        assert train.WINDOW_REFERENCE_VERSION in source

    def test_a_dataset_missing_a_v7_column_is_refused(
        self, features_status: pd.DataFrame
    ) -> None:
        # act + assert
        with pytest.raises(SystemExit, match="lacks 22 column"):
            train.training_columns(features_status, config.PROSPECTIVE_FEATURE_SET)

    def test_a_preseason_training_row_is_refused(self) -> None:
        # arrange
        frame = pd.DataFrame({config.COMPETITION_COL: ["regular", "preseason"]})

        # act + assert
        with pytest.raises(SystemExit, match="preseason"):
            train.assert_training_rows_only(frame)


class TestV7Fit:
    def test_metadata_names_the_feature_set_and_73_columns(self, v7_artifact) -> None:
        # act
        metadata = json.loads((v7_artifact["dir"] / train.META_FILE).read_text("utf-8"))

        # assert
        assert metadata["model_version"] == "testver-v7"
        assert metadata["feature_set"] == "v7-preseason-role"
        assert metadata["feature_version"] == "v7"
        assert metadata["feature_cols"] == config.FEATURE_SETS["v7-preseason-role"]
        assert len(metadata["feature_cols"]) == 73
        assert metadata["training_window"]["cutoff"] == CUTOFF
        assert metadata["cutoff_source"] == "--cutoff"
        assert "base_artifact_checksum" in metadata
        assert metadata["context"]["stage1_feature_cols"] == config.BASE_FEATURE_COLS
        assert metadata["production"]["rate_estimators"]["STL"] == "expanding"

    def test_the_artifact_holds_the_six_files_the_freeze_pins(self, v7_artifact) -> None:
        # act
        files = {p.name for p in v7_artifact["dir"].iterdir()}

        # assert
        assert files == set(config.PROSPECTIVE_ARTIFACT_CHECKSUMS)

    def test_both_models_read_the_73_columns(self, v7_artifact) -> None:
        # act
        model, minutes, base, _ = predict.load_version("testver-v7", v7_artifact["models_dir"])

        # assert
        assert base is not None
        assert base.feature_cols == config.BASE_FEATURE_COLS
        for fitted in (model, minutes):
            assert fitted.feature_cols == config.FEATURE_SETS["v7-preseason-role"]

    def test_the_registry_entry_records_v7_and_verifies(self, v7_artifact) -> None:
        # arrange
        models_dir = v7_artifact["models_dir"]

        # act
        entry = registry.find("testver-v7", models_dir / "registry.json")

        # assert
        assert entry is not None
        assert entry["feature_set"] == "v7-preseason-role"
        assert entry["feature_version"] == "v7"
        assert entry["n_features"] == 73
        assert registry.verify_artifacts("testver-v7", models_dir) == []

    def test_predict_rebuilds_context_and_scores_the_v7_artifact(
        self, v7_artifact, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # arrange
        out = tmp_path / "predictions.parquet"
        caplog.set_level(logging.INFO, logger="predict")

        # act
        code = predict.main([
            "--dataset", str(v7_artifact["dataset"]),
            "--version", "testver-v7",
            "--models-dir", str(v7_artifact["models_dir"]),
            "--out", str(out),
            "--statuses-as-of", "2024-12-01T12:00:00Z",
        ])

        # assert
        assert code == 0
        assert "context rebuilt from base p" in caplog.text
        predictions = pd.read_parquet(out)
        assert len(predictions) > 0
        assert set(predictions["FEATURE_VERSION"]) == {"v7"}

    def test_predict_refuses_a_frame_without_the_v7_columns(
        self, v7_artifact, features_status: pd.DataFrame, tmp_path: Path
    ) -> None:
        # arrange
        dataset = tmp_path / "v3_only.parquet"
        features_status.to_parquet(dataset, index=False)

        # act + assert
        with pytest.raises(SystemExit, match="refusing to score"):
            predict.main([
                "--dataset", str(dataset), "--version", "testver-v7",
                "--models-dir", str(v7_artifact["models_dir"]),
                "--out", str(tmp_path / "p.parquet"),
                "--statuses-as-of", "2024-12-01T12:00:00Z",
            ])


class TestFrozenArtifactGuard:
    def test_the_v3_shadow_artifact_is_never_refitted(
        self, features_status: pd.DataFrame, tmp_path: Path
    ) -> None:
        # arrange
        dataset = tmp_path / "dataset.parquet"
        features_status.to_parquet(dataset, index=False)

        # act + assert
        with pytest.raises(SystemExit, match="frozen v3 shadow"):
            train.main([
                "--dataset", str(dataset), "--version", train.WINDOW_REFERENCE_VERSION,
                "--cutoff", CUTOFF, "--models-dir", str(tmp_path / "models"),
            ])


class TestEvaluateWorkflow:
    WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ml_evaluate.yml"

    def _input_block(self, text: str, name: str) -> dict[str, str]:
        lines = text.splitlines()
        start = lines.index(f"      {name}:")
        block: dict[str, str] = {}
        for line in lines[start + 1:]:
            if not line.startswith("        "):
                break
            key, _, value = line.strip().partition(":")
            block[key] = value.strip()
        return block

    def test_train_feature_set_is_an_optional_string_input(self) -> None:
        # arrange
        text = self.WORKFLOW.read_text(encoding="utf-8")

        # act
        block = self._input_block(text, "train_feature_set")

        # assert
        assert block["type"] == "string"
        assert block["default"] == '""'
        assert block["required"] == "false"

    def test_the_input_trains_and_uploads_the_version_directory(self) -> None:
        # arrange
        text = self.WORKFLOW.read_text(encoding="utf-8")

        # act
        guarded = text.count("if: inputs.train_feature_set != ''")

        # assert
        assert guarded == 3
        assert 'python ml/train.py --feature-set "$FEATURE_SET" --version "$VERSION"' in text
        assert "ml/models/${{ inputs.version }}/**" in text
        assert text.index("name: Build dataset") < text.index("name: Train feature-set artifact")

    def test_every_input_has_a_type_and_a_default(self) -> None:
        # arrange
        text = self.WORKFLOW.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = lines.index("    inputs:") + 1
        names = [
            line.strip().rstrip(":") for line in lines[start:]
            if line.startswith("      ") and not line.startswith("        ")
            and line.strip().endswith(":")
        ]
        names = names[:names.index("group")] if "group" in names else names

        # act
        blocks = {name: self._input_block(text, name) for name in names}

        # assert
        assert "train_feature_set" in blocks
        assert len(blocks) <= 25
        for name, block in blocks.items():
            assert "type" in block and "default" in block, name
