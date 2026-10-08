"""Model-free Harbor/native-grader controls for SWE-bench and ScarfBench.

Requires a local Linux image with bash, git, and coreutils, the official
SWE-bench Python environment, ScarfBench CLI, and the chosen container engine.
Synthetic fixed projects test orchestration and grading, not model quality.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from acb.config import Registries, RunConfig
from acb.harbor.backend import _worker
from acb.harbor.dataset import prepare_dataset
from acb.harbor.paths import job_dir
from acb.resolver import resolve


def swebench_parity(root, prepared_request, python, environment):
    """Compare fixed patches through the bridge child and official entrypoint."""
    from acb.container import grader_env
    from acb.harbor import swebench_grade
    original = json.loads(prepared_request.read_text())
    baseline_script = """
import json, sys
from pathlib import Path
from swebench.harness.run_evaluation import _docker_client, run_instance
from swebench.harness.utils import make_test_spec
request = json.loads(Path(sys.argv[1]).read_text())
row = request['row']
client = _docker_client()
try:
    result = run_instance(make_test_spec(row), {'instance_id': row['instance_id'], 'model_name_or_path': 'harbor', 'model_patch': request['model_patch']}, client, request['run_id'], timeout=60)
finally:
    client.close()
Path('grade.json').write_text(json.dumps({'resolved': bool(result and result[1][row['instance_id']]['resolved']), 'evaluation_completed': result is not None}))
"""
    cases = {
        "pass": (original["model_patch"], None, True),
        "fail": (original["model_patch"].replace("+fixed", "+wrong"), None, False),
        "invalid-patch": ("not a patch\n", None, False),
        "test-execution-error": (original["model_patch"], "command_that_does_not_exist\n", False),
    }
    results = []
    for case, (patch, script, expected) in cases.items():
        outcomes, reports = [], []
        for route in ("bridge", "official"):
            output = root / case / route
            output.mkdir(parents=True)
            request = {**original, "row": dict(original["row"]), "model_patch": patch}
            if script is not None:
                request["row"]["eval_script"] = script
            path = output / "request.json"
            path.write_text(json.dumps(request))
            command = [python, swebench_grade.__file__, str(path)] if route == "bridge" else [python, "-c", baseline_script, str(path)]
            with (output / "grader.log").open("w") as log:
                subprocess.run(command, cwd=output, env=grader_env({"container_backend": environment}, output),
                               stdout=log, stderr=log, check=True, timeout=180)
            outcomes.append(json.loads((output / "grade.json").read_text()))
            paths = list((output / "logs").rglob("report.json"))
            reports.append(json.loads(paths[0].read_text()) if paths else None)
        assert outcomes[0] == outcomes[1], (case, outcomes)
        assert reports[0] == reports[1], (case, reports)
        assert outcomes[0]["resolved"] is expected, (case, outcomes)
        results.append({"case": case, **outcomes[0], "detailed_report_equal": True})
        print(f"SWE-bench native parity/{case}: matched", flush=True)
    (root / "parity.json").write_text(json.dumps(results, indent=2))


def fixtures(root, image):
    dataset = root / "dataset.json"
    task = root / "tasks/acb__fixture-1"
    task.mkdir(parents=True)
    (task / "Dockerfile").write_text(f"FROM {image}\nUSER root\n"
        "RUN rm -rf /testbed && mkdir /testbed && cd /testbed && git init && "
        "git config user.email acb@example.invalid && git config user.name ACB && "
        "printf 'broken\\n' > answer && git add answer && git commit -m initial\nWORKDIR /testbed\n")
    row = {"instance_id": "acb__fixture-1", "repo": "acb/fixture", "base_commit": "fixture",
           "problem_statement": "Change answer to fixed", "image": "unused", "version": "1",
           "FAIL_TO_PASS": ["test_answer"], "PASS_TO_PASS": [], "log_parser": "parse_log_pytest",
           "eval_type": "pass_and_fail", "eval_script": "cd /testbed\necho '>>>>> Start Test Output'\n"
           "if grep -qx fixed answer; then echo 'PASSED test_answer'; else echo 'FAILED test_answer'; fi\n"
           "echo '>>>>> End Test Output'\n",
           "patch": "diff --git a/answer b/answer\n--- a/answer\n+++ b/answer\n@@ -1 +1 @@\n-broken\n+fixed\n"}
    dataset.write_text(json.dumps([row]))
    bundle = root / "scarfbench"
    for framework, content in (("jakarta", "broken"), ("quarkus", "fixed")):
        directory = bundle / "business_domain/cart" / framework
        directory.mkdir(parents=True)
        (directory / "answer").write_text(content + "\n")
        (directory / "Dockerfile").write_text(f"FROM {image}\nCOPY . /app\nWORKDIR /app\nCMD [\"sleep\", \"infinity\"]\n")
        (directory / "metadata.json").write_text('{"num_smoke_tests": 1}')
        # The native compatibility Makefile builds and tests the full project.
        (directory / "test.sh").write_text("#!/bin/sh\ngrep -qx fixed /app/answer\n")
    return dataset, bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment", choices=("podman", "docker"), default="podman")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    dataset, bundle = fixtures(root, args.image)
    registry = Registries({}, {"swebench": {}, "scarfbench": {}}, {"fixture": {"api": "openai", "endpoint": "localhost:1", "tls": False}})
    records = []
    for benchmark in ("swebench", "scarfbench"):
        settings = {"reward_metric": "reward", "success_value": 1}
        if benchmark == "swebench":
            settings.update(dataset=str(dataset), task_repo_cache_dir=str(root))
        else:
            settings.update(benchmark_cache_dir=str(bundle), source="jakarta", target="quarkus", scarfbench_image=args.image)
        cfg = RunConfig(run_id=benchmark, benchmark={"name": benchmark, **settings},
                        harness="goose", model="fixture", output_dir=str(root / "runs"),
                        execution={"environment": args.environment})
        plan = resolve(cfg, registry).to_dict()
        from acb.harbor.benchmark_grader import prepare_grader
        plan["benchmark_grader"] = prepare_grader(plan)
        plan["manifest"] = prepare_dataset(plan)
        request = root / f"{benchmark}.json"
        request.write_text(json.dumps(plan, indent=2))
        for control, expected in (("oracle", 1), ("nop", 0)):
            output = root / f"{benchmark}-{control}"
            _worker(sys.executable, "control", request, output, control=control)
            results = list(job_dir(output).glob("*/result.json"))
            if not results:
                raise AssertionError(f"no native results for {benchmark}/{control}")
            for path in results:
                result = json.loads(path.read_text())
                rewards = (result.get("verifier_result") or {}).get("rewards")
                if result.get("exception_info") or rewards != {"reward": expected}:
                    raise AssertionError(f"{benchmark}/{control}: {path}: {result.get('exception_info') or rewards}")
                records.append({"benchmark": benchmark, "control": control, "rewards": rewards, "result": str(path)})
            print(f"{benchmark}/{control}: {expected}", flush=True)
    (root / "checks.json").write_text(json.dumps(records, indent=2))
    prepared = next(job_dir(root / "swebench-oracle").glob("*/verifier/native/request.json"))
    python = json.loads((root / "swebench.json").read_text())["benchmark_grader"]["binary"]
    swebench_parity(root / "swebench-parity", prepared, python, args.environment)


if __name__ == "__main__":
    main()
