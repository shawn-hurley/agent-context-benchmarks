"""Import Harbor results without converting errors or absent rewards to zero."""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import shutil
from collections import Counter

from acb.config import RunConfig
from acb.harbor.paths import job_dir
from acb.report import aggregate_per_instance_files, build_report, build_suite_report


def load_results(plan: dict, output: Path, *, control=None) -> tuple[list[dict], list[dict]]:
    """Recover healthy native results independently; keep damaged files in place."""
    from harbor.models.trial.result import TrialResult
    from pydantic import ValidationError

    results, errors, seen = [], [], set()
    harnesses = {control} if control else set(plan["harnesses"])
    tasks = {task["id"] for task in plan["manifest"]["tasks"]}
    counts = Counter()
    for path in sorted(job_dir(output).glob("*/result.json")):
        try:
            raw = path.read_text()
            data = json.loads(raw)
            if not isinstance(data, dict) or not data.get("id"):
                raise ValueError("native result is missing its trial identity")
            result = TrialResult.model_validate_json(raw, strict=True).model_dump(mode="json")
            agent = result["config"]["agent"]
            harness = agent["kwargs"].get("harness") or agent.get("name")
            slot = (harness, result["task_name"])
            if harness not in harnesses or result["task_name"] not in tasks:
                raise ValueError("result does not belong to a scheduled task/harness")
            if result["trial_name"] != path.parent.name:
                raise ValueError("result trial name does not match its evidence directory")
            if result["id"] in seen or counts[slot] >= plan.get("attempts", 1):
                raise ValueError("duplicate or excess trial result")
            seen.add(result["id"])
            counts[slot] += 1
            results.append(result)
        except (OSError, ValueError, TypeError) as error:
            detail = (error.errors(include_input=False, include_url=False)
                      if isinstance(error, ValidationError) else str(error))
            errors.append({"path": str(path), "error_type": type(error).__name__, "error": detail})
    return results, errors


def failure_phase(result: dict, exception: dict | None) -> str:
    phases = ("environment_setup", "agent_setup", "agent_execution", "verifier")
    known = {"EnvironmentStartTimeoutError": "environment_setup",
             "HarnessStartupError": "agent_setup",
             "PraxisStartupError": "proxy_startup",
             "AgentSetupTimeoutError": "agent_setup", "AgentTimeoutError": "agent_execution",
             "NonZeroAgentExitCodeError": "agent_execution", "VerifierTimeoutError": "verifier"}
    if exception and exception.get("exception_type") in known:
        return known[exception["exception_type"]]
    started = [name for name in phases if (result.get(name) or {}).get("started_at")]
    # Verification may run after an agent failure. Prefer the phase active when
    # Harbor recorded the exception; finished_at also exists for failed phases.
    if exception and exception.get("occurred_at"):
        try:
            occurred = datetime.fromisoformat(exception["occurred_at"])
            started = [name for name in started
                       if datetime.fromisoformat(result[name]["started_at"]) <= occurred]
        except (ValueError, TypeError):
            pass
    return started[-1] if started else "scheduling_or_execution"


def evaluation(result: dict, metric: str | None, success_value=None) -> dict:
    failed_step = next((step for step in result.get("step_results") or []
                        if step.get("exception_info")), None)
    exception = result.get("exception_info") or (failed_step or {}).get("exception_info")
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards")
    valid = isinstance(rewards, dict) and bool(rewards) and all(
        type(v) in (int, float) and math.isfinite(v) for v in rewards.values())
    status = "error" if exception or not valid else "completed"
    resolved = None
    verification_error = None if valid else "Verifier did not produce finite numeric rewards"
    if status == "completed" and metric is not None:
        if metric not in rewards:
            status = "error"
            verification_error = f"Verifier did not produce the configured reward metric {metric!r}"
        elif success_value is not None:
            resolved = rewards[metric] == success_value
    failure = result if result.get("exception_info") else (failed_step or result)
    phase = failure_phase(failure, exception) if exception else "verifier" if verification_error else None
    return {"status": status, "rewards": rewards, "resolved": resolved,
            "error_phase": phase, "exception": exception,
            "verification_error": verification_error,
            "failed_step": failed_step.get("step_name") if failed_step else None,
            "trial_id": result.get("id"), "trial_name": result.get("trial_name"),
            "task_checksum": result.get("task_checksum"),
            "step_results": result.get("step_results"), "trial_uri": result.get("trial_uri")}


