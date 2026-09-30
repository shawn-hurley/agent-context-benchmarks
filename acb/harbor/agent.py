"""Harbor agents retaining ACB's harness launch and integration semantics."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext

from acb.config import ModelSpec
from acb.harnesses import make_harness
from acb.integrations.base import IntegrationContext, ModelEndpoint
from acb.integrations.manager import IntegrationManager
from acb.harbor.transport import HarborTransport
from acb.harbor.praxis import HarborPraxis


class ACBHarborAgent(BaseAgent):
    def __init__(self, *args, plan: dict, harness: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan, self.harness_name = plan, harness
        self.transport = None
        self.integrations = None
        self.praxis = None
        self._run_count = 0

    @staticmethod
    def name():
        return "acb"

    def to_agent_info(self):
        # Harbor groups metrics by agent identity, not ACB's harness kwarg.
        return super().to_agent_info().model_copy(update={"name": "acb-" + self.harness_name})

    def version(self):
        return "1"

    def _use_kantra_local_proxy(self):
        return ((self.plan.get("workflow") or {}).get("agent_adapter") == "local-qwen-no-think"
                and self.plan["model"]["name"] in {
                    "mlx-community/Qwen3.8-27B-4bit", "Qwen/Qwen3.5-4B"})

    async def _start_kantra_local_proxy(self, environment):
        started = await environment.exec(
            "setsid python3 /opt/acb/no-think-proxy.py </dev/null "
            ">/tmp/acb-no-think-proxy.log 2>&1 & echo $! >/tmp/acb-no-think-proxy.pid",
            timeout_sec=10,
        )
        if started.return_code:
            raise RuntimeError("cannot start local Qwen request adapter")
        self._no_think_proxy_started = True
        for _ in range(30):
            probe = await environment.exec(
                "python3 -c \"from urllib.request import urlopen; "
                "urlopen('http://127.0.0.1:18879/v1/models', timeout=2).read()\"",
                timeout_sec=5,
            )
            if probe.return_code == 0:
                (self.artifacts / "request-adapter.json").write_text(json.dumps({
                    "enabled": True, "scope": "local Qwen Kantra pilot",
                    "request_change": "chat_template_kwargs.enable_thinking=false",
                    "tools_allowed": ["shell", "write", "edit"],
                    "upstream": "Praxis loopback; measurement remains enabled",
                }, indent=2))
                return "http://127.0.0.1:18879"
            await asyncio.sleep(.2)
        raise RuntimeError("local Qwen request adapter did not become ready")

    async def _thread(self, function, *args):
        from acb.downloads import download_policy
        with download_policy(offline=self.plan.get("offline", False), cancelled=self.transport.closed):
            task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.transport.closed.set()
            cleanup_errors = []
            for cleanup in (self.transport.kill_agent, self.transport.cancel_pending):
                try:
                    await cleanup()
                except Exception as error:
                    cleanup_errors.append(str(error))
            if cleanup_errors and getattr(self, 'artifacts', None):
                (self.artifacts / 'cancellation-cleanup.json').write_text(json.dumps(cleanup_errors))
            # A cancelled synchronous adapter must finish before Harbor teardown.
            try:
                await asyncio.wait_for(asyncio.shield(task), 130)
            except (Exception, asyncio.CancelledError):
                pass
            raise

    async def setup(self, environment):
        self.transport = HarborTransport(environment, asyncio.get_running_loop())
        self.artifacts = self.logs_dir / "acb"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        task_id = next((item["id"] for item in self.plan["manifest"]["tasks"]
                        if Path(item["path"]).resolve() == Path(environment.environment_dir).resolve().parent), None)
        if task_id is None:
            raise ValueError("environment does not match the prepared task manifest")
        task_plan = self.plan.get("task_plans", {}).get(task_id)
        config = deepcopy((task_plan or self.plan)["harnesses"][self.harness_name])
        probe = await environment.exec("id -u; uname -m; pwd; command -v setsid; command -v bash", timeout_sec=30)
        lines = (probe.stdout or "").strip().splitlines()
        if probe.return_code or len(lines) < 5:
            raise RuntimeError("agent image requires Linux, bash and setsid")
        if lines[0] != "0":
            raise ValueError("ACB harness installation currently requires a root agent user; task user is preserved")
        arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(lines[1])
        if arch is None:
            raise ValueError(f"unsupported Linux architecture {lines[1]}")
        workdir = lines[2]
        python_probe = await environment.exec(
            "for candidate in python3 python /usr/bin/python3 /opt/miniconda3/bin/python; do "
            "p=$(command -v \"$candidate\" 2>/dev/null) || continue; "
            "\"$p\" -c 'import sys,urllib.request; assert sys.version_info >= (3, 8)' "
            "2>/dev/null && { printf '%s\\n' \"$p\"; break; }; done", timeout_sec=30,
        )
        python_path = (python_probe.stdout or "").strip() or None
        runtime = {"arch": arch, "workdir": workdir, "user": lines[0], "python_path": python_path}
        from acb.harbor.contracts import inspect_container_contract
        credentials = [self.plan["model"]["key_env"]] if self.plan["model"].get("key_env") else []
        if self.plan["model"].get("vertex_model"):
            credentials.append("VERTEX_AUTH_TOKEN")
        runtime["container"] = await inspect_container_contract(environment, credentials)
        from acb.harbor.contracts import inspect_task_service_images
        runtime["service_images"] = await inspect_task_service_images(environment)
        from acb.harbor.runtime import apply_language_profile, inspect_language_environment
        task_record = next(item for item in self.plan["manifest"]["tasks"] if item["id"] == task_id)
        profile = task_record.get("language_profile", {"conda_env": None, "source": "image-path"})
        apply_language_profile(config, profile)
        runtime["language_environment"] = await inspect_language_environment(environment, profile)
        from acb.harbor.skills import task_skills, with_task_skills
        native_skills = []
        if self.skills_dir:
            snapshot = self.artifacts / "task-skills"
            await environment.download_dir(self.skills_dir, snapshot)
            native_skills, runtime["task_skills"] = task_skills(snapshot)
        (self.artifacts / "runtime.json").write_text(json.dumps({
            **runtime, "task_id": task_id, "harness_version": config["version"],
            "launch_profile": config.get("launch_profile"),
        }, indent=2))
        if task_plan and runtime != task_plan["runtime"]:
            raise ValueError(f"{task_id}: runtime changed since preparation; prepare again; see runtime.json")
        if self.plan.get("runtime_inspection_only"):
            return
        if config.get("workdir") and config["workdir"] != workdir:
            raise ValueError(f"harness workdir {config['workdir']} conflicts with task workdir {workdir}")
        config["workdir"] = workdir
        config = with_task_skills(config, native_skills, self.harness_name)
        from acb.harbor.components import with_task_mcp
        config = with_task_mcp(config, self.mcp_servers, self.harness_name)
        self.harness = make_harness(self.harness_name, config)
        self.harness.api = self.harness.effective_api(self.plan["model"]["api"])
        self.integrations = IntegrationManager(self.harness_name, config)
        cache = Path(self.plan["cache_dir"])
        try:
            await self._thread(self.harness.setup_container, self.transport, arch, cache)
            from acb.harbor.preflight import verify_harness_startup
            await verify_harness_startup(environment, self.harness_name, config['version'], self.artifacts)
            await self._thread(self.harness.setup_skills, self.transport, arch, cache)
            (self.artifacts / "skill-delivery.json").write_text(json.dumps({
                "task_skills": runtime.get("task_skills", []),
                "configured_skills": [item["name"] for item in config.get("skills", [])
                                      if item not in native_skills],
                "delivery": "adapter installer and explicit SKILL.md paths in additive instructions",
                "evidence_scope": "installation completed; model reading requires separate evidence",
            }, indent=2))
            await self._thread(self.harness.setup_mcp_servers, self.transport, arch, cache)
            self.integration_env = await self._thread(self.integrations.setup, IntegrationContext(
                container=self.transport, arch=arch, harness=self.harness_name,
                harness_version=config["version"], cache_dir=cache,
                artifact_dir=self.artifacts / "integrations",
            ))
            self.harness.integration_activation = self.integrations.activation
        except BaseException:
            if self.integrations:
                await asyncio.to_thread(self.integrations.finish)
            raise

    async def run(self, instruction: str, environment, context: AgentContext):
        if self._run_count:
            # Each step starts a fresh harness session. Workspace and recovery
            # store persist within the trial; services stay off during grading.
            if any(entry.name == "caveman" for entry in self.integrations.entries):
                await environment.start_service("acb-caveman")
                for _ in range(60):
                    ready = await environment.service_exec(
                        "python3 -c \"from urllib.request import urlopen; urlopen('http://127.0.0.1:18881/health',timeout=1)\"",
                        service="acb-caveman", timeout_sec=5)
                    if ready.return_code == 0:
                        break
                    await asyncio.sleep(.2)
                else:
                    raise RuntimeError("Caveman did not restart for the next step")
            reset = await environment.exec("rm -f /opt/acb/rtk/evidence/*", timeout_sec=10)
            if reset.return_code:
                raise RuntimeError("cannot reset per-step RTK evidence")
            await self.setup(environment)
        self._run_count += 1
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / "step-lifecycle.json").write_text(json.dumps({
            "agent_invocation": self._run_count, "session": "fresh per step",
            "recovery_scope": "trial; post-transformation bytes persist across steps",
            "services_during_verification": "stopped",
        }, indent=2))
        primary_error = None
        self._no_think_proxy_started = False
        self.praxis = HarborPraxis(environment, self.plan, self.harness_name,
                                   str(self.context_id), self.artifacts)
        try:
            from acb.harbor.contracts import verify_provider_images
            providers = await verify_provider_images(environment, self.plan.get("provider_images", {}))
            (self.artifacts / "provider-images.json").write_text(json.dumps(providers, indent=2))
            base_url = await self.praxis.start()
            if self._use_kantra_local_proxy():
                base_url = await self._start_kantra_local_proxy(environment)
            endpoint = await self._thread(self.integrations.start, ModelEndpoint(base_url, "acb-trial", self.harness.api))
            env = self.harness.build_container_env(endpoint.base_url, endpoint.api_key)
            if set(env) & set(self.integration_env):
                raise ValueError("integration/harness environment conflict")
            env.update(self.integration_env)
            context.metadata = {"instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
                                "harness": self.harness_name, "measurement_source": "praxis"}
            additive = self.harness.config.get("system_prompt", "")
            (self.artifacts / "instruction-delivery.json").write_text(json.dumps({
                "benchmark_instruction_sha256": context.metadata["instruction_sha256"],
                "additive_instruction": additive,
                "additive_instruction_sha256": hashlib.sha256(additive.encode()).hexdigest(),
                "components": self.harness.config.get("instruction_evidence", []),
                "delivery": {"goose": "system flag", "pi": "append-system-prompt flag",
                             "opencode": "global AGENTS.md", "claude-code": "append-system-prompt flag"}[self.harness_name],
                "evidence_scope": "configuration supplied to the harness adapter; not proof of model compliance",
            }, indent=2))
            result = await self._thread(self.harness.run_container, instruction, self.transport,
                                        self.plan["model"]["name"], env, self.artifacts, str(self.context_id))
            if result.timed_out:
                raise TimeoutError("ACB harness timed out")
            if result.exit_code:
                raise RuntimeError(f"ACB harness exited with status {result.exit_code}")
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self.transport.closed.clear()  # Evidence operations are allowed after agent cancellation.
            cleanup_errors = []
            try:
                errors = await asyncio.to_thread(self.integrations.finish)
                cleanup_errors.extend(errors)
            except Exception as error:
                cleanup_errors.append(str(error))
            if self._no_think_proxy_started:
                try:
                    stopped = await environment.exec(
                        "kill $(cat /tmp/acb-no-think-proxy.pid) && rm /tmp/acb-no-think-proxy.pid",
                        timeout_sec=10,
                    )
                    if stopped.return_code:
                        raise RuntimeError("local Qwen request adapter did not stop")
                except Exception as error:
                    cleanup_errors.append(str(error))
            try:
                await self.praxis.stop()
            except Exception as error:
                cleanup_errors.append(str(error))
            if primary_error is not None:
                path = self.artifacts / "measurement.json"
                if path.exists():
                    measurement = json.loads(path.read_text())
                    measurement["collection_complete"] = measurement.get("complete", False)
                    measurement["complete"] = False
                    measurement["accounting_caveat"] = "agent aborted; in-flight requests may lack final usage"
                    path.write_text(json.dumps(measurement, indent=2))
            if cleanup_errors:
                (self.artifacts / "cleanup-errors.json").write_text(json.dumps(cleanup_errors, indent=2))
                if primary_error is None:
                    raise RuntimeError("trial cleanup failed: " + "; ".join(cleanup_errors))
