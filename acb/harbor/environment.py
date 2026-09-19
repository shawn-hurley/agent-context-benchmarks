"""Compatibility fixes confined to the pinned Harbor provider boundary."""
from __future__ import annotations

from functools import cache
import platform
import shutil
import subprocess
import json
from pathlib import Path
import tempfile

from harbor.environments.podman import PodmanEnvironment
from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.docker.runtime import ContainerRuntime
from acb.harbor.processes import compose_command


def copy_log_mounts(mounts):
    """Remove only convention log binds replaced by Harbor's copy lifecycle."""
    log_targets = {"/logs/agent", "/logs/verifier", "/logs/artifacts", "/logs/user_agent"}
    return [mount for mount in mounts or []
            if mount.get("type") != "bind" or mount.get("target") not in log_targets]


class FrozenImageEnvironment:
    """Use the inspected main image without rebuilding mutable task recipes."""

    def __init__(self, *args, frozen_images=None, frozen_services=None, verifier_contracts=None, **kwargs):
        from acb.harbor.contracts import verifier_contract_key
        self._verifier_key = None
        self._expected_verifier_contract = None
        # Harbor 0.23 has two environment roles. Match the exact agent name:
        # verifier names can be truncated or end with a step called "env".
        session = kwargs.get("session_id", "")
        paths = kwargs.get("trial_paths")
        is_agent = (session == f"{paths.trial_dir.name}__env" if paths is not None
                    else session.endswith("__env") and "__verifier__" not in session)
        if not is_agent:
            self._verifier_key = verifier_contract_key(kwargs["environment_dir"], kwargs["task_env_config"])
            if verifier_contracts is not None:
                if self._verifier_key not in verifier_contracts:
                    raise ValueError("separate verifier was not inspected during preparation")
                self._expected_verifier_contract = verifier_contracts[self._verifier_key]
        self._frozen_image = None
        if is_agent:
            self._frozen_image = (frozen_images or {}).get(str(Path(kwargs["environment_dir"]).resolve()))
        service_images = (frozen_services or {}).get(str(Path(kwargs["environment_dir"]).resolve()), {}) if self._frozen_image else {}
        if self._expected_verifier_contract:
            self._frozen_image = self._expected_verifier_contract["container"]["image_id"]
            service_images = self._expected_verifier_contract["service_images"]
        self._frozen_overlay = None
        if self._frozen_image:
            kwargs["task_env_config"] = kwargs["task_env_config"].model_copy(
                update={"docker_image": self._frozen_image})
            self._frozen_overlay = tempfile.TemporaryDirectory(prefix="acb-image-")
            overlay = Path(self._frozen_overlay.name) / "image.json"
            overlay.write_text(json.dumps({"services": {
                name: {"image": image, "pull_policy": "never"}
                for name, image in {**service_images, "main": self._frozen_image}.items()
            }}))
            kwargs["extra_docker_compose"] = [*(kwargs.get("extra_docker_compose") or []), str(overlay)]
        super().__init__(*args, **kwargs)

    @property
    def _docker_compose_paths(self):
        paths = super()._docker_compose_paths
        # Harbor appends its egress overlay last. Frozen image selection must
        # follow that overlay as well, without altering its network settings.
        if self._frozen_overlay:
            path = Path(self._frozen_overlay.name) / "image.json"
            paths = [item for item in paths if item != path] + [path]
        return paths

    async def start(self, force_build):
        await super().start(False if self._frozen_image else force_build)
        if self._verifier_key:
            from acb.harbor.contracts import inspect_container_contract, inspect_task_service_images
            self._observed_verifier_contract = {
                "container": await inspect_container_contract(self),
                "service_images": await inspect_task_service_images(self),
            }
            evidence = self.trial_paths.agent_dir / "acb"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / f"verifier-runtime-{self._verifier_key}.json").write_text(
                json.dumps(self._observed_verifier_contract, indent=2))
            if (self._expected_verifier_contract is not None
                    and self._observed_verifier_contract != self._expected_verifier_contract):
                raise ValueError("separate verifier runtime changed since preparation; see verifier-runtime evidence")

    async def _run_docker_compose_command(self, command, **kwargs):
        # Prepared image IDs must survive inspection teardown. Containers and
        # trial volumes are still removed; images form ACB's preparation cache.
        command = list(command)
        if command[:1] == ["down"] and "--rmi" in command:
            index = command.index("--rmi")
            del command[index:index + 2]
        return await super()._run_docker_compose_command(command, **kwargs)


