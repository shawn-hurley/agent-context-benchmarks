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
import os
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acb.ui import ProgressTracker

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
    # Silent operation - logged to SWE-bench's own logs (only runs once)
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


def _tail_file(
    path: Path,
    prefix: str,
    stop_event: threading.Event,
    tracker: 'ProgressTracker | None' = None,
    tracker_key: str | None = None,
) -> None:
    """Track progress from SWE-bench evaluation log file.

    Updates tracker activity with evaluation progress instead of printing.
    All detailed output is written to log files; we update the Live display
    via tracker activity updates to avoid flickering from continuous prints.
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
                # Update tracker activity instead of printing (avoids display flicker)
                if tracker and tracker_key:
                    status = line.strip()[:50]  # First 50 chars for activity display
                    tracker.update_activity(tracker_key, f"eval: {status}")
                # All detailed output goes to log file; no print needed
            else:
                stop_event.wait(0.3)
        # Trailing lines also logged to file - no print needed
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
            except RuntimeError:
                # Non-critical: skip paths that can't be staged (logged in files)
                pass

        diff = container_exec_capture(container, ["git", "-C", "/testbed", "diff", "--cached"])
        return Prediction(
            instance_id=instance.instance_id,
            model_name_or_path=model,
            model_patch=diff,
        )

    def evaluate(
        self,
        predictions,
        run_id: str,
        output_dir,
        tracker: ProgressTracker | None = None,
        instance_id: str | None = None,
        tracker_key: str | None = None,
    ) -> dict[str, bool]:
        """Evaluate predictions using SWE-bench harness.
        
        Reads predictions from disk (per-instance or legacy format), runs
        SWE-bench evaluation subprocess, and updates tracker if provided.
        
        Args:
            predictions: Legacy parameter (ignored)
            run_id: Unique identifier for this run
            output_dir: Harness output directory
            tracker: Optional tracker for status updates
            instance_id: If set, only evaluate this instance
            tracker_key: Composite key {harness}-{instance_id} for tracker updates
        
        Returns:
            Dictionary mapping instance_id to resolved status
        """
        output_dir = Path(output_dir)
        preds_path = output_dir / "predictions.jsonl"
        instances_dir = output_dir / "instances"
        
        # Collect predictions to evaluate
        preds_to_eval = []
        
        if instance_id:
            # Per-instance mode: evaluate only this instance
            pred_file = instances_dir / instance_id / "prediction.json"
            if pred_file.exists():
                pred = json.loads(pred_file.read_text())
                preds_to_eval.append(Prediction(
                    instance_id=pred["instance_id"],
                    model_name_or_path=pred["model_name_or_path"],
                    model_patch=pred.get("model_patch", ""),
                ))
        else:
            # Legacy mode: aggregate all predictions
            if instances_dir.exists():
                for inst_dir in sorted(instances_dir.iterdir()):
                    if inst_dir.is_dir():
                        pred_file = inst_dir / "prediction.json"
                        if pred_file.exists():
                            pred = json.loads(pred_file.read_text())
                            preds_to_eval.append(Prediction(
                                instance_id=pred["instance_id"],
                                model_name_or_path=pred["model_name_or_path"],
                                model_patch=pred.get("model_patch", ""),
                            ))
            elif predictions:
                preds_to_eval = predictions
        
        if not preds_to_eval:
            return {instance_id: False} if instance_id else {}
        
        # Write predictions to temp file for swebench
        with preds_path.open("w") as f:
            for p in preds_to_eval:
                f.write(json.dumps({
                    "instance_id": p.instance_id,
                    "model_name_or_path": p.model_name_or_path,
                    "model_patch": p.model_patch or "",
                }) + "\n")
        
        # Update tracker: mark verification starting
        if tracker and tracker_key:
            tracker.start_verification(tracker_key)
        
        # Run swebench evaluation
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
        
        env = container_env(self.config)
        
        # Tail evaluation logs and update tracker activity
        log_dir_base = _SWEBENCH_DIR / "logs" / "run_evaluation" / run_id
        stop_event = threading.Event()
        tailers: list[threading.Thread] = []
        for p in preds_to_eval:
            log_path = log_dir_base / p.model_name_or_path / p.instance_id / "run_instance.log"
            # Pass tracker so we can update activity instead of printing
            eval_tracker_key = tracker_key if p.instance_id == instance_id else None
            t = threading.Thread(
                target=_tail_file,
                args=(log_path, f"[eval:{p.instance_id}]", stop_event),
                kwargs={'tracker': tracker, 'tracker_key': eval_tracker_key},
                daemon=True,
            )
            t.start()
            tailers.append(t)
        
        # Redirect evaluation subprocess output to log file to prevent terminal interference
        # This prevents SWE-bench's output from bypassing Rich's Live display and causing blanking
        eval_log_path = output_dir / f"swebench_eval_{run_id}.log"
        try:
            with eval_log_path.open("w") as eval_log:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_SWEBENCH_DIR),
                    env=env,
                    stdout=eval_log,
                    stderr=subprocess.STDOUT
                )
                proc.wait()
        except Exception as e:
            error_msg = f"SWE-bench evaluation failed: {str(e)}"
            if tracker and tracker_key:
                tracker.complete_verification(tracker_key, False, error=error_msg)
            raise
        finally:
            stop_event.set()
            for t in tailers:
                t.join(timeout=5)
        
        # Tracker shows verification status - no print needed
        
        # Parse results
        resolved: dict[str, bool] = {}
        for report in _SWEBENCH_DIR.glob(f"*.{run_id}.json"):
            data = json.loads(report.read_text())
            for iid in data.get("resolved_ids", []):
                resolved[iid] = True
            for iid in data.get("unresolved_ids", []):
                resolved.setdefault(iid, False)
        
        for p in preds_to_eval:
            resolved.setdefault(p.instance_id, False)
        
        # Update tracker with results
        if tracker and tracker_key and instance_id and instance_id in resolved:
            tracker.complete_verification(tracker_key, resolved[instance_id])
        
        return resolved
