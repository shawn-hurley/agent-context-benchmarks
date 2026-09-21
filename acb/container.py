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


def _run(cmd: list[str], log_output: bool = True, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    
    # Prevent TTY detection to avoid terminal control sequences
    # that interfere with Rich Live display during QUEUED→RUNNING transitions
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("close_fds", True)  # Close inherited file descriptors to prevent /dev/tty access
    env = kwargs.get("env", os.environ.copy())
    if "TERM" not in env:
        env["TERM"] = "dumb"
    
    # Additional environment variables to prevent Podman/Docker from detecting
    # TTY and showing progress bars or other interactive output that might
    # bypass stdout/stderr redirection and write directly to /dev/tty
    env.setdefault("DOCKER_BUILDKIT", "0")
    env.setdefault("PODMAN_PROGRESS_BAR", "0")
    env.setdefault("BUILDAH_PROGRESS_BAR", "0")
    
    kwargs["env"] = env
    
    try:
        result = subprocess.run(cmd, **kwargs)
        
        # Log container operations for debugging (always enabled)
        from acb.logging_config import log_debug
        cmd_str = " ".join(cmd)
        log_debug(f"[CONTAINER] {cmd_str}")
        
        # Conditionally log stdout/stderr based on log_output parameter
        # Command is always logged above; this controls output logging only
        if log_output:
            if result.stdout and result.stdout.strip():
                # Truncate to 1000 chars to keep logs readable
                stdout_truncated = result.stdout[:1000]
                if len(result.stdout) > 1000:
                    stdout_truncated += f"... ({len(result.stdout)} total chars)"
                log_debug(f"[CONTAINER OUT] {stdout_truncated}")
            
            if result.stderr and result.stderr.strip():
                stderr_truncated = result.stderr[:1000]
                if len(result.stderr) > 1000:
                    stderr_truncated += f"... ({len(result.stderr)} total chars)"
                log_debug(f"[CONTAINER ERR] {stderr_truncated}")
        
        return result
    except subprocess.CalledProcessError as e:
        # Log the failure with full details
        from acb.logging_config import log_error
        cmd_str = " ".join(cmd)
        log_error(f"[CONTAINER FAIL] {cmd_str}")
        
        if e.stdout:
            log_error(f"[CONTAINER STDOUT] {e.stdout}")
        if e.stderr:
            log_error(f"[CONTAINER STDERR] {e.stderr}")
        
        # Preserve error details in exception
        raise RuntimeError(
            f"command failed: {cmd_str}\n--- stdout ---\n{e.stdout}"
            f"\n--- stderr ---\n{e.stderr}"
        ) from e








def container_cp_in(container: str, host_path, container_path: str) -> None:
    """Copy file/directory from host into container.
    
    Includes diagnostic logging to track potential screen blanking issues.
    """
    if isinstance(container, EnvironmentTransport):
        container.upload(Path(host_path), container_path)
        return
    from acb.logging_config import log_debug
    import os
    if os.environ.get("ACB_DEBUG_UI"):
        log_debug(f"[CP_IN_START] {host_path} -> {container}:{container_path}")
    try:
        _run(["podman", "cp", str(host_path), f"{container}:{container_path}"])
        if os.environ.get("ACB_DEBUG_UI"):
            log_debug(f"[CP_IN_END] {host_path} -> {container}:{container_path}")
    except Exception as e:
        if os.environ.get("ACB_DEBUG_UI"):
            log_debug(f"[CP_IN_FAILED] {host_path} -> {container}:{container_path}: {e}")
        raise


def container_cp_out(container: str, container_path: str, host_path) -> None:
    """Copy file/directory from container to host.
    
    Includes diagnostic logging to track potential screen blanking issues.
    """
    if isinstance(container, EnvironmentTransport):
        container.download(container_path, Path(host_path))
        return
    from acb.logging_config import log_debug
    import os
    if os.environ.get("ACB_DEBUG_UI"):
        log_debug(f"[CP_OUT_START] {container}:{container_path} -> {host_path}")
    try:
        _run(["podman", "cp", f"{container}:{container_path}", str(host_path)])
        if os.environ.get("ACB_DEBUG_UI"):
            log_debug(f"[CP_OUT_END] {container}:{container_path} -> {host_path}")
    except Exception as e:
        if os.environ.get("ACB_DEBUG_UI"):
            log_debug(f"[CP_OUT_FAILED] {container}:{container_path} -> {host_path}: {e}")
        raise








def container_exec_capture(container: str, cmd: list[str], workdir: str | None = None,
                          log_output: bool = True) -> str:
    """Run ``cmd`` inside ``container`` and return stdout (raises on nonzero exit).
    
    Args:
        container: Container name
        cmd: Command to execute as list
        workdir: Optional working directory inside container
        log_output: If False, suppress stdout/stderr logging (default: True)
    
    Returns:
        Command stdout as string
    """
    if isinstance(container, EnvironmentTransport):
        return container.capture(cmd, workdir)
    full = ["podman", "exec"]
    if workdir:
        full += ["--workdir", workdir]
    full += [container, *cmd]
    return _run(full, log_output=log_output).stdout
