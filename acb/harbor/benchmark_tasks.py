"""Export retained benchmark inputs as immutable Harbor task bundles.

Only environment/ enters the agent image. Grading inputs remain in tests/ on
the controller and are consumed by BenchmarkVerifier, never uploaded to agents.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request

import yaml

from acb.benchmarks import make_benchmark
from acb.harbor.dataset import snapshot_local_task
from acb.utils import normalize_instance_id_for_path
from acb.workflows import validate_workflow_snapshot

BENCHMARKS = {"scarfbench", "swebench", "swebench-lite"}


def export_tasks(plan):
    workflow = plan.get("workflow")
    if workflow:
        validate_workflow_snapshot(workflow)
    config = {**plan["benchmark_config"], "offline": plan["offline"],
              "container_backend": plan.get("environment", "podman")}
    benchmark = make_benchmark(plan["benchmark"], config)
    instances = benchmark.load_instances(subset=plan["subset"], limit=None)
    if plan["subset"]:
        missing = set(plan["subset"]) - {item.instance_id for item in instances}
        if missing:
            raise ValueError(f"unknown task IDs: {sorted(missing)}")
    excluded = set(config.get("exclude", []))
    excluded_repos = set(config.get("exclude_repos", []))
    instances = [item for item in instances
                 if item.instance_id not in excluded and item.repo not in excluded_repos]
    if plan["limit"] is not None:
        instances = instances[:plan["limit"]]
    if not instances:
        raise ValueError("dataset selection is empty")
    paths, names = [], set()
    cache = Path(plan["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as temporary:
        for instance in instances:
            name = normalize_instance_id_for_path(instance.instance_id)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in names:
                raise ValueError(f"unsafe or colliding benchmark task ID: {instance.instance_id!r}")
            names.add(name)
            root = Path(temporary) / name
            for directory in ("environment", "tests"):
                (root / directory).mkdir(parents=True)
            (root / "instruction.md").write_text(instance.prompt)
            (root / "input.yaml").write_text(yaml.safe_dump({
                "instance_id": instance.instance_id, "repo": instance.repo,
                "base_commit": instance.base_commit,
            }))
            record = {"benchmark": plan["benchmark"], "instance_id": instance.instance_id,
                      "extra": instance.extra}
            if plan["benchmark"] == "scarfbench":
                export_scarfbench(root, instance, config, workflow=workflow)
                workdir, conda = "/work", None
            else:
                conda = export_swebench(root, instance, config, offline=plan["offline"])
                workdir = "/testbed"
            (root / "tests/benchmark.json").write_text(json.dumps(record, indent=2))
            # This stub makes the bundle valid for Harbor. ACB supplies the
            # custom verifier explicitly; using a plain shell verifier fails.
            (root / "tests/test.sh").write_text(
                "#!/bin/sh\necho 'Use acb.harbor.benchmark_verifier:BenchmarkVerifier' >&2\nexit 1\n")
            runtime = '[metadata.acb_runtime]\n' + (f'conda_env = {json.dumps(conda)}\n' if conda else '')
            if workflow:
                export_workflow(root, workflow, workdir)
            if plan["benchmark"] == "scarfbench":
                from acb.maven_cache import cached_recipe
                recipe_path = root / "environment/Dockerfile"
                recipe_path.write_text(cached_recipe(recipe_path.read_text(), config))
            task_toml = (
                'version = "1.0"\n' +
                ('multi_step_reward_strategy = "final"\n' if workflow else '') + runtime +
                '\n[environment]\nbuild_timeout_sec = 3600\n' +
                f'workdir = {json.dumps(workdir)}\n' +
                (f'skills_dir = {json.dumps(workflow["environment"]["skills_dir"])}\n'
                 if workflow and workflow.get("environment", {}).get("skills_dir") else '') +
                '\n[agent]\ntimeout_sec = 1800\n' +
                f'\n[verifier]\ntimeout_sec = {int(config.get("validate_timeout_minutes", 60)) * 60}\n')
            if workflow:
                for step in workflow["steps"]:
                    task_toml += f'\n[[steps]]\nname = {json.dumps(step["name"])}\n'
                    if step["gate"]["type"] == "artifacts":
                        task_toml += 'min_reward = 1\n'
                    task_toml += f'[steps.agent]\ntimeout_sec = {step["timeout_sec"]}\n'
            (root / "task.toml").write_text(task_toml)
            paths.append(snapshot_local_task(root, cache))
    return paths


def export_scarfbench(root, instance, config, *, workflow=None):
    from acb.benchmarks.scarfbench import _CONTAINERFILE, _HARNESS_FILES, _ensure_validation_harness
    info = instance.extra
    bundle = Path(config["benchmark_cache_dir"])
    app = bundle / info["layer"] / info["app"]
    source = root / "environment/source"
    original = app / info["source"]
    shutil.copytree(original, source,
                    ignore=lambda directory, names: set(names) & _HARNESS_FILES if Path(directory) == original else set())
    # Freeze the target separately; make compatibility files in our snapshot,
    # never mutate a user's benchmark checkout during export.
    target = root / "tests/benchmark" / info["layer"] / info["app"] / info["target"]
    shutil.copytree(app / info["target"], target)
    _ensure_validation_harness(root / "tests/benchmark", layer=info["layer"],
                               app=info["app"], framework=info["target"])
    original_target = app / info["target"]
    shutil.copytree(original_target, root / "solution/project",
                    ignore=lambda directory, names: set(names) & _HARNESS_FILES if Path(directory) == original_target else set())
    (root / "solution/solve.sh").write_text(
        "#!/bin/sh\nset -eu\nfind /work -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +\ncp -a /solution/project/. /work/\n")
    image = config.get("scarfbench_image")
    recipe = _CONTAINERFILE.read_text()
    if image:
        inspected = subprocess.run([config["container_backend"], "image", "inspect", image,
                                    "--format", "{{.Id}}"], capture_output=True, text=True, timeout=30)
        if inspected.returncode == 0 and not (workflow and workflow.get("environment", {}).get("dockerfile")):
            identity = inspected.stdout.strip()
            if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", identity):
                raise ValueError("container engine returned an invalid ScarfBench image identity")
            recipe = f"FROM {identity}\n"
        # As in the retained adapter, an uncached generation image is built
        # from the packaged recipe. Never require a local-only tag to exist
        # in a remote registry.
    (root / "environment/Dockerfile").write_text(recipe + '\nCOPY source/ /work/\nWORKDIR /work\n')


def export_workflow(root, workflow, workdir):
    """Freeze workflow instructions and image assets into each Harbor task."""
    source = Path(workflow["source_dir"])
    environment = workflow.get("environment", {})
    destination = root / "environment"
    if environment.get("dockerfile"):
        recipe = (source / environment["dockerfile"]).read_text()
        if workdir == "/work":
            recipe += "\nCOPY source/ /work/\n"
        recipe += f"\nWORKDIR {workdir}\n"
    else:
        recipe = (destination / "Dockerfile").read_text()
    recipe += "\n" + environment.get("dockerfile_append", "")
    (destination / "Dockerfile").write_text(recipe)
    for asset in environment.get("assets", []):
        target = destination / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / asset, target)
    for step in workflow["steps"]:
        instruction = root / "steps" / step["name"] / "instruction.md"
        instruction.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / step["instruction"], instruction)
    (root / "tests/workflow.json").write_text(json.dumps({
        "name": workflow["name"], "sha256": workflow["sha256"],
        "workdir": workdir,
        "steps": [{"name": step["name"], "gate": step["gate"]} for step in workflow["steps"]],
        "exclude_from_grading": workflow.get("exclude_from_grading", []),
    }, indent=2))


def export_swebench(root, instance, config, *, offline):
    from acb.benchmarks.image_builder import fetch_dockerfile, patch_dockerfile_for_arch
    row = instance.extra["dataset_row"]
    required = {"image", "repo", "version", "FAIL_TO_PASS", "PASS_TO_PASS", "log_parser", "eval_type", "eval_script"}
    missing = required - row.keys()
    if missing:
        raise ValueError(f"{instance.instance_id}: SWE-bench grading fields missing: {sorted(missing)}")
    local = config.get("task_repo_cache_dir")
    if offline and not (local and (Path(local) / "tasks" / instance.instance_id / "Dockerfile").is_file()):
        raise FileNotFoundError("offline SWE-bench export requires task_repo_cache_dir with the selected Dockerfiles")
    recipe = fetch_dockerfile(instance.instance_id, task_repo=config.get("task_repo", "SWE-bench/swe-bench-tasks"),
                              task_repo_cache_dir=local)
    arch = config.get("image_arch", "auto")
    if arch == "auto":
        arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
    recipe = patch_dockerfile_for_arch(recipe, arch)
    freeze_swebench_assets(root, row, instance.instance_id, config, offline=offline)
    conda = "testbed" if "/opt/miniconda3" in recipe else None
    (root / "environment/Dockerfile").write_text(recipe +
        '\nRUN git -C /testbed ls-files --others --exclude-standard > /tmp/.acb-baseline-untracked.txt\nWORKDIR /testbed\n')
    if row.get("patch"):
        (root / "solution").mkdir()
        (root / "solution/gold.patch").write_text(row["patch"])
        (root / "solution/solve.sh").write_text("#!/bin/sh\ncd /testbed && git apply /solution/gold.patch\n")
    return conda


def freeze_swebench_assets(root, row, instance_id, config, *, offline):
    """Snapshot binary test assets using the official task-repo layout."""
    assets = row.get("image_assets") or {}
    if isinstance(assets, str):
        assets = json.loads(assets)
    for group in ("test_patch", "patch"):
        for asset in assets.get(group) or []:
            relative = Path(asset.get("path", ""))
            if not asset.get("path"):
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe SWE-bench asset path: {relative}")
            suffix = Path("tasks") / instance_id / "test_assets" / relative
            local = Path(config["task_repo_cache_dir"]) / suffix if config.get("task_repo_cache_dir") else None
            if local and local.is_file():
                data = local.read_bytes()
            elif offline:
                raise FileNotFoundError(f"offline: missing SWE-bench test asset {relative}")
            else:
                url = asset.get("url", "")
                if not url.startswith(("https://", "http://")):
                    raise ValueError(f"SWE-bench test asset has no downloadable URL: {relative}")
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read()
            destination = root / "tests/task_repo" / suffix
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
