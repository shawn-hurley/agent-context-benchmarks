"""Location of Harbor's native job files inside an ACB run."""
from pathlib import Path


JOB_NAME = ".harbor"


def job_dir(run_dir: Path) -> Path:
    """Use the new hidden job directory, while reading older visible runs."""
    run_dir = Path(run_dir)
    current = run_dir / JOB_NAME
    previous = run_dir / "harbor"
    return previous if previous.is_dir() and not current.exists() else current
