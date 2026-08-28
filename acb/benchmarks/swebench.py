"""SWE-bench benchmark adapter.

Generation: run the harness inside the same eval image evaluation will grade
the patch in (built/resolved on demand -- acb/benchmarks/image_builder.py),
via a long-lived `sleep infinity` container; capture ``git diff`` from inside
it as ``model_patch``. The image's own build already has an anti-leakage
`/testbed` checkout (single-branch, future-history pruned at `base_commit`) --
no separate git setup is needed here.
Evaluation: shell out to the vendored ``swebench.harness.run_evaluation`` and
read back its report.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

# Path to the vendored SWE-bench checkout and its isolated venv.
# The venv is created on first use by _ensure_swebench_venv() so acb's own
# venv never needs swebench's deps (docker, modal, unidiff, etc.).
_SWEBENCH_DIR = Path(__file__).resolve().parents[2] / "SWE-bench"
_SWEBENCH_VENV = _SWEBENCH_DIR / ".venv"


def _ensure_swebench_venv() -> Path:
    """Return the path to the SWE-bench venv's Python, creating it if needed.

    Creates a uv-managed venv inside ``SWE-bench/.venv`` and installs the
    vendored swebench package (with all its own deps) into it.  Only runs
    the first time -- subsequent calls return immediately once the venv
    Python exists.

    This keeps docker, modal, unidiff, and the rest of swebench's dep tree
    completely isolated from acb's own venv.
    """
    python = _SWEBENCH_VENV / "bin" / "python"
    if python.exists():
        return python
    print("[swebench] creating isolated venv for SWE-bench evaluation ...", flush=True)
    subprocess.run(
        ["uv", "venv", str(_SWEBENCH_VENV)],
        cwd=str(_SWEBENCH_DIR),
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "-e", "."],
        cwd=str(_SWEBENCH_DIR),
        check=True,
    )
    print("[swebench] SWE-bench venv ready.", flush=True)
    return python

from acb.benchmarks.base import Benchmark, Instance, Prediction
from acb.benchmarks.image_builder import ensure_instance_image
from acb.container import (
    container_create,
    container_env,
    container_exec_capture,
    container_start,
)

DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"


def _tail_file(path: Path, prefix: str, stop_event: threading.Event) -> None:
    """Print lines appended to ``path`` as they arrive, until ``stop_event`` is set.

    SWE-bench's own per-instance progress (container start, patch application,
    test runtime, grading) is logged to this file only (its logger is created
    with add_stdout=False) -- this is how we surface it live instead of it
    being silently written to a file no one is watching during the run.
    """
    f = None
    try:
        while not stop_event.is_set():
            if f is None:
                if path.exists():
                    f = path.open("r", errors="replace")
                else:
                    stop_event.wait(0.5)
                    continue
            line = f.readline()
            if line:
                print(f"{prefix} {line.rstrip()}", flush=True)
            else:
                stop_event.wait(0.3)
        if f is not None:
            # catch any trailing lines written right before the process exited
            for line in f:
                print(f"{prefix} {line.rstrip()}", flush=True)
    finally:
        if f is not None:
            f.close()


class SWEBench(Benchmark):
    name = "swebench"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        from datasets import load_dataset

        dataset = self.config.get("dataset", DEFAULT_DATASET)
        split = self.config.get("split", "test")
        ds = load_dataset(dataset, split=split)
        instances: list[Instance] = []
        for row in ds:
            if subset and row["instance_id"] not in subset:
                continue
            instances.append(
                Instance(
                    instance_id=row["instance_id"],
                    prompt=self._prompt(row),
                    repo=row["repo"],
                    base_commit=row["base_commit"],
                    # `image`: the exact name evaluation's make_test_spec()
                    # will look for (row["image"] directly, no override
                    # mechanism) -- captured here so prepare_container() can
                    # tag our locally-built image under this exact alias.
                    extra={"problem_statement": row["problem_statement"],
                           "image": row.get("image")},
                )
            )
            if limit and len(instances) >= limit:
                break
        return instances

    def _prompt(self, row: dict) -> str:
        return (
            "You are working in a checked-out git repository. Resolve the following "
            "GitHub issue by editing the code. Do not modify tests.\n\n"
            f"<issue>\n{row['problem_statement']}\n</issue>\n"
        )

    def prepare_container(self, instance: Instance, pod: str, build_dir: Path,
                          arch: str) -> str:
        """Build/resolve the instance's image and start it as a long-lived
        container (`sleep infinity`) attached to `pod`, so the harness can
        `podman exec` into it. Returns the container name.

        The image already contains a single-branch, future-history-pruned
        `/testbed` checkout at `base_commit` (baked in at image-build time by
        the task repo's own Dockerfile) -- no separate git setup is needed
        here.

        Purely benchmark concerns (image resolution, the container itself,
        the untracked-files baseline below) -- staging the harness's own
        runtime (e.g. goose's binary) happens separately, after this
        returns, via `HarnessAdapter.setup_container()`.
        """
        image = ensure_instance_image(
            instance.instance_id, arch, build_dir,
            eval_alias=instance.extra.get("image"),
            task_repo_cache_dir=self.config.get("task_repo_cache_dir"),
        )
        container_name = f"{pod}-testbed"
        container_create(pod, image, container_name, command=["sleep", "infinity"])
        container_start(container_name)
        # Baseline of what's *already* untracked before the harness runs --
        # e.g. `build/` from the image's own `pip install .` step at build
        # time. collect_prediction_container() diffs against this so those
        # pre-existing artifacts don't get swept into model_patch (verified:
        # they were, blowing up a 1-file real fix into a 68-file/871KB diff
        # that a fresh eval container -- with its own copy of the same
        # pre-existing files -- would likely fail to `git apply` anyway).
        #
        # `--exclude-standard` (respects .gitignore, e.g. `*.pyc`) matters
        # here beyond just noise: `git add -- <paths>` *hard-fails* (nonzero
        # exit, though it still stages whatever wasn't ignored) the moment
        # any named path is gitignored. Without this flag, a stray `.pyc`
        # that appears after the baseline snapshot (e.g. from the harness
        # merely importing the package while testing its own fix) poisons
        # the whole `git add` call in collect_prediction_container(), which
        # raises before `git diff --cached` ever runs -- silently discarding
        # a real, otherwise-correct patch (verified: reproduced exactly this
        # with a real transcript that had successfully edited the fix).
        container_exec_capture(
            container_name,
            ["sh", "-c", "git -C /testbed ls-files --others --exclude-standard "
                         "> /tmp/.acb-baseline-untracked.txt"],
        )
        return container_name

    def collect_prediction_container(self, instance: Instance, container: str, model: str) -> Prediction:
        container_exec_capture(container, ["git", "-C", "/testbed", "add", "-u"])

        baseline_out = container_exec_capture(
            container, ["cat", "/tmp/.acb-baseline-untracked.txt"],
        )
        baseline = set(baseline_out.splitlines())
        current_out = container_exec_capture(
            container, ["git", "-C", "/testbed", "ls-files", "--others", "--exclude-standard"],
        )
        new_untracked = [p for p in current_out.splitlines() if p and p not in baseline]
        # One `git add -- <all paths>` at once means a single bad path takes
        # the whole prediction down with it -- verified: a model-generated
        # file with a garbled name (`test_edge_cases_final.py\n</ ...`, from
        # a malformed tool call, not something we control) made `git add`
        # exit nonzero for the *entire* call, discarding an otherwise-valid
        # diff. Adding one at a time contains the damage to that one path.
        for path in new_untracked:
            try:
                container_exec_capture(container, ["git", "-C", "/testbed", "add", "--", path])
            except RuntimeError as e:
                print(f"[swebench] warning: could not stage {path!r}, skipping: {e}", flush=True)

        diff = container_exec_capture(container, ["git", "-C", "/testbed", "diff", "--cached"])
        return Prediction(
            instance_id=instance.instance_id,
            model_name_or_path=model,
            model_patch=diff,
        )

    def evaluate(self, predictions, run_id, output_dir) -> dict[str, bool]:
        preds_path = Path(output_dir) / "predictions.jsonl"
        with preds_path.open("w") as f:
            for p in predictions:
                f.write(json.dumps({
                    "instance_id": p.instance_id,
                    "model_name_or_path": p.model_name_or_path,
                    "model_patch": p.model_patch or "",
                }) + "\n")

        dataset = self.config.get("dataset", DEFAULT_DATASET)
        swebench_python = _ensure_swebench_venv()
        cmd = [
            str(swebench_python), "-m", "swebench.harness.run_evaluation",
            "--dataset_name", dataset,
            "--predictions_path", str(preds_path),
            "--run_id", run_id,
            "--max_workers", str(self.config.get("max_workers", 4)),
        ]
        if self.config.get("namespace") is not None:
            cmd += ["--namespace", str(self.config["namespace"])]
        # route the docker SDK at the configured backend (Docker or Podman)
        env = container_env(self.config)

        # SWE-bench logs container/test progress per-instance to a file only
        # (see _tail_file docstring) -- tail those files live so the console
        # shows what's actually happening instead of going silent until the
        # whole subprocess exits.
        log_dir_base = _SWEBENCH_DIR / "logs" / "run_evaluation" / run_id
        stop_event = threading.Event()
        tailers: list[threading.Thread] = []
        for p in predictions:
            log_path = log_dir_base / p.model_name_or_path / p.instance_id / "run_instance.log"
            t = threading.Thread(
                target=_tail_file,
                args=(log_path, f"[eval:{p.instance_id}]", stop_event),
                daemon=True,
            )
            t.start()
            tailers.append(t)

        print(f"[eval] starting SWE-bench evaluation for {len(predictions)} "
              f"instance(s) (run_id={run_id})...", flush=True)
        proc = subprocess.Popen(cmd, cwd=str(_SWEBENCH_DIR), env=env)
        try:
            proc.wait()
        finally:
            stop_event.set()
            for t in tailers:
                t.join(timeout=5)
        print(f"[eval] evaluation subprocess exited with code {proc.returncode}", flush=True)

        # run_evaluation writes <model>.<run_id>.json in cwd
        resolved: dict[str, bool] = {}
        for report in _SWEBENCH_DIR.glob(f"*.{run_id}.json"):
            data = json.loads(report.read_text())
            for iid in data.get("resolved_ids", []):
                resolved[iid] = True
            for iid in data.get("unresolved_ids", []):
                resolved.setdefault(iid, False)
        for p in predictions:
            resolved.setdefault(p.instance_id, False)
        return resolved
