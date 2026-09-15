from __future__ import annotations

from dataclasses import replace
import json

from .base import Integration, IntegrationContext, ModelEndpoint
from .rtk import RTKIntegration

REGISTRY: dict[str, type[Integration]] = {"rtk": RTKIntegration}
CATEGORIES = ("execution_integrations", "model_middleware")


class IntegrationManager:
    def __init__(self, harness: str, config: dict):
        self.entries: list[Integration] = []
        self.contexts: dict[str, IntegrationContext] = {}
        self.states: dict[str, dict] = {}
        self.attempted: list[Integration] = []
        self.started: list[Integration] = []
        names: set[str] = set()
        resources: set[str] = set()
        for category in CATEGORIES:
            configs = config.get(category, [])
            if not isinstance(configs, list):
                raise ValueError(f"{category} must be a list")
            for entry in configs:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                    raise ValueError(f"{category} entries require a name")
                name = entry["name"]
                if name not in REGISTRY:
                    raise ValueError(f"unknown integration {name!r}; registered: {list(REGISTRY)}")
                if name in names:
                    raise ValueError(f"duplicate integration {name!r}")
                integration = REGISTRY[name](entry)
                if integration.category != category:
                    raise ValueError(f"{name} belongs in {integration.category}, not {category}")
                integration.validate(harness, config)
                conflicts = resources & integration.resources
                if conflicts:
                    raise ValueError(f"integration resource conflict: {sorted(conflicts)}")
                resources.update(integration.resources)
                names.add(name)
                self.entries.append(integration)
                self.states[name] = {"name": name, "category": category, "status": "configured"}

    def _write(self) -> None:
        for entry in self.entries:
            if entry.name not in self.contexts:
                continue
            context = self.contexts[entry.name]
            context.artifact_dir.mkdir(parents=True, exist_ok=True)
            manifest = {**self.states[entry.name], "harness": context.harness,
                        "harness_version": context.harness_version,
                        "arch": context.arch, "metadata": entry.metadata}
            (context.artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def setup(self, context: IntegrationContext) -> dict[str, str]:
        env: dict[str, str] = {}
        for entry in self.entries:
            self.contexts[entry.name] = replace(context, artifact_dir=context.artifact_dir / entry.name)
        self._write()
        for entry in self.entries:
            current = self.contexts[entry.name]
            self.attempted.append(entry)
            try:
                self.states[entry.name]["status"] = "installing"
                self._write()
                entry.install(current)
                additions = entry.activate(current)
                if not isinstance(additions, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in additions.items()
                ):
                    raise ValueError("activation environment must contain string keys and values")
                forbidden = {"HOME", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL",
                             "OPENAI_API_KEY", "OPENAI_HOST", "ANTHROPIC_HOST", "PI_CODING_AGENT_DIR",
                             "OPENCODE_CONFIG_CONTENT"}
                overlap = set(additions) & (set(env) | forbidden)
                if overlap:
                    raise ValueError(f"integration environment conflict: {sorted(overlap)}")
                env.update(additions)
                self.states[entry.name]["verification"] = entry.verify(current)
                self.states[entry.name]["status"] = "ready"
                self._write()
            except Exception as exc:
                self.states[entry.name].update(status="failed", error=str(exc))
                self._write()
                raise
        return env

    def start(self, upstream: ModelEndpoint) -> ModelEndpoint:
        # YAML order is agent-facing: [A, B] means agent -> A -> B -> Praxis.
        endpoint = upstream
        for entry in reversed(self.entries):
            if entry.category != "model_middleware":
                continue
            context = self.contexts[entry.name]
            try:
                self.started.append(entry)
                endpoint = entry.start(context, endpoint)
                if not isinstance(endpoint, ModelEndpoint) or endpoint.api != upstream.api:
                    raise ValueError("middleware must return an endpoint with the same API")
                entry.health_check(context)
                self.states[entry.name]["status"] = "running"
                self._write()
            except Exception as exc:
                self.states[entry.name].update(status="failed", error=str(exc))
                self._write()
                raise
        return endpoint

    def finish(self) -> list[str]:
        errors = []
        cleanup_order = list(reversed(self.started)) + [entry for entry in reversed(self.attempted) if entry not in self.started]
        for entry in cleanup_order:
            context = self.contexts[entry.name]
            for operation in (entry.collect, entry.stop):
                try:
                    operation(context)
                except Exception as exc:
                    errors.append(f"{entry.name} {operation.__name__}: {exc}")
                    self.states[entry.name].setdefault("cleanup_errors", []).append(str(exc))
            if self.states[entry.name]["status"] != "failed":
                self.states[entry.name]["status"] = "finished" if not self.states[entry.name].get("cleanup_errors") else "cleanup_failed"
        try:
            self._write()
        except OSError as exc:
            errors.append(f"writing integration manifests: {exc}")
        self.attempted.clear()
        self.started.clear()
        return errors