def import_results(plan: dict, output: Path, results: list[dict], *, control=None):
    config = plan["benchmark_config"]
    if control:
        config = {**config, "reward_metric": "reward", "success_value": 1 if control == "oracle" else 0}
    harnesses = [control] if control else list(plan["harnesses"])
    by_harness = {name: {} for name in harnesses}
    evaluations = {name: [] for name in harnesses}
    results = list(results)
    counts = Counter((item["config"]["agent"].get("kwargs", {}).get("harness", item["config"]["agent"].get("name", "unknown")),
                      item["task_name"]) for item in results)
    missing = []
    for harness in harnesses:
        for task in plan["manifest"].get("tasks", []):
            for slot in range(counts[harness, task["id"]], plan.get("attempts", 1)):
                identity = f"missing-{harness}-{task['id']}-{slot + 1}"
                missing.append({"harness": harness, "task_id": task["id"], "slot": slot + 1})
                results.append({"id": identity, "task_name": task["id"], "trial_name": identity,
                                "config": {"agent": {"kwargs": {"harness": harness}}},
                                "task_checksum": task["sha256"], "missing_trial": True,
                                "exception_info": {"exception_type": "MissingTrialResult",
                                                   "exception_message": "No result was emitted for this scheduled trial slot"}})
    (output / "missing-trials.json").write_text(json.dumps(missing, indent=2))
    for result in results:
        agent = result["config"]["agent"]
        harness = agent.get("kwargs", {}).get("harness", agent.get("name", "unknown"))
        if harness not in by_harness:
            by_harness[harness], evaluations[harness] = {}, []
        identity = str(result["id"])
        destination = output / harness / "instances" / identity
        trial_dir = job_dir(output) / result["trial_name"]
        view = None
        if trial_dir.is_dir() and not result.get("missing_trial"):
            from acb.harbor.trial_view import expose_trial
            view = expose_trial(output, harness, identity, result["trial_name"])
        destination.mkdir(parents=True, exist_ok=True)
        artifacts = trial_dir / "agent" / "acb"
        if view is not None:
            from acb.harbor.trial_view import archive_trial
            archive_trial(view, trial_dir)
            artifacts = None  # The visible trial directory now contains these artifacts.
        if artifacts is not None and artifacts.exists():
            shutil.copytree(artifacts, destination, dirs_exist_ok=True)
        step_artifacts = sorted((trial_dir / "steps").glob("*/agent/acb"))
        if step_artifacts:
            measurements = []
            archived = {step.parents[1].name for step in step_artifacts}
            missing_steps = [step["step_name"] for step in result.get("step_results") or []
                             if step["step_name"] not in archived]
            for name in missing_steps:
                measurements.append({"step_name": name, "complete": False,
                                     "errors": ["visited step artifacts missing"]})
            for step in step_artifacts:
                shutil.copytree(step, destination / "steps" / step.parents[1].name, dirs_exist_ok=True)
                measurement_path = step / "measurement.json"
                measurements.append(json.loads(measurement_path.read_text()) if measurement_path.exists()
                                    else {"complete": False, "errors": ["step measurement missing"]})
            order = {step["step_name"]: i for i, step in enumerate(result.get("step_results") or [])}
            step_artifacts.sort(key=lambda path: order.get(path.parents[1].name, len(order)))
            for filename in ("usage.jsonl", "benchmark_metrics.jsonl"):
                combined_index = 0
                with (destination / filename).open("w") as combined:
                    for step in step_artifacts:
                        source = step / filename
                        if source.exists():
                            for line in source.read_text().splitlines():
                                if not line.strip():
                                    continue
                                row = json.loads(line)
                                row["step_name"] = step.parents[1].name
                                if filename == "usage.jsonl":
                                    row["step_turn_index"] = row["turn_index"]
                                    row["turn_index"] = combined_index
                                    combined_index += 1
                                combined.write(json.dumps(row) + "\n")
            (destination / "measurement.json").write_text(json.dumps({
                "complete": all(item["complete"] for item in measurements), "steps": measurements,
                "collection_complete": all(item.get("collection_complete", item["complete"]) for item in measurements),
                "scope": "agent model requests across all visited steps",
                "missing_steps": missing_steps,
            }, indent=2))
        (destination / "harbor-result.json").write_text(json.dumps(result, indent=2))
        prediction_path = trial_dir / "verifier/native/prediction.json"
        if prediction_path.is_file():
            try:
                prediction = json.loads(prediction_path.read_text())
                prediction["benchmark_instance_id"] = prediction["instance_id"]
                prediction["instance_id"] = identity
                prediction["model_name_or_path"] = plan["model"]["name"]
                (destination / "prediction.json").write_text(json.dumps(prediction, indent=2))
            except (OSError, ValueError, TypeError, KeyError) as error:
                (destination / "prediction-import-error.json").write_text(json.dumps({
                    "artifact": str(prediction_path), "error_type": type(error).__name__,
                }))
        record = evaluation(result, config.get("reward_metric"), config.get("success_value"))
        if result.get("missing_trial"):
            record.update(trial_id=None, trial_name=None, error_phase="scheduling_or_execution", missing_trial=True)
        record["dataset"] = plan["manifest"]["source"]
        record["task_id"] = result["task_name"]
        measurement = destination / "measurement.json"
        collected = json.loads(measurement.read_text()) if measurement.exists() else {}
        record["measurement_collection_complete"] = collected.get("collection_complete", collected.get("complete", False))
        aborted = bool(result.get("exception_info")) or any(step.get("exception_info") for step in result.get("step_results") or [])
        record["measurement_complete"] = collected.get("complete", False) and not aborted
        (destination / "evaluation.json").write_text(json.dumps(record, indent=2))
        by_harness[harness][identity] = record["resolved"]
        evaluations[harness].append(record)
    for harness, resolved in by_harness.items():
        directory = output / harness
        directory.mkdir(parents=True, exist_ok=True)
        aggregate_per_instance_files(directory)
        cfg = RunConfig(run_id=plan["run_id"], benchmark=plan["benchmark"], harness=harness,
                        model=plan["model"]["name"], proxy=plan["proxy"])
        report_path = build_report(directory / "usage.jsonl", resolved, directory, cfg)
        report = json.loads(report_path.read_text())
        records = evaluations[harness]
        if config.get("reward_metric") is None or config.get("success_value") is None:
            report["resolve_rate"] = None
        report.update(dataset=plan["manifest"]["source"], dataset_revision=plan["manifest"]["revision"],
                      evaluation_errors=sum(x["status"] == "error" for x in records),
                      unknown_resolution=sum(x["resolved"] is None for x in records),
                      incomplete_measurements=sum(not x["measurement_complete"] for x in records),
                      missing_trials=sum(bool(x.get("missing_trial")) for x in records),
                      evaluations=records)
        metrics = {key for x in records for key in (x["rewards"] or {})}
        report["reward_aggregates"] = {}
        for key in metrics:
            values = [x["rewards"][key] for x in records if x["status"] == "completed" and key in (x["rewards"] or {})]
            report["reward_aggregates"][key] = {"count": len(values), "mean": sum(values) / len(values) if values else None}
        from acb.harbor.metrics import aggregate_metrics, comparison_definitions
        original_rewards = [item.get("verifier_result", {}).get("rewards")
                            if item.get("verifier_result") else None
                            for item in results
                            if item["config"]["agent"].get("kwargs", {}).get("harness",
                               item["config"]["agent"].get("name", "unknown")) == harness]
        report["dataset_metrics"] = {
            "definitions": plan["manifest"].get("metrics", []) or [{"type": "mean"}],
            "aggregates": (aggregate_metrics(plan["manifest"].get("metrics", []), original_rewards, plan.get("metric_runtime"))
                           if not any(m["type"] == "uv-script" for m in plan["manifest"].get("metrics", [])) else None),
            "scheduled_trials": len(records),
            "missing_trials": report["missing_trials"],
            "error_trials": report["evaluation_errors"],
            "semantics": "Harbor metrics over trial rewards, including None; ACB completed-only means are separate",
        }
        native_path = output / "harbor-native-stats.json"
        native_stats = json.loads(native_path.read_text()) if native_path.exists() else None
        prefix = (control or "acb-" + harness) + "__"
        report["dataset_metrics"]["native_results"] = {
            key: value for key, value in (native_stats or {}).get("evals", {}).items()
            if key.startswith(prefix)
        } if native_stats is not None else None
        report["dataset_metrics"]["semantics"] = (
            "native_results preserves Harbor output; aggregates recomputes built-in metrics over "
            "scheduled slots including missing trials as None. Custom scripts are not re-executed "
            "during import; their recomputed aggregates are unavailable. ACB completed-only means are separate")
        report["reward_aggregate_semantics"] = "ACB means over completed grades only"
        primary = config.get("reward_metric")
        report["grade_definition"] = {
            "metric": primary or "resolved",
            "direction": config.get("grade_direction", "higher" if config.get("success_value") == 1 else None),
            "tolerance": config.get("grade_tolerance", 0),
        }
        settings = plan["harnesses"].get(harness, {})
        report["comparison_provenance"] = {
            "conditions": {"timeout": settings.get("timeout"), "attempts": plan.get("attempts", 1),
                           "cache_policy": plan.get("cache_policy"), "environment": plan.get("environment"),
                           "backend": "harbor", "dataset_revision": plan["manifest"].get("revision"),
                           "native_grader": {key: value for key, value in (plan.get("benchmark_grader") or {}).items() if key != "binary"},
                           "metrics": comparison_definitions(plan["manifest"])},
            "tasks": {task["id"]: {"sha256": task["sha256"],
                      "runtime": plan.get("runtime_contracts", {}).get(task["id"])
                          or plan.get("task_plans", {}).get(task["id"], {}).get("runtime")}
                      for task in plan["manifest"].get("tasks", [])},
        }
        report_path.write_text(json.dumps(report, indent=2))
    build_suite_report(output, RunConfig(plan["run_id"], plan["benchmark"], list(by_harness), plan["model"]["name"]))
