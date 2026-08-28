"""Lightweight YAML config loading for a benchmark run.

A single run is described by a ``run.yaml`` that references, by name, entries in
the ``config/*.yaml`` registries (harnesses, benchmarks, proxy). This keeps the
matrix (which harness x which model x which benchmark) declarative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class RunConfig:
    run_id: str
    benchmark: str  # key into benchmarks.yaml
    harness: str | list[str]  # key(s) into harnesses.yaml; str or list of str
    model: str  # model id sent to the proxy (drives Praxis routing)
    proxy: str = "praxis"  # key into proxy.yaml
    subset: list[str] | None = None  # instance_ids; None = full split
    limit: int | None = None
    max_workers: int = 4
    output_dir: str = "runs"
    # free-form overrides merged into the harness/benchmark/proxy config
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def harnesses(self) -> list[str]:
        """Return harnesses as a list, supporting both str and list[str] formats."""
        if isinstance(self.harness, str):
            return [self.harness]
        return list(self.harness) if self.harness else []

    @classmethod
    def from_file(cls, path: str | Path) -> "RunConfig":
        path = Path(path)
        if not path.exists():
            # _load_yaml() silently returns {} for a missing file (that's the
            # right behavior for optional registry files in Registries.load()
            # below, but not here) -- without this check, a typo'd --config
            # path surfaces as a confusing `RunConfig.__init__() missing 4
            # required positional arguments` TypeError instead of naming the
            # actual problem.
            raise FileNotFoundError(f"run config not found: {path}")
        data = _load_yaml(path)
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
    """A model backend the proxy connects to (resolved from proxy.yaml)."""

    name: str
    api: str = "anthropic"  # api the backend speaks: anthropic | openai
    endpoint: str = ""      # host:port
    tls: bool = True
    key_env: str | None = None
    reports_cache: bool = False
    # Vertex AI fields -- present only for Google Vertex Anthropic backends.
    # vertex_model is the Vertex model path segment (e.g.
    # "claude-haiku-4-5@20251001"); its presence is the signal that this is a
    # Vertex model and that the proxy needs vertex_anthropic_prepare + Bearer
    # auth + path_rewrite instead of the normal Anthropic filter chain.
    # Project is read from GOOGLE_CLOUD_PROJECT and region from CLOUD_ML_REGION
    # at build_config() time -- neither is baked into the model spec so a
    # single proxy.yaml entry works across GCP environments.
    vertex_model: str | None = None

    @property
    def is_vertex(self) -> bool:
        """True when this backend routes through Vertex AI's Anthropic endpoint."""
        return self.vertex_model is not None

    @property
    def upstream_url(self) -> str:
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.endpoint}"


@dataclass
class Registries:
    harnesses: dict[str, Any]
    benchmarks: dict[str, Any]
    proxy: dict[str, Any]  # full proxy.yaml: {models: {...}, backends: {...}}

    @classmethod
    def load(cls, config_dir: Path = CONFIG_DIR) -> "Registries":
        return cls(
            harnesses=_load_yaml(config_dir / "harnesses.yaml"),
            benchmarks=_load_yaml(config_dir / "benchmarks.yaml"),
            proxy=_load_yaml(config_dir / "proxy.yaml"),
        )

    def model_spec(self, model: str) -> ModelSpec:
        models = self.proxy.get("models", {})
        if model not in models:
            raise KeyError(f"model {model!r} not in proxy.yaml models; have {list(models)}")
        return ModelSpec(name=model, **(models[model] or {}))

    def backend_config(self, backend: str) -> dict[str, Any]:
        return (self.proxy.get("backends", {}) or {}).get(backend, {}) or {}
