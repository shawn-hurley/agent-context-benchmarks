"""Adapt Harbor lifecycle events to ACB's established terminal UI."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from acb.ui import ProgressTracker


class HarborProgress:
    def __init__(self, plan: dict, output: Path, *, control: str | None = None):
        harnesses = [control] if control else list(plan["harnesses"])
        attempts = plan.get("attempts", 1)
        tasks = [task["id"] for task in plan["manifest"]["tasks"]]
        self.tracker = ProgressTracker(
            total_instances=len(tasks) * len(harnesses) * attempts,
            harness_names=harnesses,
            max_workers=plan["max_workers"],
            run_id=plan["run_id"],
            model=plan["model"]["name"],
            benchmark=plan["benchmark"],
        )
        self.output = Path(output)
        self._slots: dict[tuple[str, str], deque[str]] = defaultdict(deque)
        self._trials: dict[str, str] = {}
        for harness in harnesses:
            for task in tasks:
                for attempt in range(1, attempts + 1):
                    label = task if attempts == 1 else f"{task} (attempt {attempt})"
                    self.tracker.add_instance(label, harness)
                    self._slots[harness, task].append(f"{harness}-{label}")

    def update(self, event: dict) -> None:
        trial_id = str(event.get("trial_id", ""))
        if event.get("event") == "trial-started":
            slots = self._slots[event["harness"], event["task_id"]]
            if not slots:
                return
            key = slots.popleft()
            self._trials[trial_id] = key
            self.tracker.start_instance(key, pod_name=event.get("trial_name"))
            self.tracker.update_activity(key, "starting environment")
            return
        key = self._trials.get(trial_id)
        if key is None:
            return
        kind = event.get("event")
        if kind == "environment-started":
            self.tracker.update_activity(key, "environment ready")
        elif kind == "agent-started":
            self.tracker.update_activity(key, "agent running")
        elif kind == "agent-ended":
            self.tracker.update_activity(key, "agent completed")
        elif kind == "verification-started":
            self.tracker.complete_instance(key, success=True)
            self.tracker.start_verification(key)
        elif kind == "trial-ended":
            error = event.get("error")
            if not error and event.get("status") == "error":
                error = "Verification did not produce a usable grade; see the trial evidence"
            if error:
                self.tracker.complete_instance(key, success=False, error=error)
                self.tracker.record_pipeline_error(key, error)
            else:
                self.tracker.complete_instance(key, success=True)
                self.tracker.start_verification(key)
                self.tracker.complete_verification(key, bool(event.get("resolved")))

    def finish_unreported(self, error: BaseException | None = None) -> None:
        message = str(error) if error else "Harbor did not report a result"
        for key, item in list(self.tracker.instances.items()):
            if item.status.value in ("queued", "running", "generated", "verifying"):
                self.tracker.complete_instance(key, success=False, error=message)

    def save_and_print(self) -> None:
        self.tracker.console.print(self.tracker.summary())
        self.tracker.save_summary_to_file(self.output)
        self.tracker.console.print(f"\n[dim]Full logs saved to: {(self.output / 'acb.log').resolve()}[/dim]")
