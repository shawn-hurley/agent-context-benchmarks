"""Separate processes must reserve distinct run evidence directories."""
import json
import multiprocessing
import os
from pathlib import Path

from acb.provenance import save_configuration
from acb.run_paths import reserve_run_directory


def reserve_concurrently(root, barrier):
    barrier.wait(timeout=15)
    directory, identity = reserve_run_directory(root, "experiment")
    document = save_configuration(directory, {"run_id": "experiment", "owner": os.getpid()},
                                  effective_run_id=identity)
    assert document["run_id"] == directory.name


def test_concurrent_processes_preserve_separate_provenance(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(6)
    processes = [context.Process(target=reserve_concurrently, args=(str(tmp_path), barrier)) for _ in range(6)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        directories = sorted(tmp_path.iterdir())
        assert {p.name for p in directories} == {"experiment", *(f"experiment-{i}" for i in range(1, 6))}
        documents = [json.loads((p / "resolved.json").read_text()) for p in directories]
        assert len({d["owner"] for d in documents}) == 6
        for directory, document in zip(directories, documents):
            assert document["run_id"] == directory.name
            assert document["requested_run_id"] == "experiment"
            assert Path(document["run_dir"]) == directory
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)


def test_reservation_preserves_suffix_gaps_and_skips_existing_files(tmp_path):
    (tmp_path / "experiment").symlink_to(tmp_path / "missing")
    (tmp_path / "experiment-2").mkdir()
    (tmp_path / "experiment-7").write_text("preserve")
    directory, identity = reserve_run_directory(tmp_path, "experiment")
    assert identity == "experiment-8"
    assert directory.is_dir()
    assert (tmp_path / "experiment-7").read_text() == "preserve"
