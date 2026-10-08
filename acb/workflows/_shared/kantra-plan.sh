#!/bin/bash
set -euo pipefail
out=/work/.konveyor/kantra
rm -rf "$out"
mkdir -p "$out"
cd /tmp
log=/work/.konveyor/kantra.log
if ! kantra analyze --run-local --mode source-only --input /work \
    --output "$out" --overwrite --target quarkus > "$log" 2>&1; then
  tail -c 4000 "$log" >&2
  exit 1
fi
test -s "$out/output.yaml"
python3 /opt/acb/kantra-summary.py "$out/output.yaml" > "$out/violations-summary.md"
printf 'kantra analyze completed successfully\n' > "$out/analysis.ok"
printf 'Kantra source-only analysis completed; findings: %s; summary: %s\n' \
  "$out/output.yaml" "$out/violations-summary.md"
cat /root/.agents/skills/kantra/SKILL.md
cat "$out/violations-summary.md"
printf '\nSource smoke test:\n'
cat /work/test.sh
