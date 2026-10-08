"""Harbor verifier bridge to the benchmarks' native, controller-owned graders.

Candidate code executes in grading containers. The agent receives neither
grading inputs nor the controller's container-engine connection.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sys
from uuid import uuid4

import yaml

from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier


async def run_grader(command, *, cwd, env, log):
    """Keep native grader descendants within a cancellable process group."""
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=cwd, env=env, stdout=output, stderr=output,
            start_new_session=True,
        )
        try:
            return await process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 15)
            except asyncio.TimeoutError:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise


from acb.container import grader_env


class BenchmarkVerifier(BaseVerifier):
    def __init__(self, *args, benchmark_config, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = benchmark_config

    async def verify(self):
        record = json.loads((self.task.paths.tests_dir / "benchmark.json").read_text())
        workflow_file = self.task.paths.tests_dir / "workflow.json"
        workflow = json.loads(workflow_file.read_text()) if workflow_file.is_file() else None
        if workflow:
            step = next((item for item in workflow["steps"] if item["name"] == self.step_name), None)
            if step is None:
                raise ValueError(f"unknown workflow stage: {self.step_name!r}")
            if step["gate"]["type"] == "artifacts":
                return await self._artifact_gate(step["name"], step["gate"], workflow["workdir"])
            if step["gate"].get("archive"):
                # Save final reports before native submission strips workflow files.
                # Artifact success does not replace the native benchmark reward.
                await self._artifact_gate(step["name"], step["gate"], workflow["workdir"])
        output = self.trial_paths.verifier_dir / "native"
        output.mkdir(parents=True, exist_ok=True)
        if record["benchmark"] == "scarfbench":
            command, env = await self._scarfbench(record, output, workflow)
        else:
            command, env = await self._swebench(record, output, workflow)
        code = await run_grader(command, cwd=output, env=env, log=output / "grader.log")
        status = output / "grade.json"
        if code != 0 or not status.is_file():
            raise RuntimeError(f"native benchmark grader did not complete (exit {code}); see verifier/native/grader.log")
        grade = json.loads(status.read_text())
        if type(grade.get("resolved")) is not bool:
            raise ValueError("native grader returned an invalid resolved status")
        rewards = {"reward": int(grade["resolved"])}
        self.trial_paths.reward_json_path.write_text(json.dumps(rewards))
        return VerifierResult(rewards=rewards)

    async def _artifact_gate(self, stage, gate, workdir):
        evidence = self.trial_paths.verifier_dir / gate.get("evidence_dir", f"workflow-{stage}")
        evidence.mkdir(parents=True, exist_ok=True)
        try:
            for item in gate["archive"]:
                relative = item.rstrip("/")
                target = evidence / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.endswith("/"):
                    await self.environment.download_dir(workdir + "/" + relative, target)
                else:
                    await self.environment.download_file(workdir + "/" + relative, target)
        except Exception as error:
            raise RuntimeError(f"{stage} stage: missing workflow artifact: {error}") from error
        for item in gate.get("required", []):
            required = evidence / item
            if not required.is_file() or not required.read_bytes().strip():
                raise RuntimeError(f"{stage} stage: required artifact missing or empty: {item}")
        if gate.get("yaml_violations"):
            report = evidence / gate["yaml_violations"]
            if not report.is_file():
                raise RuntimeError(f"{stage} stage: violations report missing: {gate['yaml_violations']}")
            try:
                rulesets = yaml.safe_load(report.read_text())
                if not isinstance(rulesets, list) or not all(isinstance(item, dict) for item in rulesets):
                    raise ValueError("expected a list of rulesets")
                if any(not isinstance(item.get("violations") or {}, dict) for item in rulesets):
                    raise ValueError("ruleset violations must be mappings")
                violations = sum(len(item.get("violations") or {}) for item in rulesets)
            except (ValueError, TypeError, yaml.YAMLError) as error:
                raise RuntimeError(f"{stage} stage: invalid violations report: {error}") from error
            if violations < 1:
                raise RuntimeError(f"{stage} stage: no parseable violations")
            (evidence / "summary.json").write_text(json.dumps({"violations": violations}, indent=2))
        return VerifierResult(rewards={"reward": 1})

    async def _scarfbench(self, record, output, workflow=None):
        from acb.benchmarks.scarfbench import ScarfBench, _write_metadata_json
        config = dict(self.config)
        target = output / "benchmark"
        shutil.copytree(self.task.paths.tests_dir / "benchmark", target)
        config["benchmark_cache_dir"] = str(target)
        bench = ScarfBench(config)
        run = bench._run_dir_for_instance(output, record["instance_id"])
        run.mkdir(parents=True)
        await self.environment.download_dir("/work", run / "output")
        if workflow:
            strip_workflow_artifacts(run / "output", workflow.get("exclude_from_grading", []))
        validate_candidate_tree(run / "output")
        shutil.copytree(self.task.paths.environment_dir / "source", run / "input")
        (run / "validation").mkdir()
        for name in ("agent.out", "agent.err"):
            (run / "validation" / name).write_text("")
        info = record["extra"]
        _write_metadata_json(run / "metadata.json", agent="acb", layer=info["layer"],
                             app=info["app"], source_framework=info["source"],
                             target_framework=info["target"], model="harbor")
        (output / "prediction.json").write_text(json.dumps({
            "instance_id": record["instance_id"], "model_name_or_path": "harbor",
            "output": str(run), "model_patch": None,
        }))
        (output / "request.json").write_text(json.dumps({**record, "config": config, "run": str(run)}))
        env = grader_env(config, output)
        # The child uses ACB's interpreter, including editable installations.
        command = [sys.executable, str(Path(__file__).with_name("benchmark_grade.py")), str(output / "request.json")]
        return command, env

    async def _swebench(self, record, output, workflow=None):
        from acb.benchmarks.swebench import SWEBench, _ensure_swebench_venv
        async def capture(command):
            result = await self.environment.exec(command, timeout_sec=60)
            if result.return_code:
                raise RuntimeError(f"cannot collect SWE-bench prediction: {result.stderr}")
            return result.stdout or ""
        await capture("git -C /testbed add -u")
        baseline = set((await capture("cat /tmp/.acb-baseline-untracked.txt")).splitlines())
        current = (await capture("git -C /testbed ls-files --others --exclude-standard")).splitlines()
        excluded = tuple((workflow or {}).get("exclude_from_grading", []))
        def workflow_artifact(path):
            return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in excluded)
        for path in (await capture("git -C /testbed diff --cached --name-only")).splitlines():
            if workflow_artifact(path):
                await capture("git -C /testbed reset -q HEAD -- " + shlex.quote(path))
        for path in SWEBench(self.config)._filter_excluded_paths([p for p in current if p not in baseline]):
            if workflow_artifact(path):
                continue
            # Match legacy staging semantics: a bad untracked path must not
            # discard a valid tracked-file patch.
            await self.environment.exec("git -C /testbed add -- " + shlex.quote(path), timeout_sec=30)
        patch = await capture("git -C /testbed diff --cached")
        (output / "model.patch").write_text(patch)
        (output / "prediction.json").write_text(json.dumps({
            "instance_id": record["instance_id"], "model_name_or_path": "harbor", "model_patch": patch,
        }))
        from acb.harbor.contracts import inspect_container_contract
        image = (await inspect_container_contract(self.environment))["image_id"]
        row = dict(record["extra"]["dataset_row"], image=image)
        request = {"row": row, "model_patch": patch, "run_id": "acb-" + uuid4().hex,
                   "task_repo": str(self.task.paths.tests_dir / "task_repo")}
        (output / "request.json").write_text(json.dumps(request))
        python = self.config.get("swebench_python") or str(_ensure_swebench_venv())
        env = grader_env(self.config, output)
        return [python, str(Path(__file__).with_name("swebench_grade.py")), str(output / "request.json")], env


def validate_candidate_tree(root):
    """Do not let native validator copies follow candidate links off-tree."""
    root = root.resolve()
    for path in root.rglob("*"):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"candidate symlink escapes the project: {path.relative_to(root)}")
        elif not (path.is_file() or path.is_dir()):
            raise ValueError(f"unsupported candidate entry: {path.relative_to(root)}")


def strip_workflow_artifacts(root, names):
    """Remove declared workflow evidence from the native candidate copy."""
    for name in names:
        candidate_artifact = root / name
        if candidate_artifact.is_dir() and not candidate_artifact.is_symlink():
            shutil.rmtree(candidate_artifact)
        else:
            candidate_artifact.unlink(missing_ok=True)
