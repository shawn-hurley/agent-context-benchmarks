"""Discover task IDs without exporting tasks, starting containers or calling models."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import tempfile


def _cached_tasks(plan):
    cache = Path(plan["cache_dir"])
    tasks = []
    if plan["benchmark_config"].get("dataset") == "rounakbende/rh-swe-bench":
        revision = plan["benchmark_config"].get("revision", "")
        return [{"id": path.name} for path in (cache / "datasets/rh-swe-bench" / revision).glob("*/tasks/*")
                if (path / "task.toml").is_file()]
    from acb.report_data import read_object
    for path in cache.glob("plans/*/preparation-*/prepared.json"):
        record = read_object(path)
        keys = ("dataset", "revision", "split", "version", "registry", "source", "target", "instances", "benchmark_cache_dir")
        if (record.get("benchmark") != plan["benchmark"] or any(
                record.get("benchmark_config", {}).get(key) != plan["benchmark_config"].get(key) for key in keys)):
            continue
        for task in record.get("manifest", {}).get("tasks", []):
            info = task.get("metadata", {})
            tasks.append({"id": info.get("instance_id") or task["id"], "repo": info.get("repo")})
    return tasks


def _remote_tasks(plan):
    config = plan["benchmark_config"]
    if plan["benchmark"] in {"swebench", "swebench-lite"}:
        from acb.benchmarks import make_benchmark
        try:
            instances = make_benchmark(plan["benchmark"], {**config, "offline": False}).load_instances()
        except ModuleNotFoundError as error:
            if error.name != "datasets":
                raise
            raise ValueError("SWE-bench discovery requires the datasets extra: uv sync --extra datasets") from error
        return [{"id": item.instance_id, "repo": item.repo} for item in instances]
    if config.get("dataset") == "rounakbende/rh-swe-bench":
        from huggingface_hub import HfApi
        prefix = config.get("task_root", "tasks").strip("/") + "/"
        paths = HfApi().list_repo_files(config["dataset"], repo_type="dataset", revision=config.get("revision"))
        return [{"id": name[len(prefix):].split("/")[0]} for name in paths
                if name.startswith(prefix) and name.endswith("/task.toml")]
    from harbor.models.job.config import DatasetConfig
    dataset = DatasetConfig(name=config.get("dataset"), version=config.get("version"), registry_url=config.get("registry"))
    tasks = asyncio.run(dataset.get_task_configs())
    names = [item.path.name if item.path is not None else item.name.rsplit("/", 1)[-1] for item in tasks]
    if len(names) != len(set(names)):
        raise ValueError("dataset contains duplicate task names; use a dataset with unique task IDs")
    return [{"id": name} for name in names]


def discover_tasks(plan, *, allow_download=False):
    config = plan["benchmark_config"]
    if not config.get("path") and config.get("dataset") == "rounakbende/rh-swe-bench" and not re.fullmatch(
            r"[a-f0-9]{40}", str(config.get("revision", ""))):
        raise ValueError("RH SWE-bench requires an immutable 40-character dataset revision")
    if allow_download and plan["offline"]:
        raise ValueError("--download conflicts with execution.offline; use local/cached discovery or disable offline in YAML")
    identity = hashlib.sha256(json.dumps({"benchmark": plan["benchmark"], "config": config}, sort_keys=True).encode()).hexdigest()
    index = Path(plan["cache_dir"]) / "discovery" / (identity + ".json")
    source = config.get("path") or config.get("dataset") or config.get("benchmark_cache_dir") or plan["benchmark"]
    complete, notes = True, []
    if config.get("path"):
        root = Path(config["path"])
        if not root.is_dir():
            raise FileNotFoundError(f"task directory not found: {root}")
        tasks = [{"id": path.name} for path in sorted(root.iterdir()) if (path / "task.toml").is_file()]
    elif plan["benchmark"] == "scarfbench":
        from acb.benchmarks.scarfbench import ScarfBench
        instances = ScarfBench(config).load_instances()
        tasks = [{"id": item.instance_id} for item in instances]
    elif config.get("dataset") and Path(config["dataset"]).is_file():
        from acb.benchmarks import make_benchmark
        instances = make_benchmark(plan["benchmark"], config).load_instances()
        tasks = [{"id": item.instance_id, "repo": item.repo} for item in instances]
    elif allow_download:
        tasks = _remote_tasks(plan)
        index.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=index.parent, delete=False) as stream:
            json.dump(tasks, stream)
            temporary = Path(stream.name)
        try:
            temporary.replace(index)
        finally:
            temporary.unlink(missing_ok=True)
    elif index.is_file():
        tasks = json.loads(index.read_text())
        notes.append("Using cached discovery metadata; --download refreshes it.")
    else:
        tasks = _cached_tasks(plan)
        complete = False
        notes.append("Only previously cached task IDs are shown. Use --download to fetch the full listing; this may download dataset metadata/data.")
    native_benchmark = plan["benchmark"] in {"scarfbench", "swebench", "swebench-lite"} and not config.get("path")
    excluded = set(config.get("exclude", [])) if native_benchmark else set()
    excluded_repos = set(config.get("exclude_repos", [])) if native_benchmark else set()
    tasks = list({item["id"]: item for item in tasks
                  if item["id"] not in excluded and item.get("repo") not in excluded_repos}.values())
    # Preserve adapter order: ScarfBench limits cover apps by conversion; local
    # and registry Harbor tasks use the sorted order in dataset.select().
    if config.get("path") or plan["benchmark"] not in {"scarfbench", "swebench", "swebench-lite"}:
        tasks.sort(key=lambda item: item["id"])
    selected = [item["id"] for item in tasks if not plan.get("subset") or item["id"] in plan["subset"]]
    if plan.get("limit"):
        selected = selected[:plan["limit"]]
    known = {item["id"] for item in tasks}
    missing = set(plan.get("subset") or []) - known
    if missing:
        notes.append("Requested IDs absent from this listing: " + ", ".join(sorted(missing)))
    uncertain_limit = (not complete and plan.get("limit") is not None and
                       (not plan.get("subset") or len(plan["subset"]) > plan["limit"]))
    if uncertain_limit:
        notes.append("Selection under the task limit is unknown with partial coverage; --download fetches the full listing.")
    if not tasks:
        notes.append("No eligible tasks found in this listing.")
    return {"benchmark": plan["benchmark"], "source": str(source), "complete": complete,
            "tasks": [{**item, "selected": (None if uncertain_limit and
                      (not plan.get("subset") or item["id"] in plan["subset"]) else item["id"] in selected)}
                      for item in tasks], "notes": notes}
