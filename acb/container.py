"""Container backend resolution (Docker or Podman).

SWE-bench evaluation talks to a container daemon through the `docker` Python SDK,
which honors ``DOCKER_HOST``. Podman exposes a Docker-compatible API socket, so
"use Podman" is just "point DOCKER_HOST at Podman's socket".

Resolution order (first hit wins):
  1. explicit ``docker_host`` in benchmark config
  2. ``DOCKER_HOST`` already in the environment
  3. Podman's API socket, if ``container_backend`` is podman/auto and podman is
     installed with a running machine
  4. None -> the docker SDK falls back to its own default
"""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path
from acb.transport import EnvironmentTransport


def podman_socket() -> str | None:
    """Return the running Podman machine's API socket path, or None."""
    if not shutil.which("podman"):
        return None
    try:
        out = subprocess.run(
            ["podman", "machine", "inspect", "--format",
             "{{.ConnectionInfo.PodmanSocket.Path}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    sock = out.stdout.strip()
    return sock if sock and os.path.exists(sock) else None


def resolve_docker_host(config: dict | None = None) -> str | None:
    config = config or {}
    if config.get("docker_host"):
        return config["docker_host"]
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    backend = config.get("container_backend", "auto")
    if backend in ("podman", "auto"):
        sock = podman_socket()
        if sock:
            return f"unix://{sock}"
    return None


_DOCKER_CONFIG_DIR = Path.home() / ".cache" / "acb" / "docker-config"


def _ensure_empty_docker_config() -> Path:
    """A DOCKER_CONFIG dir with the credential store disabled.

    The docker Python SDK's registry-auth resolution shells out to a
    credential-store helper binary (e.g. `docker-credential-desktop`) even
    for a plain anonymous pull -- and that Docker-Desktop-only helper
    doesn't exist on a Podman-only machine. Verified: this turns a normal
    "image not found, falling back to pull" into a hard crash
    (`docker.errors.DockerException: Credentials store error: ...`) before
    the pull is even attempted, regardless of whether the pull itself would
    have succeeded. Setting `credsStore: ""` skips invoking that helper.
    """
    _DOCKER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_path = _DOCKER_CONFIG_DIR / "config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"credsStore": ""}))
    return _DOCKER_CONFIG_DIR


def container_env(config: dict | None = None) -> dict[str, str]:
    """Resolve SDK connectivity and disable Podman-incompatible credentials."""
    env = os.environ.copy()
    host = resolve_docker_host(config)
    if host:
        env["DOCKER_HOST"] = host
    if (config or {}).get("container_backend") == "podman":
        env["DOCKER_CONFIG"] = str(_ensure_empty_docker_config())
    return env


def grader_env(config, output):
    """Container env for a native grader, with a `docker` shim under Podman.

    Native graders call `docker`; create the selected Podman wrapper in the
    grading artifact directory so installed packages work without a checkout.
    """
    env = container_env(config)
    if config.get("container_backend") == "podman":
        binary = shutil.which("podman")
        if not binary:
            raise FileNotFoundError("Podman is required by the selected grading backend")
        shim = output / "bin"
        shim.mkdir(parents=True, exist_ok=True)
        (shim / "docker").write_text("#!/bin/sh\nexec " + shlex.quote(binary) + ' "$@"\n')
        (shim / "docker").chmod(0o755)
        remaining = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry != str(shim)]
        env["PATH"] = os.pathsep.join([str(shim), *remaining])
    return env


def _transport(container) -> EnvironmentTransport:
    if not isinstance(container, EnvironmentTransport):
        raise TypeError("adapter operations require EnvironmentTransport")
    return container


def container_cp_in(container: EnvironmentTransport, host_path, container_path: str) -> None:
    _transport(container).upload(Path(host_path), container_path)


def container_cp_out(container: EnvironmentTransport, container_path: str, host_path) -> None:
    _transport(container).download(container_path, Path(host_path))


def container_exec_capture(container: EnvironmentTransport, cmd: list[str], workdir: str | None = None,
                           log_output: bool = True) -> str:
    return _transport(container).capture(cmd, workdir)
