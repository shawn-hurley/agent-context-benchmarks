"""Run orchestration: benchmark x harness x model, measured through the proxy.

Generation is container-only. For each instance:
  1. create a Podman pod; start the benchmark's testbed container in it
     (built/resolved on demand -- acb/benchmarks/image_builder.py for
     SWE-bench) and a sibling Praxis container tagged with
     (run, harness, model, benchmark, id)
  2. run the harness via `podman exec` inside the testbed container, pointed
     at the (containerized) proxy's base_url
  3. stop the proxy (flushes usage rows), collect the prediction from the
     container, tear down the pod

Then evaluate all predictions and merge with usage into a report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform as _platform
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dataclasses import asdict

from acb.benchmarks import make_benchmark, Prediction
from acb.config import RunConfig, Registries
from acb.container import build_image, container_stop_rm, image_exists, pod_create, pod_remove
from acb.harnesses import make_harness
from acb.logging_config import setup_acb_logger, log_debug
from acb.proxy import ProxyTags
from acb.proxy.praxis import PraxisContainerBackend
from acb.report import aggregate_per_instance_files, build_report
from acb.ui import ProgressTracker, setup_interrupt_handler, LiveTrackerDisplay
from acb.usage import InstanceMetrics, read_records

# praxis-ai, not core praxis: only praxis-ai has the `token_count` filter
# that computes real per-request token usage -- see acb/proxy/praxis.py's
# module docstring for the live-verified gaps in how it exposes that data
# (and how acb works around them) that make this less simple than it sounds.
PRAXIS_IMAGE_DEFAULT = "acb-praxis-ai:latest"

# Repo root: two levels up from this file (acb/runner.py → acb/ → repo root).
# Used as the build context for the self-contained Containerfile that compiles
# praxis-ai with the praxis-vertex-anthropic filter baked in.
_REPO_ROOT = Path(__file__).parent.parent

_ARCH_MAP = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}


def _write_per_instance_prediction(prediction: Prediction, instance_dir: Path) -> None:
    """Write a single prediction to its instance directory immediately."""
    pred_path = instance_dir / "prediction.json"
    pred_path.write_text(json.dumps({
        "instance_id": prediction.instance_id,
        "model_name_or_path": prediction.model_name_or_path,
        "model_patch": prediction.model_patch or "",
    }, indent=2))


def _write_per_instance_metrics(instance_dir: Path, cfg) -> None:
    """Compute and write metrics for a single instance."""
    usage_path = instance_dir / "usage.jsonl"
    if not usage_path.exists():
        return  # No usage data (test may have errored before making requests)
    
    records = list(read_records(usage_path))
    if not records:
        return
    
    metrics = InstanceMetrics.from_records(records)
    # Note: resolved status not known yet (evaluation hasn't run)
    # Will be updated later by build_report()
    
    metrics_path = instance_dir / "metrics.json"
    metrics_path.write_text(json.dumps(asdict(metrics), indent=2))


def _resolve_arch(bench_cfg: dict) -> str:
    arch = bench_cfg.get("image_arch", "auto")
    if arch != "auto":
        return arch
    return _ARCH_MAP.get(_platform.machine(), "amd64")


def _ensure_praxis_image(bench_cfg: dict) -> str:
    """Return the praxis-ai image tag to use, building it if absent.

    The image tag is taken from ``bench_cfg["praxis_image"]`` when present
    (set via benchmarks.yaml's ``praxis_image`` key -- useful during
    development to keep a side tag like ``acb-praxis-ai:vertex-dev``
    separate from the stable ``acb-praxis-ai:latest``).  Falls back to
    ``PRAXIS_IMAGE_DEFAULT`` when not set.

    Build source (when the image is absent):

    1. ``bench_cfg["praxis_ai_repo"]`` -- optional override pointing at a
       local checkout of https://github.com/praxis-proxy/ai that has its own
       ``Containerfile``.  Use this when you need to test against a modified
       upstream tree.

    2. The ``Containerfile`` at the repository root (default) -- self-
       contained: clones praxis-proxy/ai at a pinned commit and compiles the
       praxis-vertex-anthropic filter in, all within the container build.
       No external checkout required.
    """
    image = bench_cfg.get("praxis_image", PRAXIS_IMAGE_DEFAULT)
    if image_exists(image):
        return image
    praxis_ai_repo = bench_cfg.get("praxis_ai_repo")
    if praxis_ai_repo:
        # Legacy / override path: build from a local checkout of
        # https://github.com/praxis-proxy/ai (its own Containerfile).
        praxis_ai_repo = Path(praxis_ai_repo)
        print(f"[praxis-ai] building {image} from {praxis_ai_repo} ...", flush=True)
        build_image(praxis_ai_repo / "Containerfile", praxis_ai_repo, image)
    else:
        # Default path: use the self-contained Containerfile at the repo root.
        # Build context is the repo root so COPY praxis-vertex-anthropic/ works.
        containerfile = _REPO_ROOT / "Containerfile"
        print(f"[praxis-ai] building {image} from {containerfile} ...", flush=True)
        build_image(containerfile, _REPO_ROOT, image)
    print(f"[praxis-ai] built {image}", flush=True)
    return image


def _fetch_vertex_token() -> str:
    """Fetch a fresh GCP OAuth2 Bearer token via Application Default Credentials.

    Reads ``GOOGLE_APPLICATION_CREDENTIALS`` (service account key JSON)
    automatically via ``google.auth.default()``.  Called once per instance
    immediately before the praxis container is started; the token is valid
    for ~1 hour, well beyond any single-instance benchmark run.

    The token is passed explicitly to ``PraxisContainerBackend`` via
    ``extra_env`` rather than written to ``os.environ``, which would be a
    race condition under ``max_workers > 1`` (multiple ``do_one()`` threads
    share the same process environment).

    Raises ``RuntimeError`` with a clear message if ``google-auth`` is not
    installed or credentials cannot be resolved.
    """
    try:
        import google.auth  # type: ignore[import]
        import google.auth.transport.requests  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-auth is required for Vertex AI runs: "
            "pip install 'google-auth>=2.0'"
        ) from exc

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    token: str = credentials.token
    if not token:
        raise RuntimeError(
            "google.auth.default() returned credentials but no token was produced; "
            "verify GOOGLE_APPLICATION_CREDENTIALS points to a valid service account key."
        )
    return token


def _praxis_extra_env(model_spec) -> dict[str, str]:
    """Return real provider credentials to inject into the Praxis container.

    Harness containers only receive placeholder keys; Praxis owns upstream
    authentication and injects the real credential selected by proxy.yaml.
    """
    if model_spec.is_vertex:
        return {"GCP_ACCESS_TOKEN": _fetch_vertex_token()}
    if not model_spec.key_env:
        return {}
    value = os.environ.get(model_spec.key_env)
    if not value:
        raise RuntimeError(
            f"{model_spec.key_env} is required for model {model_spec.name!r}; "
            "set it in the host environment before running ACB"
        )
    return {model_spec.key_env: value}


def _setup_harness_directories(
    harness_names: list[str],
    out_dir: Path,
    instances: list,
) -> None:
    """Pre-create harness and instance directories before running work queue.
    
    This ensures all directories exist before any worker threads try to
    write files, avoiding race conditions and making directory structure
    visible immediately.
    
    Args:
        harness_names: List of harness names to set up
        out_dir: Run output directory
        instances: List of benchmark instances
    """
    for harness_name in harness_names:
        harness_out_dir = out_dir / harness_name
        instances_dir = harness_out_dir / "instances"
        instances_dir.mkdir(parents=True, exist_ok=True)
        
        # Pre-create per-instance directories
        for inst in instances:
            inst_dir = instances_dir / inst.instance_id
            inst_dir.mkdir(parents=True, exist_ok=True)


def _run_instance_pipeline(
    instance,
    harness_name: str,
    harness_out_dir: Path,
    cfg: RunConfig,
    registries: Registries,
    benchmark,
    bench_cfg: dict,
    model_spec,
    proxy_cfg: dict,
    cache_dir: Path,
    tracker: ProgressTracker | None = None,
) -> tuple[Prediction, dict[str, bool]]:
    """Execute per-instance pipeline: generation → verification.
    
    This function runs one atomic unit of work: generates a prediction
    for one instance with one harness, then immediately evaluates it.
    
    Args:
        instance: Benchmark instance to process
        harness_name: Name of harness to use
        harness_out_dir: Harness output directory
        cfg: Run configuration
        registries: Loaded registries
        benchmark: Benchmark instance
        bench_cfg: Benchmark configuration
        model_spec: Model specification
        proxy_cfg: Proxy configuration
        cache_dir: Cache directory for shared assets
        tracker: Optional progress tracker
    
    Returns:
        (prediction, {instance_id: resolved_bool})
    """
    harness_cfg = {**registries.harnesses.get(harness_name, {}), **cfg.overrides.get("harness", {})}
    instances_dir = harness_out_dir / "instances"
    instance_dir = instances_dir / instance.instance_id
    usage_path = instance_dir / "usage.jsonl"
    
    # Composite key for tracker: {harness}-{instance_id}
    tracker_key = f"{harness_name}-{instance.instance_id}"
    
    # Create deterministic pod name from composite key (run_id/harness/instance)
    pod_name_input = f"{cfg.run_id}/{harness_name}/{instance.instance_id}"
    pod_hash = hashlib.sha256(pod_name_input.encode()).hexdigest()[:16]
    pod_name = f"acb-{pod_hash}"
    
    # Generate prediction
    prediction = None
    if tracker:
        tracker.start_instance(tracker_key, pod_name=pod_name)
    
    arch = _resolve_arch(bench_cfg)
    build_dir = harness_out_dir / "image_build"
    
    # Create pod with run_id label for tracking/cleanup
    pod_create(
        pod_name,
        labels={
            "acb-run-id": cfg.run_id,
            "acb-harness": harness_name,
            "acb-instance": instance.instance_id,
        }
    )
    testbed_container = None
    
    try:
        if cfg.proxy != "praxis":
            raise RuntimeError(
                f"proxy backend {cfg.proxy!r} has no container-mode "
                "implementation; only `praxis` does today."
            )
        
        harness = make_harness(harness_name, harness_cfg)
        harness.api = harness.effective_api(model_spec.api)
        harness._tracker = tracker
        harness._tracker_key = tracker_key  # Composite key {harness}-{instance_id} for activity updates
        
        testbed_container = benchmark.prepare_container(
            instance, pod_name, build_dir, arch,
        )
        harness.setup_container(testbed_container, arch, cache_dir)
        
        tags = ProxyTags(
            run_id=cfg.run_id, benchmark=cfg.benchmark, harness=harness_name,
            model=cfg.model, instance_id=instance.instance_id,
        )
        praxis_backend = PraxisContainerBackend(
            tags=tags, usage_path=usage_path, config=proxy_cfg,
            model_spec=model_spec, harness_api=harness.api,
            pod=pod_name, image=_ensure_praxis_image(bench_cfg),
            extra_env=_praxis_extra_env(model_spec),
            instance_dir=instance_dir,
        )
        
        with praxis_backend:
            env = harness.build_container_env(praxis_backend.base_url, praxis_backend.api_key)
            harness.run_container(
                instance.prompt, testbed_container, cfg.model, env,
                instance_dir, instance.instance_id,
            )
        
        prediction = benchmark.collect_prediction_container(instance, testbed_container, cfg.model)
        
        # Write per-instance files immediately
        _write_per_instance_prediction(prediction, instance_dir)
        _write_per_instance_metrics(instance_dir, cfg)
        
        # Write pod name for debugging/inspection
        pod_info_file = instance_dir / "pod_name.txt"
        pod_info_file.write_text(f"{pod_name}\n# Harness: {harness_name}\n# Instance: {instance.instance_id}\n")
        
        # Mark generation complete
        metrics_path = instance_dir / "metrics.json"
        tokens = None
        if metrics_path.exists():
            try:
                metrics_data = json.loads(metrics_path.read_text())
                tokens = metrics_data.get("total_tokens")
            except (json.JSONDecodeError, OSError):
                pass
        
        if tracker:
            tracker.complete_instance(tracker_key, success=True, tokens=tokens)
    
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        # Don't print - traceback is captured in tb and written to error.json
        # This prevents stderr output from bypassing Live display
        
        error_path = instance_dir / "error.json"
        error_path.write_text(json.dumps({
            "instance_id": instance.instance_id,
            "error": str(e),
            "traceback": tb
        }, indent=2))
        
        if tracker:
            tracker.complete_instance(tracker_key, success=False, error=str(e))
        
        return Prediction(instance_id=instance.instance_id,
                        model_name_or_path=cfg.model, error=str(e)), {}
    
    finally:
        if not os.environ.get("ACB_DEBUG_KEEP_CONTAINERS"):
            # Normal cleanup: remove container and pod
            if testbed_container:
                container_stop_rm(testbed_container)
            pod_remove(pod_name)
        # Debug: Pod info written to pod_name.txt (line 322-323) for inspection
    
    if prediction is None:
        return Prediction(instance_id=instance.instance_id,
                        model_name_or_path=cfg.model, error="No prediction generated"), {}
    
    # Verify prediction
    try:
        if tracker:
            tracker.start_verification(tracker_key)
        resolved = benchmark.evaluate(
            predictions=None,
            run_id=cfg.run_id,
            output_dir=harness_out_dir,
            tracker=tracker,
            instance_id=instance.instance_id,
            tracker_key=tracker_key,
        )
        return prediction, resolved
    except Exception as e:  # noqa: BLE001
        error_msg = f"Evaluation failed: {str(e)}"
        if tracker:
            tracker.complete_verification(tracker_key, False, error=error_msg)
        # Return empty resolved dict (instance will be marked as failed)
        return prediction, {}


def _resolve_run_dir(output_dir: str, run_id: str) -> tuple[Path, str]:
    """Pick a unique output directory for this run, appending ``-N`` on collision.

    First run:  ``runs/<run_id>/``        (no suffix)
    Second run: ``runs/<run_id>-1/``
    Third run:  ``runs/<run_id>-2/``
    …and so on.

    Returns ``(out_dir, effective_run_id)`` — the caller should use
    ``effective_run_id`` everywhere (reports, usage records, pod names)
    so metadata stays consistent with the on-disk path.
    """
    base = Path(output_dir).resolve()
    candidate = base / run_id
    if not candidate.exists():
        return candidate, run_id

    # Scan for existing <run_id>-N directories to find the next number.
    pattern = re.compile(rf"^{re.escape(run_id)}-(\d+)$")
    max_n = 0  # 0 means only the unsuffixed dir exists → next is -1
    for entry in base.iterdir():
        if entry.is_dir():
            m = pattern.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))

    next_n = max_n + 1
    effective_id = f"{run_id}-{next_n}"
    return base / effective_id, effective_id


def _setup_logging(out_dir: Path, verbose: bool = False) -> logging.Logger:
    """Setup logging to file and optionally to console.
    
    Uses the centralized logging_config module for thread-safe setup.
    
    Args:
        out_dir: Output directory for the run
        verbose: If True, also log to console (shows live output during run)
    
    Returns:
        Configured logger instance
    """
    log_file = out_dir / "acb.log"
    return setup_acb_logger(log_file, verbose=verbose)


def run(cfg: RunConfig, registries: Registries | None = None, verbose: bool = False) -> Path:
    registries = registries or Registries.load()
    # Absolute: SWE-bench's evaluation subprocess runs with cwd=SWE-bench/, so
    # any relative path derived from out_dir (predictions.jsonl etc.) would
    # otherwise resolve against the wrong directory once passed to it.
    out_dir, effective_run_id = _resolve_run_dir(cfg.output_dir, cfg.run_id)
    if effective_run_id != cfg.run_id:
        print(f"[acb] previous run exists, using {effective_run_id}")
        cfg = RunConfig(**{**cfg.__dict__, "run_id": effective_run_id})
    
    # Create output directory first, then setup logging to file
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_dir, verbose=verbose)

    bench_cfg = {**registries.benchmarks.get(cfg.benchmark, {}), **cfg.overrides.get("benchmark", {})}
    proxy_cfg = {**registries.backend_config(cfg.proxy), **cfg.overrides.get("proxy", {})}
    model_spec = registries.model_spec(cfg.model)

    benchmark = make_benchmark(cfg.benchmark, bench_cfg)
    instances = benchmark.load_instances(subset=cfg.subset, limit=cfg.limit)
    
    harnesses_to_run = cfg.harnesses

    # Create output-dir-level cache directory, shared by all runs under it.
    cache_dir = (Path(cfg.output_dir).resolve() / ".cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Create a single tracker for all harnesses (single or multi)
    # This ensures the display shows progress across all harnesses
    tracker = ProgressTracker(
        total_instances=len(instances) * len(harnesses_to_run),
        harness_names=harnesses_to_run,
        max_workers=cfg.max_workers,
        run_id=cfg.run_id,
        model=cfg.model,
        benchmark=cfg.benchmark,
    )

    # Pre-create all harness/instance directories before starting workers
    _setup_harness_directories(harnesses_to_run, out_dir, instances)

    # Pre-register all instances with tracker so they show in initial display
    for harness_name in harnesses_to_run:
        for instance in instances:
            tracker.add_instance(instance.instance_id, harness_name)

    # Build work queue: all (harness, instance) pairs
    work_queue = [
        (harness_name, instance)
        for harness_name in harnesses_to_run
        for instance in instances
    ]

    # Execute global work queue with per-instance pipeline
    from rich.live import Live
    
    tracker.console.print(f"\n[cyan]Starting global work queue: {len(work_queue)} (harness, instance) pairs[/cyan]")
    
    all_results = {}  # (harness_name, instance_id) -> (prediction, resolved)
    harness_reports: dict[str, dict] = {}
    
    ex = None
    try:
        ex = ThreadPoolExecutor(max_workers=cfg.max_workers)
        setup_interrupt_handler(tracker, ex)
        
        display = LiveTrackerDisplay(tracker)
        
         # Diagnostic logging: record Live display startup
        log_debug(f"Starting Live display: verbose={verbose}, redirect_stderr={not verbose}")
        
        # In verbose mode, show stderr output live (don't redirect it)
        # In normal mode, redirect stderr to prevent blanking the display
        with Live(display, refresh_per_second=2, console=tracker.console, 
                  redirect_stderr=not verbose) as live:
            # Force initial render so display appears immediately with all queued instances
            live.refresh()
            
            # Submit all work items
            futures = {}
            for harness_name, instance in work_queue:
                harness_out_dir = out_dir / harness_name
                future = ex.submit(
                    _run_instance_pipeline,
                    instance, harness_name, harness_out_dir,
                    cfg, registries, benchmark, bench_cfg,
                    model_spec, proxy_cfg, cache_dir,
                    tracker=tracker,
                )
                futures[future] = (harness_name, instance.instance_id)
            
            # Collect results as they complete
            for future in as_completed(futures):
                if tracker.interrupted:
                    break
                harness_name, instance_id = futures[future]
                try:
                    prediction, resolved = future.result()
                    all_results[(harness_name, instance_id)] = (prediction, resolved)
                except Exception as e:  # noqa: BLE001
                    # Record error in tracker instead of printing (avoids blanking Live display)
                    # Errors will be shown in the final summary after Live context exits
                    tracker_key = f"{harness_name}-{instance_id}"
                    error_with_type = f"{type(e).__name__}: {str(e)}"
                    tracker.record_pipeline_error(tracker_key, error_with_type)
        
        # Diagnostic logging: Live display exited normally
        log_debug("Live display exited normally")
    finally:
        if ex:
            ex.shutdown(wait=True)
    
    # Show final summary to console AND save to file
    tracker.console.print(tracker.summary())
    tracker.save_summary_to_file(out_dir)
    
    # Show log file location
    log_file = out_dir / "acb.log"
    tracker.console.print(f"\n[dim]Full logs saved to: {log_file.resolve()}[/dim]")

    # Post-process results by harness and build reports
    for harness_name in harnesses_to_run:
        harness_out_dir = out_dir / harness_name
        
        # Aggregate predictions and resolved status for this harness
        harness_predictions = []
        harness_resolved = {}
        
        for (h_name, inst_id), (pred, resolved) in all_results.items():
            if h_name == harness_name:
                harness_predictions.append(pred)
                harness_resolved.update(resolved)
        
        # Aggregate per-instance files into combined files for backwards compatibility
        aggregate_per_instance_files(harness_out_dir)
        
        # Build per-harness report
        usage_path = harness_out_dir / "usage.jsonl"
        report_path = build_report(usage_path, harness_resolved, harness_out_dir, cfg)
        console = tracker.console if tracker else None
        if console:
            console.print(f"[green]✅ {harness_name} report:[/green] {report_path}")
        else:
            print(f"[acb] {harness_name} report: {report_path}")
        
        # Load and store report for suite aggregation
        if report_path.exists():
            harness_reports[harness_name] = json.loads(report_path.read_text())
        
        # Build per-harness HTML report
        try:
            from acb.html_report import build_html_report
            html_content = build_html_report(harness_out_dir)
            html_path = harness_out_dir / "report.html"
            html_path.write_text(html_content)
            if console:
                console.print(f"[green]✅ {harness_name} HTML report:[/green] {html_path}")
            else:
                print(f"[acb] {harness_name} html report: {html_path}")
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️  Warning: failed to build HTML report for {harness_name}: {e}[/yellow]")
            else:
                print(f"[acb] warning: failed to build HTML report for {harness_name}: {e}")

    # Build suite-level report (aggregate across harnesses)
    console = tracker.console if tracker else None
    if len(harnesses_to_run) > 1:
        from acb.report import build_suite_report
        suite_report_path = build_suite_report(out_dir, cfg)
        if console:
            console.print(f"[green]✅ Suite report:[/green] {suite_report_path}")
        else:
            print(f"[acb] suite report: {suite_report_path}")
        
        # Build suite-level HTML report
        try:
            from acb.html_report import build_html_report
            html_content = build_html_report(out_dir)
            html_path = out_dir / "report.html"
            html_path.write_text(html_content)
            if console:
                console.print(f"[green]✅ Suite HTML report:[/green] {html_path}")
            else:
                print(f"[acb] suite html report: {html_path}")
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️  Warning: failed to build suite HTML report: {e}[/yellow]")
            else:
                print(f"[acb] warning: failed to build suite HTML report: {e}")

    # Return the suite directory path
    return out_dir
