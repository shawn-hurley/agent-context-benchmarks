"""SWE-bench dataset selection and patch-filtering rules.

Harbor exports the task image and collects patches. The official SWE-bench
harness runs in its own interpreter through acb.harbor.swebench_grade."""

from __future__ import annotations

import json
import subprocess
from fnmatch import fnmatch
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
    # Check if SWE-bench submodule has been initialized
    if not _SWEBENCH_DIR.exists():
        raise FileNotFoundError(
            f"SWE-bench submodule not found at {_SWEBENCH_DIR}\n\n"
            f"Initialize the submodule with:\n"
            f"  git submodule update --init\n\n"
            f"Or clone with --recursive flag:\n"
            f"  git clone --recursive <repo-url>"
        )
    
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

from acb.benchmarks.base import Benchmark, Instance

DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"


class SWEBench(Benchmark):
    name = "swebench"

    def __init__(self, config: dict | None = None):
        """Initialize SWEBench adapter.
        
        Args:
            config: Configuration dict, optionally containing patch_exclude_patterns
                   (list of glob patterns for files to exclude from model_patch).
        """
        super().__init__(config)
        # Extract patch exclusion patterns from config (default: empty list = no filtering)
        self.patch_exclude_patterns = self.config.get("patch_exclude_patterns", [])

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        dataset = self.config.get("dataset", DEFAULT_DATASET)
        split = self.config.get("split", "test")
        if Path(dataset).is_file() and Path(dataset).suffix in (".json", ".jsonl"):
            text = Path(dataset).read_text()
            ds = json.loads(text) if Path(dataset).suffix == ".json" else [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            from datasets import DownloadConfig, load_dataset
            ds = load_dataset(dataset, split=split, revision=self.config.get("revision"),
                              download_config=DownloadConfig(local_files_only=self.config.get("offline", False)))
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
                           "image": row.get("image"), "dataset_row": dict(row)},
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


    def _filter_excluded_paths(self, paths: list[str]) -> list[str]:
        """Filter out paths matching configured exclusion patterns.
        
        Uses fnmatch for glob-style pattern matching (* and ? wildcards).
        Supports both direct file matches and directory patterns (e.g., .rgctl/**).
        
        Args:
            paths: List of file paths relative to /testbed
            
        Returns:
            Filtered list with excluded patterns removed
        """
        if not self.patch_exclude_patterns:
            return paths
        
        filtered = []
        for path in paths:
            excluded = False
            for pattern in self.patch_exclude_patterns:
                # Handle directory-recursive patterns (e.g., .rgctl/**)
                if pattern.endswith('/**'):
                    dir_prefix = pattern[:-3]  # Remove '/**'
                    if path == dir_prefix or path.startswith(dir_prefix + '/'):
                        excluded = True
                        break
                # Standard glob pattern matching
                elif fnmatch(path, pattern):
                    excluded = True
                    break
            
            if not excluded:
                filtered.append(path)
        
        return filtered
