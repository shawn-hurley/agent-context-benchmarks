from __future__ import annotations

from dataclasses import replace
import json

from .base import Integration, IntegrationActivation, IntegrationContext, IntegrationFailure, ModelEndpoint
from .rtk import RTKIntegration
from .caveman import CavemanIntegration

REGISTRY: dict[str, type[Integration]] = {"rtk": RTKIntegration, "caveman": CavemanIntegration}
CATEGORIES = ("execution_integrations", "model_middleware")


class IntegrationManager:
    def __init__(self, harness: str, config: dict):
        self.harness = harness
        self.activation = IntegrationActivation()
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
        if {"rtk", "caveman"} <= names:
            next(entry for entry in self.entries if entry.name == "caveman").metadata["composition"] = {
                "order": ["rtk", "caveman"],
                "recovery": "exact bytes after RTK; pre-RTK output is not reconstructed",
            }

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
        if context.harness != self.harness:
            raise ValueError("integration context harness does not match configuration")
        env: dict[str, str] = {}
        pi_extensions: tuple[str, ...] = ()
        opencode_plugins: tuple[str, ...] = ()
        claude_settings: tuple[str, ...] = ()
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
                activation = entry.activate(current)
                if isinstance(activation, dict):
                    activation = IntegrationActivation(env=activation)
                if not isinstance(activation, IntegrationActivation):
                    raise ValueError("invalid integration activation")
                additions = activation.env
                if not isinstance(additions, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in additions.items()
                ):
                    raise ValueError("activation environment must contain string keys and values")
                forbidden = {"HOME", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL",
                             "OPENAI_API_KEY", "OPENAI_HOST", "ANTHROPIC_HOST", "PI_CODING_AGENT_DIR",
                             "OPENCODE_CONFIG_CONTENT"}
                # Reviewed Goose composition: capture the output of RTK's shell
                # wrapper, then annotate successful output for the gateway.
                # Other environment collisions remain errors.
                if (self.harness == "goose" and entry.name == "caveman"
                        and additions.get("GOOSE_SHELL") == "/opt/acb/caveman/goose.py"
                        and env.get("GOOSE_SHELL") == "/opt/acb/rtk/shell/bash"):
                    additions = {**additions, "ACB_CAVEMAN_INNER_SHELL": env.pop("GOOSE_SHELL")}
                    entry.metadata["composition"] = {
                        "order": ["rtk", "caveman"],
                        "recovery": "exact bytes after RTK; pre-RTK output is not reconstructed",
                    }
                overlap = set(additions) & (set(env) | forbidden)
                if overlap:
                    raise ValueError(f"integration environment conflict: {sorted(overlap)}")
                for target, paths, existing in (
                    ("pi", activation.pi_extensions, pi_extensions),
                    ("opencode", activation.opencode_plugins, opencode_plugins),
                    ("claude-code", activation.claude_settings, claude_settings),
                ):
                    if not isinstance(paths, tuple) or any(not isinstance(p, str) or not p.startswith("/") for p in paths):
                        raise ValueError("integration assets require absolute paths")
                    if paths and context.harness != target:
                        raise ValueError(f"{target} activation cannot be used with {context.harness}")
                    if len(set(paths)) != len(paths) or set(paths) & set(existing):
                        raise ValueError("duplicate integration activation asset")
                pi_extensions += activation.pi_extensions
                opencode_plugins += activation.opencode_plugins
                claude_settings += activation.claude_settings
                if len(claude_settings) > 1:
                    raise ValueError("Claude Code requires one composed integration settings file")
                env.update(additions)
                self.activation = IntegrationActivation(dict(env), pi_extensions, opencode_plugins, claude_settings)
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
                    if operation == entry.collect and "activity" in entry.metadata:
                        activity = entry.metadata["activity"]
                        self.states[entry.name].setdefault("verification", {}).update(
                            agent_tool_verified=activity["agent_tool_verified"],
                            adapter_loaded=activity["adapter_loaded"],
                        )
                        if "compression_verified" in activity:
                            self.states[entry.name]["verification"]["compression_verified"] = activity["compression_verified"]
                except Exception as exc:
                    errors.append(f"{entry.name} {operation.__name__}: {exc}")
                    self.states[entry.name].setdefault("cleanup_errors", []).append(str(exc))
                    if isinstance(exc, IntegrationFailure):
                        self.states[entry.name].update(status="failed", error=str(exc))
            if self.states[entry.name]["status"] != "failed":
                self.states[entry.name]["status"] = "finished" if not self.states[entry.name].get("cleanup_errors") else "cleanup_failed"
        try:
            self._write()
        except OSError as exc:
            errors.append(f"writing integration manifests: {exc}")
        self.attempted.clear()
        self.started.clear()
        return errors
