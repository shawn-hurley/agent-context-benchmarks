"""Locate native submissions in ordinary and archived staged Harbor trials."""
from pathlib import Path


def prediction_path(trial: Path, result: dict) -> Path | None:
    roots = []
    for step in reversed(result.get("step_results") or []):
        name = step.get("step_name")
        if isinstance(name, str) and name and Path(name).name == name and name not in (".", ".."):
            roots.append(trial / "steps" / name / "verifier" / "native")
    roots.append(trial / "verifier" / "native")
    for native in roots:
        path = native / "prediction.json"
        if path.is_file() and path.resolve().is_relative_to(trial.resolve()):
            return path
    return None


def snapshot_pair(prediction: dict, path: Path) -> tuple[Path, Path] | None:
    output = prediction.get("output")
    if not isinstance(output, str) or prediction.get("model_patch") is not None:
        return None
    native = path.parent.resolve()
    candidate = Path(output).resolve()
    candidates = [candidate] if candidate.is_relative_to(native) else []
    # Staged execution moves verifier artifacts after writing the prediction.
    # Rebase only the suffix beneath verifier/native into the actual archive.
    parts = Path(output).parts
    for i in range(len(parts) - 1):
        if parts[i:i + 2] == ("verifier", "native"):
            candidates.append(native.joinpath(*parts[i + 2:]).resolve())
    for candidate in candidates:
        before, after = candidate / "input", candidate / "output"
        if (candidate.is_relative_to(native) and before.is_dir() and after.is_dir()
                and before.resolve().is_relative_to(native) and after.resolve().is_relative_to(native)):
            return before, after
    return None
