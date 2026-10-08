"""Native ScarfBench grading child; execution is cancelled with its process group."""
import json
from pathlib import Path
import sys

from acb.benchmarks.scarfbench import ScarfBench


def main(request):
    document = json.loads(request.read_text())
    resolved = ScarfBench(document["config"]).grade_run(document["instance_id"], Path(document["run"]), request.parent)
    (request.parent / "grade.json").write_text(json.dumps({"resolved": resolved}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
