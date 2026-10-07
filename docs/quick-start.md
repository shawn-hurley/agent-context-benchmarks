# Quick Start: clone, run, and inspect a benchmark

Start with a small included repair task, then run a ScarfBench migration. The
repair task verifies setup; ScarfBench evaluates application migration behavior.

## 1. Install and clone

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Python 3.12 or newer (uv can install it), and a running container engine with a
Compose frontend. Docker builds also require Buildx/BuildKit. Harnesses, Node,
and Java run inside containers.

For a new macOS Podman installation:

```sh
brew install uv podman docker-compose
podman machine init --rootful --cpus 4 --memory 12288 --disk-size 100
podman machine start
podman info
podman compose ls
```

If a Podman machine already exists, start it instead of initializing another.
Podman supports one managed VM running at a time. Linux users can use Podman
with a working user socket and Compose provider, or Docker Engine with Compose
V2. Docker Desktop also works; select `docker` in the starter machine config.

Another macOS option is Colima with Docker:

```sh
brew install uv colima docker docker-compose docker-buildx
mkdir -p "$HOME/.docker/cli-plugins"
ln -s "$(command -v docker-compose)" "$HOME/.docker/cli-plugins/docker-compose"
ln -s "$(brew --prefix)/lib/docker/cli-plugins/docker-buildx" "$HOME/.docker/cli-plugins/docker-buildx"
colima start --cpu 4 --memory 12 --disk 100
docker info
docker compose version
docker buildx version
export DOCKER_BUILDKIT=1
```

For a fresh Homebrew Docker installation, the symlinks above expose the
plugins to `docker compose` and `docker buildx`. Skip creating symlinks that
already exist. If you set `DOCKER_CONFIG`, put `cli-plugins` under that directory
instead. Verify both plugin version commands before running ACB. The packaged
proxy and Maven cache recipes require BuildKit features; the legacy Docker
builder cannot assemble them. Docker Desktop includes these plugins.

