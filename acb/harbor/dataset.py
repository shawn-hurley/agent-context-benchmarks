"""Pinned task preparation, deterministic selection, and content manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile

RH_DATASET = "rounakbende/rh-swe-bench"
TASK_CHECKSUM_VERSION = 2


def checksum(path: Path) -> str:
    """Hash a versioned inventory of bytes, entry types and permissions."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"task bundle must be a real directory: {path}")
    inventory = []
    for entry in [path, *sorted(path.rglob("*"))]:
        mode = entry.lstat().st_mode
        record = {"path": entry.relative_to(path).as_posix(), "mode": stat.S_IMODE(mode)}
        if stat.S_ISDIR(mode):
            record["type"] = "directory"
        elif stat.S_ISREG(mode):
            record.update(type="file", sha256=hashlib.sha256(entry.read_bytes()).hexdigest())
        else:
            raise ValueError(f"task bundle contains a symlink or unsupported entry: {entry}")
        inventory.append(record)
    document = {"version": TASK_CHECKSUM_VERSION, "entries": inventory}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def select(names: list[str], subset: list[str] | None, limit: int | None) -> list[str]:
    available = sorted(set(names))
    if subset is not None:
        missing = set(subset) - set(available)
        if missing:
            raise ValueError(f"unknown task IDs: {sorted(missing)}")
        available = [name for name in available if name in subset]
    if limit is not None:
        available = available[:limit]
    if not available:
        raise ValueError("dataset selection is empty")
    return available


def prepare_dataset(plan: dict) -> dict:
    from filelock import FileLock
    from harbor.models.task.task import Task
    config = plan["benchmark_config"]
    from acb.harbor.metrics import metric_definitions
    definitions = metric_definitions(config.get("metrics", []))
    def finish(manifest):
        from acb.harbor.metrics import freeze_metrics
        manifest["metrics"], manifest["metric_files"] = freeze_metrics(definitions, plan["cache_dir"])
        return manifest
    source = config.get("dataset")
    revision = config.get("revision")
    if config.get("path"):
        root = Path(config["path"]).resolve()
        source = source or "local:" + str(root)
    elif source == RH_DATASET:
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("RH SWE-bench requires an immutable 40-character dataset revision")
        from huggingface_hub import HfApi, snapshot_download
        selection_key = hashlib.sha256(json.dumps([plan["subset"], plan["limit"]], sort_keys=True).encode()).hexdigest()[:16]
        root = Path(plan["cache_dir"]) / "datasets" / "rh-swe-bench" / revision / selection_key / "tasks"
        root.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(root.parent / ".lock")):
            if not root.exists():
                if plan["offline"]:
                    raise FileNotFoundError(f"offline: missing dataset bundle {root}; run acb prepare online")
                entries = HfApi().list_repo_files(source, repo_type="dataset", revision=revision)
                prefix = config.get("task_root", "tasks").strip("/") + "/"
                names = [p[len(prefix):].split('/')[0] for p in entries if p.startswith(prefix) and p.endswith('/task.toml')]
                chosen = select(names, plan["subset"], plan["limit"])
                snapshot = Path(snapshot_download(source, repo_type="dataset", revision=revision,
                    allow_patterns=[prefix + name + "/*" for name in chosen]))
                with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
                    staging = Path(tmp) / "tasks"
                    staging.mkdir()
                    for name in chosen:
                        shutil.copytree(snapshot / prefix / name, staging / name)
                    staging.rename(root)
            else:
                # Fetching a larger subset uses a distinct selection cache below.
                pass
    else:
        if plan["offline"]:
            raise ValueError("offline registry resolution is unavailable; use a prepared local task path")
        from harbor.models.job.config import DatasetConfig
        import asyncio
        dataset = DatasetConfig(name=source, version=config.get("version"),
                                registry_url=config.get("registry"))
        if dataset.is_registry():
            from harbor.registry.client.factory import RegistryClientFactory
            client = RegistryClientFactory.create(registry_url=dataset.registry_url)
            name = f"{source}@{dataset.version}" if dataset.version else source
            metadata = asyncio.run(client.get_dataset_metadata(name))
            definitions = metric_definitions([m.model_dump(mode="json") for m in metadata.metrics]) + definitions
            revision = metadata.version
            dataset.version = metadata.version
        elif dataset.is_package():
            from harbor.registry.client.package import PackageDatasetClient
            client = PackageDatasetClient()
            metadata = asyncio.run(client.get_dataset_metadata(f"{source}@{dataset.ref or 'latest'}"))
            definitions = metric_definitions([m.model_dump(mode="json") for m in metadata.metrics]) + definitions
            files = asyncio.run(client.download_dataset_files(metadata))
            if "metric.py" in files:
                definitions.insert(0, {"type": "uv-script", "kwargs": {"script_path": str(files["metric.py"])}})
            revision = metadata.dataset_version_content_hash or metadata.version
            dataset.ref = metadata.version
        task_configs = asyncio.run(dataset.get_task_configs())
        from harbor.tasks.client import TaskClient
        downloads = asyncio.run(TaskClient().download_tasks(task_ids=[item.get_task_id() for item in task_configs]))
        paths = {item.path.name: item.path for item in downloads.results}
        if len(paths) != len(downloads.results):
            raise ValueError("registry contains duplicate task names; select a dataset with unique task identities")
        chosen = select(list(paths), plan["subset"], plan["limit"])
        return finish(_manifest(plan, source, revision, [paths[name] for name in chosen]))
    if not root.is_dir():
        raise FileNotFoundError(f"Harbor task directory not found: {root}")
    names = [p.name for p in root.iterdir() if p.is_dir() and (p / "task.toml").is_file()]
    chosen = select(names, plan["subset"], plan["limit"])
    paths = [root / name for name in chosen]
    if config.get("path"):
        paths = [snapshot_local_task(path, Path(plan["cache_dir"])) for path in paths]
    return finish(_manifest(plan, source, revision, paths))


