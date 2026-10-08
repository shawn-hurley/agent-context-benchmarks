# RTK execution integrations

RTK intercepts ordinary shell-tool commands and filters supported command output
before it reaches the model. Select `extensions: [rtk]` in run YAML, independently
of skills and MCP. Preparation obtains a verified Linux binary for the inspected
task architecture and selects the adapter mode for each harness.

| Harness | Harness release | Mode | Intercepted tool |
| --- | --- | --- | --- |
| Goose | Packaged default `1.50.1`; earlier pilot tested `1.50.0` | `shell-wrapper` | Developer shell |
| Pi | `0.84.3` | `native` | `bash` |
| OpenCode | `1.18.22` | `native` | `bash` |
| Claude Code | `2.1.241` | `native`, `isolated-hooks` profile | `Bash` |

Native adapters require RTK `0.48.0`; other releases fail validation until their
compatibility is tested. Named selection supplies the reviewed mode and
`experimental: true` automatically. The packaged Claude Code profile is
`isolated-hooks`, required for RTK.

## Configure a named treatment

Start from a working baseline and change only the treatment and run ID:

```yaml
skills: []
extensions: [rtk]
```

Resolve, prepare and execute the baseline and treatment separately:

```sh
acb resolve --config treatment.yaml
acb prepare --config treatment.yaml
acb run --config baseline.yaml
acb run --config treatment.yaml
acb compare BASELINE_RUN TREATMENT_RUN --html --bundle runs/rtk-comparison.zip
```

