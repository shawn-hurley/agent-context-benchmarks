"""Location of Harbor's native job files inside an ACB run."""
from pathlib import Path


JOB_NAME = ".harbor"


def job_dir(run_dir: Path) -> Path:
    """Return the current native job directory."""
    return Path(run_dir) / JOB_NAME


def trial_directory(run_dir: Path, harness: str, trial_id: str) -> Path:
    for value in (harness, trial_id):
        if not value or Path(value).name != value or value in (".", ".."):
            raise ValueError("invalid harness or trial identity")
    return Path(run_dir) / harness / trial_id
