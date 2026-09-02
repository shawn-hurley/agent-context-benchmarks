"""Rich-based progress UI for ACB runs.

Provides real-time status display with:
- Progress bar showing completion percentage
- Live status table with running and completed instances
- Activity updates and token counts
- Unicode emoji support with ASCII fallback
- Color-coded status indicators
"""

from __future__ import annotations

import locale
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn
from rich.table import Table
from rich.text import Text


class InstanceStatus(Enum):
    """Status of a single instance."""
    QUEUED = "queued"
    RUNNING = "running"
    GENERATED = "generated"
    VERIFYING = "verifying"
    VERIFIED_PASS = "verified_pass"
    VERIFIED_FAIL = "verified_fail"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class InstanceProgress:
    """State of a single instance for display."""
    instance_id: str  # Original instance ID (without harness prefix)
    harness: str
    status: InstanceStatus = InstanceStatus.QUEUED
    start_time: float | None = None
    end_time: float | None = None
    last_activity: str = "queued"
    tokens_used: int | None = None
    error_message: str | None = None
    pod_name: str | None = None  # Pod name for debugging/inspection (e.g., "acb-12345678")

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        if not self.start_time:
            return 0.0
        end = self.end_time or time.monotonic()
        return end - self.start_time

    @property
    def elapsed_str(self) -> str:
        """Format elapsed time as string."""
        elapsed = self.elapsed
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes}m{seconds}s"

    @property
    def tokens_str(self) -> str:
        """Format tokens as string with K suffix."""
        if self.tokens_used is None:
            return "—"
        if self.tokens_used >= 1000:
            return f"{self.tokens_used / 1000:.1f}K"
        return str(self.tokens_used)


