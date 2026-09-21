# agent-context-benchmarks (`acb`)

Compare **context/token usage** of agent harnesses (claude-code, goose,
opencode, pi) running coding benchmarks through Harbor, including SWE-bench,
SWE-bench Lite and ScarfBench, against the same cloud or local model.
LiveCodeBench remains a stub.

Every LLM call flows through a proxy that records it, so we get per-request token
accounting per (harness, model, benchmark, instance). See [DESIGN.md](DESIGN.md).

Generation is **container-only**. Harbor runs agents in Docker or Podman task
environments; native benchmark verifiers grade the resulting work separately.
See [operations](docs/harbor-operations.md) for supported environments and limits.

## Install

### Prerequisites

1. **Python 3.12 or newer.** Harbor 0.23.0 is installed as a pinned ACB
   dependency and runs from the same Python environment as `acb`.

2. **Podman** (required for container-mode generation):
   ```bash
   # macOS (Apple Silicon or Intel)
   brew install podman
   podman machine init && podman machine start
   ```

3. **SWE-bench evaluation harness** (vendored as git submodule):
   ```bash
   # Initialize the submodule (if you didn't clone with --recursive)
   git submodule update --init
   ```

4. **Podman shim for macOS** (SWE-bench's evaluation code shells out to `docker`):
   ```bash
   mkdir -p bin
   echo '#!/bin/sh\nexec podman "$@"' > bin/docker
   chmod +x bin/docker
   ```

### Install ACB

```bash
# If you didn't clone with --recursive, initialize submodules first
git submodule update --init

pip install -e '.[datasets]'          # datasets extra needed for SWE-bench
# Note: SWE-bench venv is created automatically on first evaluation run
```

**Quick clone with all submodules:**
```bash
git clone --recursive https://github.com/shawn-hurley/agent-context-benchmarks.git
```

`acb` auto-detects Podman (`container_backend: auto` in `benchmarks.yaml`) and
the `bin/docker` shim makes SWE-bench's evaluation subprocess work seamlessly.

## Configure

For optional RTK shell-output compression in Goose, Pi, OpenCode, and Claude Code, see
[RTK configuration and compatibility tests](RTK.md).

Verify all four RTK integrations with a self-contained fixture image (no model
server required): `bash scripts/run_rtk_smoke.sh`. See the RTK guide for setup
requirements on macOS and Linux ARM64.

Copy the example configuration files and customize them:

```bash
cp -r config.example config
```

Then edit the configuration files:

- `config/proxy.yaml`  — model backends the proxy owns (cloud + local) and proxy backends.
- `config/harnesses.yaml` — per-harness CLI knobs.
- `config/benchmarks.yaml` — dataset selection + container-mode settings (image arch, task repo, praxis image source).
- `config/costs.yaml` — token pricing for cost estimation (copy from `config.example/costs.yaml`).
- `config/swebench-lite/run.*.yaml` — example run configs (benchmark × harness × model).

**Important**: `acb/costs.py` expects `config/costs.yaml` to exist. Copy it from `config.example/`:
```bash
cp config.example/costs.yaml config/costs.yaml
```

Set the real provider key(s) in your environment; the proxy injects them so the
harness never sees them:

```bash
export ANTHROPIC_API_KEY=sk-...
export OPENAI_API_KEY=sk-...          # if using OpenAI / for local, none needed
```

Matched baseline, response-skill, RTK and context-compression examples for Harbor
and SWE-bench through Harbor are in [config.example/phase4](config.example/phase4/README.md).
See [Harbor operations](docs/harbor-operations.md) for functional evidence and runtime limits.

SWE-bench, SWE-bench Lite, and ScarfBench now use Harbor by default. See
[benchmark migration](docs/harbor-benchmark-migration.md) for native grader
dependencies, model-free controls, and retained grading evidence.

See [report rendering](docs/report-rendering.md) for output structure and module
boundaries, and [cleanup status](docs/harbor-cleanup.md) for remaining tasks.

## How generation works

Harbor schedules each trial and owns its Docker or Podman environment. ACB
exports benchmark inputs into immutable task bundles, prepares the selected
harness and extensions, and runs the harness through Harbor's environment API.
Goose, Claude Code, OpenCode and Pi use the same adapter path.

Praxis runs as a trial service for model routing and token accounting. Optional
RTK, Caveman, skills and MCP configuration are prepared before execution.
Agent services are stopped before grading. SWE-bench and ScarfBench use their
native graders in controller-owned processes and containers; their grading
inputs are kept outside the agent environment.

See [operations](docs/harbor-operations.md) for lifecycle and isolation details,
and [benchmark migration](docs/harbor-benchmark-migration.md) for native grader
setup and controls. Report rendering targets the current output format; backward compatibility is not required.

## Building the containers

Preparation builds and caches provider images from packaged resources. Harbor
builds task images from exported environment recipes and records their image
identities. `acb prepare --config run.yaml` performs this work before execution.
The commands below are manual equivalents for troubleshooting.

**1. The praxis-ai image** (`acb-praxis-ai:latest`) — built from the
`Containerfile` at the root of this repo.  It is self-contained: the build
clones [praxis-proxy/ai](https://github.com/praxis-proxy/ai) at a pinned
commit and compiles the `praxis-vertex-anthropic` filter directly into the
binary — no separate checkout required.  praxis-ai is a superset of the core
[praxis-proxy/praxis](https://github.com/praxis-proxy/praxis) gateway that
adds the AI-specific filters this project needs: `token_count` for real token
accounting, the Anthropic↔OpenAI translation chain for claude-code against
local models, and our custom `vertex_anthropic_prepare` / `benchmark_metrics`
filters (see `praxis-vertex-anthropic/`).

```bash
podman build --tag acb-praxis-ai:latest .
```

`acb` builds and tags this image automatically the first time it is needed;
the manual command above is only necessary to force a rebuild (e.g. after
updating the filter source in `praxis-vertex-anthropic/`).

### Building the Linux `rgctl` Binary

The `rgctl` build Dockerfile uses Ubuntu 22.04 to produce an ARM Linux binary
with compatible GLIBC and C++ ABI versions. Build it from the adjacent
`skills-mcp-testing/rgctl` checkout. The build disables the optional
`semantic-onnx` feature because its prebuilt ONNX Runtime dependency requires
newer C++ ABI symbols than Ubuntu 22.04 provides:

```bash
cd ../skills-mcp-testing/rgctl

podman build --platform linux/arm64 \
  --file /Users/shurley/repos/agent-context-benchmarks/Dockerfile.rgctl-build \
  --tag rgctl-builder:ubuntu22.04 \
  .
```

Copy the binary out of the image without starting it:

```bash
mkdir -p /tmp/rgctl-build-output
container=$(podman create --platform linux/arm64 rgctl-builder:ubuntu22.04)
podman cp "$container:/output/rgctl" /tmp/rgctl-build-output/rgctl
podman rm "$container"
chmod +x /tmp/rgctl-build-output/rgctl
```

The resulting binary is `/tmp/rgctl-build-output/rgctl`.

### When to Rebuild the Praxis-AI Image

The `acb-praxis-ai:latest` image is built once and cached in `podman images`. 
**Python code changes** (`acb/*.py`) take effect immediately, but **Rust filter 
changes** in `praxis-vertex-anthropic/` require rebuilding the image because 
they're compiled into the binary.

Rebuild when you:
- Pull code updates that modify custom filters in `praxis-vertex-anthropic/`
- Check out an older commit (image may have newer filters than code expects)
- See error: `fatal: unknown filter type: 'benchmark_metrics'`

```bash
# Quick rebuild: remove image, next run rebuilds automatically
podman rmi acb-praxis-ai:latest

# Or rebuild immediately
podman build --tag acb-praxis-ai:latest .
```

**Custom filters included:**
- `vertex_anthropic_prepare` — Rewrites Anthropic requests for Vertex AI compatibility
- `benchmark_metrics` — Comprehensive token tracking (all token types, all backends)

**2. A per-instance testbed image** (e.g. `sweb.eval.arm64.psf_1776_requests-1142:latest`)
— built from that instance's Dockerfile in the public task repo. On
x86_64 machines the published Dockerfile builds as-is. On Apple Silicon
(arm64, no emulation) it needs three patches, all handled automatically by
`acb/benchmarks/image_builder.py`:

```bash
# 1. fetch the instance's Dockerfile
curl -O https://raw.githubusercontent.com/SWE-bench/swe-bench-tasks/main/tasks/<instance_id>/Dockerfile

# 2. patch it for arm64:
#    - drop the hardcoded `FROM --platform=linux/amd64` (it overrides any
#      --platform flag passed to the build, so it has to be removed, not
#      just overridden)
#    - swap the Miniconda installer from `-Linux-x86_64.sh` to `-Linux-aarch64.sh`
#    - relax the embedded environment.yml's exact conda pins to name/major-version
#      only -- some are pinned by exact build hash (x86_64-only) and some
#      (e.g. `ld_impl_linux-64`, `libgcc-ng`) are architecture-coded by
#      *package name* and don't exist under linux-aarch64 at all; conda's
#      solver picks aarch64-native equivalents transitively once they're
#      dropped/relaxed

# 3. build natively for arm64 (no docker/buildx needed -- podman's own
#    `build` supports --platform directly)
podman build --platform linux/arm64/v8 \
  --tag sweb.eval.arm64.psf_1776_requests-1142:latest \
  --file Dockerfile .
```

This trades exact transitive-dependency fidelity with the official x86_64
image for an image that actually builds. Verified end-to-end against a real
instance's `eval.sh`/`gold.patch`/`test.patch` (`psf__requests-1142`):
`PASS_TO_PASS` tests pass before and after, `FAIL_TO_PASS` fails before the
patch and passes after — the same signal SWE-bench's real evaluation harness
checks for a `resolved` verdict. Dependency-heavy repos (old pinned
scientific-stack instances like astropy/scikit-learn/matplotlib) are more
likely to need per-instance attention than something like `psf/requests`,
since some exact pins may have no aarch64 build at all.

**One-time config for container mode:**

```yaml
# config/benchmarks.yaml
swebench:
  image_arch: auto          # auto | amd64 | arm64  (auto = your machine's arch)
  # praxis_ai_repo is optional -- only needed if you want to build from a
  # local checkout of https://github.com/praxis-proxy/ai instead of the
  # self-contained Containerfile at the repo root.
```

The first run for a given harness also downloads that harness's Linux
binary into `runs/.cache/` (goose's static release binary, or
claude-code's standalone `@anthropic-ai/claude-code-linux-{arch}` npm
package -- ~340MB, no `npm`/`node` needed on the host to fetch it) --
reused across instances and later runs under the same output directory.

## Run

```bash
# from a config file (SWE-bench Lite examples)
acb run --config config/swebench-lite/run.goose-lite.yaml         # goose, local model
acb run --config config/swebench-lite/run.claude-code-lite.yaml   # claude-code, local model (translated)
acb run --config config/swebench-lite/run.opencode-lite.yaml      # opencode, local model
acb run --config config/swebench-lite/run.pi-lite.yaml            # pi, local model

# multi-harness comparison
acb run --config config/swebench-lite/run.multi-harness-comparison.yaml

# or inline
acb run --benchmark swebench-lite --harness goose \
        --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 \
        --run-id demo --limit 1 --proxy praxis

# view reports
acb report runs/demo/goose            # single-harness run report
acb report runs/demo --html           # suite index + individual benchmark HTML
acb compare runs/a/goose runs/b/goose # side-by-side comparison
acb compare runs/baseline runs/candidate --html # suite comparison with deltas
```

Comparisons match individual benchmarks, show same/better/worse/mixed quality with
coverage, and flag unmatched tasks or changed/unknown conditions. Token deltas use
only comparable complete measurements. Each benchmark has a standalone HTML page
with tool calls/results, turns, model requests, timing, tokens and artifacts.
See [comparison and metric behavior](docs/harbor-operations.md#dataset-metrics-and-comparisons).

**Report path notes**:
- **Single-harness runs** write to `runs/<run_id>/<harness>/report.json` → use `acb report runs/<run_id>/<harness>`
- **Multi-harness runs** write to `runs/<run_id>/report.json` (suite-level) → use `acb report runs/<run_id>`

Note: claude-code only speaks the Anthropic Messages API. Pairing it with an
`api: openai` model in `proxy.yaml` (like the local vLLM one above) works
*only* through praxis-ai's translation chain (see "How generation works"
above) -- pairing it with a real `api: anthropic` model
(`claude-opus-4-8`/`claude-sonnet-4-5`) needs no translation but does make
real, billed API calls (`ANTHROPIC_API_KEY` must be set).

A run config can override benchmark/harness/proxy settings without editing
the shared registry YAMLs, via `overrides` (merged over the registry config
at run time):

```yaml
overrides:
  benchmark:
    image_arch: arm64
```

### Clean run outputs

`acb clean --config path/to/run.yaml --dry-run` previews cleanup using the
YAML's `output_dir`, resolved relative to the invocation directory just as for `acb run`.
Absolute output paths are used unchanged; input paths remain config-relative.
Remove `--dry-run` to confirm deletion interactively, or add `--yes` to skip
confirmation. This cleans all entries in the selected output directory except
`.cache` and `.gitkeep`, not only the YAML's run ID. Without `--config`, cleanup
uses `./runs`; `--output-dir PATH` selects a directory relative to the current
working directory instead. `--config` and `--output-dir` are mutually exclusive.

## Output Structure

### Single-harness run: `runs/<run_id>/<harness>/`

```
runs/demo/
├── .cache/                          # shared binary cache (all runs)
│   ├── goose-x86_64-unknown-linux-gnu/
│   ├── claude-code-linux-x64-2.1.241/
│   ├── opencode-linux-x64-1.18.22/
│   └── pi-linux-x64-0.84.3/
└── goose/                           # harness output
    ├── instances/                   # per-instance data
    │   └── <instance_id>/
    │       ├── usage.jsonl          # LLM requests
    │       ├── transcript.jsonl     # harness output
    │       ├── prediction.json      # patch/output
    │       └── metrics.json         # derived metrics
    ├── usage.jsonl                  # aggregated usage
    ├── predictions.jsonl            # aggregated predictions
    ├── metrics.jsonl                # aggregated metrics
    ├── report.json                  # run-level rollup
    └── report.html                  # visualization
```

### Multi-harness run: `runs/<run_id>/`

```
runs/demo/
├── .cache/                          # shared binary cache
├── goose/                           # first harness
│   ├── instances/
│   ├── report.json
│   └── report.html
├── claude-code/                     # second harness
│   ├── instances/
│   ├── report.json
│   └── report.html
├── report.json                      # suite-level rollup
└── report.html                      # suite comparison
```

### Key files

| File              | Contents                                                    |
|-------------------|-------------------------------------------------------------|
| `usage.jsonl`     | one row per LLM request (the atomic measurement)            |
| `predictions.jsonl` | harness patches in the benchmark's expected format        |
| `metrics.jsonl`   | derived per-instance context metrics + resolved status      |
| `report.json`     | run-level rollup (resolve rate, avg/peak tokens, cache eff.)|
| `report.html`     | interactive visualization with charts                       |

Suite comparisons require the same benchmark and instance IDs on both sides.
Models may differ. Harnesses are compared by union: shared harnesses receive
baseline/candidate deltas, while harnesses present on only one side are marked
accordingly. In the HTML comparison, click a harness's token value to inspect
content classifications and tool-level token usage.

## Status

- ✅ **All four harnesses** (goose, claude-code, opencode, pi) support container-mode 
  generation with SWE-bench
- ✅ End-to-end (arm64/Podman, manually verified): SWE-bench × all four harnesses × 
  container-mode generation × Google Vertex AI Anthropic (`google-vertex-anthropic/claude-haiku-4-5`).
  `psf__requests-1142` resolves for every harness; per-turn cache token
  accounting confirmed working (`avg_cache_efficiency` 91–95% across harnesses).
- ✅ End-to-end (arm64/Podman, manually verified): SWE-bench × goose/claude-code ×
  container-mode generation, real local vLLM model through praxis-ai's
  Anthropic↔OpenAI translation chain -- full multi-turn tool-calling sessions 
  with correct per-turn token accounting.
- ✅ **ScarfBench** is fully implemented (`acb/benchmarks/scarfbench.py`). Requires
  external setup: install `scarf` CLI and run `scarf bench pull`. See 
  `config.example/benchmarks.yaml` scarfbench section for configuration.
- 🚧 **LiveCodeBench** benchmark is a stub (not yet implemented).
- 🚧 The `recording` proxy backend has no container-mode implementation
  (it's a host subprocess); only `praxis` (really: praxis-ai, see "How
  generation works" above) can be used for real runs today.
- ⚠️ Dataset: use `SWE-bench/SWE-bench_Verified` (the default), not
  `princeton-nlp/SWE-bench_Verified` — the vendored harness (v5.0.2) requires
  per-instance `image`/`eval_script`/`log_parser`/`eval_type` fields the
  princeton-nlp dataset predates and doesn't have.

## Troubleshooting

### Error: `fatal: unknown filter type: 'benchmark_metrics'`

**Symptom:** Benchmark run fails immediately when starting praxis-ai with:
```
fatal: unknown filter type: 'benchmark_metrics'
```
or similar error for `vertex_anthropic_prepare`.

**Root Cause:** The `acb-praxis-ai:latest` container image was built before 
custom filters were added (commit `d0cd5fe`, Aug 26 2026), or you pulled code 
updates but didn't rebuild the image. The image contains a compiled Rust 
binary; code changes to filters in `praxis-vertex-anthropic/` require 
recompiling.

**Quick Fix:**

```bash
podman rmi acb-praxis-ai:latest
# Next acb run will rebuild automatically with current filters
```

**Detailed Diagnosis:**

If the quick fix doesn't resolve it, verify your environment:

```bash
# 1. Check your code is up to date
git log --oneline -1
# Should show commit 0f6c41d or later (has benchmark_metrics)

# 2. Verify filter source exists in your checkout
ls -la praxis-vertex-anthropic/src/metrics_collector.rs
# Should exist (this implements benchmark_metrics filter)

# 3. Check current image age
podman images | grep acb-praxis-ai
# If created before your last git pull, rebuild needed

# 4. Detailed image inspection
podman inspect acb-praxis-ai:latest | grep Created
```

**Manual Rebuild Process:**

```bash
# 1. Remove outdated image
podman rmi acb-praxis-ai:latest

# 2. Rebuild from current code
podman build --tag acb-praxis-ai:latest .
# Build takes ~5-10 minutes (compiles Rust dependencies)
# Uses podman's build cache on subsequent rebuilds

# 3. Verify new image
podman images | grep acb-praxis-ai
# Should show recently created image (~40MB)
```

**Prevention:** After `git pull`, check for filter changes and proactively rebuild:

```bash
git pull origin main
git diff HEAD@{1} HEAD -- praxis-vertex-anthropic/
# If you see changes, rebuild:
podman rmi acb-praxis-ai:latest
```

### Slow Container Builds

**Symptom:** `podman build` takes 10+ minutes or seems stuck during Rust compilation.

**Cause:** First build compiles the entire praxis-ai Rust project from scratch, 
including all dependencies. This is normal. Subsequent builds use podman's 
layer cache and complete much faster.

**Solutions:**

1. **Be patient on first build** — 5-10 minutes is normal
2. **Increase VM resources** (Podman machine on macOS):
   ```bash
   podman machine stop
   podman machine set --cpus 4 --memory 8192
   podman machine start
   ```

3. **Check available disk space:**
   ```bash
   podman system df
   # If low on space, clean up old images:
   podman image prune -a
   ```

4. **View build progress** — If it seems stuck, watch for Rust compilation output:
   ```bash
   podman build --tag acb-praxis-ai:latest . 2>&1 | grep -E "Compiling|Finished"
   ```

### Rust Compilation Errors During Build

**Symptom:** `podman build` fails with Rust compiler errors like:
```
error[E0425]: cannot find value `foo` in this scope
```

**Cause:** The `praxis-vertex-anthropic/` filter code has syntax errors or 
incompatible changes.

**Solutions:**

1. **If you didn't modify filter code:**
   ```bash
   # Reset to clean state
   git status
   git diff praxis-vertex-anthropic/
   # If unexpected changes, restore:
   git checkout HEAD -- praxis-vertex-anthropic/
   ```

2. **If you're developing filters:**
   ```bash
   # Test compilation locally first
   cd praxis-vertex-anthropic
   cargo check
   # Fix errors before rebuilding image
   ```

3. **Check Containerfile is unmodified:**
   ```bash
   git diff Containerfile
   # Should show no changes unless intentional
   ```

### Container Image Cleanup

**Symptom:** Multiple old `acb-praxis-ai` images accumulating disk space.

**Cause:** Each rebuild creates a new image; old images aren't auto-deleted.

**Solution:**

```bash
# List all praxis images
podman images | grep praxis

# Remove specific old image by ID
podman rmi <IMAGE_ID>

# Remove all unused images (including old praxis-ai versions)
podman image prune -a

# Check disk usage
podman system df
```

### Harness Binary Download Failures

**Symptom:** Run fails with:
```
Failed to download goose binary
```
or similar for claude-code/opencode/pi.

**Cause:** Network issues, GitHub/npm rate limits, or temporary service outage.

**Solutions:**

```bash
# 1. Check network connectivity
curl -I https://github.com

# 2. Check if .cache directory is writable
ls -la runs/.cache/
# Should exist and be writable

# 3. Manual download (example for goose):
cd runs/.cache/
curl -L https://github.com/aaif-goose/goose/releases/download/stable/goose-x86_64-unknown-linux-gnu.tar.bz2 | tar xj

# 4. Retry the run
# Harness checks .cache/ first before downloading
```

### Binary Cache Location

Harness binaries are cached once per output directory under `runs/.cache/` by
default, not inside a particular `runs/<run_id>/` directory. This lets repeated
runs and collision-suffixed runs like `runs/demo-1/` reuse the same downloaded
goose, claude-code, opencode, or pi binary assets.

**Cache behavior**:
- Default `output_dir: runs` → cache at `runs/.cache/`
- Custom `output_dir: /path/to/results` → cache at `/path/to/results/.cache/`
- Cache is shared across all `<run_id>` directories under the same `output_dir`
- Binaries are downloaded once per (harness, arch, version) and reused across:
  - Multiple run IDs (e.g., `demo`, `demo-1`, `demo-2`)
  - All harnesses in multi-harness runs
  - All instances in a single run (parallel execution safe via file locks)

### Podman Machine Won't Start (macOS)

**Symptom:**
```bash
podman machine start
Error: unable to start host networking: ...
```

**Common Causes:**

1. **Port conflict:** Another VM or service using podman's ports
   ```bash
   podman machine stop
   podman machine rm
   podman machine init --cpus 4 --memory 8192
   podman machine start
   ```

2. **Stale VM state:**
   ```bash
   # Clean restart
   podman machine stop
   podman machine rm
   podman machine init
   podman machine start
   ```

3. **Check podman version:**
   ```bash
   podman --version
   # Ensure 4.0+ for best macOS support
   brew upgrade podman
   ```

### Configuration schema

Schema v2 is the only supported configuration format. `schema_version: 2` is
optional because 2 is the default; explicit older versions are rejected. Relative
input paths resolve against the YAML file declaring them; `output_dir` resolves
against the invocation directory. CLI `--config-dir` is
relative to the invocation directory and overrides the run-file setting. See the
[path rules](docs/configuration-ux-plan.md#implemented-path-rules).