Use the actual reserved result paths in the comparison. Match model-server
settings, task inputs, harness profiles and cache policy between arms. See
[feature examples](../config.example/feature-tests/README.md) and
[configuration precedence](configuration.md#precedence-and-component-selections).

## Advanced binary configuration

Copy the [Goose](../config.example/rtk-native/goose.rtk.yaml), [Pi](../config.example/rtk-native/pi.rtk.yaml), [OpenCode](../config.example/rtk-native/opencode.rtk.yaml), or [Claude Code](../config.example/rtk-native/claude-code.rtk.yaml) template, along with its corresponding baseline configuration, into `config/`. Set `binary_path` to a Linux RTK executable matching the benchmark container's architecture, and `sha256` to its SHA-256. A macOS RTK binary cannot run inside the container.

```yaml
overrides:
  harness:
    version: "0.84.3"
    execution_integrations:
      - name: rtk
        version: "0.48.0"
        mode: native
        experimental: true
        binary_path: /absolute/path/to/linux/rtk
        sha256: "<binary-SHA256>"
```

These low-level templates retain the versions/model/task of the original pilot;
named feature examples use current packaged defaults. Run each arm with
`uv run --extra datasets acb run --config <config-file>`. Harbor's worker uses the
same Python environment as ACB, not a second evaluator environment. The templates
select a local Qwen model and `psf__requests-1142`; configure your endpoint first.
All advanced entries require `experimental: true`. Use one harness per template,
or [per-harness overrides](configuration.md#precedence-and-component-selections)
for adapter-specific settings. Do not also select named extensions.

Pi loads a reviewed local extension through explicit `-e`. OpenCode adds a reviewed local plugin to its final provider/MCP configuration. Adapters preserve other tool arguments and native permissions. They do not add RTK instructions to the model prompt or compress native read/edit/search tools.

OpenCode `1.18.22` normally waits for an npm dependency bootstrap when loading a plugin. ACB stages its plugin API dependency tree during harness setup for both arms, using the bundled dependency lock and SHA-512-verified npm tarballs. Cache misses need registry access during setup; generation needs no package downloads. No npm executable or package install scripts are required. Optional native accelerators are omitted.

## Claude Code launch profile

Use `launch_profile: isolated-hooks` in **both** Claude configurations. This is
the packaged default; an explicit `bare` override cannot be used with RTK. The
pinned release skips explicitly configured hooks under `--bare`, so historical
bare runs require a new matched baseline. The profile uses explicit settings, an
isolated `CLAUDE_CONFIG_DIR`, empty settings sources, disabled project instructions
and auto-memory, and explicit Bash/Edit/Read exposure. Slash commands and discovered
MCP servers are disabled. Configured system prompts, explicit skill-reading hints
and explicitly selected MCP servers are preserved.

The RTK settings file registers SessionStart loading verification, a Bash PreToolUse hook, and result hooks. Its repository-owned Python shim returns the full tool input with only `command` changed and leaves Claude's native permission checks intact. This follows Claude's [hook JSON protocol](https://code.claude.com/docs/en/hooks) and [explicit CLI configuration](https://code.claude.com/docs/en/cli-reference). No permission bypass or global host initialization is used.

Preparation selects the task's inspected helper Python (3.8+) for the hook; an
explicit `python_path` must identify a suitable interpreter inside the container.
Setup checks its standard-library dependencies. RTK is exposed through a
container-local `/usr/local/bin/rtk` symlink; an existing file at that location is
rejected. Rewriting runs in the isolated adapter directory because RTK itself reads
Claude permission files from its working directory. The rewritten command still
executes in the native tool's original working directory. Project instruction/settings
isolation is tested against the exact pinned binary, including conflicting project
hooks and deny rules.

Claude always uses the Anthropic API. Local Qwen pilots route through Praxis translation; the offline compatibility fixture speaks Anthropic directly and does not test model quality or that proxy translation.

## Evidence and failures

Per-instance `integrations/rtk/` contains a manifest, `gain.json`, and `evidence/` with tracking, native loading information, and decision/result records. Preflight uses separate logs and tracking, so its commands do not inflate measured activity.

The manifest's final `verification` and `metadata.activity` distinguish adapter loading, observed agent interception, rewrites, passthroughs, denials, errors, and estimated savings. Native decision/result records correlate tool calls using IDs and command/output hashes. Loading with no shell calls is reported as no observed interception.

Unsupported commands pass through unchanged. Already-prefixed RTK calls avoid double rewriting. Denials block the original command; rewrite errors, empty/malformed responses, timeouts, and missing binaries cannot silently turn an enabled experiment into a baseline. A persistent failure marker makes collection fail even if the agent recovers and exits normally. Original command failures and stderr remain tool results.

RTK's estimated savings are auxiliary diagnostics. Praxis/provider token usage measures what reached the model and how much inference the full run consumed. Cached input is part of total input when the provider reports it that way. A rewrite with zero compression is valid; more model turns or file reads can outweigh compressed shell output.

## Compatibility tests

From a fresh checkout on macOS or Linux ARM64, install Python 3.12+, uv, and
Podman. On macOS, initialize and start `podman machine` first. Run:

```bash
bash scripts/run_rtk_smoke.sh
```

The script builds [Dockerfile.rtk-smoke](../Dockerfile.rtk-smoke), obtains RTK
`0.48.0` from its immutable upstream release commit, compiles it inside Ubuntu
22.04 with locked Cargo dependencies, and exports the Linux binary to
`runs/.cache/rtk-smoke/rtk`. The fixture image contains a real, offline-cloned
conda testbed environment. Its pinned Miniconda installer is verified against
the hashes in the [published installer index](https://repo.anaconda.com/miniconda/).
No sibling checkout, local model server, credentials, prebuilt task image,
host RTK, Rust, npm, or Node installation is required for this live test.
First-time setup needs network access for image, compiler, RTK, harness, and
OpenCode dependency downloads; generation containers use `--network none`.

The exported binary can also be used in a pilot configuration. Print its
checksum with a portable Python command:

```bash
uv run python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('runs/.cache/rtk-smoke/rtk').read_bytes()).hexdigest())"
```

Use that checksum and the binary's absolute path in your RTK YAML. Copy the
registry examples with `cp -R config.example config` if `config/` does not
exist, then configure a model endpoint reachable from Podman. The supplied
Qwen identifier is a pilot example; an actual benchmark needs a matching
running model server or a configured cloud backend, the SWE-bench submodule,
dataset dependencies, and the normal ACB evaluation prerequisites.

The integration implementation, hook assets, lock, tests, templates, and setup
recipe must all be included in the commit being cloned. Local `config/` and
`runs/` are deliberately ignored and are recreated on the receiving machine.

```bash
uv sync --locked --group dev
uv run pytest tests -q
ACB_RTK_LIVE=1 ACB_RTK_BINARY=/path/to/linux/rtk \
  uv run pytest tests/test_rtk_native_live.py -v
```

The opt-in tests cover all four real pinned harnesses inside temporary,
network-disabled Podman containers and a deterministic OpenAI/Anthropic API
fixture. The default is the image built by the script; set `ACB_RTK_IMAGE` to
use a compatible existing image instead. Tests verify smaller supported-command
output in the next provider request, unsupported-command passthrough,
stderr/nonzero exit behavior, execution exactly once, and failure blocking.
Artifacts remain under `runs/rtk-native-smoke/`; test-owned containers are removed.
The separate JavaScript protocol unit tests require host Node; without it those
tests skip, while the live tests still exercise each harness's bundled runtime.

These are adapter compatibility tests, not model quality or SWE-bench savings measurements. Repeat matched benchmark pilots before making savings claims.

Verified on 2026-09-15 using candidate source copies with no local configs,
virtual environment, harness cache, or sibling checkout:

| Host | Regular tests | Real-harness smoke |
| --- | --- | --- |
| macOS ARM64, Python 3.13 | 88 passed, 4 live tests skipped | All 4 passed |
| Linux ARM64 Podman VM, Python 3.12, native Podman CLI | 77 passed, 15 skipped (optional Node tests and separate live tests) | All 4 passed |

The fixture image was built from scratch with the new recipe. Each host ran
`bash scripts/run_rtk_smoke.sh`, downloading its harnesses into an empty checkout
cache. The Linux host reused that fixture image's completed build layers.
The wheel built from the candidate checkout contains Goose configuration,
all native RTK hooks, and the locked OpenCode runtime metadata.
