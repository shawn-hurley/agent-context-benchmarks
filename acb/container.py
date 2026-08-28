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
import subprocess
from pathlib import Path


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


_REPO_BIN = Path(__file__).resolve().parent.parent / "bin"
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
    """os.environ plus a resolved DOCKER_HOST (for the eval subprocess).

    When the resolved backend is Podman, also:
    * prepends this repo's `bin/` to PATH: SWE-bench's own
      `cleanup_container()` (swebench/harness/docker_utils.py) shells out to
      a literal `docker` binary for stop/kill/rm regardless of DOCKER_HOST,
      and there's no `podman-docker` package on Homebrew (it's a Linux-only
      package) to provide one -- `bin/docker` here is a one-line
      `exec podman "$@"` shim.
    * points DOCKER_CONFIG at a config with the credential store disabled
      (see `_ensure_empty_docker_config`).
    """
    env = os.environ.copy()
    host = resolve_docker_host(config)
    if host:
        env["DOCKER_HOST"] = host
        if "podman" in host or shutil.which("docker") is None:
            env["PATH"] = f"{_REPO_BIN}:{env.get('PATH', '')}"
            env["DOCKER_CONFIG"] = str(_ensure_empty_docker_config())
    return env


# ---------------------------------------------------------------------------
# Pod/container orchestration for container-mode generation.
#
# This talks to `podman` directly (CLI, not the docker SDK) since it needs
# pods, which are a Podman concept with no Docker equivalent. Files are moved
# in/out via `podman cp` rather than bind mounts: this Podman machine (AppleHV
# backend on macOS) has no host directory shared into the VM by default, so
# `-v <hostpath>:...` silently fails with "no such file or directory" inside
# the VM even though the path exists on the Mac host.
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    try:
        return subprocess.run(cmd, **kwargs)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n--- stdout ---\n{e.stdout}"
            f"\n--- stderr ---\n{e.stderr}"
        ) from e


def pod_create(name: str) -> str:
    """Create a pod (shared network namespace) and return its name."""
    _run(["podman", "pod", "create", "--name", name])
    return name


def pod_remove(name: str, force: bool = True) -> None:
    cmd = ["podman", "pod", "rm"]
    if force:
        cmd.append("-f")
    cmd.append(name)
    subprocess.run(cmd, capture_output=True, text=True)  # best-effort cleanup


def container_create(
    pod: str, image: str, name: str, command: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Create (but do not start) a container attached to ``pod``."""
    cmd = ["podman", "create", "--pod", pod, "--name", name]
    if env:
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
    cmd += [image]
    if command:
        cmd += command
    _run(cmd)
    return name


def container_cp_in(container: str, host_path, container_path: str) -> None:
    _run(["podman", "cp", str(host_path), f"{container}:{container_path}"])


def container_cp_out(container: str, container_path: str, host_path) -> None:
    _run(["podman", "cp", f"{container}:{container_path}", str(host_path)])


def container_start(container: str) -> None:
    _run(["podman", "start", container])


def container_stop_rm(container: str) -> None:
    subprocess.run(["podman", "stop", "-t", "5", container],
                   capture_output=True, text=True)  # best-effort cleanup
    subprocess.run(["podman", "rm", "-f", container],
                   capture_output=True, text=True)


def container_logs(container: str) -> str:
    out = subprocess.run(["podman", "logs", container], capture_output=True, text=True)
    return out.stdout + out.stderr


def container_exec_capture(container: str, cmd: list[str], workdir: str | None = None) -> str:
    """Run ``cmd`` inside ``container`` and return stdout (raises on nonzero exit)."""
    full = ["podman", "exec"]
    if workdir:
        full += ["--workdir", workdir]
    full += [container, *cmd]
    return _run(full).stdout


def image_exists(name: str) -> bool:
    out = subprocess.run(["podman", "image", "exists", name], capture_output=True)
    return out.returncode == 0


def image_tag(src: str, dst: str) -> None:
    """Alias ``src`` as ``dst`` (``podman tag``) -- no data is copied."""
    _run(["podman", "tag", src, dst])


def container_untar_in(container: str, host_dir: Path, container_path: str) -> None:
    """Tar the *contents* of ``host_dir`` and untar them at ``container_path``.

    Equivalent to ``cd host_dir && tar c . | podman exec -i container tar -xC container_path``.
    Using a tar pipe (rather than ``podman cp dir container:dir``) avoids the
    directory-vs-contents ambiguity in podman cp semantics: ``podman cp src
    container:dst`` copies *src itself* (not its contents) when dst already
    exists as a directory, so the files land at dst/src-basename/** rather than
    dst/**.  The tar pipe always extracts the archive's root into container_path
    directly, regardless of whether container_path already exists.
    """
    import io
    import tarfile as _tarfile

    buf = io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(host_dir, arcname=".")
    buf.seek(0)

    proc = subprocess.Popen(
        ["podman", "exec", "-i", container, "tar", "-xC", container_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, stderr = proc.communicate(input=buf.read())
    if proc.returncode != 0:
        raise RuntimeError(
            f"container_untar_in: tar unpack into {container}:{container_path} failed\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )


def build_image(dockerfile: Path, context_dir: Path, tag: str, platform: str | None = None) -> None:
    cmd = ["podman", "build", f"--tag={tag}", f"--file={dockerfile}"]
    if platform:
        cmd.append(f"--platform={platform}")
    cmd.append(str(context_dir))
    _run(cmd, capture_output=False)
