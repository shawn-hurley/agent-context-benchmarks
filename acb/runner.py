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
import os
import platform as _platform
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dataclasses import asdict

from acb.benchmarks import make_benchmark, Prediction
from acb.config import RunConfig, Registries
from acb.container import build_image, container_stop_rm, image_exists, pod_create, pod_remove
from acb.harnesses import make_harness
from acb.proxy import ProxyTags
from acb.proxy.praxis import PraxisContainerBackend
from acb.report import aggregate_per_instance_files, build_report
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


def _run_single_harness(
    harness_name: str,
    harness_out_dir: Path,
    cfg: RunConfig,
    registries: Registries,
    benchmark,
    instances: list,
    bench_cfg: dict,
    model_spec,
    proxy_cfg: dict,
    cache_dir: Path,
) -> tuple[list[Prediction], dict[str, bool]]:
    """Run a single harness against all instances and evaluate.
    
    Returns (predictions, resolved_dict).
    """
    harness_cfg = {**registries.harnesses.get(harness_name, {}), **cfg.overrides.get("harness", {})}
    harness_out_dir.mkdir(parents=True, exist_ok=True)
    instances_dir = harness_out_dir / "instances"
    instances_dir.mkdir(exist_ok=True)

    print(f"[acb]   {harness_name}: {len(instances)} instances")

    def do_one(instance) -> Prediction:
        """One Podman pod per instance, holding two sibling containers
        sharing a network namespace -- the testbed (built/reused via
        `benchmark.prepare_container`) and a Praxis proxy instance. Praxis
        reaches the host's model server via `host.containers.internal`
        (Podman's gvproxy host gateway); the harness reaches Praxis via the
        pod's shared loopback. See acb/proxy/praxis.py, acb/container.py.
        """
        # Create per-instance directory for all this instance's data
        instance_dir = instances_dir / instance.instance_id
        instance_dir.mkdir(exist_ok=True)
        usage_path = instance_dir / "usage.jsonl"
        
        arch = _resolve_arch(bench_cfg)
        build_dir = harness_out_dir / "image_build"

        # Podman uses the pod name as the shared hostname for its network
        # namespace, which Linux caps at 64 bytes (HOST_NAME_MAX) -- verified:
        # a 65-char pod name fails `podman start` on a member container with
        # a bare "internal libpod error" and no further detail. run_id can be
        # arbitrarily long, so it's hashed rather than embedded verbatim.
        run_hash = hashlib.sha256(cfg.run_id.encode()).hexdigest()[:8]
        safe_instance = instance.instance_id.replace("_", "-").replace("/", "-").lower()
        pod_name = f"acb-{run_hash}-{safe_instance}"[:64]
        pod_create(pod_name)
        testbed_container = None
        try:
            if cfg.proxy != "praxis":
                # RecordingProxyBackend (acb/proxy/recording.py) launches a
                # host subprocess with no container equivalent -- only
                # PraxisContainerBackend exists today (acb/proxy/praxis.py).
                raise RuntimeError(
                    f"proxy backend {cfg.proxy!r} has no container-mode "
                    "implementation; only `praxis` does today."
                )
            harness = make_harness(harness_name, harness_cfg)
            # Resolve which API the harness will speak for this model backend.
            # Stored as an instance variable so both PraxisContainerBackend
            # (which needs it to decide whether proxy translation is required)
            # and build_container_env() (which uses self.api to pick env vars
            # and CLI flags) see the same resolved value without needing a
            # separate parameter threaded through every call.
            harness.api = harness.effective_api(model_spec.api)
            testbed_container = benchmark.prepare_container(
                instance, pod_name, build_dir, arch,
            )
            # Harness-specific asset staging (e.g. goose's binary,
            # claude-code's) is the harness's own job now, not the
            # benchmark's -- see HarnessAdapter.setup_container().
            # Pass cache_dir (run-level, shared across all harnesses and instances)
            # so binary caching is shared across all instances, avoiding concurrent
            # download race conditions.
            harness.setup_container(testbed_container, arch, cache_dir)
            tags = ProxyTags(
                run_id=cfg.run_id, benchmark=cfg.benchmark, harness=harness_name,
                model=cfg.model, instance_id=instance.instance_id,
            )
            # Fetch a fresh GCP OAuth2 token for Vertex AI backends.
            # Done here (per-instance, inside do_one) rather than once at
            # run() level so a long multi-instance run never uses an expired
            # token (tokens live ~1 hour). Passed explicitly via extra_env
            # rather than os.environ to avoid races between concurrent
            # do_one() threads sharing the same process environment.
            praxis_extra_env: dict[str, str] = {}
            if model_spec.is_vertex:
                praxis_extra_env["GCP_ACCESS_TOKEN"] = _fetch_vertex_token()

            praxis_backend = PraxisContainerBackend(
                tags=tags, usage_path=usage_path, config=proxy_cfg,
                model_spec=model_spec, harness_api=harness.api,
                pod=pod_name, image=_ensure_praxis_image(bench_cfg),
                extra_env=praxis_extra_env,
            )
            with praxis_backend:
                env = harness.build_container_env(praxis_backend.base_url, praxis_backend.api_key)
                harness.run_container(
                    instance.prompt, testbed_container, cfg.model, env,
                    instance_dir, instance.instance_id,
                )
            prediction = benchmark.collect_prediction_container(instance, testbed_container, cfg.model)
            
            # Write per-instance files immediately for visibility during long runs
            _write_per_instance_prediction(prediction, instance_dir)
            _write_per_instance_metrics(instance_dir, cfg)
            
            return prediction
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            traceback.print_exc()
            
            # Write error to per-instance directory for easy debugging
            error_path = instance_dir / "error.json"
            error_path.write_text(json.dumps({
                "instance_id": instance.instance_id,
                "error": str(e),
                "traceback": tb
            }, indent=2))
            
            return Prediction(instance_id=instance.instance_id,
                              model_name_or_path=cfg.model, error=str(e))
        finally:
            # ACB_DEBUG_KEEP_CONTAINERS=1 skips teardown so the pod/container
            # can be inspected post-mortem (`podman exec ... git status`,
            # etc.) -- how the git-add/gitignore bug below was actually
            # root-caused, instead of guessing from transcripts alone.
            if os.environ.get("ACB_DEBUG_KEEP_CONTAINERS"):
                print(f"[debug] ACB_DEBUG_KEEP_CONTAINERS set -- leaving "
                      f"pod={pod_name} container={testbed_container} running", flush=True)
            else:
                if testbed_container:
                    container_stop_rm(testbed_container)
                pod_remove(pod_name)

    predictions: list[Prediction] = []
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
        futures = {ex.submit(do_one, inst): inst for inst in instances}
        for fut in as_completed(futures):
            pred = fut.result()
            predictions.append(pred)
            print(f"[acb]     done {pred.instance_id}"
                  + (f" (error: {pred.error})" if pred.error else ""))

    # predictions.jsonl (below, via benchmark.evaluate()) only carries the
    # fields SWE-bench's own harness expects (instance_id/model_name_or_path/
    # model_patch) -- an exception during generation/collection is otherwise
    # only visible in this run's console output, easy to lose in a long log
    # and easy to misread as "no error, genuinely empty patch" when
    # inspecting predictions.jsonl after the fact (verified: this exact
    # confusion happened -- a real, correct patch was discarded by an
    # exception in collect_prediction_container(), silently, since nothing
    # durable recorded it).
    errored = [p for p in predictions if p.error]
    if errored:
        errors_path = harness_out_dir / "errors.jsonl"
        with errors_path.open("w") as f:
            for p in errored:
                f.write(json.dumps({"instance_id": p.instance_id, "error": p.error}) + "\n")
        print(f"[acb]   {len(errored)}/{len(predictions)} instance(s) errored "
              f"during generation -- see {errors_path}", flush=True)

    print("[acb]   evaluating predictions ...")
    resolved = benchmark.evaluate(predictions, cfg.run_id, harness_out_dir)
    
    return predictions, resolved


