"""Host-side preparation and isolated worker lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from acb.harbor import HARBOR_VERSION, PROTOCOL_VERSION
from acb.harbor.paths import job_dir
from acb.resolver import ResolvedPlan, resolve


def _worker_log_path(action, output):
    output = Path(output)
    if action == "prepare":
        return output.parent / "worker-prepare.log"
    return output / ".harbor" / "worker.log"


def _worker_failure(action, code, output):
    """Surface recorded trial causes without mistaking every worker exit for a crash."""
    output = Path(output)
    details = []
    result_paths = sorted((output / ".harbor").glob("*/result.json"))
    if not result_paths:
        result_paths = sorted((output / "harbor").glob("*/result.json"))
    for result_path in result_paths:
        try:
            result = json.loads(result_path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        failure = result.get("exception_info")
        if not isinstance(failure, dict) or not failure:
            continue
        kind = failure.get("exception_type") or "TrialError"
        message = failure.get("exception_message") or "No exception message recorded"
        evidence = result_path.with_name("exception.txt")
        if not evidence.is_file():
            evidence = result_path
        details.append(f"  {result_path.parent.name}: {kind}: {message}\n    Details: {evidence}")
    summary = f"Harbor {action} failed (worker exit {code}). Worker log: {_worker_log_path(action, output)}."
    if details:
        return summary + "\nRecorded trial failures:\n" + "\n".join(details) + f"\nResults: {output}"
    return summary + f" No trial failure details were recorded; inspect the worker log and {output}."


def _stop_worker(process, timeouts=(150, 30, 10)):
    """Stop our session and reap its leader, including on repeated interrupts."""
    for sig, timeout in zip((signal.SIGINT, signal.SIGTERM, signal.SIGKILL), timeouts):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
            # The leader may exit before its Compose descendants do.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            continue
    raise RuntimeError(f"could not reap Harbor worker {process.pid} after shutdown")


def _worker(python, action, input_path, output, *, control=None, on_event=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    event_path = None
    event_offset = 0
    pending = ""
    if on_event is not None:
        event_path = Path(output) / ".harbor" / "events.jsonl"
        event_path.unlink(missing_ok=True)
        env["ACB_HARBOR_EVENT_PATH"] = str(event_path.resolve())
    command = [str(python), "-m", "acb.harbor.worker", action, str(input_path), str(output)]
    if control:
        command += ["--control", control]
    log_path = _worker_log_path(action, output)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w")
    try:
        process = subprocess.Popen(command, env=env, start_new_session=True,
                                   stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()

    def read_events():
        nonlocal event_offset, pending
        if event_path is None or not event_path.exists():
            return
        with event_path.open() as stream:
            stream.seek(event_offset)
            pending += stream.read()
            event_offset = stream.tell()
        lines = pending.split("\n")
        pending = lines.pop()
        for line in lines:
            if line:
                on_event(json.loads(line))

    try:
        if on_event is None:
            code = process.wait()
        else:
            while True:
                code = process.poll()
                read_events()
                if code is not None:
                    read_events()
                    break
                time.sleep(.1)
    except BaseException as error:
        try:
            _stop_worker(process)
        except BaseException as cleanup_error:
            error.add_note(f"Harbor worker cleanup failed: {cleanup_error}")
        raise
    if code:
        raise RuntimeError(_worker_failure(action, code, output))


def runtime(plan):
    if plan["benchmark_config"].get("python"):
        raise ValueError("benchmark.python was removed; Harbor uses ACB's Python environment")
    # A virtual environment's python is commonly a symlink to the base binary.
    # Resolving it drops the venv and its installed dependencies in the worker.
    return Path(os.path.abspath(sys.executable))


def prepare(plan: ResolvedPlan, *, probe=True, control=False, status=None) -> dict:
    document = plan.to_dict()
    if document["execution_backend"] != "harbor":
        raise ValueError("prepare requires execution_backend: harbor")
    python = runtime(document)
    from acb.harbor.environment import check_compose
    if status:
        status("Checking container engine and Compose")
    check_compose(document["environment"])
    cache = Path(document["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(plan.document.encode()).hexdigest()
    plan_root = cache / "plans" / identity
    plan_root.mkdir(parents=True, exist_ok=True)
    # Every preparation gets immutable provenance files. Concurrent runs may
    # share verified assets, but must not overwrite each other's worker input.
    destination = Path(tempfile.mkdtemp(prefix="preparation-", dir=plan_root))
    (destination / "requested.json").write_text(json.dumps(
        document.get("requested_config", {"unavailable": True}), indent=2))
    requested = destination / "resolved-input.json"
    requested.write_text(plan.document)
    prepared = destination / "prepared.json"
    if status:
        status("Loading Harbor tasks and dataset")
    _worker(python, "prepare", requested, prepared)
    document = json.loads(prepared.read_text())
    from acb.preparation import prepare_assets
    if not control:
        import time
        inspection = destination / ("inspect-" + str(time.time_ns()))
        if status:
            status("Inspecting task environments and verifier")
        _worker(python, "inspect", prepared, inspection)
        document["verifier_contracts"] = json.loads((inspection / "verifier-contracts.json").read_text())
        contracts = {}
        for path in job_dir(inspection).glob("*/agent/acb/runtime.json"):
            record = json.loads(path.read_text())
            contracts[record["task_id"]] = {key: record[key] for key in (
                "arch", "workdir", "user", "python_path", "language_environment", "container", "service_images")}
            if "task_skills" in record:
                contracts[record["task_id"]]["task_skills"] = record["task_skills"]
        expected = {task["id"] for task in document["manifest"]["tasks"]}
        if set(contracts) != expected:
            raise RuntimeError("runtime inspection did not produce every selected task contract")
        document["runtime_contracts"] = contracts
        from acb.harbor.benchmark_grader import verify_grading_images
        verify_grading_images(document, {record["container"]["image_id"] for record in contracts.values()})
        if status:
            status("Preparing harness assets")
        document = prepare_assets(document)
        from acb.preparation import freeze_provider_images
        if status:
            status("Checking provider images")
        document = freeze_provider_images(document)
    else:
        from acb.preparation import freeze_metric_runtime
        if status:
            status("Preparing metric runtime")
        document = freeze_metric_runtime(document)
    prepared.write_text(json.dumps(document, indent=2))
    if probe:
        import time
        if status:
            status("Probing model, grading and measurement collection")
        _worker(python, "probe", prepared, destination / ("probe-" + str(time.time_ns())))
        document["pending_checks"] = ["measured model protocol, task grading and measurement collection"]
        document["preparation"] = {"probed": True, "harbor_version": HARBOR_VERSION}
        prepared.write_text(json.dumps(document, indent=2))
    document["prepared_file"] = str(prepared)
    document["worker_python"] = str(python)
    return document


def run(cfg, registries=None, verbose=False, control=None):
    return run_plan(resolve(cfg, registries), verbose=verbose, control=control)


def run_plan(plan: ResolvedPlan, *, verbose=False, control=None):
    """Execute an already resolved plan without reading configuration again."""
    from rich.console import Console
    console = Console()
    with console.status("Preparing Harbor run") as preparing:
        def show_status(message):
            preparing.update(message)
            if not console.is_terminal:
                console.print(f"[acb] {message}")

        document = prepare(plan, probe=not bool(control), control=bool(control),
                           status=show_status)
    from acb.run_paths import reserve_run_directory
    output, effective = reserve_run_directory(document["output_dir"], document["run_id"])
    from acb.provenance import save_configuration
    document = save_configuration(output, document, effective_run_id=effective)
    resolved = output / "resolved.json"
    from acb.harbor.job_log import write_job_log
    write_job_log(document, output)
    from acb.logging_config import setup_acb_logger
    setup_acb_logger(output / "acb.log", verbose=verbose)
    from acb.harbor.progress import HarborProgress
    progress = HarborProgress(document, output, control=control)
    from acb.ui import LiveTrackerDisplay
    from rich.live import Live
    progress.tracker.console.print(
        f"\n[cyan]Starting global work queue: {progress.tracker.total} (harness, instance) pairs[/cyan]"
    )
    failure = None
    try:
        if progress.tracker.console.is_terminal:
            display = LiveTrackerDisplay(progress.tracker)
            with Live(display, refresh_per_second=2, console=progress.tracker.console,
                      redirect_stderr=not verbose) as live:
                _worker(document["worker_python"], "control" if control else "run", resolved, output,
                        control=control, on_event=progress.update)
                live.refresh()
        else:
            _worker(document["worker_python"], "control" if control else "run", resolved, output,
                    control=control, on_event=progress.update)
    except BaseException as error:
        failure = error
        raise
    finally:
        progress.finish_unreported(failure)
        progress.save_and_print()
        if (output / "report.json").exists():
            from acb.comparison_html import write_reports
            try:
                write_reports(output, output / "report.html")
            except Exception as error:
                import warnings
                warnings.warn(f"HTML rendering failed; raw results retained in {output}: {error}")
        try:
            write_job_log(document, output, error=failure, finished=True)
            from acb.harbor.trial_view import archive_job_log
            archive_job_log(output, list(document["harnesses"]) if not control else [control])
            print(f"ACB job log: {output / 'job.log'}", flush=True)
        except OSError as log_error:
            import warnings
            warnings.warn(f"ACB job summary could not be written in {output}: {log_error}")
    print(f"Harbor results: {output}")
    return output
