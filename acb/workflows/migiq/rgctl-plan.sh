#!/bin/bash
set -euo pipefail
mode=${1:?Usage: rgctl-plan.sh structural|migiq [workspace]}
work=${2:-/work}
case "$mode" in
  structural) out="$work/.konveyor/rgctl"; flags=(-l java) ;;
  migiq) out="$work/migiq-workspace/analysis";
    flags=(-l java --with-cfg --with-security --with-taint --with-dashboard --with-harmonic --export-migration-hints) ;;
  *) echo "Unknown rgctl analysis mode: $mode" >&2; exit 2 ;;
esac
mkdir -p "$out"
cd "$work"
rgctl --version > "$out/version.txt"
rgctl discover . -e 'target/**,.konveyor/**,*-workspace/**' "${flags[@]}" > "$out/discovery.log" 2>&1
rgctl -f json status > "$out/status.json"
rgctl -f json inventory --by type > "$out/inventory.json"
rgctl -f json find --type class --limit 50 > "$out/classes.json"
rgctl -f json relations --edge annotatedwith --to-type annotation --limit 50 > "$out/annotations.json"
if [ "$mode" = migiq ]; then
  rgctl -f json metrics --pagerank --communities > "$out/metrics.json"
  rgctl -f json communities list > "$out/communities.json"
  test -s .rgctl/migration_plan.json
  cp .rgctl/migration_plan.json "$out/migration_plan.json"
fi
python3 - "$out" <<'CHECK'
import json, sys
from pathlib import Path
for path in Path(sys.argv[1]).glob('*.json'):
    document = json.loads(path.read_text())
    if not isinstance(document, (dict, list)):
        raise ValueError(f'Invalid rgctl evidence: {path}')
CHECK
python3 - "$out/status.json" <<'STATUS'
import json, sys
status = json.load(open(sys.argv[1]))
if status.get('status') != 'ok' or not status.get('snapshot') or status.get('nodes', 0) < 1:
    raise ValueError('rgctl did not produce a usable graph snapshot')
STATUS
printf 'rgctl analysis completed successfully\n' > "$out/analysis.ok"
printf 'rgctl evidence: %s\n' "$out"
