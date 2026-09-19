#!/bin/bash
set -eu
mkdir -p /logs/verifier
/opt/miniconda3/bin/python - <<'PY'
import json
import urllib.request
from pathlib import Path
try:
    urllib.request.urlopen('http://127.0.0.1:18880/v1/models', timeout=2)
except OSError:
    Path('/logs/verifier/proxy-stopped.json').write_text(json.dumps({'stopped': True}))
else:
    raise RuntimeError('agent proxy was still reachable during verification')
PY
if [ "$(cat /work/answer.txt)" = fixed ]; then
    printf '1\n' > /logs/verifier/reward.txt
else
    printf '0\n' > /logs/verifier/reward.txt
fi
