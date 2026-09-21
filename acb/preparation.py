"""Verified immutable assets shared by all prepared trials."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile

from acb.harnesses._cache import binary_cache_lock


def prepare(plan) -> dict:
    """Dispatch preparation using the same normalized backend as execution."""
    from acb.harbor.backend import prepare as prepare_harbor
    return prepare_harbor(plan)


def prepare_rtk(config: dict, harness: str, plan: dict, arch: str, *, runtime=None) -> None:
    """Prepare shared RTK assets; interpreter discovery is task-runtime specific."""
    for extension in config.get("execution_integrations", []):
        if extension["name"] != "rtk":
            continue
        if not extension.get("binary_path"):
            extension["binary_path"], extension["sha256"] = ensure_rtk(
                Path(plan["cache_dir"]), plan["environment"], arch, plan["offline"])
        else:
            digest = hashlib.sha256(Path(extension["binary_path"]).read_bytes()).hexdigest()
            if digest != str(extension.get("sha256", "")).lower():
                raise ValueError("RTK artifact checksum mismatch")
        if harness == "claude-code" and runtime is not None:
            if not runtime.get("python_path"):
                raise ValueError("Claude RTK hooks require Python >=3.8 in the task environment")
            extension.setdefault("python_path", runtime["python_path"])


def _freeze_image_references(plan, references):
    records = {}
    for service, reference in references.items():
        command = [plan["environment"], "image", "inspect", reference]
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if inspected.returncode:
            if plan["offline"]:
                raise FileNotFoundError(f"offline: missing provider image {reference}")
            subprocess.run([plan["environment"], "pull", reference], check=True, timeout=120)
            inspected = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        image = json.loads(inspected.stdout)[0]
        identity = image.get("Id", "").removeprefix("sha256:")
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError(f"provider image {service} has no immutable image ID")
        records[service] = {"reference": reference, "image_id": "sha256:" + identity,
                            "architecture": image.get("Architecture"), "os": image.get("Os")}
    return records


def freeze_metric_runtime(plan: dict) -> dict:
    result = deepcopy(plan)
    if any(m["type"] == "uv-script" for m in plan.get("manifest", {}).get("metrics", [])):
        image = _freeze_image_references(plan, {
            "dataset-metric": "ghcr.io/astral-sh/uv:python3.12-bookworm-slim",
        })["dataset-metric"]
        result["metric_runtime"] = {**image, "engine": plan["environment"], "offline": plan["offline"]}
    return result


def freeze_provider_images(plan: dict) -> dict:
    """Resolve service references once; execution uses local immutable IDs."""
    result = freeze_metric_runtime(plan)
    references = {"acb-praxis": plan["benchmark_config"]["praxis_image"]}
    if plan.get("caveman_image"):
        references["acb-caveman"] = plan["caveman_image"]
    result["provider_images"] = _freeze_image_references(plan, references)
    return result

RTK_COMMIT = "fde0a8f185945556f51718de0f4c430bb62b3df6"
RTK_RECIPE = '''FROM rust:1.91-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends git libssl-dev pkg-config
RUN git init /src && git -C /src remote add origin https://github.com/rtk-ai/rtk.git && git -C /src fetch --depth 1 origin COMMIT && git -C /src checkout --detach FETCH_HEAD
WORKDIR /src
RUN cargo build --release --locked --bin rtk && test "$(target/release/rtk --version)" = 'rtk 0.48.0'
FROM debian:bookworm-slim
COPY --from=build /src/target/release/rtk /rtk
'''.replace('COMMIT', RTK_COMMIT)


# Static upstream amd64 release avoids executing an amd64 Rust compiler under
# QEMU on ARM controllers. Tag v0.48.0 resolves to RTK_COMMIT.
RTK_AMD64_URL = "https://github.com/rtk-ai/rtk/releases/download/v0.48.0/rtk-x86_64-unknown-linux-musl.tar.gz"
RTK_AMD64_SHA256 = "e4e650fa1677c0de2f6839a6040d7b17f312d32f163c402b75af70e9e5af1a91"


def ensure_rtk(cache: Path, engine: str, arch: str, offline: bool) -> tuple[str, str]:
    if arch not in ("arm64", "amd64"):
        raise ValueError(f"unsupported RTK architecture: {arch}")
    recipe_id = RTK_AMD64_URL + RTK_AMD64_SHA256 if arch == "amd64" else RTK_RECIPE
    key = hashlib.sha256((recipe_id + arch).encode()).hexdigest()
    root = cache / "rtk" / key
    binary, manifest = root / "rtk", root / "manifest.json"
    with binary_cache_lock(cache, "rtk-" + key):
        if binary.exists() and manifest.exists():
            try:
                record = json.loads(manifest.read_text())
            except (OSError, ValueError) as error:
                raise ValueError(f"invalid cached RTK manifest: {manifest}") from error
            expected = {"source_commit": RTK_COMMIT, "architecture": arch, "recipe": key}
            if not isinstance(record, dict) or any(record.get(name) != value for name, value in expected.items()):
                raise ValueError(f"cached RTK provenance mismatch: {manifest}")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            if digest != record.get("sha256"):
                raise ValueError(f"cached RTK checksum mismatch: {binary}")
            if not binary.stat().st_mode & 0o111:
                raise ValueError(f"cached RTK binary is not executable: {binary}")
            return str(binary), digest
        if offline:
            raise FileNotFoundError(f"offline: missing RTK artifact for {arch}")
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent) as temp:
            staging = Path(temp)
            artifact = staging / "artifact"
            artifact.mkdir()
            if arch == "amd64":
                from acb.downloads import download_file
                archive = staging / "release.tar.gz"
                download_file(RTK_AMD64_URL, archive)
                if hashlib.sha256(archive.read_bytes()).hexdigest() != RTK_AMD64_SHA256:
                    raise ValueError("RTK release archive checksum mismatch")
                with tarfile.open(archive, "r:gz") as bundle:
                    entries = [entry for entry in bundle.getmembers()
                               if entry.isfile() and Path(entry.name).name == "rtk"]
                    if len(entries) != 1 or entries[0].size > 64 * 1024 * 1024:
                        raise ValueError("invalid RTK release archive")
                    with bundle.extractfile(entries[0]) as handle:
                        (artifact / "rtk").write_bytes(handle.read())
                (artifact / "rtk").chmod(0o755)
            else:
                recipe = staging / "Dockerfile"
                recipe.write_text(RTK_RECIPE)
                image = "acb-rtk-build:" + key[:16]
                subprocess.run([engine, "build", "--platform", "linux/" + arch, "-t", image, str(staging)], check=True)
                container = subprocess.check_output([engine, "create", image, "/bin/true"], text=True).strip()
                try:
                    subprocess.run([engine, "cp", container + ":/rtk", str(artifact / "rtk")], check=True)
                finally:
                    subprocess.run([engine, "rm", "-f", container], check=True, capture_output=True)
            if not (artifact / "rtk").stat().st_mode & 0o111:
                raise ValueError("built RTK binary is not executable")
            digest = hashlib.sha256((artifact / "rtk").read_bytes()).hexdigest()
            (artifact / "manifest.json").write_text(json.dumps({"sha256": digest, "source_commit": RTK_COMMIT, "architecture": arch, "recipe": key}))
            # Publish the binary and manifest together. Keep an incomplete old
            # entry until its replacement is ready, and restore it on failure.
            previous = staging / "previous"
            if root.exists():
                root.replace(previous)
            try:
                artifact.replace(root)
            except BaseException:
                if previous.exists():
                    previous.replace(root)
                raise
            return str(binary), digest


def prepare_assets(plan: dict) -> dict:
    contracts = plan.get("runtime_contracts")
    expected = {task["id"] for task in plan["manifest"]["tasks"]}
    if not contracts or set(contracts) != expected:
        raise ValueError("inspect every selected task environment before preparing assets")
    result = deepcopy(plan)
    result["task_plans"] = {}
    for task_id in sorted(contracts):
        runtime = contracts[task_id]
        prepared = _prepare_assets_for_runtime(plan, runtime)
        result["task_plans"][task_id] = {"runtime": deepcopy(runtime), "harnesses": prepared["harnesses"]}
        result["benchmark_config"]["praxis_image"] = prepared["benchmark_config"]["praxis_image"]
        if "caveman_image" in prepared:
            result["caveman_image"] = prepared["caveman_image"]
    return result


def prepare_response_skills(plan: dict) -> dict:
    """Materialize pinned response instructions identically for both backends."""
    plan = deepcopy(plan)
    cache = Path(plan["cache_dir"])
    for config in plan["harnesses"].values():
        for skill in config.get("skills", []):
            if skill.get("name") != "caveman" or "source_type" in skill:
                continue
            asset = files("acb").joinpath("catalog/caveman/SKILL.md").read_bytes()
            if hashlib.sha256(asset).hexdigest() != skill["sha256"]:
                raise ValueError("Caveman instruction asset checksum mismatch")
            intensity = skill.get("options", {}).get("intensity", "lite")
            if intensity not in ("lite", "full", "ultra"):
                raise ValueError("skills.caveman.options.intensity must be lite, full or ultra")
            root = cache / "skills" / "caveman" / skill["sha256"]
            with binary_cache_lock(cache, "caveman-" + skill["sha256"]):
                root.mkdir(parents=True, exist_ok=True)
                (root / "SKILL.md").write_bytes(asset)
            skill.update(source_type="local", source_path=str(root))
            # Explicit delivery through the existing additive system instruction path.
            content = asset.decode().split('---', 2)[2].strip()
            instruction = f"Caveman response style, intensity={intensity}. Apply to narration only; preserve task instructions, code and exact errors.\n{content}\nSelected intensity: {intensity}."
            config["system_prompt"] = '\n\n'.join(filter(None, [config.get("system_prompt"), instruction]))
            config.setdefault("instruction_evidence", []).append({
                "name": "caveman", "sha256": skill["sha256"], "content": instruction,
                "priority": "additive system instruction", "intensity": intensity,
                "status": "prepared; delivery recorded at harness launch", "source_revision": skill["version"],
            })
    return plan


def _prepare_assets_for_runtime(plan: dict, runtime: dict) -> dict:
    plan = prepare_response_skills(plan)
    arch = runtime["arch"]
    if arch not in ("arm64", "amd64"):
        raise ValueError("architecture must be arm64 or amd64")
    requested_arch = plan["benchmark_config"].get("architecture")
    if requested_arch and requested_arch != arch:
        raise ValueError(f"task architecture {arch} conflicts with requested {requested_arch}")
    for name, config in plan["harnesses"].items():
        if "language_environment" in runtime:
            from acb.harbor.runtime import apply_language_profile
            apply_language_profile(config, runtime["language_environment"])
        if config.get("model_middleware"):
            for middleware in config["model_middleware"]:
                if middleware["name"] == "caveman":
                    if not runtime.get("python_path"):
                        raise ValueError("Caveman requires Python >=3.8 in the task environment")
                    middleware["python_path"] = runtime["python_path"]
            add_recovery_guidance(config)
        prepare_rtk(config, name, plan, arch, runtime=runtime)
    plan["architecture"] = arch
    if not plan["benchmark_config"].get("praxis_image"):
        root = Path(str(files("acb").joinpath("assets/praxis")))
        plan["benchmark_config"]["praxis_image"] = prepare_image(root, "Containerfile", "acb-praxis", plan)
    if any(settings.get("model_middleware") for settings in plan["harnesses"].values()):
        root = Path(str(files("acb.integrations").joinpath("assets/caveman")))
        plan["caveman_image"] = prepare_image(root, "Containerfile", "acb-caveman", plan)
    return plan


def prepare_image(context: Path, recipe: str, prefix: str, plan: dict) -> str:
    from acb.image_cache import prepare_image as prepare
    return prepare(context, recipe, prefix, plan)


def add_recovery_guidance(config: dict) -> None:
    recovery = "Successful repetitive tool logs may be compressed. To recover their exact original bytes, run acb-recall HANDLE using your shell tool. Preserve task requirements and inspect originals whenever details are needed."
    if recovery not in config.get("system_prompt", ""):
        config["system_prompt"] = "\n\n".join(filter(None, [config.get("system_prompt"), recovery]))
