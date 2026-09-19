"""Per-trial Praxis service using Harbor's public service operations."""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import shlex

import yaml

from acb.config import ModelSpec
from acb.usage import is_model_request, parse_measurements
from acb.proxy.base import ProxyTags
from acb.proxy.praxis import build_container_config, PraxisContainerBackend

SERVICE = "acb-praxis"
PORT = 18880


class PraxisStartupError(RuntimeError):
    """The measurement service failed before agent model work started."""



def compose_overlay(image: str, credential_names: list[str], environment="podman") -> dict:
    overlay = {"services": {SERVICE: {
        "image": image,
        "network_mode": "service:main",
        "depends_on": ["main"],
        "entrypoint": ["/bin/sh", "-c"],
        "command": ["while :; do if [ -f /tmp/acb-praxis.yaml ]; then praxis-ai -c /tmp/acb-praxis.yaml >> /tmp/acb-praxis.log 2>&1 & p=$$!; echo $$p > /tmp/acb-praxis.pid; wait $$p; rm -f /tmp/acb-praxis.yaml /tmp/acb-praxis.pid; fi; sleep 0.1; done"],
        "healthcheck": {"disable": True},
        "environment": ["PRAXIS_LOG_FORMAT=json", *credential_names],
    }}}
    if environment == "docker":
        overlay["services"][SERVICE]["extra_hosts"] = ["host.containers.internal:host-gateway"]
    return overlay


class HarborPraxis:
    def __init__(self, environment, plan: dict, harness: str, trial_id: str, directory: Path):
        self.environment, self.plan = environment, plan
        self.directory = directory
        spec = ModelSpec(**plan["model"])
        from acb.harnesses import make_harness
        api = make_harness(harness).effective_api(spec.api)
        self.config = build_container_config(PORT, spec, api)
        self.parser = PraxisContainerBackend(
            tags=ProxyTags(run_id=plan["run_id"], benchmark=plan["benchmark"],
                           harness=harness, model=spec.name, instance_id=trial_id),
            usage_path=directory / "usage.jsonl", model_spec=spec, harness_api=api,
            config=plan["proxy_config"], pod="unused", image="unused",
        )
        self.started = False
        self._log_task: asyncio.Task | None = None
        self._log_offset = 0

    async def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "praxis.log").touch()
        try:
            endpoint = await asyncio.wait_for(self._start(), timeout=60)
        except Exception as error:
            (self.directory / "praxis-startup.json").write_text(json.dumps({
                "passed": False, "error_type": type(error).__name__,
                "error": str(error), "timeout_sec": 60,
            }, indent=2))
            raise PraxisStartupError("Praxis startup failed; see praxis-startup.json") from error
        (self.directory / "praxis-startup.json").write_text(json.dumps({"passed": True}))
        self._log_task = asyncio.create_task(self._stream_log())
        return endpoint

    async def _stream_log(self):
        """Copy bounded chunks of sidecar diagnostics while the agent runs."""
        while True:
            try:
                result = await self.environment.service_exec(
                    "if test -f /tmp/acb-praxis.log; then "
                    f"tail -c +{self._log_offset + 1} /tmp/acb-praxis.log | head -c 65536 | base64 | tr -d '\\n'; fi",
                    service=SERVICE, timeout_sec=5,
                )
                if result.return_code == 0 and result.stdout:
                    chunk = base64.b64decode(result.stdout)
                    with (self.directory / "praxis.log").open("ab") as log:
                        log.write(chunk)
                    self._log_offset += len(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The final download in stop() remains the complete source.
                pass
            await asyncio.sleep(1)

    async def _start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(self.config)
        (self.directory / "praxis.yaml").write_text(text)
        payload = base64.b64encode(text.encode()).decode()
        result = await self.environment.service_exec(
            f"touch /tmp/benchmark_metrics.jsonl; printf %s {shlex.quote(payload)} | base64 -d > /tmp/acb-praxis.pending && mv /tmp/acb-praxis.pending /tmp/acb-praxis.yaml",
            service=SERVICE, timeout_sec=20,
        )
        if result.return_code:
            raise RuntimeError("failed to configure Praxis sidecar")
        self.started = True
        for _ in range(60):
            result = await self.environment.service_exec(
                f"wget -qO- --timeout=2 http://127.0.0.1:{PORT}/v1/models 2>/dev/null | grep -q .",
                service=SERVICE, timeout_sec=5,
            )
            if result.return_code == 0:
                # Health requests are not measured agent traffic. Truncate the
                # existing inode because the metrics filter keeps its file open.
                cleared = await self.environment.service_exec(
                    ": > /tmp/benchmark_metrics.jsonl", service=SERVICE, timeout_sec=5,
                )
                if cleared.return_code:
                    raise RuntimeError("cannot reset Praxis preparation metrics")
                return f"http://127.0.0.1:{PORT}"
            await asyncio.sleep(0.5)
        raise RuntimeError("Praxis sidecar did not become healthy")

    async def stop(self):
        if self._log_task is not None:
            self._log_task.cancel()
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass
            self._log_task = None
        errors = []
        # Praxis/Pingora handles SIGINT as shutdown. Keep the service available
        # for another step, but stop the model proxy
        # before verification so verifier requests cannot enter agent accounting.
        try:
            result = await self.environment.service_exec(
                "if test -f /tmp/acb-praxis.pid; then kill -INT $(cat /tmp/acb-praxis.pid); fi; "
                "for i in $(seq 1 100); do test ! -f /tmp/acb-praxis.pid && exit 0; sleep .1; done; exit 1",
                service=SERVICE, timeout_sec=15,
            )
            if result.return_code:
                raise RuntimeError("Praxis did not stop and flush")
        except Exception as error:
            errors.append(str(error))
        for source, filename in (("/tmp/acb-praxis.log", "praxis.log"),
                                 ("/tmp/benchmark_metrics.jsonl", "benchmark_metrics.raw.jsonl")):
            try:
                await self.environment.service_download_file(source, self.directory / filename, service=SERVICE)
            except Exception as error:
                errors.append(f"{filename}: {error}")
        raw = self.directory / "benchmark_metrics.raw.jsonl"
        collection_errors = list(errors)
        records = []
        if raw.exists():
            records, parse_errors = parse_measurements(raw.read_text())
            errors.extend(parse_errors)
            metrics = self.directory / "benchmark_metrics.jsonl"
            metrics.write_text("".join(json.dumps(record) + "\n" for record in records))
            model_metrics = self.directory / "model_metrics.jsonl"
            model_metrics.write_text("".join(json.dumps(record) + "\n" for record in records if is_model_request(record)))
            self.parser._metrics_path = model_metrics
            self.parser._read_metrics_file(records)
        (self.directory / "measurement.json").write_text(json.dumps({
            "complete": not errors and raw.exists(), "errors": errors,
            "collection_complete": not collection_errors and raw.exists(),
            "source": "praxis", "scope": "agent model requests",
            "non_model_requests": sum(not is_model_request(record) for record in records),
        }, indent=2))
        if collection_errors and self.started:
            raise RuntimeError("Praxis collection failed: " + "; ".join(collection_errors))
