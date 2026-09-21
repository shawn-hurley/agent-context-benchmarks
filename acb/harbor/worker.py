"""Versioned JSON worker entry point; invoked in the isolated Harbor environment."""
from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal

import yaml

from acb.harbor import HARBOR_VERSION, PROTOCOL_VERSION
from acb.harbor.dataset import prepare_dataset, verify_manifest
from acb.harbor.praxis import compose_overlay
from acb.harbor.paths import JOB_NAME, job_dir
from acb.harbor.results import evaluation, import_results, load_results


def execution_summary(plan, results, *, control=None, import_errors=()):
    """Execution status is independent of verification and artifact completeness."""
    config = plan["benchmark_config"]
    metric = "reward" if control else config.get("reward_metric")
    success = (1 if control == "oracle" else 0) if control else config.get("success_value")
    records = [evaluation(result, metric, success) for result in results]
    infrastructure = [record for record in records if record["exception"] and
                      record["error_phase"] in ("environment_setup", "agent_setup", "proxy_startup",
                                                "scheduling_or_execution")]
    expected = len(plan["manifest"]["tasks"]) * (1 if control else len(plan["harnesses"])) * plan.get("attempts", 1)
    return {
        "status": "infrastructure_error" if infrastructure else "completed",
        "infrastructure_failures": infrastructure,
        "scheduled_trials": expected, "imported_trials": len(results),
        "missing_trials": max(0, expected - len(results)), "import_errors": len(import_errors),
        "evaluation_errors": sum(record["status"] == "error" for record in records),
        "verification_failures": sum(record["resolved"] is False for record in records),
        "control": control,
    }


def job_config(plan: dict, output: Path, *, install_only=False, control=None):
    from harbor.models.job.config import JobConfig
    if plan.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported ACB worker protocol")
    agents = [{"import_path": "acb.harbor.agent:ACBHarborAgent", "model_name": plan["model"]["name"],
               "kwargs": {"plan": plan, "harness": name},
               "override_setup_timeout_sec": 600,
               "max_timeout_sec": settings["timeout"]}
              for name, settings in plan["harnesses"].items()]
    environment = {"type": plan["environment"], "delete": True}
    if plan["environment"] == "podman":
        environment["import_path"] = "acb.harbor.environment:ACBPodmanEnvironment"
    else:
        environment["import_path"] = "acb.harbor.environment:ACBDockerEnvironment"
    if plan.get("task_plans") and not control:
        environment["kwargs"] = {"frozen_images": {
            str((Path(task["path"]) / "environment").resolve()):
                plan["task_plans"][task["id"]]["runtime"]["container"]["image_id"]
            for task in plan["manifest"]["tasks"]
        }}
        environment["kwargs"]["frozen_services"] = {
            str((Path(task["path"]) / "environment").resolve()):
                plan["task_plans"][task["id"]]["runtime"].get("service_images", {})
            for task in plan["manifest"]["tasks"]
        }
    if control:
        agents = [{"name": control}]
    if "verifier_contracts" in plan:
        environment.setdefault("kwargs", {})["verifier_contracts"] = plan["verifier_contracts"]
    overlay_data = {}
    if not control and not install_only:
        names = [plan["model"]["key_env"]] if plan["model"].get("key_env") else []
        if plan["model"].get("vertex_model"):
            names.append("VERTEX_AUTH_TOKEN")
        overlay_data = compose_overlay(plan["benchmark_config"].get("praxis_image", "acb-praxis-ai:latest"), names, plan["environment"])
    if not control and not plan.get("runtime_inspection_only") and any(settings.get("model_middleware") for settings in plan["harnesses"].values()):
        overlay_data.setdefault("services", {})["acb-caveman"] = {
            "image": plan["caveman_image"], "network_mode": "service:main",
            "depends_on": ["main"], "healthcheck": {"disable": True},
        }
    if overlay_data:
        for service, record in plan.get("provider_images", {}).items():
            if service in overlay_data.get("services", {}):
                overlay_data["services"][service].update(image=record["image_id"], pull_policy="never")
        from acb.harbor.contracts import validate_service_names
        validate_service_names(plan["manifest"]["tasks"], overlay_data.get("services", {}))
        overlay = output / "services-compose.yaml"
        overlay.write_text(yaml.safe_dump(overlay_data))
        environment["extra_docker_compose"] = [str(overlay)]
    from acb.harbor.benchmark_tasks import BENCHMARKS
    verifier = {}
    if plan.get("benchmark") in BENCHMARKS and not plan["benchmark_config"].get("path"):
        settings = {**plan["benchmark_config"], "container_backend": plan["environment"]}
        if plan.get("benchmark_grader"):
            settings["scarf_binary" if plan["benchmark"] == "scarfbench" else "swebench_python"] = plan["benchmark_grader"]["binary"]
        verifier = {"import_path": "acb.harbor.benchmark_verifier:BenchmarkVerifier",
                    "kwargs": {"benchmark_config": settings}}
    return JobConfig.model_validate({
        "job_name": JOB_NAME, "jobs_dir": str(output), "quiet": True,
        "install_only": install_only, "n_attempts": plan["attempts"],
        "n_concurrent_trials": plan["max_workers"], "retry": {"max_retries": 0},
        "environment": environment, "agents": agents, "verifier": verifier,
        "metrics": [] if install_only else plan["manifest"].get("metrics", []),
        "tasks": [{"path": item["path"], "source": plan["manifest"]["source"]} for item in plan["manifest"]["tasks"]],
    })


