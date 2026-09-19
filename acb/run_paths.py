"""Output directory reservation shared by execution backends."""
from pathlib import Path
import re


def reserve_run_directory(output_dir: str | Path, run_id: str) -> tuple[Path, str]:
    """Reserve an unused directory, retaining the original name and -N suffixes.

    Exclusive mkdir owns the name before either backend writes provenance or
    starts work. Concurrent invocations retry instead of sharing run evidence.
    Existing numbered runs advance the suffix; gaps are not filled.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
        raise ValueError("run_id must be a path-safe name")
    base = Path(output_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / run_id
    try:
        candidate.mkdir()
        return candidate, run_id
    except FileExistsError:
        pass

    pattern = re.compile(rf"^{re.escape(run_id)}-(\d+)$")
    suffix = 1 + max((int(match.group(1)) for entry in base.iterdir()
                      if (match := pattern.fullmatch(entry.name))), default=0)
    while True:
        effective = f"{run_id}-{suffix}"
        candidate = base / effective
        try:
            candidate.mkdir()
            return candidate, effective
        except FileExistsError:
            suffix += 1
