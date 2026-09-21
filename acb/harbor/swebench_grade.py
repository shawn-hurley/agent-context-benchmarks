"""Run with the official SWE-bench interpreter, without importing ACB."""
import json
from pathlib import Path
import sys


def main(request):
    from swebench.harness.run_evaluation import _docker_client, run_instance
    from swebench.harness.utils import make_test_spec
    document = json.loads(request.read_text())
    row = document["row"]
    prediction = {"instance_id": row["instance_id"], "model_name_or_path": "harbor",
                  "model_patch": document["model_patch"]}
    # The native grader creates a fresh container from the exact image Harbor
    # used, applies the prediction, runs eval_script and grades individual tests.
    client = _docker_client()
    try:
        client.ping()
        client.images.get(row["image"])
        result = run_instance(make_test_spec(row), prediction, client, document["run_id"],
                              timeout=3600, task_repo=document.get("task_repo"))
    finally:
        client.close()
    resolved = bool(result and result[1][row["instance_id"]]["resolved"])
    (request.parent / "grade.json").write_text(json.dumps({"resolved": resolved,
        "evaluation_completed": result is not None}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
