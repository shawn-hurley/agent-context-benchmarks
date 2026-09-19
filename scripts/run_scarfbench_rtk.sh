#!/usr/bin/env bash
# Run the four locally configured RTK harnesses against discovered ScarfBench migrations.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
config_dir="${1:-config/scarfbench-rtk}"
for harness in goose pi opencode claude-code; do
    test -f "$config_dir/$harness.yaml" || { echo "Missing config: $config_dir/$harness.yaml" >&2; exit 1; }
done
podman build -t scarfbench:latest acb/scarfbench
podman build -f Dockerfile.rtk-scarfbench -t localhost/acb-scarfbench-rtk:1 .
for harness in goose pi opencode claude-code; do
    UV_MANAGED_PYTHON=1 UV_PYTHON=3.12 uv run acb run --config "$config_dir/$harness.yaml"
done