class ManagedComposeEnvironment:
    async def start_service(self, service):
        # Harbor exposes stop_service; restarting our owned sidecar between
        # steps stays in this versioned Compose compatibility boundary.
        if service != "acb-caveman":
            raise ValueError("only the managed Caveman service may be restarted")
        await self._run_docker_compose_command(["start", service])

    async def _run_docker_compose_command(self, command, **kwargs):
        return await compose_command(self, command, **kwargs)


class ACBDockerEnvironment(FrozenImageEnvironment, ManagedComposeEnvironment, DockerEnvironment):
    pass


class ACBPodmanEnvironment(FrozenImageEnvironment, ManagedComposeEnvironment, PodmanEnvironment):
    async def _run_docker_compose_command(self, command, **kwargs):
        # Noninteractive commands must preserve bytes (not PTY CRLF conversion)
        # and keep the invoked process attached until completion.
        if command[:2] == ["exec", "--no-TTY"]:
            command = ["exec", "-T", *command[2:]]
        if command and command[0] == "exec" and "-T" not in command:
            command = ["exec", "-T", *command[1:]]
        return await super()._run_docker_compose_command(command, **kwargs)

    @classmethod
    @cache
    def runtime(cls):
        # Pin the tested frontend when installed. `podman compose` otherwise
        # changes providers when Docker Compose is installed, importing its
        # credential helpers and behavior into previously working Podman runs.
        if shutil.which("podman-compose"):
            return ContainerRuntime(engine=("podman",), compose=("podman-compose", "--in-pod=false"),
                                    supports_compose_wait=False, supports_compose_cp=False,
                                    supports_compose_project_directory=False)
        # Harbor 0.23 assumes Compose V2 flags. Reject unsupported frontends.
        help_result = subprocess.run(["podman", "compose", "--help"], capture_output=True, timeout=10)
        output = (help_result.stdout or b"") + (help_result.stderr or b"")
        if help_result.returncode or b"--project-directory" not in output:
            raise RuntimeError("Podman requires podman-compose or a Compose V2 compatible frontend")
        return super().runtime()

    def __init__(self, *args, **kwargs):
        self.copy_logs = platform.system() == "Darwin"
        self._reported_tar_upload = False
        if self.copy_logs:
            # Harbor downloads convention logs/artifacts when mounted=False.
            # The macOS Podman VM need not share the controller's filesystem.
            kwargs["mounts"] = copy_log_mounts(kwargs.get("mounts"))
        super().__init__(*args, **kwargs)

    @property
    def capabilities(self):
        return super().capabilities.model_copy(update={"mounted": not self.copy_logs})

    async def upload_file(self, source_path, target_path):
        if type(self).runtime().supports_compose_cp:
            return await super().upload_file(source_path, target_path)
        await self._platform._upload_file_with_tar(source_path, target_path)
        self._report_tar_upload()

    async def upload_dir(self, source_dir, target_dir):
        if type(self).runtime().supports_compose_cp:
            return await super().upload_dir(source_dir, target_dir)
        await self._platform._upload_dir_with_tar(source_dir, target_dir)
        self._report_tar_upload()

    def _report_tar_upload(self):
        if not self._reported_tar_upload:
            self.logger.info("Compose cp is unavailable; Podman task uploads use tar successfully")
            self._reported_tar_upload = True

    async def start(self, force_build):
        await super().start(force_build)
        if self.copy_logs:
            result = await self.exec("mkdir -p /logs/agent /logs/verifier /logs/artifacts", user="root")
            if result.return_code:
                raise RuntimeError("cannot initialize Harbor log directories")
