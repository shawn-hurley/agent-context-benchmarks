#!/usr/bin/env bash
# Run from any working directory. Requires Podman, uv, and setup network access.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
smoke_image=localhost/acb-rtk-smoke:0.48.0
podman build --file Dockerfile.rtk-smoke --tag "$smoke_image" .
mkdir -p runs/.cache/rtk-smoke
fixture_container=$(podman create "$smoke_image")
trap 'podman rm -f "$fixture_container" >/dev/null' EXIT
podman cp "$fixture_container:/opt/acb-fixture/rtk" runs/.cache/rtk-smoke/rtk
chmod +x runs/.cache/rtk-smoke/rtk
export ACB_RTK_LIVE=1
export ACB_RTK_BINARY="$PWD/runs/.cache/rtk-smoke/rtk"
export ACB_RTK_IMAGE="$smoke_image"
uv run --managed-python --python 3.12 --with pytest python -m pytest tests/test_rtk_native_live.py -v "$@"