def run(cfg: RunConfig, registries: Registries | None = None) -> Path:
    registries = registries or Registries.load()
    # Absolute: SWE-bench's evaluation subprocess runs with cwd=SWE-bench/, so
    # any relative path derived from out_dir (predictions.jsonl etc.) would
    # otherwise resolve against the wrong directory once passed to it.
    out_dir = (Path(cfg.output_dir) / cfg.run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bench_cfg = {**registries.benchmarks.get(cfg.benchmark, {}), **cfg.overrides.get("benchmark", {})}
    proxy_cfg = {**registries.backend_config(cfg.proxy), **cfg.overrides.get("proxy", {})}
    model_spec = registries.model_spec(cfg.model)

    benchmark = make_benchmark(cfg.benchmark, bench_cfg)
    instances = benchmark.load_instances(subset=cfg.subset, limit=cfg.limit)
    
    harnesses_to_run = cfg.harnesses
    print(f"[acb] {cfg.run_id}: {len(instances)} instances "
          f"({', '.join(harnesses_to_run)} / {cfg.model} / {cfg.benchmark} via {cfg.proxy})")

    # Create run-level cache directory for all harnesses and instances
    cache_dir = (out_dir / ".cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Run each harness, collecting reports
    harness_reports: dict[str, dict] = {}
    for harness_name in harnesses_to_run:
        harness_out_dir = out_dir / harness_name
        predictions, resolved = _run_single_harness(
            harness_name, harness_out_dir, cfg, registries, benchmark, instances,
            bench_cfg, model_spec, proxy_cfg, cache_dir,
        )
        
        # Aggregate per-instance files into combined files for backwards compatibility
        aggregate_per_instance_files(harness_out_dir)
        
        # Build per-harness report
        usage_path = harness_out_dir / "usage.jsonl"
        report_path = build_report(usage_path, resolved, harness_out_dir, cfg)
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
            print(f"[acb] {harness_name} html report: {html_path}")
        except Exception as e:
            print(f"[acb] warning: failed to build HTML report for {harness_name}: {e}")

    # Build suite-level report (aggregate across harnesses)
    if len(harnesses_to_run) > 1:
        from acb.report import build_suite_report
        suite_report_path = build_suite_report(out_dir, cfg)
        print(f"[acb] suite report: {suite_report_path}")
        
        # Build suite-level HTML report
        try:
            from acb.html_report import build_html_report
            html_content = build_html_report(out_dir)
            html_path = out_dir / "report.html"
            html_path.write_text(html_content)
            print(f"[acb] suite html report: {html_path}")
        except Exception as e:
            print(f"[acb] warning: failed to build suite HTML report: {e}")

    # Return the suite directory path
    return out_dir
