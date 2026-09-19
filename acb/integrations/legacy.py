"""Task transport and owned Caveman sidecar for the retained Podman runners."""
from pathlib import Path
import time

from acb import container as engine
from acb.transport import command


class LegacyIntegrationTransport:
    def __init__(self, container: str, pod: str, image: str):
        self.container = container
        self.service = f"{pod}-caveman"
        self.pod = pod
        self.image = image
        self.owned = False

    def start(self):
        # Record ownership before creation so interrupted startup is cleaned up.
        self.owned = True
        engine.container_create(self.pod, self.image, self.service)
        engine.container_start(self.service)
        deadline = time.monotonic() + 30
        while True:
            try:
                self.service_capture("acb-caveman", ["python3", "-c",
                    "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:18881/health',timeout=2).read()==b'ready'"])
                return
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Caveman service failed to become ready")
                time.sleep(0.2)

    def capture(self, argv, workdir=None):
        return engine.container_exec_capture(self.container, argv, workdir=workdir)

    def upload(self, source: Path, destination: str):
        engine.container_cp_in(self.container, source, destination)

    def download(self, source: str, destination: Path):
        engine.container_cp_out(self.container, source, destination)

    def execute(self, request, **kwargs):
        from acb.harnesses._streaming import execute
        argv = command(self.container, list(request.argv), request.env, request.workdir)
        return execute(argv, env=None, cwd=None, label=self.container, **kwargs)

    def _service(self, service):
        if service != "acb-caveman":
            raise ValueError(f"unknown task service: {service}")
        return self.service

    def service_capture(self, service, argv):
        return engine.container_exec_capture(self._service(service), argv)

    def service_download(self, service, source, destination, directory=False):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if directory:
            destination.mkdir(parents=True, exist_ok=True)
            source = source.rstrip("/") + "/."
        engine.container_cp_out(self._service(service), source, destination)

    def stop_service(self, service):
        self._service(service)
        if self.owned:
            engine.container_stop_rm(self.service)
            self.owned = False

    def close(self):
        self.stop_service("acb-caveman")
