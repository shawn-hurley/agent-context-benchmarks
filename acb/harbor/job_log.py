"""A readable run-level log built from ACB's imported Harbor results."""
from __future__ import annotations

import json
from pathlib import Path

from acb.harbor.paths import job_dir


def _report(output: Path) -> dict | None:
    path = output / "report.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_job_log(plan: dict, output: Path, *, error: BaseException | None = None,
                  finished: bool = False) -> Path:
    """Write a current, navigable summary without replacing native trial records."""
    output = Path(output).resolve()
    report = _report(output)
    harnesses = (report or {}).get("harnesses") or {}
    records = [(harness, item)
               for harness, summary in harnesses.items()
               if isinstance(summary, dict)
               for item in summary.get("evaluations", [])]
    status = ("failed" if error or any(item.get("status") == "error" for _, item in records) else
              "running" if not finished else
              "incomplete" if report is None else
              "completed" if records and all(item.get("status") == "completed" and
                                              item.get("measurement_complete") is True
                                              for _, item in records)
              else "incomplete")
    lines = [f"ACB job: {plan['run_id']}", f"Status: {status}",
             f"Benchmark: {plan['benchmark']}",
             f"Harnesses: {', '.join(plan['harnesses'])}",
             f"Model: {plan['model']['name']}",
             f"Run directory: {output}", ""]
    execution_path = output / "execution-status.json"
    if execution_path.is_file():
        try:
            execution = json.loads(execution_path.read_text())
            lines += [f"ACB execution: {execution['status']} (independent of verification outcomes)",
                      f"Execution details: {execution_path}", ""]
        except (OSError, ValueError, KeyError, TypeError):
            lines += [f"Execution status unavailable; inspect {execution_path}", ""]
    if (output / "import-errors.json").is_file():
        lines += [f"Result import diagnostics: {output / 'import-errors.json'}", ""]
    if records:
        lines += [f"Trials: {len(records)}", ""]
        for harness, item in records:
            task = item.get("task_id") or "unknown task"
            label = item.get("trial_name") or "no Harbor trial was recorded"
            lines.append(f"{task} | {harness} | {label}")
            lines.append(f"  Status: {item.get('status', 'unknown')}; phase: {item.get('error_phase') or 'none'}")
            lines.append(f"  Rewards: {json.dumps(item.get('rewards'), sort_keys=True)}; measurement complete: {item.get('measurement_complete') is True}")
            failure = item.get("exception") or {}
            if failure:
                lines.append(f"  Failure: {failure.get('exception_type') or 'TrialError'}: {failure.get('exception_message') or 'No message recorded'}")
            if item.get("verification_error"):
                lines.append(f"  Verification: {item['verification_error']}")
            trial = job_dir(output) / label if item.get("trial_name") else None
            trial_id = item.get("artifact_id") or item.get("trial_id")
            evidence = None
            if trial_id:
                from acb.harbor.paths import trial_directory
                evidence = trial_directory(output, harness, str(trial_id))
                if evidence.is_dir():
                    lines.append(f"  ACB trial evidence: {evidence}")
            for title, name in (("Traceback", "exception.txt"),
                                ("Trial log", "trial.log"),
                                ("Verifier output", "verifier/test-stdout.txt"),
                                ("Agent transcript", "transcript.log"),
                                ("Praxis log", "praxis.log")):
                path = next((root / name for root in (evidence, trial)
                             if root is not None and (root / name).is_file()), None)
                if path is not None:
                    lines.append(f"  {title}: {path}")
            lines.append(f"  ACB report: {output / harness / 'report.json'}")
            lines.append("")
    elif report is not None:
        lines += ["No trial evaluations were recorded.", ""]
    elif finished:
        lines += ["No run report was recorded.", ""]
    else:
        lines += ["Trial results are pending.", ""]
    if error:
        lines += ["Worker error:", str(error), ""]
    lines += [f"HTML report: {output / 'report.html'}",
              f"Harbor's native job log: {job_dir(output) / 'job.log'}",
              f"Harbor's job config and lock are in {job_dir(output).name}/; each trial also has its own config and lock.",
              "Each harness trial is under <harness>/<trial-id>/."]
    path = output / "job.log"
    path.write_text("\n".join(lines) + "\n")
    return path
