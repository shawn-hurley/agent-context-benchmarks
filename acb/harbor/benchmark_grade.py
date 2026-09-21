"""Native ScarfBench grading child; execution is cancelled with its process group."""
import json
from pathlib import Path
import sys

from acb.benchmarks.base import Prediction
from acb.benchmarks.scarfbench import ScarfBench


def main(request):
    document = json.loads(request.read_text())
    prediction = Prediction(instance_id=document["instance_id"], model_name_or_path="harbor", output=document["run"])
    resolved = ScarfBench(document["config"]).evaluate([prediction], "harbor", request.parent)
    (request.parent / "grade.json").write_text(json.dumps({"resolved": resolved[prediction.instance_id]}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
