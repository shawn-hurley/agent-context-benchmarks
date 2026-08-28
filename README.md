# agent-context-benchmarks (`acb`)

Compare **context/token usage** of agent harnesses (claude-code, goose,
opencode, pi) running the same coding benchmarks (SWE-bench today;
LiveCodeBench / ScarfBench scaffolded) against the same model — cloud or local.

Every LLM call flows through a proxy that records it, so we get per-request token
accounting per (harness, model, benchmark, instance). See [DESIGN.md](DESIGN.md).

Generation is **container-only**: the harness always runs inside the same
eval image evaluation will grade the patch in, not a plain host checkout, so
its dev environment matches evaluation exactly instead of whatever happens to
be on the machine running `acb`. This means Podman is required to run
anything, not just to evaluate — see below.

## Install

```bash
pip install -e '.[datasets]'          # datasets extra needed for SWE-bench
pip install -e ./SWE-bench            # vendored evaluation harness (needs Podman)
```

On Apple Silicon Macs there's no Docker Desktop requirement — a [Podman
machine](https://podman.io) works:

```bash
podman machine init && podman machine start
```

`acb` auto-detects it (`container_backend: auto` in `benchmarks.yaml`) and
ships a `bin/docker` shim so SWE-bench's own code that shells out to a
literal `docker` binary still works against Podman.

## Configure

Copy the example configuration files and customize them:

```bash
cp -r config.example config
```

Then edit the configuration files:

- `config/proxy.yaml`  — model backends the proxy owns (cloud + local) and proxy backends.
- `config/harnesses.yaml` — per-harness CLI knobs.
- `config/benchmarks.yaml` — dataset selection + container-mode settings (image arch, task repo, praxis image source).
- `config/run.requests-1142.yaml` — one run (benchmark × harness × model).

Set the real provider key(s) in your environment; the proxy injects them so the
harness never sees them:

```bash
export ANTHROPIC_API_KEY=sk-...
export OPENAI_API_KEY=sk-...          # if using OpenAI / for local, none needed
```

## How generation works

Per instance, a Podman **pod** (shared network namespace) holds two sibling
containers:

```
┌─ pod ──────────────────────────────────────────────────┐
│  testbed container              praxis-ai container     │
│  (SWE-bench eval image,   ──►   (built from this repo's  │
│   harness binary copied in,      Containerfile)          │
│   /testbed checkout)                                     │
│        │                              │                  │
└────────┼──────────────────────────────┼──────────────────┘
         │ podman exec                  │ host.containers.internal
         ▼                              ▼
   goose or claude-code            your local model server
   runs headless                   (e.g. vLLM on :8000) --
                                    or api.anthropic.com,
                                    translated if needed
```

- **Testbed container** — the SWE-bench eval image for that instance, built
  on demand from the public task repo (`SWE-bench/swe-bench-tasks`, one
  Dockerfile per instance) — see `acb/benchmarks/image_builder.py`. Already
  contains a single-branch, future-history-pruned `/testbed` checkout at
  `base_commit` (baked in at image-build time, same anti-leakage guarantee a
  host-mode clone would need to construct itself). The harness's own binary
  is staged in separately by `HarnessAdapter.setup_container()` (each
  harness's own hook, called after this container starts) and run via
  `podman exec`.
- **Praxis-ai container** — built once from the `Containerfile` at the root
  of this repo (which clones [praxis-proxy/ai](https://github.com/praxis-proxy/ai)
  at a pinned commit and compiles our custom filter in), tagged
  `acb-praxis-ai:latest`, and reused across every instance/run after that.
  Not the core
  [praxis-proxy/praxis](https://github.com/praxis-proxy/praxis) gateway --
  praxis-ai is a superset that adds the `token_count` filter (real per-
  request token accounting) and, for claude-code specifically, an
  Anthropic↔OpenAI translation chain (`anthropic_messages_format` /
  `anthropic_to_openai` / `anthropic_stream_events`) that lets an Anthropic-
  speaking harness target an OpenAI-compatible local model -- see
  `acb/proxy/praxis.py`'s module docstring for what's actually verified
  live about both.
- The two containers share a network namespace, so the harness reaches
  praxis-ai on plain `127.0.0.1`; praxis-ai reaches your host's model server
  via Podman's `host.containers.internal` gateway (gvproxy), or the real
  Anthropic/OpenAI API directly for cloud models.
- Files move in/out via `podman cp`, not bind mounts: Podman-machine-on-macOS
  doesn't share arbitrary host directories into the VM by default (verified:
  `-v <hostpath>:...` silently fails inside the VM even for paths that exist
  on the Mac host).

All four harnesses support container-mode today (`HarnessAdapter.run_container()`):
goose (static release binary), claude-code (standalone native executable in the
`@anthropic-ai/claude-code-linux-{arm64,x64}` npm package -- *not* a Node.js
package needing a runtime; see `acb/harnesses/claude_code.py`'s module
docstring), opencode (standalone binary from GitHub Releases), and pi (same
pattern). Each binary is downloaded once, cached under `runs/<run_id>/.cache/`,
and `podman cp`'d into every container for that run.

## Building the containers

Both images are built automatically the first time they're needed
(`acb/runner.py`'s `_ensure_praxis_image()`, `acb/benchmarks/image_builder.py`'s
`ensure_instance_image()`) and cached in `podman images` for every run after
that. This section is the manual/by-hand equivalent, useful for
understanding what's happening or troubleshooting a build failure.

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
binary into `runs/<run_id>/.cache/` (goose's static release binary, or
claude-code's standalone `@anthropic-ai/claude-code-linux-{arch}` npm
package -- ~340MB, no `npm`/`node` needed on the host to fetch it) --
reused across instances in that run.

## Run

```bash
# from a config file
acb run --config config/run.requests-1142.yaml               # goose, local model
acb run --config config/run.claude-code-requests-1142.yaml   # claude-code, local model (translated)

# or inline
acb run --benchmark swebench --harness goose \
        --model mlx-community/Qwen3.8-27B-4bit --run-id demo --limit 1 --proxy praxis

acb report runs/demo                  # show the rollup
acb compare runs/a runs/b runs/c      # side-by-side across runs
```

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

## Output (per run, under `runs/<run_id>/`)

| File              | Contents                                                    |
|-------------------|-------------------------------------------------------------|
| `usage.jsonl`     | one row per LLM request (the atomic measurement)            |
| `predictions.jsonl` | harness patches in the benchmark's expected format        |
| `metrics.jsonl`   | derived per-instance context metrics + resolved status      |
| `report.json`     | run-level rollup (resolve rate, avg/peak tokens, cache eff.)|

## Status

- ✅ End-to-end (arm64/Podman, manually verified): SWE-bench × **all four
  harnesses** (goose, claude-code, opencode, pi) × container-mode generation ×
  Google Vertex AI Anthropic (`google-vertex-anthropic/claude-haiku-4-5`).
  `psf__requests-1142` resolves for every harness; per-turn cache token
  accounting confirmed working (`avg_cache_efficiency` 91–95% across
  harnesses).
- ✅ End-to-end (arm64/Podman, manually verified): SWE-bench × goose ×
  container-mode generation, real local vLLM model through a containerized
  praxis-ai. `config/run.requests-1142.yaml`.
- ✅ End-to-end (arm64/Podman, manually verified): SWE-bench × claude-code ×
  container-mode generation, real local vLLM model through praxis-ai's
  Anthropic↔OpenAI translation chain -- a full real multi-turn tool-calling
  session (real `Bash`/`Read` calls and results), correct per-turn token
  accounting throughout. `config/run.claude-code-requests-1142.yaml`.
- 🚧 LiveCodeBench / ScarfBench benchmarks are stubs.
- 🚧 The `recording` proxy backend has no container-mode implementation
  (it's a host subprocess); only `praxis` (really: praxis-ai, see "How
  generation works" above) can be used for real runs today.
- ⚠️ Dataset: use `SWE-bench/SWE-bench_Verified` (the default), not
  `princeton-nlp/SWE-bench_Verified` — the vendored harness (v5.0.2) requires
  per-instance `image`/`eval_script`/`log_parser`/`eval_type` fields the
  princeton-nlp dataset predates and doesn't have.
