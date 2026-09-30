"""Load and validate user-authored, run-level staged workflows."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

import yaml


def _relative(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts or value in (".", "./"):
        raise ValueError(f"workflow {label} must be a relative path inside the workflow or workspace")
    return value


def _asset(root: Path, value: str) -> Path:
    path = root / _relative(value, "asset")
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"workflow asset is missing or unsafe: {value}")
    return path


def load_workflow(selection: str, origin: Path, benchmark: str, harnesses: list[str]) -> dict:
    """Resolve a bundled name or a path relative to the declaring run file."""
    if not isinstance(selection, str) or not selection:
        raise ValueError("workflow must be a bundled name or directory path")
    bundled = Path(__file__).parent / selection
    root = bundled if bundled.is_dir() and "/" not in selection else origin / selection
    root = root.expanduser().resolve()
    definition = root / "workflow.yaml"
    if not definition.is_file():
        raise FileNotFoundError(f"workflow definition not found: {definition}")
    data = yaml.safe_load(definition.read_text())
    if not isinstance(data, dict):
        raise ValueError("workflow.yaml must contain a mapping")
    allowed = {"version", "name", "benchmarks", "harnesses", "environment", "steps",
               "reward_strategy", "exclude_from_grading", "agent_adapter"}
    if set(data) - allowed:
        raise ValueError(f"workflow.yaml has unknown fields: {sorted(set(data) - allowed)}")
    if data.get("version") != 1 or not isinstance(data.get("name"), str):
        raise ValueError("workflow.yaml requires version: 1 and a name")
    for field, selected in (("benchmarks", benchmark), ("harnesses", harnesses)):
        values = data.get(field)
        if values is not None and (not isinstance(values, list) or not values or
                                   any(not isinstance(item, str) for item in values)):
            raise ValueError(f"workflow {field} must be a nonempty list of names")
        if values is not None and (selected not in values if isinstance(selected, str) else any(x not in values for x in selected)):
            raise ValueError(f"workflow {data['name']} does not support {field}: {selected}")
    if data.get("reward_strategy", "final") != "final":
        raise ValueError("workflow reward_strategy currently supports only final")
    environment = data.get("environment") or {}
    if not isinstance(environment, dict) or set(environment) - {"dockerfile", "assets", "dockerfile_append"}:
        raise ValueError("workflow environment has unknown fields")
    if "dockerfile" in environment:
        _asset(root, environment["dockerfile"])
    assets = environment.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("workflow environment.assets must be a list")
    for asset in assets:
        _asset(root, asset)
        if Path(asset).parts[0] in ("Dockerfile", "source"):
            raise ValueError(f"workflow asset conflicts with the benchmark environment: {asset}")
    if not isinstance(environment.get("dockerfile_append", ""), str):
        raise ValueError("workflow environment.dockerfile_append must be text")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("workflow steps must be a nonempty list")
    names = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"name", "instruction", "timeout_sec", "gate"}:
            raise ValueError("workflow step has unknown fields")
        name = step.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or name in names:
            raise ValueError(f"workflow step name is unsafe or repeated: {name!r}")
        names.add(name)
        _asset(root, step.get("instruction"))
        timeout = step.get("timeout_sec")
        if type(timeout) is not int or timeout < 1:
            raise ValueError(f"workflow step {name} needs a positive timeout_sec")
        gate = step.get("gate") or {}
        if not isinstance(gate, dict) or set(gate) - {"type", "archive", "required", "yaml_violations", "evidence_dir"}:
            raise ValueError(f"workflow step {name} has an invalid gate")
        if gate.get("type") not in ("artifacts", "native"):
            raise ValueError(f"workflow step {name} needs an artifacts or native gate")
        for key in ("archive", "required"):
            values = gate.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"workflow step {name} gate.{key} must be a list")
            for value in values:
                _relative(value, f"step {name} gate.{key}")
        if gate["type"] == "artifacts" and not gate.get("archive"):
            raise ValueError(f"workflow step {name} must archive its required artifacts")
        if gate.get("type") == "native" and step is not steps[-1]:
            raise ValueError("native grading must be the final workflow step")
        if gate.get("yaml_violations"):
            _relative(gate["yaml_violations"], "yaml_violations")
        if gate.get("evidence_dir"):
            _relative(gate["evidence_dir"], "evidence_dir")
    if steps[-1]["gate"]["type"] != "native":
        raise ValueError("the final workflow step must run the native benchmark grader")
    exclusions = data.get("exclude_from_grading", [])
    if not isinstance(exclusions, list):
        raise ValueError("workflow exclude_from_grading must be a list")
    for item in exclusions:
        _relative(item, "exclude_from_grading")
    if data.get("agent_adapter") not in (None, "local-qwen-no-think"):
        raise ValueError("workflow agent_adapter is unsupported")
    inventory = sorted([definition, *(_asset(root, value) for value in assets),
                        *(_asset(root, step["instruction"]) for step in steps),
                        *([_asset(root, environment["dockerfile"])] if "dockerfile" in environment else [])])
    digest = sha256()
    for path in inventory:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return {**data, "source_dir": str(root), "sha256": digest.hexdigest()}


def validate_workflow_snapshot(workflow: dict) -> None:
    """Stop preparation if a workflow changed after run resolution."""
    current = load_workflow(workflow["source_dir"], Path("/"),
                            (workflow.get("benchmarks") or [""])[0],
                            (workflow.get("harnesses") or [""])[0:1])
    if current["sha256"] != workflow["sha256"]:
        raise ValueError("workflow files changed after resolution; resolve the run again")