def snapshot_local_task(path: Path, cache: Path) -> Path:
    """Freeze local input so editing a source task cannot alter an active job."""
    from filelock import FileLock
    digest = checksum(path)
    parent = cache / "datasets" / "local" / f"v{TASK_CHECKSUM_VERSION}" / digest
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / path.name
    with FileLock(str(parent / ".lock")):
        if destination.exists():
            if checksum(destination) != digest:
                raise ValueError(f"cached local task was modified: {destination}")
            return destination
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            staging = Path(temp) / path.name
            shutil.copytree(path, staging)
            if checksum(staging) != digest:
                raise ValueError(f"local task changed during preparation: {path}")
            staging.rename(destination)
    return destination


def _manifest(plan, source, revision, paths):
    from harbor.models.task.task import Task
    tasks = []
    for path in paths:
        task = Task(path)
        config = task.config
        from acb.harbor.runtime import language_profile
        profile = language_profile(source, revision, config.metadata)
        if config.environment.os != "linux":
            raise ValueError(f"{path.name}: only Linux tasks are supported")
        # The existing harness installers use /root and /usr/local/bin.
        if config.agent.user not in (None, "root", "0", 0):
            raise ValueError(f"{path.name}: unsupported agent user {config.agent.user!r}")
        metadata = {}
        if (path / "input.yaml").exists():
            import yaml
            value = yaml.safe_load((path / "input.yaml").read_text())
            if isinstance(value, dict):
                metadata = {key: value[key] for key in ("instance_id", "repo", "base_commit") if key in value}
        tasks.append({"id": path.name, "path": str(path.resolve()), "sha256": checksum(path),
                      "metadata": metadata, "requirements": config.model_dump(mode="json"),
                      "language_profile": profile,
                      "oracle": (path / "solution/solve.sh").is_file()})
    return {"source": source, "revision": revision, "tasks": tasks,
            "task_checksum_version": TASK_CHECKSUM_VERSION,
            "selection_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}


def verify_manifest(manifest):
    if manifest.get("task_checksum_version") != TASK_CHECKSUM_VERSION:
        raise ValueError("prepared task checksum format is outdated or unsupported; prepare again")
    from acb.harbor.metrics import verify_metrics
    verify_metrics(manifest)
    for task in manifest["tasks"]:
        if checksum(Path(task["path"])) != task["sha256"]:
            raise ValueError(f"prepared task changed: {task['id']}; prepare again")