async def execute(plan, output, install_only=False, control=None):
    from harbor.job import Job
    verify_manifest(plan["manifest"])
    from acb.harbor.benchmark_grader import verify_grader
    verify_grader(plan)
    output.mkdir(parents=True, exist_ok=True)
    if not install_only and not control and plan["model"].get("vertex_model"):
        from acb.auth import fetch_vertex_token
        os.environ["VERTEX_AUTH_TOKEN"] = fetch_vertex_token()
    config = job_config(plan, output, install_only=install_only, control=control)
    job = await Job.create(config)
    if not install_only:
        from acb.harbor.events import emit
        from acb.harbor.results import evaluation

        def details(event):
            agent = event.config.agent
            harness = (agent.kwargs or {}).get("harness") or agent.name
            return {"trial_id": str(event.trial_id), "trial_name": event.trial_name,
                    "task_id": event.task_name, "harness": harness}

        from acb.harbor.trial_view import expose_trial
        async def show_trial(event):
            record = details(event)
            expose_trial(output, record["harness"], record["trial_id"], event.trial_name)
            emit("trial-started", **record)
        job.on_trial_started(show_trial)

        async def environment_started(event):
            emit("environment-started", **details(event))

        async def agent_started(event):
            emit("agent-started", **details(event))

        async def agent_ended(event):
            emit("agent-ended", **details(event))

        async def verification_started(event):
            emit("verification-started", **details(event))

        async def trial_ended(event):
            result = event.result.model_dump(mode="json")
            metric = "reward" if control else plan["benchmark_config"].get("reward_metric")
            success = (1 if control == "oracle" else 0) if control else plan["benchmark_config"].get("success_value")
            summary = evaluation(result, metric, success)
            failure = summary.get("exception") or {}
            message = summary.get("verification_error")
            if failure:
                message = f"{failure.get('exception_type') or 'TrialError'}: {failure.get('exception_message') or 'No message recorded'}"
            emit("trial-ended", **details(event), resolved=summary.get("resolved"),
                 status=summary["status"], error=message)

        job.on_environment_started(environment_started)
        job.on_agent_started(agent_started)
        job.on_agent_ended(agent_ended)
        job.on_verification_started(verification_started)
        job.on_trial_ended(trial_ended)
    from acb.harbor.metrics import bind_container_metrics
    bind_container_metrics(job, plan)
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, current.cancel)
    primary_error = None
    try:
        if plan.get("runtime_inspection_only"):
            from acb.harbor.verifier import inspect_verifiers
            contracts = await inspect_verifiers(job, output)
            (output / "verifier-contracts.json").write_text(json.dumps(contracts, indent=2))
        result = await job.run()
        (output / "harbor-native-stats.json").write_text(result.stats.model_dump_json(indent=2))
    except BaseException as error:
        primary_error = error
        raise
    finally:
        # Partial trial files remain authoritative if cancellation interrupts the job.
        results, import_errors = [], []
        try:
            results, import_errors = load_results(plan, output, control=control)
        except Exception as error:
            import_errors.append({"stage": "native result loading", "error_type": type(error).__name__, "error": str(error)})
        try:
            if not install_only:
                import_results(plan, output, results, control=control)
        except Exception as error:
            import_errors.append({"stage": "report import", "error_type": type(error).__name__, "error": str(error)})
        try:
            (output / "import-errors.json").write_text(json.dumps(import_errors, indent=2, default=str))
            summary = execution_summary(plan, results, control=control, import_errors=import_errors)
            if install_only and (summary["missing_trials"] or any(item.get("exception_info") for item in results)):
                summary["status"] = "infrastructure_error"
            if primary_error:
                summary.update(status="interrupted" if isinstance(primary_error, (asyncio.CancelledError, KeyboardInterrupt))
                               else "infrastructure_error", error=str(primary_error))
            (output / "execution-status.json").write_text(json.dumps(summary, indent=2))
        except Exception as error:
            if primary_error is None:
                raise
            primary_error.add_note(f"Could not save result import diagnostics: {error}")
        if import_errors:
            print(f"ACB retained partial results; {len(import_errors)} import issue(s). See {output / 'import-errors.json'}", flush=True)
    if install_only:
        failures = [item for item in results if item.get("exception_info")]
        if failures or len(results) != len(plan["manifest"]["tasks"]) * len(plan["harnesses"]) * plan["attempts"]:
            raise RuntimeError(f"preparation probes failed; inspect {job_dir(output)}")
    elif summary["infrastructure_failures"]:
        from acb.harbor.backend import _worker_failure
        raise RuntimeError(_worker_failure("run", 1, output))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "inspect", "run", "probe", "control", "check"))
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--control", choices=("nop", "oracle"))
    args = parser.parse_args()
    if version("harbor") != HARBOR_VERSION:
        raise RuntimeError(f"ACB requires Harbor {HARBOR_VERSION}")
    plan = json.loads(Path(args.input).read_text())
    if plan.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported worker protocol")
    output = Path(args.output)
    if args.action == "prepare":
        from acb.harbor.benchmark_grader import prepare_grader
        plan["benchmark_grader"] = prepare_grader(plan)
        plan["manifest"] = prepare_dataset(plan)
        output.write_text(json.dumps(plan, indent=2))
    elif args.action == "check":
        verify_manifest(plan["manifest"])
        job_config(plan, output.parent, install_only=True)
    else:
        if args.action == "inspect":
            plan["runtime_inspection_only"] = True
            name = next(iter(plan["harnesses"]))
            plan["harnesses"] = {name: plan["harnesses"][name]}
            plan["attempts"] = 1
        asyncio.run(execute(plan, output, args.action in ("inspect", "probe"), args.control))


if __name__ == "__main__":
    main()
