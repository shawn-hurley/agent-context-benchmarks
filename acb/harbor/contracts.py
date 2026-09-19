"""Inspect engine-enforced task identity and limits at the pinned Harbor boundary."""
import asyncio
import hashlib
import json
import math
import re
from pathlib import Path

import yaml


def verifier_contract_key(environment_dir, config):
    value = [str(Path(environment_dir).resolve()), config.model_dump(mode="json")]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_service_names(tasks, reserved):
    for task in tasks:
        path = Path(task["path"]) / "environment/docker-compose.yaml"
        if not path.exists():
            continue
        services = (yaml.safe_load(path.read_text()) or {}).get("services", {})
        collisions = set(services) & set(reserved)
        if collisions:
            raise ValueError(f"{task['id']}: task Compose services conflict with ACB services: {sorted(collisions)}")


async def engine_json(environment, *arguments):
    process = await asyncio.create_subprocess_exec(
        *environment.runtime().engine, *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"container engine inspection failed ({process.returncode})")
    return json.loads(stdout)


def container_contract(container, image, requirements):
    """Keep only stable identity/policy fields; never persist container secrets."""
    identity = container.get("Image", "").removeprefix("sha256:")
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("container engine did not return an immutable image identity")
    if image.get("Id", "").removeprefix("sha256:") != identity:
        raise ValueError("container image identity differs from inspected image")
    host = container.get("HostConfig", {})
    cpus = host.get("NanoCpus", 0) / 1_000_000_000
    if not cpus and host.get("CpuQuota", 0) > 0 and host.get("CpuPeriod", 0) > 0:
        cpus = host["CpuQuota"] / host["CpuPeriod"]
    memory = host.get("Memory", 0)
    requested_cpu, requested_memory = requirements.get("cpus"), requirements.get("memory_mb")
    if requested_cpu is not None and not math.isclose(cpus, requested_cpu):
        raise ValueError(f"CPU limit not enforced: requested {requested_cpu}, observed {cpus}")
    if requested_memory is not None and memory != requested_memory * 1024 * 1024:
        raise ValueError(f"memory limit not enforced: requested {requested_memory} MiB, observed {memory} bytes")
    # Harbor's Docker/Podman providers cannot enforce these task requirements.
    for field in ("storage_mb", "gpus", "gpu_types", "tpu"):
        if requirements.get(field):
            raise ValueError(f"unsupported task resource requirement: {field}")
    return {
        "image_id": "sha256:" + identity,
        "image_architecture": image.get("Architecture"),
        "image_os": image.get("Os"),
        "resources": {"cpu_limit": cpus, "memory_limit_bytes": memory},
        "privileged": bool(host.get("Privileged", False)),
        "mounts": sorted([
            {"destination": mount["Destination"], "type": mount["Type"],
             "read_only": not mount.get("RW", True)}
            for mount in container.get("Mounts", [])
        ], key=lambda mount: mount["destination"]),
    }


async def inspect_container_contract(environment, credential_names=()):
    # Harbor 0.23 exposes no public container-inspection operation. Keep this
    # version-specific access confined to the provider boundary.
    container_id = await environment._platform._resolve_service_container("main")
    container = (await engine_json(environment, "container", "inspect", container_id))[0]
    image = (await engine_json(environment, "image", "inspect", container["Image"]))[0]
    env_names = {entry.partition("=")[0] for entry in container.get("Config", {}).get("Env", [])}
    if env_names.intersection(credential_names):
        raise ValueError("provider credential variable is present in the agent container")
    contract = container_contract(container, image, environment.task_env_config.model_dump(mode="json"))
    contract["network_policy"] = environment.network_policy.model_dump(mode="json")
    contract["network_capabilities"] = {
        key: getattr(environment.capabilities, key)
        for key in ("disable_internet", "network_allowlist", "dynamic_network_policy")
    }
    return contract


async def inspect_task_service_images(environment):
    path = Path(environment.environment_dir) / "docker-compose.yaml"
    services = (yaml.safe_load(path.read_text()) or {}).get("services", {}) if path.exists() else {}
    names = set(services) - {"main"}
    if environment._enable_egress_control:
        names.add(environment._EGRESS_CONTROL_SERVICE_NAME)
    images = {}
    for name in sorted(names):
        identifier = await environment._platform._resolve_service_container(name)
        container = (await engine_json(environment, "container", "inspect", identifier))[0]
        image = (await engine_json(environment, "image", "inspect", container["Image"]))[0]
        record = container_contract(container, image, {})
        images[name] = record["image_id"]
    return images


async def verify_provider_images(environment, records):
    observed = {}
    for service, record in sorted(records.items()):
        identifier = await environment._platform._resolve_service_container(service)
        container = (await engine_json(environment, "container", "inspect", identifier))[0]
        identity = "sha256:" + container["Image"].removeprefix("sha256:")
        if identity != record["image_id"]:
            raise ValueError(f"{service}: provider image changed since preparation")
        observed[service] = identity
    return observed
