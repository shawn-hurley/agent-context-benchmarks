"""Experimental Caveman engine adapter for the isolated per-task service lifecycle."""
from __future__ import annotations

from importlib.resources import files
import json
import hashlib
from pathlib import Path
import tempfile

from acb.container import container_cp_in, container_exec_capture
from acb.integrations.base import Integration, IntegrationActivation, IntegrationFailure, ModelEndpoint

REVISION = "ed37ab132393899c129bbeef2b9743ff3af19c68"
SERVICE = "acb-caveman"


class CavemanIntegration(Integration):
    name = "caveman"
    category = "model_middleware"
    resources = frozenset({"caveman-proxy", "caveman-recovery"})

    def validate(self, harness, harness_config):
        allowed = {"name", "version", "mode", "python_path"}
        if set(self.config) - allowed:
            raise ValueError("unknown Caveman options")
        if self.config.get("version") != REVISION:
            raise ValueError("Caveman requires the catalog's pinned source revision")
        if self.config.get("mode", "record") not in ("record", "compress"):
            raise ValueError("Caveman mode must be record or compress")
        self.metadata = {"source_revision": REVISION, "mode": self.config.get("mode", "record"),
                         "policy": "explicitly successful repetitive INFO logs only",
                         "estimates": "engine estimates; not provider usage"}

    def install(self, context):
        if not hasattr(context.container, "service_capture"):
            raise ValueError("Caveman requires a service-capable task transport")
        python = self.config.get("python_path", "python3")
        if not python.startswith("/") or any(char.isspace() for char in python):
            raise ValueError("Caveman requires a prepared absolute Python interpreter path")
        container_exec_capture(context.container, [python, "-c", "import urllib.request"])
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "acb-recall"
            source = files("acb.integrations").joinpath("assets/caveman/recall.py").read_text()
            client.write_text("#!" + python + "\n" + source.split("\n", 1)[1])
            container_cp_in(context.container, client, "/usr/local/bin/acb-recall")
        container_exec_capture(context.container, ["chmod", "+x", "/usr/local/bin/acb-recall"])
        if context.harness in ("pi", "opencode", "goose"):
            with tempfile.TemporaryDirectory() as temp:
                name = context.harness + (".py" if context.harness == "goose" else ".ts")
                adapter = Path(temp) / name
                data = files("acb.integrations").joinpath("assets/caveman/" + name).read_bytes()
                if context.harness == "goose":
                    data = ("#!" + python + "\n").encode() + data.split(b"\n", 1)[1]
                adapter.write_bytes(data)
                container_exec_capture(context.container, ["mkdir", "-p", "/opt/acb/caveman"])
                container_cp_in(context.container, adapter, "/opt/acb/caveman/" + name)
                container_exec_capture(context.container, ["chmod", "+x", "/opt/acb/caveman/" + name])
                self.metadata["tool_adapter_sha256"] = hashlib.sha256(data).hexdigest()
        context.container.service_capture(SERVICE, ["python3", "-c",
            f"from pathlib import Path; Path('/data/mode').write_text({self.config.get('mode', 'record')!r})"])

    def activate(self, context):
        return IntegrationActivation(
            env={"GOOSE_SHELL": "/opt/acb/caveman/goose.py"} if context.harness == "goose" else {},
            pi_extensions=("/opt/acb/caveman/pi.ts",) if context.harness == "pi" else (),
            opencode_plugins=("/opt/acb/caveman/opencode.ts",) if context.harness == "opencode" else ())

    def verify(self, context):
        result = context.container.service_capture(SERVICE, ["python3", "-c",
            "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:18881/health',timeout=5).read()==b'ready'"])
        # Exercise the actual recovery client inside each harness environment.
        probe = "INFO preparation probe: cache entry available\n" * 1000
        script = (
            "import subprocess,json; "
            f"r=subprocess.run(['caveman-engine','compress','--type','log'],input={probe!r}.encode(),capture_output=True,check=True); "
            "m=json.loads(r.stderr); print(m.get('recovery_handle',''))"
        )
        handle = context.container.service_capture(SERVICE, ["python3", "-c", script]).strip()
        if not handle:
            raise RuntimeError("Caveman recovery probe produced no handle")
        recovered = container_exec_capture(context.container, ["/usr/local/bin/acb-recall", handle])
        if recovered != probe:
            raise RuntimeError("Caveman recovery probe failed to recover exact bytes")
        # Keep preparation retrievals separate from measured recovery activity.
        context.container.service_capture(SERVICE, ["python3", "-c",
            "from pathlib import Path; p=Path('/data/evidence.jsonl'); "
            "p.rename('/data/preflight-evidence.jsonl') if p.exists() else None"])
        self.metadata["recovery_probe"] = "exact bytes retrieved inside agent environment"
        return {"installed": True, "recovery_verified": True, "agent_tool_verified": False,
                "adapter_loaded": True, "compression_verified": False}

    def start(self, context, upstream):
        if upstream.base_url != "http://127.0.0.1:18880":
            raise ValueError("Caveman expects the dedicated Praxis endpoint")
        return ModelEndpoint("http://127.0.0.1:18881", upstream.api_key, upstream.api)

    def collect(self, context):
        failure = None
        try:
            context.container.service_capture(SERVICE, ["python3", "-c",
                "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:18881/health',timeout=5).read()==b'ready'"])
        except Exception as error:
            failure = error
        context.artifact_dir.mkdir(parents=True, exist_ok=True)
        context.container.service_download(SERVICE, "/data", context.artifact_dir / "runtime", directory=True)
        evidence = context.artifact_dir / "runtime/evidence.jsonl"
        events = [json.loads(line) for line in evidence.read_text().splitlines() if line] if evidence.exists() else []
        self.metadata["activity"] = {"adapter_loaded": True, "agent_tool_verified": any(e.get("status") == "retrieved" and e.get("success") is True for e in events),
                                     "compression_verified": any(e.get("status") == "compressed" for e in events),
                                     "compressed": sum(e.get("status") == "compressed" for e in events),
                                     "passthrough": sum(e.get("status") == "passthrough" for e in events)}

        if any(event.get("recovery_verified") is False for event in events):
            raise IntegrationFailure("Caveman compression/recovery engine failed; original output was preserved")
        if failure is not None:
            raise IntegrationFailure("required Caveman service failed before collection") from failure

    def stop(self, context):
        context.container.stop_service(SERVICE)
