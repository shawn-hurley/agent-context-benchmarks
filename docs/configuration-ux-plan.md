# Configuration user experience review

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

Status: Phase 1 complete as of September 17, 2026. Schema-v2 resolution,
shared selections, declaring-file paths, validation, execution handoff, and
requested/resolved provenance are implemented and verified. The
[UX/structure assessment](harbor-ux-structure-review.md) records the final review
and requested pause before phase 4. The later design proposals below remain
subject to their assigned migration phases; MCP remains deferred.
Companion: [shared extension plan](extension-configuration-plan.md).

Execution and combined delivery: [Harbor integration plan](../HARBOR_PLAN.md).
This UX is the frontend for Harbor jobs. Goose, Pi, OpenCode, and Claude Code
are required, with container-only execution on Docker and Podman. The host runs
configuration/preparation/controller tooling; agents, Praxis, MCP services, and
extension runtimes run in containers. A host model endpoint remains allowed.
Host agent execution and Podman-only orchestration are retired after migration
parity checks, rather than retained as fallbacks.

## Phase 1 implementation checkpoint — September 17

The completed work is tracked as [H01-01 through H01-06](harbor-migration-tasks.md#phase-1-discrete-implementation-tasks).
H01-01 is complete: schema-v2 local skill `source_path` and RTK `binary_path`
values are normalized before layers merge. Catalog definitions and default
options use the catalog file's directory; selections in `harnesses.yaml` use
that registry's directory; run selections and shared/per-harness overrides use
the run file's directory. Inline legacy asset declarations follow the same rule
in schema v2. Schema v2 is the only supported format and is the default when
`schema_version` is omitted; explicit older versions are rejected.
Container workdirs and interpreter paths keep their container meaning.

H01-02 is complete: registry discovery and remaining host path rules are covered
by resolver and outside-checkout CLI tests. H01-03 completes the
validation/precedence audit. H01-04 completes normalized execution consumption and H01-05 completes
configuration provenance. H01-06 completes the final UX/structure review, full
regression suite (354 passed, 4 skipped), and installed-package CLI check.
Phase 1 is complete; Phase 4 has resumed with shared extension support for the
existing runners. Benchmark conversion/retirement is deferred to a follow-on. Phases 2/3 remain closed.

## Recommendation

Keep the existing registry-and-run architecture, but make a run describe the
experiment rather than container setup. Start by lifting shared skills, MCP, and
extensions to top-level selections and resolving adapter details centrally. Avoid
introducing a general workflow language or requiring users to rewrite every config.

Separate three kinds of configuration:

| Layer | User purpose | Contents |
| --- | --- | --- |
| Run file | What to measure | Benchmark/task selection, harnesses, model, skills, MCP, extensions, concurrency and limits |
| Project defaults/catalogs | What is available | Exact versions, named models/components, adapter compatibility, shared policies |
| Model definitions | Which provider/model to contact | Alias, wire model ID, endpoint, API mode, credential environment references |
| Machine settings | Where/how to run | Explicit Docker/Podman environment and cache directory |

Requested and fully resolved configuration should be saved with every result, with
secrets omitted. Simplicity in the run file must not remove experimental provenance.

## Original friction and proposed improvements (design history)

| Current friction | Proposed improvement |
| --- | --- |
| Skills/MCP live inside a generic harness override, making a shared experiment look harness-specific | Add top-level `skills`, `mcp_servers`, and `extensions`; normalize into the existing lifecycles |
| RTK asks users for adapter mode, binary path/hash, exact versions, and Claude launch profile | Resolve reviewed requirements from a catalog, with verified artifact preparation |
| One shared harness override cannot express different version pins/options | Add optional per-harness overrides; ordinary runs use shared selections and exact defaults |
| Model definitions are buried in `proxy.yaml`; aliases must currently equal provider model IDs | Introduce a model registry and separate a friendly alias from the actual provider model ID |
| Scarfbench examples repeat `/work`, null conda, runtime images, and benchmark data paths | Benchmarks declare container environment; machine settings supply data locations |
| Arbitrary override keys and unknown harness/backend names may reach execution with defaults | Validate names, field types, options, capabilities, and conflicts before setup/model calls |
| Package-relative `config` location and current-directory paths make checkout/install behavior confusing | Add explicit project config discovery and `--config-dir`; define path resolution by schema version |
| Users must copy several large, heavily commented registry files | Provide built-in defaults, small optional project overrides, and an initialization command that never overwrites existing files |
| `max_workers` appears at run and benchmark levels, and examples describe the wrong execution order | Define one global worker limit and distinguish total tasks from concurrent tasks; correct examples |
| Costs are a separate required local file | Provide versioned optional pricing defaults; usage-only runs remain possible without prices, and unknown cost stays unknown |

## Original first-step proposal (design history)

Preserve existing key names. This proposed run is intentionally close to today's
format; the only new selections are shared components:

```yaml
run_id: scarfbench-rtk-caveman
benchmark: scarfbench
harness: [goose, pi, opencode, claude-code]
model: google-vertex-anthropic/claude-haiku-4-5
skills: [caveman]
extensions: [rtk]
limit: 1
max_workers: 1
overrides:
  benchmark:
    instances:
      - layer: business_domain
        app: cart
        source: jakarta
        target: quarkus
```

`skills/caveman` is the existing response-style skill. `extensions/caveman` will
refer to its context runtime once supported. No `caveman-context` alias is needed.
Users can enable either or both; catalog lookup is scoped by category, not global
name. Extension runtime dependencies do not silently activate the response skill.

Shared list selections apply identically across the selected harnesses. Support
name shorthand plus an explicit mapping for version/options. If a selected
component cannot be supported by a harness, report the incompatible pair before
starting any instance. Do not silently skip components.

Allow advanced `overrides.harnesses.<name>` without making it the primary UX.
Default per-harness list selections replace shared lists when explicitly supplied;
do not silently append and accidentally keep a supposedly disabled component.
Reject legacy/new selections of the same category when they are ambiguous.

## Later structured syntax

After the first step, evaluate an explicitly versioned schema for task selection
and execution settings. This should replace free-form overrides for common knobs:

```yaml
schema_version: 2
run_id: scarfbench-rtk-caveman
benchmark:
  name: scarfbench
  instances:
    - layer: business_domain
      app: cart
      source: jakarta
      target: quarkus
harness: [goose, pi, opencode, claude-code]
model: vertex-haiku
skills: [caveman]
extensions: [rtk]
execution:
  max_workers: 1
  timeout: 1800
```

Use seconds consistently initially; duration strings can wait for demonstrated
need. Support scalar benchmark shorthand when no options are needed. Keep model
aliases in a model registry, with the literal provider ID and endpoint recorded
in the resolved artifact. Do not change what model ID goes over the wire by merely
renaming the display key.

V2 input paths resolve relative to the declaring config file, including machine overrides.
Run `output_dir` resolves relative to the invocation directory.
Explicit command-line config paths win over discovery; discovered project settings
must be named in `acb resolve` output. Do not automatically expand arbitrary shell
commands or persist credential values in resolved YAML.

## Implemented precedence and validation

H01-03 is complete. Precedence below is from lowest to highest priority. Lists
replace the lower layer as a whole; `[]` explicitly disables that selection.

| Setting | Precedence / rule |
| --- | --- |
| Benchmark settings | Built-in catalog → benchmark registry → structured run benchmark → `overrides.benchmark` |
| Harness scalar settings | Built-in catalog → harness registry → `overrides.harness` → `overrides.harnesses.<name>` |
| Harness timeout | Harness precedence above, then `execution.timeout` applies to every selected harness |
| Shared components | Harness registry → shared override or top-level selection → per-harness selection |
| Component options | Catalog defaults → selection options; versions must exist in the catalog |
| Container environment | Default `podman` → machine environment → benchmark environment → `execution.environment` |
| Global trial concurrency | Default 4 → run `max_workers` → `execution.max_workers` |
| Benchmark `max_workers` | Separate existing evaluator setting; does not alter global trial concurrency |
| Offline mode | Default false → benchmark `offline` → `execution.offline` |
| Model alias | `models.yaml` alias selects wire ID; alias fields override `proxy.yaml` fields for that wire ID |
| Proxy settings | Selected proxy backend definition → `overrides.proxy` |
| Machine settings | Supported keys are `environment` and `cache_dir`; endpoints and credential references belong in model definitions |

Declaring both a top-level component list and the same shared override is an
error even when per-harness selections exist. Named extension selections conflict
with nonempty effective inline integration settings; explicit replacement with
an empty inline list removes that inherited conflict. Inline skills are validated
for known fields, duplicate identities, source types, and boolean requirements.

Resolution rejects unknown run/machine/selected benchmark/harness/model/proxy
fields, invalid schemas, malformed mappings, invalid worker/timeout values,
non-boolean offline/TLS flags, invalid Claude launch profiles, and nonpositive or
nonfinite monetary budgets. Selected named assets validate their catalog version
and option keys. Errors identify configuration fields before preparation. Exact
binary availability, runtime compatibility, and asset contents remain preparation
checks. MCP delivery remains deferred.

Current source CLI and resolver tests cover these rules without container startup
or model calls. H01-04 now makes the legacy runner consume the resolved settings.
Benchmark grading parity remains phase 5.

## Implemented path rules

Schema v1 support has been removed by user decision (September 17). Existing
files without a version now use v2 rules. Rebase relative paths against their
declaring files; a previous working-directory path is no longer preserved.

| Setting | Behavior |
| --- | --- |
| CLI `--config`, `--config-dir` | Relative to invocation working directory; explicit registry directory wins |
| Run-file `config_dir` | Relative to run file |
| Registry discovery without `config_dir` | Start beside run file (or working directory without a file), walk ancestors; nearest `.acb` then `config` containing a registry wins |
| Run `output_dir` | Relative to invocation directory; absolute paths unchanged |
| Benchmark `path`, `cache_dir`, `scarfbench_dir`, `praxis_ai_repo`, `task_repo_cache_dir`, `benchmark_cache_dir` | Relative to `benchmarks.yaml`, or run file when declared/overridden there |
| Machine `cache_dir` | Relative to `machine.yaml`; explicit benchmark cache wins |
| Skill `source_path`, RTK `binary_path` and option defaults | Relative to declaring catalog, harness registry, or run file |
| Explicit paths in `scarf_binary` and proxy `binary` | Relative to declaring registry or run override; bare command names retain PATH lookup |
| Dataset `task_root` | Dataset-internal prefix, never a host filesystem path |
| Container `workdir`, harness/integration `python_path` | Container paths, never rebased onto host directories |

Home-directory expansion is supported for host paths. Interpreter normalization
preserves virtualenv symlinks. If no cache is declared, the cache is `.cache`
under the normalized output directory. Discovery records loaded registry files
in resolved `sources`. Optional registry files may be absent.

The proxy `binary` setting is retained compatibility configuration; current
container execution does not launch that host executable. The legacy
runner consumes normalized benchmark settings under H01-04; path and handoff
tests establish configuration behavior, not grading parity.
The initial outside-checkout test imports current source in a fresh process.
H01-06 also verifies a fresh wheel installation outside the checkout.

## Execution handoff checkpoint

`acb run` resolves once and dispatches on the resolved backend. Harbor's
`run_plan` accepts that immutable plan directly. The legacy runner uses copied
prepared harness/benchmark settings, the resolved wire model ID, cache/output
paths, task selection, and worker count; workers do not read registries or merge
run overrides again. Requested/resolved artifact completion is recorded under H01-05.

`acb prepare` dispatches through the same preparation entry point. Harbor retains
its runtime inspection and asset preparation. Legacy preparation validates the
settings and local integration prerequisites; image/harness/skill setup still
happens per instance, so it reports `probed: false` and pending setup checks.
Legacy execution requires Podman, one attempt, and online mode. Automatic RTK artifact preparation now shares the verified cache/build path on
both backends; explicit binary/checksum overrides remain supported. Legacy
preparation freezes the selected image architecture, then task-container setup
checks actual architecture and verifies Claude hook Python >=3.8 before installing
RTK. The per-instance `rtk-runtime.json` records effective settings and failures;
standalone legacy prepare still reports these runtime checks as pending.
The packaged Caveman response skill now uses shared preparation on both paths;
`skills: [caveman]` no longer needs an explicit local skill workaround.
Unsupported combinations fail before loading datasets or creating run output.
These are limits of the retained execution backend, not schema-version fallbacks.

## Saved configuration records

Both backends write these records before measured execution:

- `requested.json`: the run configuration snapshot captured during resolution,
  including declared selections, overrides, model alias, and source file. Defaults
  from the parser are included; this is a structured snapshot, not the original
  YAML text. Inline credential keys and environment maps in this metadata are
  redacted.
- `resolved.json`: the effective prepared execution document, including normalized
  settings, model alias and wire ID, sources, preparation evidence, requested run
  ID, effective run ID, and absolute `run_dir`. `output_dir` remains the parent
  directory for runs. Harbor passes this exact file to the worker; legacy workers
  consume copies of its settings.

Model credentials remain `key_env` references; creating these records does not
read their environment values. Supported model configuration uses references,
not inline credentials. Requested metadata redaction does not introduce support
for arbitrary inline secrets in runtime configuration; MCP delivery remains deferred.

Preparation cache directories retain the requested snapshot plus
`resolved-input.json` (the normalized input sent to the preparation worker).
`prepared.json` retains preparation output. Existing historical artifacts are not
rewritten. Run-directory collisions preserve the original request and record the
new effective identity without overwriting previous results.

## Execution semantics and reproducibility

Resolution first validates experiment selections and exact harness/component
settings, then combines the selected Harbor task manifest with provider and
runtime requirements into immutable per-task/per-harness plans. Task workdir,
user, OS, resources, and language requirements are validated constraints. Unknown
runtime details remain pending until preparation probes; `resolve` starts no
containers and makes no model calls. Execution consumes the resolved plan without
re-merging settings. Reject known incompatibilities across the selected matrix
before measured execution; preparation confirms runtime requirements before agent calls.

The legacy runner builds a global queue of every `(harness, instance)` pair and
uses one `ThreadPoolExecutor(max_workers=cfg.max_workers)`. The example claiming
workers are per harness and all harnesses execute sequentially is incorrect.

Under Harbor, map `max_workers` to its global trial concurrency and remove the
duplicate ACB scheduler. Document it as the maximum number of concurrent measured tasks across
the run. With 10 instances and 4 harnesses, users should see 40 tasks and the
selected concurrency before execution. With `max_workers: 1`, tasks are sequential.
Do not add an unrelated concurrency control while retaining unclear old semantics.

Pin harness/catalog versions for baseline and treatment. Disabling an extension
must not switch Claude's launch profile, task prompt, tool permissions, or runtime
image. Report genuinely necessary treatment differences, such as recovery tools.
Cache warmup/reset and generation settings should be recorded; do not promise
independent model-server caches merely because task containers are isolated.

Introduce an experiment variant/matrix feature only after shared config resolution
is stable. It could express baseline, RTK, Caveman skill, and combined arms without
duplicating task/model settings, but is outside the initial parser refactor.

## Commands that improve the experience

- `acb init`: create a small starter run and optional machine-settings template;
  leave existing files untouched.
- `acb list harnesses|models|skills|mcp|extensions|benchmarks`: show configured names,
  resolved versions, and support status rather than requiring YAML inspection.
- `acb resolve --config <file>`: show requested/resolved components, exact versions,
  adapter choices, data paths, runtime requirements, task count, and concurrency.
  This performs no model calls or container startup.
- `acb prepare --config <file>`: populate verified artifacts/data/runtime caches.
  Downloads/builds happen explicitly here or through the same preparation during run.
- `acb run --config <file>`: validate, show the resolved experiment, prepare what is
  missing, execute, and save requested/resolved provenance with reports.

Errors should identify the config field and concrete pair, for example:
`extensions.rtk: claude-code with launch_profile=bare cannot load required hooks`.
Warnings must distinguish unknown cost, intentional payload passthrough, and an
extension that loaded but was never used.

## Implementation order

The sequence below describes UX dependencies. Use the combined Harbor delivery
sequence for execution work: shared resolution, Docker/Podman/Praxis spike,
four-harness runtime bridges, shared component pilots, then legacy execution
removal. Structured v2 syntax is supported; experiment arms remain a later feature.

1. Central resolver and validation, shared top-level selections, per-harness
   overrides, exact defaults, and a requested/resolved artifact.
2. Category-scoped catalogs, RTK preparation and compatibility gates, and shared
   Caveman skill/MCP setup through existing adapters.
3. Benchmark runtime requirements, eliminating copied workdir/conda/image details.
4. Clear concurrency documentation plus resolve/list/prepare commands.
5. Model aliases/registry, project/machine configuration discovery, and optional
   pricing defaults.
6. Only then consider experiment arms.

Use one schema-v2 parsing and normalization path. Reject unsupported schema
versions and retired fields. Tests cover merge precedence, category
identity, unknown fields, identical preflight/execution resolution, paths, package
defaults, and baseline parity. The acceptance example is one portable Scarfbench
run across all four harnesses with shared RTK/Caveman selections and attributable
results on both Docker and Podman. Require all four harnesses, equivalent task
grading, complete container service cleanup, and no host execution fallback.
This review proposes configuration behavior; it does not implement it.