ACB checks the selected engine and Compose frontend before preparation or run
startup. An unavailable frontend or provider connection fails with the command
and setup guidance before downloads or trial creation. Compose is required even
for the included single-container task because Harbor uses it for the trial
lifecycle; see [container prerequisites](harbor-operations.md#container-prerequisites-and-compose).

Then clone and install ACB:

```sh
git clone https://github.com/shawn-hurley/agent-context-benchmarks.git
cd agent-context-benchmarks
uv sync --locked
uv run acb --help
mkdir -p config
cp -R config.example/quickstart config/quickstart
```

Commands below run from this checkout. For Docker, change
`config/quickstart/machine.yaml` from `environment: podman` to
`environment: docker`. This directory explicitly selects its own registries,
cache, and output; there is no need to reset existing configuration.

First preparation downloads task images and the selected Linux harness binary,
and builds the pinned Praxis measurement proxy from source. Reserve enough VM
disk space for image layers and build intermediates. A cold source build may
take substantially longer than subsequent runs; its build timeout is 30 minutes.
These VM settings are a starting point, not a guarantee for large applications
or concurrent trials. Check free space **inside** the VM as well as on the host.

## 2. Check execution without making model calls

```sh
uv run acb resolve --config config/quickstart/run.yaml
uv run acb run --config config/quickstart/run.yaml --control oracle
uv run acb run --config config/quickstart/run.yaml --control nop
```

`resolve` validates configuration without starting containers. Oracle writes the
known solution and must receive grade **1**. Nop leaves the task broken and must
receive grade **0**. Both controls should complete without infrastructure or
verification errors. They do not contact a model and do not verify credentials,
tool calling, or token accounting.

Results are under `runs/quickstart/smoke`, then `smoke-1`, `smoke-2`, and so on.
ACB prints the reserved output path; use that path in subsequent report commands.
An exit code of zero alone does not prove the benchmark passed. Inspect the
reported reward and evaluation status.

## 3. Configure a model and run one task

Edit `config/quickstart/models.yaml`: replace `YOUR_MODEL_ID` with a model
available through your provider. The example uses an OpenAI-compatible API.
`endpoint` is a host and optional port, without a scheme or `/v1` suffix.
Set `key_env` to the environment-variable name holding your provider credential.
Use `api: anthropic` for an Anthropic backend. Local compatible servers may use
`tls: false` and omit `key_env`; containers must be able to reach that server.

```sh
export OPENAI_API_KEY='your-provider-key'
uv run acb resolve --config config/quickstart/run.yaml
uv run acb prepare --config config/quickstart/run.yaml
uv run acb run --config config/quickstart/run.yaml
```

`prepare` builds/downloads prerequisites and probes the harness without making
model requests. Normal `run` contacts the model and may incur charges. This
starter schedules Goose once on one task, with one worker and a 300-second
agent timeout. The model must edit `/work/answer.txt` and check its work.

Success means a completed trial with reward **1**, complete measurements,
positive model usage, and recorded tool calls. A grade of zero with a completed
verifier means the agent failed the task. Missing rewards, exceptions, or
incomplete measurements require checking the retained logs. Prices are optional:
missing pricing data does not prevent execution or token accounting.

## 4. Inspect and share the report

Replace `ACTUAL_RUN` with the output path printed by your model run:

```sh
uv run acb report ACTUAL_RUN --html --bundle runs/quickstart/report.zip
```

Open `ACTUAL_RUN/report.html` for the overview, then follow links to the task
details, model requests, tool interactions, and retained evidence. Unzip
`report.zip` elsewhere and open `index.html`; charts and navigation work offline.
Native ScarfBench and SWE-bench reports also retain the submitted source diff.
The setup smoke task has no native migration submission.

For comparisons:

```sh
uv run acb compare BASELINE_RUN CANDIDATE_RUN --html --bundle runs/quickstart/comparison.zip
```

Workflow, skill, timeout, image, and cache changes are experimental context.
Changed application inputs or behavior tests can prevent grade comparisons;
incomplete measurements prevent affected token comparisons.

## 5. Run the corrected ScarfBench benchmark

The corrected tests are in
[shawn-hurley/scarf-benchmark, PR #1](https://github.com/shawn-hurley/scarf-benchmark/pull/1).
This guide requires commit `d0a6a3d3bd7c0e8a80bc6474737475a593ba4418` to be
published to branch `fix/realworld-shared-behavior`. A checkout failure means
that revision is not published yet; do not silently substitute an older tree.

Install the tested native CLI **0.1.2**, Git, and Make on the controller. See
[ScarfBench installation](https://scarfbench.info/installing/) for installers.
For a pinned prebuilt release, select your platform below. The Apple Silicon
asset and checksum have been downloaded and verified during onboarding checks;
other platform assets are listed by the upstream Homebrew formula.

| Platform | Target | SHA-256 |
| --- | --- | --- |
| macOS Apple Silicon | `aarch64-apple-darwin` | `30abd5b3f6d910d2dff0ce69c3f03ee0c868beaa11edd20b795a1a6b63baa2d8` |
| macOS Intel | `x86_64-apple-darwin` | `df9a8e1ff3b18abfe7e70abc0ec35de841358b254f73e2247efc52a306275f8f` |
| Linux ARM64 | `aarch64-unknown-linux-gnu` | `c2ba52a2b2ed180f260bfe4810bbca3014817edaafb0c8234b316499af4b4719` |
| Linux AMD64 | `x86_64-unknown-linux-gnu` | `608f0867e1b28f5047a801952c6de8305caf80bc4d105d30497d0638d77d1cd7` |

Run from the ACB checkout, replacing target and checksum for your platform:

```sh
acb_scarf_target=aarch64-apple-darwin
acb_scarf_sha=30abd5b3f6d910d2dff0ce69c3f03ee0c868beaa11edd20b795a1a6b63baa2d8
curl --fail --location \
  "https://github.com/scarfbench/scarfbench-cli/releases/download/v0.1.2/scarfbench-cli-$acb_scarf_target.tar.xz" \
  -o config/quickstart/scarf-cli.tar.xz
uv run python - "$acb_scarf_target" "$acb_scarf_sha" <<'PYINSTALL'
import hashlib, sys, tarfile
from pathlib import Path
archive = Path('config/quickstart/scarf-cli.tar.xz')
if hashlib.sha256(archive.read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('Scarf CLI checksum mismatch; installation stopped')
with tarfile.open(archive) as package:
    data = package.extractfile('scarfbench-cli-' + sys.argv[1] + '/scarf').read()
binary = Path('config/quickstart/bin/scarf')
binary.parent.mkdir(parents=True, exist_ok=True)
binary.write_bytes(data)
binary.chmod(0o755)
PYINSTALL
export PATH="$PWD/config/quickstart/bin:$PATH"
scarf --version
scarf validate --help
make --version
```

ACB invokes the native `scarf validate` command. Its controller installs the
Podman compatibility shim automatically when needed; no manual `bin/docker`
file is required. Clone the corrected benchmark directly:

```sh
mkdir -p config/quickstart/assets
git clone --single-branch --branch fix/realworld-shared-behavior \
  https://github.com/shawn-hurley/scarf-benchmark.git \
  config/quickstart/assets/scarf-benchmark
git -C config/quickstart/assets/scarf-benchmark checkout --detach \
  d0a6a3d3bd7c0e8a80bc6474737475a593ba4418
uv run acb resolve --config config/quickstart/scarfbench-cart.yaml
uv run acb run --config config/quickstart/scarfbench-cart.yaml --control oracle
uv run acb run --config config/quickstart/scarfbench-cart.yaml --control nop
uv run acb prepare --config config/quickstart/scarfbench-cart.yaml
uv run acb run --config config/quickstart/scarfbench-cart.yaml
```

The Cart task migrates Jakarta to Quarkus. Its shared behavior test checks
health, adding and removing books, cart size, and the missing-book response.
Oracle must pass native compilation, deployment, and behavior validation; nop
must fail the migration contract. A real model's migration grade is its result,
not an installation guarantee.

Maven downloads are cached across preparation and native grading in the
dedicated `acb-quickstart-maven-v1` cache. See
[cache configuration](scarfbench-maven-cache.md). Initial runs require Maven
repository access; a cache does not bypass repository rate limits.

Remove `subset` to select all Jakarta-to-Quarkus applications. Keep one worker
initially. To compare migration workflows, add `workflow: kantra-rgctl` or
`workflow: migiq`; see [workflow setup](workflows.md). These have larger image
builds and three 900-second stages. SWE-bench requires additional dataset and
grader setup, including `git submodule update --init SWE-bench` and
`uv sync --locked --extra datasets`; see [native benchmark setup](harbor-benchmark-migration.md).

## Repeatable acceptance check

The check creates a new configuration, cache, run directory, logs, HTML, and
extracted report ZIPs. Choose a new output directory each time:

```sh
uv run python scripts/check_quickstart.py /tmp/acb-quickstart-check --environment podman
uv run python scripts/check_quickstart.py /tmp/acb-quickstart-live --environment podman \
  --live --model-config config/quickstart/models.yaml
```

Use `--environment docker` for Docker. The first check makes no model calls;
the second runs exactly one model-driven repair task in addition to controls.
Add `--scarf` after the corrected fork revision is published to check public
download, native Cart controls, and its source diff. For explicit local
diagnosis, `--scarf-benchmark /path/to/benchmark` checks native controls but
does **not** claim publication was verified. Results are recorded in `check.json`.

A fresh clone and empty ACB cache do not imply an empty container-engine store.
Use a disposable runner or separate engine for cold-install acceptance; never
delete another experiment's images or caches to simulate one.

See [the recorded validation results and limits](quick-start-validation.md).

## Publishing these changes (maintainer)

Push the ACB commits to the public repository before asking others to follow
the GitHub clone instructions. To update the existing benchmark PR from the
local corrected checkout:

```sh
git -C ../scarfbench-benchmark push \
  https://github.com/shawn-hurley/scarf-benchmark.git \
  d0a6a3d3bd7c0e8a80bc6474737475a593ba4418:refs/heads/fix/realworld-shared-behavior
```

This is a regular fast-forward push. Verify the public branch contains the
pinned commit before claiming the public ScarfBench quick start is validated.
