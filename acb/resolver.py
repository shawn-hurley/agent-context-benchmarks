"""Pure configuration resolution shared by resolve, prepare and run."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from importlib.resources import files
import json
import os
from pathlib import Path
import re

import yaml

from acb.config import ModelSpec, Registries, RunConfig

CATEGORIES = ("skills", "mcp_servers", "extensions")
HARNESSES = ("goose", "pi", "opencode", "claude-code")
HARNESS_FIELDS = {
    "version", "timeout", "binary", "system_prompt", "max_tool_repetitions",
    "max_budget_usd", "launch_profile", "workdir", "conda_env", "skills",
    "mcp_servers", "extensions", "execution_integrations", "model_middleware",
    "model", "provider", "thinking", "max_tokens", "temperature", "python_path",
}
BENCH_FIELDS = {
    "execution_backend", "dataset", "revision", "task_root", "path", "environment",
    "attempts", "reward_metric", "success_value", "praxis_image", "python",
    "metrics", "grade_direction", "grade_tolerance",
    "registry", "version", "git_url", "architecture", "cache_dir", "offline",
    "instances", "split", "exclude", "exclude_repos", "scarfbench_dir",
    "scarfbench_image", "container_backend", "docker_host", "max_workers", "praxis_ai_repo",
    "task_repo_cache_dir", "benchmark_cache_dir", "scarf_binary",
    "image_arch", "task_repo", "namespace", "patch_exclude_patterns",
    "source", "target", "validate_timeout_minutes", "swebench_python",
}
EXEC_FIELDS = {"max_workers", "timeout", "environment", "offline", "cache_policy"}
SKILL_FIELDS = {
    "name", "version", "source_type", "source_url", "source_path", "ref",
    "binary_pattern", "binary_name", "binary_install_path", "skill_md_url",
    "required", "post_install", "options", "sha256", "status",
}


def defaults() -> dict:
    return yaml.safe_load(files("acb").joinpath("catalog/defaults.yaml").read_text())


def discover_config_dir(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        for name in (".acb", "config"):
            path = parent / name
            if any((path / file).is_file() for file in ("harnesses.yaml", "benchmarks.yaml", "machine.yaml", "models.yaml", "proxy.yaml", "skills.yaml", "extensions.yaml", "mcp.yaml")):
                return path
    return start / "config"


def _unknown(value: dict, allowed: set, field: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"{field}: unknown fields {sorted(extra, key=str)}")


def _positive(value, field):
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")


def _inline_skills(entries, field):
    seen = set()
    for entry in entries:
        _unknown(entry, SKILL_FIELDS, field)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{field}: entries require a name")
        if name in seen:
            raise ValueError(f"{field}: duplicate {name}")
        seen.add(name)
        if entry.get("source_type", "github_release") not in ("local", "git", "github_release"):
            raise ValueError(f"{field}.{name}.source_type is unsupported")
        if "required" in entry and type(entry["required"]) is not bool:
            raise ValueError(f"{field}.{name}.required must be a boolean")


def _select(entries, catalog, category):
    if not isinstance(entries, list):
        raise ValueError(f"{category} must be a list")
    selected, seen = [], set()
    for entry in entries:
        item = {"name": entry} if isinstance(entry, str) else deepcopy(entry)
        _unknown(item, {"name", "version", "options"}, category)
        name = item.get("name")
        if not isinstance(name, str) or name not in catalog:
            raise ValueError(f"{category}: unknown component {name!r}")
        if name in seen:
            raise ValueError(f"{category}: duplicate {name}")
        seen.add(name)
        options = item.get("options", {})
        allowed = {"binary_path", "sha256", "python_path"} if category == "extensions" and name == "rtk" else set(catalog[name].get("options", {}))
        _unknown(options, allowed, f"{category}.{name}.options")
        definition = deepcopy(catalog[name])
        if "version" in item and item["version"] != definition.get("version"):
            raise ValueError(f"{category}.{name}: version is not present in the catalog")
        selected.append({**definition, **item, "name": name,
                         "options": {**definition.get("options", {}), **options}})
    return selected


def _catalog_selection(entries):
    """Distinguish named selections from supported legacy inline configuration."""
    return isinstance(entries, list) and all(
        isinstance(entry, str) or isinstance(entry, dict) and
        set(entry) <= {"name", "version", "options"} for entry in entries
    )


def _selection(cfg, category, registered, shared, local):
    top = getattr(cfg, category)
    if top is not None and not _catalog_selection(top):
        raise ValueError(f"{category}: top-level selections require name, version and options only")
    sources = (registered, shared, local)
    if top is not None:
        for source in sources:
            if source.get(category) and not _catalog_selection(source[category]):
                raise ValueError(f"{category}: ambiguous shared and legacy selection")
    if top is not None and category in shared:
        raise ValueError(f"{category}: ambiguous top-level and shared override selection")
    if category in local:
        selection = local[category]
    elif top is not None:
        selection = top
    else:
        selection = shared.get(category, registered.get(category))
    if category == "extensions" and selection is not None and any(
        {**registered, **shared, **local}.get(key) for key in ("execution_integrations", "model_middleware")
    ):
        raise ValueError("extensions: ambiguous shared and legacy selection")
    return selection


@dataclass(frozen=True)
class ResolvedPlan:
    """Canonical JSON prevents execution from mutating shared configuration."""
    document: str

    def to_dict(self) -> dict:
        return json.loads(self.document)


def resolve(cfg: RunConfig, registries: Registries | None = None) -> ResolvedPlan:
    from acb.provenance import requested_config
    cfg.validate_schema()
    if not isinstance(cfg.run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", cfg.run_id):
        raise ValueError("run_id must be a path-safe name")
    if not isinstance(cfg.harness, (str, list)) or any(not isinstance(name, str) for name in cfg.harnesses):
        raise ValueError("harness must be a name or list of names")
    if not isinstance(cfg.model, str) or not cfg.model:
        raise ValueError("model must be a nonempty name")
    for key in ("output_dir", "config_dir", "source_file"):
        value = getattr(cfg, key)
        if (key == "output_dir" or value is not None) and (not isinstance(value, str) or not value):
            raise ValueError(f"{key} must be a nonempty path string")
    if not cfg.harnesses or len(set(cfg.harnesses)) != len(cfg.harnesses):
        raise ValueError("harness must contain unique harness names")
    unknown = set(cfg.harnesses) - set(HARNESSES)
    if unknown:
        raise ValueError(f"unknown harnesses: {sorted(unknown)}")
    _unknown(cfg.overrides, {"benchmark", "harness", "harnesses", "proxy"}, "overrides")
    _unknown(cfg.execution, EXEC_FIELDS, "execution")
    for key, allowed in (("benchmark", BENCH_FIELDS), ("harness", HARNESS_FIELDS),
                         ("harnesses", set(cfg.harnesses)), ("proxy", {"binary"})):
        if key in cfg.overrides:
            _unknown(cfg.overrides[key], allowed, "overrides." + key)
    _positive(cfg.max_workers, "max_workers")
    for key in ("max_workers", "timeout"):
        if key in cfg.execution:
            _positive(cfg.execution[key], "execution." + key)
    if "offline" in cfg.execution and type(cfg.execution["offline"]) is not bool:
        raise ValueError("execution.offline must be a boolean")
    if "cache_policy" in cfg.execution and not isinstance(cfg.execution["cache_policy"], str):
        raise ValueError("execution.cache_policy must be a string")
    registry = registries or Registries.load(cfg.registry_dir())
    for key in ("harnesses", "benchmarks", "models", "skills", "extensions", "proxy", "machine"):
        if not isinstance(getattr(registry, key), dict):
            raise ValueError(f"registry.{key} must be a mapping")
    _unknown(registry.machine, {"environment", "cache_dir"}, "machine")
    if "environment" in registry.machine and registry.machine["environment"] not in ("docker", "podman"):
        raise ValueError("machine.environment must be docker or podman")
    if "cache_dir" in registry.machine and not isinstance(registry.machine["cache_dir"], str):
        raise ValueError("machine.cache_dir must be a path string")
    _unknown(registry.proxy, {"models", "backends"}, "proxy")
    for key in ("models", "backends"):
        if key in registry.proxy and not isinstance(registry.proxy[key], dict):
            raise ValueError(f"proxy.{key} must be a mapping")
    base = Path(cfg.source_file).parent if cfg.source_file else Path.cwd()

    def source_base(filename):
        return next((Path(source).parent for source in registry.sources
                     if Path(source).name == filename), base)

    def absolute(value, origin):
        # Resolving a virtualenv's Python symlink selects the base interpreter
        # and loses its installed packages. Normalize without dereferencing.
        if not isinstance(value, str) or not value:
            raise ValueError(f"configuration path must be a nonempty string: {value!r}")
        return os.path.abspath(origin / Path(value).expanduser())

    def asset_paths(entries, origin):
        """Normalize host assets before merging layers with different origins."""
        entries = deepcopy(entries)
        if not isinstance(entries, list):
            return entries
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("source_path", "binary_path"):
                if entry.get(key):
                    entry[key] = absolute(entry[key], origin)
            options = entry.get("options")
            if isinstance(options, dict) and options.get("binary_path"):
                options["binary_path"] = absolute(options["binary_path"], origin)
        return entries

    def harness_assets(settings, origin):
        settings = deepcopy(settings)
        for category in ("skills", "extensions", "execution_integrations"):
            if category in settings:
                settings[category] = asset_paths(settings[category], origin)
        return settings
    catalog = defaults()
    overrides = deepcopy(cfg.overrides)
    benchmark = cfg.benchmark
    if isinstance(benchmark, dict):
        benchmark = deepcopy(benchmark)
        name = benchmark.pop("name", None)
        if not isinstance(name, str) or name not in catalog["benchmarks"] and name not in registry.benchmarks:
            raise ValueError(f"unknown benchmark {name!r}")
        _unknown(benchmark, BENCH_FIELDS, "benchmark")
        _unknown(registry.benchmarks.get(name, {}), BENCH_FIELDS, f"benchmarks.{name}")
        benchmark = {**catalog["benchmarks"].get(name, {}), **registry.benchmarks.get(name, {}), **benchmark}
    else:
        name = benchmark
        if not isinstance(name, str) or name not in catalog["benchmarks"] and name not in registry.benchmarks:
            raise ValueError(f"unknown benchmark {name!r}")
        _unknown(registry.benchmarks.get(name, {}), BENCH_FIELDS, f"benchmarks.{name}")
        benchmark = {**catalog["benchmarks"].get(name, {}), **registry.benchmarks.get(name, {})}
    benchmark.update(overrides.get("benchmark", {}))
    _unknown(benchmark, BENCH_FIELDS, "benchmark")
    if benchmark.get("grade_direction", "higher") not in ("higher", "lower"):
        raise ValueError("benchmark.grade_direction must be higher or lower")
    import math
    tolerance = benchmark.get("grade_tolerance", 0)
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("benchmark.grade_tolerance must be a finite nonnegative number")
    if "offline" in benchmark and type(benchmark["offline"]) is not bool:
        raise ValueError("benchmark.offline must be a boolean")
    for key in ("max_workers", "validate_timeout_minutes"):
        if key in benchmark:
            _positive(benchmark[key], "benchmark." + key)
    backend = benchmark.get("execution_backend", "harbor")
    if backend != "harbor":
        raise ValueError("benchmark.execution_backend must be harbor; the legacy runner has been retired")
    environment = cfg.execution.get("environment", benchmark.get("environment", registry.machine.get("environment", "podman")))
    if environment not in ("docker", "podman"):
        raise ValueError("execution.environment must be docker or podman; host execution is unsupported")
    workers = cfg.execution.get("max_workers", cfg.max_workers)
    _positive(workers, "max_workers")
    if cfg.limit is not None:
        _positive(cfg.limit, "limit")
    if cfg.subset is not None and (not isinstance(cfg.subset, list) or not cfg.subset or any(not isinstance(x, str) for x in cfg.subset) or len(set(cfg.subset)) != len(cfg.subset)):
        raise ValueError("subset must be a nonempty list of unique task IDs")
    _positive(benchmark.get("attempts", 1), "benchmark.attempts")
    if cfg.proxy != "praxis":
        raise ValueError("proxy must be praxis for container execution")
    model_data = deepcopy(registry.models.get(cfg.model, {}))
    _unknown(model_data, {item.name for item in fields(ModelSpec)} - {"name"} | {"model"}, f"models.{cfg.model}")
    wire_model = model_data.pop("model", cfg.model)
    if not isinstance(wire_model, str) or not wire_model:
        raise ValueError(f"models.{cfg.model}.model must be a nonempty string")
    _unknown(registry.proxy.get("models", {}).get(wire_model, {}),
             {item.name for item in fields(ModelSpec)} - {"name"}, f"proxy.models.{wire_model}")
    model_data = {**registry.proxy.get("models", {}).get(wire_model, {}), **model_data}
    if not model_data:
        raise ValueError(f"model {cfg.model!r} needs a models.yaml or proxy.yaml definition")
    model = ModelSpec(name=wire_model, **model_data)
    for key in ("tls", "reports_cache"):
        if type(getattr(model, key)) is not bool:
            raise ValueError(f"model.{key} must be a boolean")
    for key in ("key_env", "vertex_model"):
        if getattr(model, key) is not None and not isinstance(getattr(model, key), str):
            raise ValueError(f"model.{key} must be a string")
    if not isinstance(model.endpoint, str):
        raise ValueError("model.endpoint must be a string")
    if model.api not in ("anthropic", "openai") or not model.endpoint:
        raise ValueError("model requires an openai/anthropic api and endpoint")
    if "@" in model.endpoint:
        raise ValueError("model.endpoint must not embed credentials; use key_env")
    per_harness = overrides.get("harnesses", {})
    _unknown(per_harness, set(cfg.harnesses), "overrides.harnesses")
    shared = overrides.get("harness", {})
    _unknown(shared, HARNESS_FIELDS, "overrides.harness")
    shared = harness_assets(shared, base)
    pending, resolved = [], {}
    catalogs = {k: {**catalog.get(k, {}), **getattr(registry, k)} for k in CATEGORIES}
    for category in ("skills", "extensions"):
        for component_name, definition in catalogs[category].items():
            _unknown(definition, SKILL_FIELDS if category == "skills" else
                     {"version", "source_commit", "status", "options"}, f"{category}.{component_name}")
        catalogs[category] = {
            name: asset_paths([definition], source_base(category + ".yaml"))[0]
            for name, definition in catalogs[category].items()
        }
    for harness in cfg.harnesses:
        local = per_harness.get(harness, {})
        _unknown(local, HARNESS_FIELDS, f"overrides.harnesses.{harness}")
        local = harness_assets(local, base)
        _unknown(registry.harnesses.get(harness, {}), HARNESS_FIELDS, f"harnesses.{harness}")
        registered = harness_assets(registry.harnesses.get(harness, {}), source_base("harnesses.yaml"))
        settings = {**catalog["harnesses"][harness], **registered, **shared, **local}
        _unknown(settings, HARNESS_FIELDS, f"harness.{harness}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(settings.get("version", ""))):
            raise ValueError(f"harness.{harness}.version requires an exact release")
        if harness == "claude-code" and settings.get("launch_profile") not in ("bare", "isolated-hooks"):
            raise ValueError("harness.claude-code.launch_profile must be bare or isolated-hooks")
        for key in ("max_tool_repetitions", "max_tokens"):
            if key in settings:
                _positive(settings[key], f"harness.{harness}.{key}")
        if "max_budget_usd" in settings and (type(settings["max_budget_usd"]) not in (int, float) or not 0 < settings["max_budget_usd"] < float("inf")):
            raise ValueError(f"harness.{harness}.max_budget_usd must be a positive finite number")
        _positive(settings.get("timeout", 1800), f"harness.{harness}.timeout")
        if "timeout" in cfg.execution:
            _positive(cfg.execution["timeout"], "execution.timeout")
            settings["timeout"] = cfg.execution["timeout"]
        for category in CATEGORIES:
            selection = _selection(cfg, category, registered, shared, local)
            if selection is None:
                continue
            if category != "extensions" and not _catalog_selection(selection):
                if not isinstance(selection, list):
                    raise ValueError(f"{category} must be a list")
                if category == "skills":
                    _inline_skills(selection, f"harness.{harness}.skills")
                settings[category] = deepcopy(selection)
                continue
            if category in ("skills", "extensions"):
                selection = asset_paths(selection, base)
            selected = _select(selection, catalogs[category], category)
            if category == "skills":
                _inline_skills(selected, f"harness.{harness}.skills")
            if category == "mcp_servers":
                selected_origin = source_base("harnesses.yaml") if selection is registered.get(category) else base
                for entry in selected:
                    for key in ("source_path", "binary_path"):
                        if entry.get(key):
                            entry[key] = absolute(entry[key], source_base("mcp.yaml"))
                    if entry["options"].get("binary_path"):
                        entry["options"]["binary_path"] = absolute(entry["options"]["binary_path"], selected_origin)
            settings[category] = selected
            if category == "extensions":
                if settings.get("execution_integrations") or settings.get("model_middleware"):
                    raise ValueError(f"extensions: ambiguous legacy selection for {harness}")
                settings["execution_integrations"] = []
                settings["model_middleware"] = []
                for extension in selected:
                    if extension["name"] == "caveman":
                        mode = extension["options"].get("mode", "record")
                        if mode not in ("record", "compress"):
                            raise ValueError("extensions.caveman.options.mode must be record or compress")
                        settings["model_middleware"].append({"name": "caveman", "version": extension["version"], "mode": mode})
                        pending.append(f"{harness}: Caveman service and exact recovery probe")
                        continue
                    if extension["name"] != "rtk":
                        raise ValueError(f"unregistered extension {extension['name']}")
                    if extension["version"] != "0.48.0" or settings["version"] != catalog["harnesses"][harness]["version"]:
                        raise ValueError(f"extensions.rtk: unsupported version pair for {harness}")
                    if harness == "claude-code" and settings.get("launch_profile") != "isolated-hooks":
                        raise ValueError("extensions.rtk: claude-code with launch_profile=bare cannot load required hooks")
                    settings["execution_integrations"].append({
                        "name": "rtk", "version": extension["version"],
                        "mode": "shell-wrapper" if harness == "goose" else "native",
                        "experimental": True, **extension["options"],
                    })
                    if "binary_path" not in extension["options"]:
                        pending.append(f"{harness}: prepare RTK Linux artifact")
            elif category == "mcp_servers":
                settings[category] = [{**entry.get("config", {}), **entry["options"], "name": entry["name"]} for entry in selected]
        resolved[harness] = settings
    run_benchmark = cfg.benchmark if isinstance(cfg.benchmark, dict) else {}
    for key in ("path", "cache_dir", "scarfbench_dir", "praxis_ai_repo", "task_repo_cache_dir", "benchmark_cache_dir"):
        if benchmark.get(key):
            origin = base if key in overrides.get("benchmark", {}) or key in run_benchmark else source_base("benchmarks.yaml")
            benchmark[key] = absolute(benchmark[key], origin)
    for metric in benchmark.get("metrics", []):
        if isinstance(metric, dict) and metric.get("type") == "uv-script":
            kwargs = metric.get("kwargs", {})
            if kwargs.get("script_path"):
                origin = base if "metrics" in overrides.get("benchmark", {}) or "metrics" in run_benchmark else source_base("benchmarks.yaml")
                kwargs["script_path"] = absolute(kwargs["script_path"], origin)
    for field in ("scarf_binary", "swebench_python"):
        binary = benchmark.get(field)
        if binary and ("/" in binary or binary.startswith("~")):
            origin = base if field in overrides.get("benchmark", {}) or field in run_benchmark else source_base("benchmarks.yaml")
            benchmark[field] = absolute(binary, origin)
    dataset = benchmark.get("dataset")
    if name in ("swebench", "swebench-lite") and isinstance(dataset, str) and dataset.endswith((".json", ".jsonl")):
        origin = base if "dataset" in overrides.get("benchmark", {}) or "dataset" in run_benchmark else source_base("benchmarks.yaml")
        benchmark["dataset"] = absolute(dataset, origin)
    output = str(cfg.output_path())
    cache = benchmark.get("cache_dir", registry.machine.get("cache_dir", str(Path(output) / ".cache")))
    if "cache_dir" not in benchmark and "cache_dir" in registry.machine:
        cache = absolute(cache, source_base("machine.yaml"))
    _unknown(registry.backend_config(cfg.proxy), {"binary"}, f"proxy.backends.{cfg.proxy}")
    proxy_config = {**registry.backend_config(cfg.proxy), **overrides.get("proxy", {})}
    binary = proxy_config.get("binary")
    if binary and ("/" in binary or binary.startswith("~")):
        origin = base if "binary" in overrides.get("proxy", {}) else source_base("proxy.yaml")
        proxy_config["binary"] = absolute(binary, origin)
    document = {
        "requested_config": requested_config(asdict(cfg)),
        "protocol_version": 1, "catalog_revision": catalog["revision"],
        "run_id": cfg.run_id, "benchmark": name, "benchmark_config": benchmark,
        "execution_backend": backend, "environment": environment,
        "model_alias": cfg.model, "model": asdict(model), "harnesses": resolved,
        "proxy": cfg.proxy, "proxy_config": proxy_config,
        "max_workers": workers, "attempts": benchmark.get("attempts", 1),
        "subset": cfg.subset, "limit": cfg.limit, "output_dir": output,
        "cache_dir": str(Path(cache).expanduser().resolve()),
        "offline": cfg.execution.get("offline", benchmark.get("offline", False)),
        "cache_policy": cfg.execution.get("cache_policy", "provider-managed; no reset"),
        "sources": registry.sources + ([cfg.source_file] if cfg.source_file else []),
        "pending_checks": sorted(set(pending + ["task manifest", "container runtime and image prerequisites"])),
    }
    return ResolvedPlan(json.dumps(document, sort_keys=True))
