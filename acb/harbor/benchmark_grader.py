"""Resolve and fingerprint native grading dependencies before inference."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from acb.harbor.benchmark_tasks import BENCHMARKS


def uses_native_grader(plan):
    return plan.get("benchmark") in BENCHMARKS and not plan["benchmark_config"].get("path")


def prepare_grader(plan):
    if not uses_native_grader(plan):
        return None
    config = plan["benchmark_config"]
    if plan["benchmark"] == "scarfbench":
        binary = shutil.which(config.get("scarf_binary", "scarf"))
        if not binary:
            raise FileNotFoundError("ScarfBench grading requires scarf on PATH or benchmark.scarf_binary")
        version = subprocess.run([binary, "--version"], check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        return {"binary": binary, "version": version,
                "sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest()}
    from acb.benchmarks.swebench import _ensure_swebench_venv
    python = config.get("swebench_python")
    if not python:
        if plan["offline"]:
            from acb.benchmarks.swebench import _SWEBENCH_VENV
            if not (_SWEBENCH_VENV / "bin/python").is_file():
                raise FileNotFoundError("offline: prepare the SWE-bench interpreter first or set benchmark.swebench_python")
        python = str(_ensure_swebench_venv())
    python = shutil.which(python)
    if not python:
        raise FileNotFoundError("benchmark.swebench_python does not exist")
    script = """
import hashlib, json
from pathlib import Path
import swebench
from swebench.harness.run_evaluation import run_instance, _docker_client
from swebench.harness.utils import make_test_spec
from importlib.metadata import version
root = Path(swebench.__file__).parent
inventory = [(str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.rglob('*.py'))]
print(json.dumps({'version': version('swebench'), 'sha256': hashlib.sha256(json.dumps(inventory).encode()).hexdigest()}))
"""
    result = subprocess.run([python, "-c", script], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("SWE-bench grader must provide run_instance, make_test_spec and the task-repo dataset schema; check benchmark.swebench_python")
    return {"binary": python, **json.loads(result.stdout)}


def verify_grader(plan):
    if uses_native_grader(plan) and prepare_grader(plan) != plan.get("benchmark_grader"):
        raise ValueError("native benchmark grader changed or was not prepared; prepare again")


def verify_grading_images(plan, images):
    """Check the official SDK sees Harbor's images before starting inference."""
    if not uses_native_grader(plan) or plan["benchmark"] == "scarfbench":
        return
    from acb.container import container_env
    script = """
import json, sys
from swebench.harness.run_evaluation import _docker_client
client = _docker_client()
try:
    client.ping()
    for image in json.loads(sys.argv[1]):
        client.images.get(image)
finally:
    client.close()
"""
    result = subprocess.run([plan["benchmark_grader"]["binary"], "-c", script, json.dumps(sorted(images))],
                            env=container_env({**plan["benchmark_config"], "container_backend": plan["environment"]}),
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("SWE-bench grading cannot access Harbor's prepared images; check benchmark.docker_host and the selected engine")
