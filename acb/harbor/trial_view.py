"""Expose live Harbor trial logs under the selected ACB harness."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from acb.harbor.paths import job_dir


def _link(destination: Path, source: Path) -> None:
    if not destination.exists() and not destination.is_symlink():
        destination.symlink_to(os.path.relpath(source, destination.parent))


def expose_trial(run_dir: Path, harness: str, trial_id: str, trial_name: str) -> Path:
    """Create paths before agent execution so `tail -F` can follow live files."""
    run_dir = Path(run_dir)
    if Path(trial_name).name != trial_name or not trial_name:
        raise ValueError("invalid Harbor trial name")
    if Path(harness).name != harness or not harness:
        raise ValueError("invalid harness name")
    trial = job_dir(run_dir) / trial_name
    view = run_dir / harness / trial_id
    view.mkdir(parents=True, exist_ok=True)
    aliases = run_dir / harness / "instances"
    aliases.mkdir(exist_ok=True)
    _link(aliases / trial_id, view)
    _link(view / "job.log", run_dir / "job.log")
    _link(view / "trial.log", trial / "trial.log")
    for name in ("transcript.jsonl", "praxis.log"):
        _link(view / name, trial / "agent" / "acb" / name)
    _link(view / "transcript.log", view / "transcript.jsonl")
    return view


def archive_trial(view: Path, trial: Path) -> None:
    """Replace live links with saved files after Harbor closes the trial."""
    for name in ("transcript.jsonl", "praxis.log", "trial.log"):
        destination = view / name
        if destination.is_symlink():
            destination.unlink()
    agent = trial / "agent" / "acb"
    if agent.is_dir():
        shutil.copytree(agent, view, dirs_exist_ok=True)
    for name in ("trial.log", "exception.txt", "config.json", "lock.json"):
        source = trial / name
        if source.is_file():
            shutil.copy2(source, view / name)
    for name in ("verifier", "artifacts"):
        source = trial / name
        if source.is_dir():
            shutil.copytree(source, view / name, dirs_exist_ok=True)
    if not (view / "transcript.jsonl").is_file():
        (view / "transcript.log").unlink(missing_ok=True)


def archive_job_log(run_dir: Path, harnesses: list[str]) -> None:
    """Snapshot the finished job summary into each visible trial directory."""
    source = Path(run_dir) / "job.log"
    for harness in harnesses:
        directory = Path(run_dir) / harness
        if not directory.is_dir():
            continue
        for trial in directory.iterdir():
            if trial.name == "instances" or not trial.is_dir():
                continue
            destination = trial / "job.log"
            if destination.is_symlink():
                destination.unlink()
            shutil.copy2(source, destination)