class ProgressTracker:
    """Main UI controller for tracking and displaying progress."""

    def __init__(
        self,
        total_instances: int,
        harness_names: list[str],
        max_workers: int,
        run_id: str,
        model: str,
        benchmark: str,
    ):
        self.total = total_instances
        self.harnesses = harness_names
        self.max_workers = max_workers
        self.run_id = run_id
        self.model = model
        self.benchmark = benchmark

        self.instances: dict[str, InstanceProgress] = {}
        self.completion_order: list[str] = []  # For "Recent" display
        self.interrupted = False
        self.start_time = time.monotonic()
        self._lock = threading.Lock()  # Thread safety for concurrent updates

        self.console = Console()
        self.use_unicode = self._detect_unicode_support()
        
        if os.environ.get("ACB_DEBUG_UI"):
            import sys
            print(f"[DEBUG] ProgressTracker initialized: {total_instances} instances, "
                  f"harnesses={harness_names}, unicode={self.use_unicode}", 
                  file=sys.stderr, flush=True)

    def _detect_unicode_support(self) -> bool:
        """Detect if terminal supports Unicode/emoji."""
        # Check if we're in a terminal
        if not self.console.is_terminal:
            return False

        # Check encoding
        encoding = sys.stdout.encoding or locale.getpreferredencoding() or "utf-8"
        if "utf" not in encoding.lower():
            return False

        # Check NO_COLOR environment variable
        if os.environ.get("NO_COLOR"):
            return False

        # Check TERM variable for very basic terminals
        term = os.environ.get("TERM", "")
        if term in ("dumb", "unknown"):
            return False

        return True

    def add_instance(self, instance_id: str, harness: str) -> None:
        """Register an instance (called during setup).
        
        Args:
            instance_id: Original instance ID (without harness prefix)
            harness: Harness name
        
        Stores using composite key: {harness}-{instance_id}
        """
        with self._lock:
            composite_key = f"{harness}-{instance_id}"
            self.instances[composite_key] = InstanceProgress(
                instance_id=instance_id, harness=harness, status=InstanceStatus.QUEUED
            )
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                print(f"[DEBUG] add_instance({composite_key}, harness={harness}) - now QUEUED", 
                      file=sys.stderr, flush=True)

    def start_instance(self, tracker_key: str, pod_name: str | None = None) -> None:
        """Mark instance as started.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
            pod_name: Optional pod name for debugging
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
            inst = self.instances[tracker_key]
            inst.status = InstanceStatus.RUNNING
            inst.start_time = time.monotonic()
            if pod_name:
                inst.pod_name = pod_name
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                pod_info = f" pod={pod_name}" if pod_name else ""
                print(f"[DEBUG] start_instance({tracker_key}){pod_info} - now RUNNING", 
                      file=sys.stderr, flush=True)

    def update_activity(
        self, tracker_key: str, activity: str, tokens: int | None = None
    ) -> None:
        """Update instance's last activity and token count.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
            inst = self.instances[tracker_key]
            # Truncate to 50 chars max
            inst.last_activity = activity[:50].strip()
            if tokens is not None:
                inst.tokens_used = tokens
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                print(f"[DEBUG] update_activity({tracker_key}) - {activity[:30]}... tokens={tokens}", 
                      file=sys.stderr, flush=True)

    def set_pod_name(self, tracker_key: str, pod_name: str) -> None:
        """Store pod name for instance.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
            pod_name: Name of the created pod
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
            
            inst = self.instances[tracker_key]
            inst.pod_name = pod_name
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                print(f"[DEBUG] set_pod_name({tracker_key}) - {pod_name}", 
                      file=sys.stderr, flush=True)

    def complete_instance(
        self,
        tracker_key: str,
        success: bool,
        tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        """Mark instance generation as completed.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
            inst = self.instances[tracker_key]
            inst.end_time = time.monotonic()
            inst.status = InstanceStatus.GENERATED if success else InstanceStatus.FAILED
            if tokens is not None:
                inst.tokens_used = tokens
            if error:
                inst.error_message = error
                inst.last_activity = f"Error: {error[:40]}"
            else:
                inst.last_activity = "generated"

            self.completion_order.append(tracker_key)
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                status = "GENERATED" if success else "FAILED"
                print(f"[DEBUG] complete_instance({tracker_key}) - {status} elapsed={inst.elapsed:.1f}s tokens={tokens}", 
                      file=sys.stderr, flush=True)

    def start_verification(self, tracker_key: str) -> None:
        """Mark instance as starting verification phase.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
                
            inst = self.instances[tracker_key]
            if inst.status == InstanceStatus.GENERATED:
                inst.status = InstanceStatus.VERIFYING
                inst.last_activity = "verifying..."
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                print(f"[DEBUG] start_verification({tracker_key})", 
                      file=sys.stderr, flush=True)

    def complete_verification(self, tracker_key: str, resolved: bool, error: str | None = None) -> None:
        """Mark instance verification as completed.
        
        Args:
            tracker_key: Composite key {harness}-{instance_id}
        """
        with self._lock:
            if tracker_key not in self.instances:
                return
                
            inst = self.instances[tracker_key]
            
            if error:
                inst.status = InstanceStatus.FAILED
                inst.error_message = error
                inst.last_activity = f"Eval error: {error[:40]}"
            else:
                inst.status = InstanceStatus.VERIFIED_PASS if resolved else InstanceStatus.VERIFIED_FAIL
                inst.last_activity = "verified: pass" if resolved else "verified: fail"
            
            if os.environ.get("ACB_DEBUG_UI"):
                import sys
                status = "PASS" if resolved and not error else ("FAIL" if not error else "ERROR")
                print(f"[DEBUG] complete_verification({tracker_key}) - {status}", 
                      file=sys.stderr, flush=True)

    def _format_status_icon(self, status: InstanceStatus) -> str:
        """Return emoji or ASCII icon for status."""
        if self.use_unicode:
            icons = {
                InstanceStatus.RUNNING: "🔄",
                InstanceStatus.GENERATED: "📝",
                InstanceStatus.VERIFYING: "🔍",
                InstanceStatus.VERIFIED_PASS: "✅",
                InstanceStatus.VERIFIED_FAIL: "❌",
                InstanceStatus.FAILED: "💥",
                InstanceStatus.TIMEOUT: "⏱️",
                InstanceStatus.QUEUED: "⏳",
            }
        else:
            icons = {
                InstanceStatus.RUNNING: "[>>]",
                InstanceStatus.GENERATED: "[GN]",
                InstanceStatus.VERIFYING: "[VF]",
                InstanceStatus.VERIFIED_PASS: "[OK]",
                InstanceStatus.VERIFIED_FAIL: "[!!]",
                InstanceStatus.FAILED: "[XX]",
                InstanceStatus.TIMEOUT: "[TO]",
                InstanceStatus.QUEUED: "[..]",
            }
        return icons.get(status, "?")

    def _format_pod_name(self, pod_name: str | None, status: InstanceStatus) -> str:
        """Format pod name for display.
        
        Shows short hash (6 chars) for active pods, "—" otherwise.
        Format: acb-abcd (fits in 12-char column)
        """
        if not pod_name:
            return "—"
        
        # Only show pod name for running/verifying instances
        if status not in (InstanceStatus.RUNNING, InstanceStatus.VERIFYING):
            return "—"
        
        # Extract short hash from full pod name
        # Full format: "acb-{16-char-hash}"
        # Display: "acb-{6-char-hash}" (total 10 chars, fits in 12-char column)
        if len(pod_name) >= 10:  # "acb-" + at least 6 chars
            return pod_name[:10]  # "acb-{6-char-hash}"
        return pod_name

    def _get_visible_instances(self) -> list[InstanceProgress]:
        """Return instances to show in table.
        
        Shows: all running instances + last 5 completed instances.
        This keeps the table from becoming huge while showing recent activity.
        """
        with self._lock:
            running = [i for i in self.instances.values() if i.status == InstanceStatus.RUNNING]
            completed = [i for i in self.instances.values() if i.status in (
                InstanceStatus.GENERATED,
                InstanceStatus.VERIFYING,
                InstanceStatus.VERIFIED_PASS,
                InstanceStatus.VERIFIED_FAIL,
                InstanceStatus.FAILED
            )]
            
            # Sort completed by end_time (most recent first), take last 5
            completed_sorted = sorted(
                [i for i in completed if i.end_time is not None],
                key=lambda x: x.end_time if x.end_time is not None else 0
            )
            recent_completed = completed_sorted[-5:]
            
            # Return running first, then recent completions
            return running + recent_completed

    def _build_table(self) -> Table:
        """Build the instance status table."""
        table = Table(title="Instance Status", show_header=True, header_style="bold cyan")
        table.add_column("Instance", style="white", width=16)
        table.add_column("Harness", style="white", width=8)
        table.add_column("Pod", style="dim", width=12)  # Pod name for debugging
        table.add_column("Time", justify="right", width=6)
        table.add_column("Tokens", justify="right", width=7)
        table.add_column("Last Activity", style="dim", width=20)

        visible = self._get_visible_instances()
        
        # Track how many completed instances we're showing for hidden count
        with self._lock:
            completed = [i for i in self.instances.values() if i.status in (
                InstanceStatus.GENERATED,
                InstanceStatus.VERIFYING,
                InstanceStatus.VERIFIED_PASS,
                InstanceStatus.VERIFIED_FAIL,
                InstanceStatus.FAILED
            )]
            completed_sorted = sorted(
                [i for i in completed if i.end_time is not None],
                key=lambda x: x.end_time if x.end_time is not None else 0
            )
            recent_completed = completed_sorted[-5:]
        
        # Show running first, then completed
        for inst in sorted(visible, key=lambda x: (x.status != InstanceStatus.RUNNING, x.start_time or 0)):
            icon = self._format_status_icon(inst.status)
            
            # Truncate instance ID to fit column width (icon + space + text ≤ 16)
            instance_display = inst.instance_id
            if len(instance_display) > 13:
                instance_display = instance_display[:10] + "..."
            
            # Color code by status
            if inst.status == InstanceStatus.RUNNING:
                status_color = "cyan"
            elif inst.status == InstanceStatus.VERIFYING:
                status_color = "blue"
            elif inst.status in (InstanceStatus.VERIFIED_PASS, InstanceStatus.GENERATED):
                status_color = "green"
            elif inst.status in (InstanceStatus.VERIFIED_FAIL, InstanceStatus.FAILED):
                status_color = "red"
            elif inst.status == InstanceStatus.QUEUED:
                status_color = "dim"
            else:
                status_color = "white"

            # Format pod name (short hash for active pods)
            pod_display = self._format_pod_name(inst.pod_name, inst.status)
            
            row = [
                Text(f"{icon} {instance_display}", style=status_color),
                inst.harness[:8],  # Truncate harness name
                Text(pod_display, style="dim"),  # Pod name (short hash or "—")
                inst.elapsed_str,
                Text(inst.tokens_str, style="yellow"),
                inst.last_activity,
            ]
            table.add_row(*row)

        # Show count of hidden completed instances
        hidden_count = len(completed) - len(recent_completed)
        if hidden_count > 0:
            table.add_row(
                Text(f"... and {hidden_count} more completed", style="dim"),
                "", "", "", "", ""
            )

        return table

    def _get_stats(self) -> tuple[int, int, int, int]:
        """Return (completed, running, failed, queued) counts.
        
        Returns:
            (completed, running, failed, queued) where completed includes all
            instances that finished generation (GENERATED, VERIFYING, VERIFIED_*, FAILED)
        """
        with self._lock:
            statuses = [i.status for i in self.instances.values()]
            completed = sum(1 for s in statuses if s in (
                InstanceStatus.GENERATED, InstanceStatus.VERIFYING,
                InstanceStatus.VERIFIED_PASS, InstanceStatus.VERIFIED_FAIL,
                InstanceStatus.FAILED
            ))
            running = sum(1 for s in statuses if s == InstanceStatus.RUNNING)
            failed = sum(1 for s in statuses if s == InstanceStatus.FAILED)
            queued = sum(1 for s in statuses if s == InstanceStatus.QUEUED)
            return completed, running, failed, queued

    def _estimate_remaining(self) -> str:
        """Calculate ETA based on completed instances."""
        completed, running, failed, queued = self._get_stats()
        
        if completed == 0:
            return "calculating..."

        # Calculate average time per instance (excluding running)
        with self._lock:
            finished = [
                i for i in self.instances.values() 
                if i.end_time is not None and i.start_time is not None
            ]
        if not finished:
            return "calculating..."

        avg_time = sum(i.elapsed for i in finished) / len(finished)
        remaining_instances = queued + running

        # Estimate total time remaining (accounting for parallelism)
        # Rough estimate: remaining_instances / max_workers * avg_time
        estimated_remaining = (remaining_instances / self.max_workers) * avg_time
        
        if estimated_remaining < 60:
            return f"~{estimated_remaining:.0f}s"
        minutes = int(estimated_remaining // 60)
        return f"~{minutes}m"

    def _build_header(self) -> Panel:
        """Build the header panel."""
        completed, running, failed, queued = self._get_stats()
        
        # Format status counts
        if self.use_unicode:
            status_text = (
                f"✅ {completed} Completed  │  ❌ {failed} Failed  │  "
                f"🔄 {running} Running  │  ⏳ {queued} Queued"
            )
        else:
            status_text = (
                f"[OK] {completed} Completed  |  [!!] {failed} Failed  |  "
                f"[>>] {running} Running  |  [..] {queued} Queued"
            )

        progress_pct = (completed / self.total * 100) if self.total > 0 else 0
        
        # Calculate averages
        with self._lock:
            finished = [
                i for i in self.instances.values() 
                if i.end_time is not None and i.start_time is not None
            ]
        avg_time = sum(i.elapsed for i in finished) / len(finished) if finished else 0
        avg_time_str = f"{avg_time:.1f}s" if avg_time > 0 else "—"

        header_lines = [
            f"[bold cyan]ACB Run: {self.run_id}[/]",
            f"Harnesses: {', '.join(self.harnesses)}",
            f"Model: {self.model}  │  Benchmark: {self.benchmark}",
            "",
            f"[yellow]Progress: {completed}/{self.total} ({progress_pct:.0f}%)[/]",
            status_text,
            f"⏱️  Avg: {avg_time_str}/instance  │  Est. remaining: {self._estimate_remaining()}",
        ]

        return Panel("\n".join(header_lines), title="[bold]Overview[/]", style="bold")

    def render(self) -> Layout:
        """Generate Rich Layout for Live display."""
        # Note: _build_header and _build_table handle their own locking
        layout = Layout()
        layout.split_column(
            Layout(self._build_header(), name="header", size=8),
            Layout(self._build_table(), name="table"),
        )
        return layout

    def summary(self) -> str:
        """Generate final summary text."""
        elapsed = time.monotonic() - self.start_time
        elapsed_str = f"{elapsed / 60:.1f}m" if elapsed >= 60 else f"{elapsed:.1f}s"
        
        # Count by status (with lock for thread safety)
        with self._lock:
            generated = sum(1 for i in self.instances.values() 
                           if i.status == InstanceStatus.GENERATED)
            verifying = sum(1 for i in self.instances.values() 
                           if i.status == InstanceStatus.VERIFYING)
            verified_pass = sum(1 for i in self.instances.values() 
                               if i.status == InstanceStatus.VERIFIED_PASS)
            verified_fail = sum(1 for i in self.instances.values() 
                               if i.status == InstanceStatus.VERIFIED_FAIL)
            gen_failed = sum(1 for i in self.instances.values() 
                            if i.status == InstanceStatus.FAILED)
            running = sum(1 for i in self.instances.values() 
                         if i.status == InstanceStatus.RUNNING)
            queued = sum(1 for i in self.instances.values() 
                        if i.status == InstanceStatus.QUEUED)
            
            # Calculate averages from finished instances
            finished = [
                i for i in self.instances.values() 
                if i.end_time is not None and i.start_time is not None
            ]
            avg_time = sum(i.elapsed for i in finished) / len(finished) if finished else 0
            avg_tokens = sum(i.tokens_used or 0 for i in finished) / len(finished) if finished else 0
        
        summary_lines = [
            "",
            "[bold green]═══════════════════════════════════════════════[/]",
            "[bold green]  ACB Run Summary[/]",
            "[bold green]═══════════════════════════════════════════════[/]",
            f"  Total time: {elapsed_str}",
            f"  Total instances: {self.total}",
            "",
            f"  Generation:",
            f"    ✅ Completed: {generated + verified_pass + verified_fail}",
            f"    ❌ Failed: {gen_failed}",
            f"    🔄 Running: {running}",
            f"    ⏳ Queued: {queued}",
            "",
            f"  Verification:",
            f"    ✅ Passed: {verified_pass}",
            f"    ❌ Failed: {verified_fail}",
            f"    🔍 In Progress: {verifying}",
            f"    📝 Pending: {generated}",
            "",
            f"  Performance:",
            f"    Average time per instance: {avg_time:.1f}s",
            f"    Average tokens per instance: {avg_tokens:.0f}",
        ]

        # Show failed instances if any
        if verified_fail > 0 or gen_failed > 0:
            summary_lines.append("")
            summary_lines.append("[bold red]  Failed instances:[/]")
            with self._lock:
                for inst in self.instances.values():
                    if inst.status == InstanceStatus.FAILED:
                        error_short = (inst.error_message or "Unknown error")[:60]
                        summary_lines.append(f"    [red]💥 {inst.instance_id}: {error_short}[/]")
                    elif inst.status == InstanceStatus.VERIFIED_FAIL:
                        summary_lines.append(f"    [red]❌ {inst.instance_id}: Verification failed[/]")

        summary_lines.append("[bold green]═══════════════════════════════════════════════[/]")

        return "\n".join(summary_lines)


class LiveTrackerDisplay:
    """Custom Rich renderable for live display of tracker progress.
    
    This class implements Rich's renderable protocol so that Rich can call
    our render method on every refresh, ensuring the display always shows
    the current state of the tracker.
    
    Usage:
        from rich.live import Live
        tracker = ProgressTracker(...)
        display = LiveTrackerDisplay(tracker)
        with Live(display, refresh_per_second=2) as live:
            # do work that updates tracker
            # display auto-refreshes to show current state
    """
    
    def __init__(self, tracker: ProgressTracker):
        """Initialize with a ProgressTracker instance.
        
        Args:
            tracker: The ProgressTracker to display
        """
        self.tracker = tracker
        self._render_count = 0
    
    def __rich_console__(self, console, options):
        """Called by Rich to render this object.
        
        This is called on every refresh cycle, allowing us to display the
        current tracker state dynamically.
        
        Args:
            console: The Rich console object
            options: Display options (width, height, etc.)
            
        Yields:
            The current tracker layout
        """
        self._render_count += 1
        
        # Debug logging (can be disabled with env var)
        if os.environ.get("ACB_DEBUG_UI"):
            import sys
            print(f"[DEBUG] LiveTrackerDisplay.__rich_console__ call #{self._render_count}", 
                  file=sys.stderr, flush=True)
        
        # Yield the current layout from the tracker with exception handling
        # If render() fails, prevent the refresh thread from dying silently
        try:
            yield self.tracker.render()
        except Exception as e:
            import sys
            import traceback
            
            # Log the error to stderr so it's visible
            print(f"\n[ERROR] Display render failed: {e}", file=sys.stderr, flush=True)
            if os.environ.get("ACB_DEBUG_UI"):
                traceback.print_exc(file=sys.stderr)
            
            # Yield fallback error display instead of crashing
            from rich.panel import Panel
            error_msg = str(e)[:100]  # Truncate long error messages
            yield Panel(
                f"[red]Display render error:[/red]\n{error_msg}\n\n"
                f"[dim]Check stderr for full traceback[/dim]",
                title="[bold red]ACB Display Error[/]",
                border_style="red"
            )


def cleanup_all_pods(tracker: ProgressTracker) -> None:
    """Find and forcefully remove all currently running ACB pods.
    
    Iterates through tracker instances to find all pods that are running,
    verifying, or in generation/verification states, and force-removes them.
    Also performs a safety scan for any orphaned acb-* pods in the system.
    """
    pod_names_to_remove = set()
    
    # 1. Collect pod names from tracker
    for inst in tracker.instances.values():
        if inst.pod_name and inst.status in (
            InstanceStatus.RUNNING,
            InstanceStatus.VERIFYING,
            InstanceStatus.GENERATED,
        ):
            pod_names_to_remove.add(inst.pod_name)
    
    # 2. Remove tracked pods
    for pod_name in pod_names_to_remove:
        try:
            subprocess.run(
                ["podman", "pod", "rm", "-f", pod_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            print(f"[acb] Cleaned up pod: {pod_name}", flush=True)
        except subprocess.TimeoutExpired:
            print(f"[acb] Timeout removing pod {pod_name}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[acb] Failed to remove pod {pod_name}: {e}", flush=True)
    
    # 3. Safety net: Find and remove any orphaned acb-* pods from this run
    # (in case some slipped through the tracking system)
    try:
        result = subprocess.run(
            ["podman", "pod", "ls", "--format", "{{.Name}}", "--filter", f"label=acb-run-id={tracker.run_id}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.stdout:
            orphaned_pods = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
            for pod_name in orphaned_pods:
                if pod_name not in pod_names_to_remove:
                    try:
                        subprocess.run(
                            ["podman", "pod", "rm", "-f", pod_name],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        print(f"[acb] Cleaned up orphaned pod: {pod_name}", flush=True)
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001
        pass  # Safety net scan failed, but we already removed the tracked pods


def setup_interrupt_handler(tracker: ProgressTracker, executor) -> None:
    """Setup aggressive Ctrl+C handler with immediate pod cleanup.
    
    When Ctrl+C is pressed (SIGINT):
    1. Sets interrupted flag to prevent new work
    2. Immediately force-removes all running pods
    3. Cancels pending futures (ones not yet started)
    4. Prints final summary
    5. Hard exits (bypasses normal cleanup to exit immediately)
    """
    def signal_handler(sig, frame):
        print("\n[acb] 🛑 Interrupt received! Cleaning up pods immediately...", flush=True)
        
        # 1. Set interrupt flag (prevents new work from starting)
        tracker.interrupted = True
        
        # 2. Immediately force-remove all running pods
        cleanup_all_pods(tracker)
        
        # 3. Cancel any futures that haven't started yet
        executor.shutdown(wait=False, cancel_futures=True)
        
        # 4. Show final summary
        try:
            print("\n" + tracker.summary(), flush=True)
        except Exception:  # noqa: BLE001
            pass  # If summary fails, still exit
        
        # 5. Hard exit (bypasses normal Python cleanup/context managers)
        # Using os._exit() ensures immediate termination without waiting for threads
        os._exit(1)

    signal.signal(signal.SIGINT, signal_handler)
