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

from acb.benchmarks import make_benchmark, Prediction
from acb.config import RunConfig, Registries
from acb.container import build_image, container_stop_rm, image_exists, pod_create, pod_remove
from acb.harnesses import make_harness
from acb.harnesses.goose import ensure_linux_binary
from acb.proxy import ProxyTags
from acb.proxy.praxis import PraxisContainerBackend
from acb.report import build_report

# praxis-ai, not core praxis: only praxis-ai has the `token_count` filter
# that computes real per-request token usage -- see acb/proxy/praxis.py's
# module docstring for the live-verified gaps in how it exposes that data
# (and how acb works around them) that make this less simple than it sounds.
PRAXIS_IMAGE = "acb-praxis-ai:latest"

_ARCH_MAP = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}


def _resolve_arch(bench_cfg: dict) -> str:
    arch = bench_cfg.get("image_arch", "auto")
    if arch != "auto":
        return arch
    return _ARCH_MAP.get(_platform.machine(), "amd64")


def _ensure_praxis_image(bench_cfg: dict) -> str:
    if image_exists(PRAXIS_IMAGE):
        return PRAXIS_IMAGE
    praxis_ai_repo = bench_cfg.get("praxis_ai_repo")
    if not praxis_ai_repo:
        raise RuntimeError(
            f"{PRAXIS_IMAGE} not found and no `praxis_ai_repo` configured to build it "
            "(benchmarks.yaml swebench.praxis_ai_repo -- path to a checkout of "
            "https://github.com/praxis-proxy/ai with a Containerfile)."
        )
    praxis_ai_repo = Path(praxis_ai_repo)
    print(f"[praxis-ai] building {PRAXIS_IMAGE} from {praxis_ai_repo} ...", flush=True)
    build_image(praxis_ai_repo / "Containerfile", praxis_ai_repo, PRAXIS_IMAGE)
    print(f"[praxis-ai] built {PRAXIS_IMAGE}", flush=True)
    return PRAXIS_IMAGE


def run(cfg: RunConfig, registries: Registries | None = None) -> Path:
    registries = registries or Registries.load()
    # Absolute: SWE-bench's evaluation subprocess runs with cwd=SWE-bench/, so
    # any relative path derived from out_dir (predictions.jsonl etc.) would
    # otherwise resolve against the wrong directory once passed to it.
    out_dir = (Path(cfg.output_dir) / cfg.run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    usage_path = out_dir / "usage.jsonl"

    bench_cfg = {**registries.benchmarks.get(cfg.benchmark, {}), **cfg.overrides.get("benchmark", {})}
    harness_cfg = {**registries.harnesses.get(cfg.harness, {}), **cfg.overrides.get("harness", {})}
    proxy_cfg = {**registries.backend_config(cfg.proxy), **cfg.overrides.get("proxy", {})}
    model_spec = registries.model_spec(cfg.model)

    benchmark = make_benchmark(cfg.benchmark, bench_cfg)
    instances = benchmark.load_instances(subset=cfg.subset, limit=cfg.limit)
    print(f"[acb] {cfg.run_id}: {len(instances)} instances "
          f"({cfg.harness} / {cfg.model} / {cfg.benchmark} via {cfg.proxy})")

    def do_one(instance) -> Prediction:
        """One Podman pod per instance, holding two sibling containers
        sharing a network namespace -- the testbed (built/reused via
        `benchmark.prepare_container`) and a Praxis proxy instance. Praxis
        reaches the host's model server via `host.containers.internal`
        (Podman's gvproxy host gateway); the harness reaches Praxis via the
        pod's shared loopback. See acb/proxy/praxis.py, acb/container.py.
        """
        arch = _resolve_arch(bench_cfg)
        build_dir = out_dir / "image_build"
        goose_binary = ensure_linux_binary(arch, out_dir / ".cache")

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
            harness = make_harness(cfg.harness, harness_cfg)
            testbed_container = benchmark.prepare_container(
                instance, pod_name, build_dir, arch, goose_binary,
            )
            tags = ProxyTags(
                run_id=cfg.run_id, benchmark=cfg.benchmark, harness=cfg.harness,
                model=cfg.model, instance_id=instance.instance_id,
            )
            praxis_backend = PraxisContainerBackend(
                tags=tags, usage_path=usage_path, config=proxy_cfg,
                model_spec=model_spec, harness_api=harness.api,
                pod=pod_name, image=_ensure_praxis_image(bench_cfg),
            )
            with praxis_backend:
                env = harness.build_container_env(praxis_backend.base_url, praxis_backend.api_key)
                harness.run_container(
                    instance.prompt, testbed_container, cfg.model, env,
                    out_dir, instance.instance_id,
                )
            return benchmark.collect_prediction_container(instance, testbed_container, cfg.model)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
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
            print(f"[acb]   done {pred.instance_id}"
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
        errors_path = out_dir / "errors.jsonl"
        with errors_path.open("w") as f:
            for p in errored:
                f.write(json.dumps({"instance_id": p.instance_id, "error": p.error}) + "\n")
        print(f"[acb] {len(errored)}/{len(predictions)} instance(s) errored "
              f"during generation -- see {errors_path}", flush=True)

    print("[acb] evaluating predictions ...")
    resolved = benchmark.evaluate(predictions, cfg.run_id, out_dir)

    report_path = build_report(usage_path, resolved, out_dir, cfg)
    print(f"[acb] report: {report_path}")
    return report_path
