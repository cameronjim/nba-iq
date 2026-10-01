"""ml/models/registry.json - what was trained, on what, and from which commit.

model artifacts are committed to git (they are small and there is no artifact
store in this project), so the registry has to be enough on its own to answer
"which code and which data produced this file". every entry carries the git
commit, the training window, the hyperparameters, the headline metrics and a
sha256 per artifact.

the only git command used is ``git rev-parse HEAD``, which is read-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import FEATURE_VERSION, MODELS_DIR, SERVED_FEATURE_SET

log = logging.getLogger(__name__)

REGISTRY_PATH = MODELS_DIR / "registry.json"
REGISTRY_SCHEMA = 1


def git_commit(repo_root: Path | None = None) -> str | None:
    """current HEAD sha, or None outside a git checkout."""
    cwd = repo_root or MODELS_DIR.parent.parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd), capture_output=True, text=True, timeout=15, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not read the git commit: %s", exc)
        return None
    return out.stdout.strip() or None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_records(version_dir: Path) -> list[dict[str, object]]:
    """one record per file in the version directory, sorted for stable diffs."""
    records = []
    for path in sorted(version_dir.rglob("*")):
        if path.is_file():
            records.append({
                "path": path.relative_to(version_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return records


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, object]:
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "entries": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_entry(
    model_version: str,
    version_dir: Path,
    training_window: dict[str, object],
    hyperparams: dict[str, object],
    metrics: dict[str, object],
    champions: dict[str, str],
    universe_source: str,
    feature_cols: list[str],
    feature_set: str = SERVED_FEATURE_SET,
) -> dict[str, object]:
    return {
        "model_version": model_version,
        "feature_version": FEATURE_VERSION,
        "feature_set": feature_set,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "universe_source": universe_source,
        "training_window": training_window,
        "champions": champions,
        "hyperparams": hyperparams,
        "metrics": metrics,
        "n_features": len(feature_cols),
        "feature_cols": feature_cols,
        "artifacts": artifact_records(version_dir),
    }


def upsert(entry: dict[str, object], path: Path = REGISTRY_PATH) -> dict[str, object]:
    """replace any existing entry with the same model_version, then append."""
    registry = load_registry(path)
    entries = [e for e in registry.get("entries", [])
               if e.get("model_version") != entry["model_version"]]
    entries.append(entry)
    entries.sort(key=lambda e: str(e.get("created_at", "")))
    registry = {"schema": REGISTRY_SCHEMA, "entries": entries}

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, sort_keys=False)
        fh.write("\n")
    log.info("registry updated: %s (%d entries)", path, len(entries))
    return registry


def record_prediction_run(
    model_version: str,
    run: dict[str, object],
    path: Path = REGISTRY_PATH,
) -> dict[str, object] | None:
    """link a database prediction_runs row back to the model that produced it.

    the two records answer different questions and neither is redundant:
    prediction_runs says "what was claimed and when", the registry entry says
    "what produced it". without this list, going from a suspicious prediction to
    the artifact that made it means matching on model_version strings by hand
    and hoping no version was ever rebuilt.

    a missing entry is a warning, not an error: the predictions are already
    committed to the database by the time this is called, and failing here would
    report a failure for a run that in fact succeeded.
    """
    registry = load_registry(path)
    entries = list(registry.get("entries", []))
    for entry in entries:
        if entry.get("model_version") == model_version:
            runs = list(entry.get("prediction_runs", []))  # type: ignore[arg-type]
            runs.append(run)
            entry["prediction_runs"] = runs
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"schema": REGISTRY_SCHEMA, "entries": entries}, fh, indent=2)
                fh.write("\n")
            log.info("registry: linked prediction run %s to %s", run.get("run_id"), model_version)
            return entry

    log.warning(
        "no registry entry for %r; the prediction run was written to the database "
        "but is not linked to a model artifact", model_version,
    )
    return None


def find(model_version: str, path: Path = REGISTRY_PATH) -> dict[str, object] | None:
    for entry in load_registry(path).get("entries", []):
        if entry.get("model_version") == model_version:
            return entry
    return None


def latest(path: Path = REGISTRY_PATH) -> dict[str, object] | None:
    entries = load_registry(path).get("entries", [])
    return entries[-1] if entries else None


def verify_artifacts(model_version: str, models_dir: Path = MODELS_DIR) -> list[str]:
    """re-hash a version's files. returns the list of mismatched paths."""
    entry = find(model_version, models_dir / "registry.json")
    if entry is None:
        raise KeyError(f"no registry entry for {model_version!r}")
    version_dir = models_dir / model_version
    bad = []
    for record in entry.get("artifacts", []):
        path = version_dir / str(record["path"])
        if not path.exists() or sha256_file(path) != record["sha256"]:
            bad.append(str(record["path"]))
    return bad
