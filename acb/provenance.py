"""Configuration records shared by both execution backends."""
from copy import deepcopy
import json
from pathlib import Path


def requested_config(config: dict) -> dict:
    """Retain declarations, redacting inline credentials in requested metadata."""
    def redact(value, key=""):
        if key.lower() in {"env", "environment_variables", "headers"} and isinstance(value, dict):
            return {name: "[redacted]" for name in value}
        if key.lower() in {"api_key", "token", "password", "secret", "authorization"}:
            return "[redacted]"
        if isinstance(value, dict):
            return {name: redact(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    return redact(deepcopy(config))


def save_configuration(output: Path, prepared: dict, *, effective_run_id: str) -> dict:
    """Save the effective plan before execution and return that same document."""
    document = deepcopy(prepared)
    document["requested_run_id"] = document["run_id"]
    document["run_id"] = effective_run_id
    document["run_dir"] = str(output)
    (output / "requested.json").write_text(json.dumps(
        document.get("requested_config", {"unavailable": True}), indent=2) + "\n")
    (output / "resolved.json").write_text(json.dumps(document, indent=2) + "\n")
    return document
