"""Lightweight YAML config loading for a benchmark run.

A single run is described by a ``run.yaml`` that references, by name, entries in
the ``config/*.yaml`` registries (harnesses, benchmarks, models). This keeps the
matrix (which harness x which model x which benchmark) declarative.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        value = yaml.safe_load(f)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a mapping")
    return value


@dataclass
class RunConfig:
    run_id: str
    benchmark: str  # key into benchmarks.yaml
    harness: str | list[str]  # key(s) into harnesses.yaml; str or list of str
    model: str  # alias in models.yaml; resolved to the provider model ID
    workflow: str | None = None  # bundled name or path to a workflow directory
    subset: list[str] | None = None  # instance_ids; None = full split
    limit: int | None = None
    output_dir: str = "runs"
    # Validated overrides merged into harness and benchmark settings
    overrides: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 2
    skills: list | None = None
    mcp_servers: list | None = None
    extensions: list | None = None
    execution: dict[str, Any] = field(default_factory=dict)
    config_dir: str | None = None
    source_file: str | None = field(default=None, repr=False)
    interactive_run: str | None = field(default=None, repr=False)

    def __post_init__(self):
        self.validate_schema()

    def validate_schema(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("schema_version must be 2; older configuration schemas are unsupported")

    @property
    def harnesses(self) -> list[str]:
        """Return harnesses as a list, supporting both str and list[str] formats."""
        if isinstance(self.harness, str):
            return [self.harness]
        return list(self.harness) if self.harness else []

    def output_path(self) -> Path:
        """Resolve run output relative to the invocation directory."""
        if not isinstance(self.output_dir, str) or not self.output_dir:
            raise ValueError("output_dir must be a nonempty path string")
        return Path(self.output_dir).expanduser().resolve()

    def registry_dir(self) -> Path:
        """Find registries using the run file's schema and declaring directory."""
        from acb.resolver import discover_config_dir
        self.validate_schema()
        origin = Path(self.source_file).parent if self.source_file else Path.cwd()
        if self.config_dir:
            path = Path(self.config_dir).expanduser()
            path = origin / path
            return path.absolute()
        return discover_config_dir(origin)

    @classmethod
    def from_file(cls, path: str | Path) -> "RunConfig":
        path = Path(path).expanduser()
        if not path.exists():
            # _load_yaml() silently returns {} for a missing file (that's the
            # right behavior for optional registry files in Registries.load()
            # below, but not here) -- without this check, a typo'd --config
            # path surfaces as a confusing `RunConfig.__init__() missing 4
            # required positional arguments` TypeError instead of naming the
            # actual problem.
            raise FileNotFoundError(f"run config not found: {path}")
        data = _load_yaml(path)
        unknown = set(data) - {item.name for item in fields(cls) if item.name not in {"source_file", "interactive_run"}}
        if unknown:
            guidance = {"proxy": "Praxis is fixed; define model connections in models.yaml",
                        "max_workers": "use execution.max_workers"}
            advice = "; ".join(guidance[key] for key in sorted(unknown, key=str) if key in guidance)
            raise ValueError(f"run config {path}: unknown fields {sorted(unknown, key=str)}" + (f"; {advice}" if advice else ""))
        data["source_file"] = str(path.resolve())
        try:
            return cls(**data)
        except TypeError as e:
            required = {"run_id", "benchmark", "harness", "model"}
            missing = required - data.keys()
            if missing:
                raise ValueError(
                    f"run config {path} is missing required field(s): {sorted(missing)}"
                ) from e
            raise


@dataclass
class ModelSpec:
    """A model backend the proxy connects to (resolved from models.yaml)."""

    name: str
    api: str = "anthropic"  # api the backend speaks: anthropic | openai
    endpoint: str = ""      # host:port
    tls: bool = True
    key_env: str | None = None
    # Vertex AI fields -- present only for Google Vertex Anthropic backends.
    # vertex_model is the Vertex model path segment (e.g.
    # "claude-haiku-4-5@20251001"); its presence is the signal that this is a
    # Vertex model and that the proxy needs vertex_anthropic_prepare + Bearer
    # auth + path_rewrite instead of the normal Anthropic filter chain.
    # Project is read from GOOGLE_CLOUD_PROJECT and region from CLOUD_ML_REGION
    # at build_config() time -- neither is baked into the model spec so a
    # single models.yaml entry works across GCP environments.
    vertex_model: str | None = None

    @property
    def is_vertex(self) -> bool:
        """True when this backend routes through Vertex AI's Anthropic endpoint."""
        return self.vertex_model is not None



@dataclass
class Registries:
    harnesses: dict[str, Any]
    benchmarks: dict[str, Any]
    models: dict[str, Any] = field(default_factory=dict)
    skills: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    machine: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Registries":
        from acb.resolver import discover_config_dir
        config_dir = Path(config_dir) if config_dir else discover_config_dir()
        return cls(
            harnesses=_load_yaml(config_dir / "harnesses.yaml"),
            benchmarks=_load_yaml(config_dir / "benchmarks.yaml"),
            models=_load_yaml(config_dir / "models.yaml"),
            skills=_load_yaml(config_dir / "skills.yaml"),
            mcp_servers=_load_yaml(config_dir / "mcp.yaml"),
            extensions=_load_yaml(config_dir / "extensions.yaml"),
            machine=_load_yaml(config_dir / "machine.yaml"),
            sources=[str((config_dir / name).resolve()) for name in
                     ("harnesses.yaml", "benchmarks.yaml", "models.yaml", "skills.yaml",
                      "mcp.yaml", "extensions.yaml", "machine.yaml")
                     if (config_dir / name).is_file()],
        )
