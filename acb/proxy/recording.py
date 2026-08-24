"""RecordingProxyBackend -- launches the stdlib recording server as a subprocess."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from acb.proxy.base import ProxyBackend


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RecordingProxyBackend(ProxyBackend):
    name = "recording"

    def start(self) -> str:
        port = _free_port()
        # the recording proxy owns the connection to the model backend
        if self.model_spec is not None:
            upstream = self.model_spec.upstream_url
            api = self.model_spec.api
            key_env = self.model_spec.key_env
        else:  # fallback for ad-hoc use
            upstream = self.config.get("upstream", "https://api.anthropic.com")
            api = "anthropic"
            key_env = "ANTHROPIC_API_KEY"
        if api != self.harness_api:
            raise RuntimeError(
                f"recording proxy is same-API only (harness={self.harness_api}, "
                f"backend={api}); use the praxis backend for cross-API translation"
            )
        self._proc = subprocess.Popen(
            [
                sys.executable, "-m", "acb.proxy.record_server",
                "--port", str(port),
                "--usage-path", str(self.usage_path),
                "--upstream", upstream,
                "--api", api,
                "--key-env", key_env or "",
                "--run-id", self.tags.run_id,
                "--benchmark", self.tags.benchmark,
                "--harness", self.tags.harness,
                "--model", self.tags.model,
                "--instance-id", self.tags.instance_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # wait for the port to accept connections
        deadline = time.time() + 15
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError("recording proxy failed to start")
        self._base_url = f"http://127.0.0.1:{port}"
        return self._base_url

    def stop(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
