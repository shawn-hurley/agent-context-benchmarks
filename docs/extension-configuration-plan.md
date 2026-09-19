# Shared extension configuration

> September 17 revised scope: Phase 4 now covers shared RTK/Caveman preparation
> and functional extension support on both Harbor and existing benchmark runners.
> Basic context compression/recovery/protocol acceptance belongs to Phase 4.
> Phase 5 benchmark conversion and runner retirement are deferred follow-on work.
> Phase 6 retains metrics, multi-step lifecycle, RTK/context composition and
> measured effectiveness. See the [current tasks](harbor-migration-tasks.md#phase-4-shared-extensions-on-harbor-and-existing-runners).
> Historical delivery sequences below are superseded where they conflict.


Status: partially implemented as of September 16, 2026. Shared resolution and
per-task asset preparation exist; live RTK/skill treatments and measured Caveman
compression remain open. See the [implementation checkpoint](harbor-migration-status.md)
and [task checklist](harbor-migration-tasks.md) for evidence and remaining gates.

The four RTK adapters exist; this plan replaces the requirement
to expose adapter modes, binary paths, hashes, and launch profiles in each run
configuration. It does not change compression behavior or promise token savings.

Companions: [configuration UX](configuration-ux-plan.md) and
[combined Harbor architecture/delivery](../HARBOR_PLAN.md). All four harnesses
are required, with Docker and Podman container-only execution. The host may run
the resolver, preparation tooling, and controller, but measured agents, Praxis,
MCP services, and extension runtimes execute in containers. Local model endpoints
remain supported. The combined plan governs migration and execution acceptance.

## Intended run configuration

```yaml
run_id: scarfbench-rtk
benchmark: scarfbench
harness: [goose, pi, opencode, claude-code]
model: google-vertex-anthropic/claude-haiku-4-5
proxy: praxis
extensions: [rtk]
limit: 1
max_workers: 1
output_dir: runs/scarfbench-rtk
overrides:
  benchmark:
    instances:
      - layer: business_domain
        app: cart
        source: jakarta
        target: quarkus
```

Shared extension syntax is implemented in the resolver, but the legacy ScarfBench
execution path does not yet consume the full normalized plan; this example still
requires that migration. The corresponding baseline uses `extensions: []` with a
different run ID. Versions, launch profile,
tool definitions, runtime image, and budgets must otherwise match.

Allow a mapping when an extension has user-facing options:

```yaml
extensions:
  - name: rtk
    version: "0.48.0"  # Optional override of the catalog default.
    options: {}       # Validated against this extension's option schema.
```

Do not require users to select shell-wrapper versus native hooks. Do not add a
failure-output option until an implementation and compatibility tests exist.

## Existing constraints

- `RunConfig.harness` already accepts a list, but `overrides.harness` is applied
  uniformly. Different RTK modes and version pins currently require separate files.
- `IntegrationManager` already installs, activates, verifies, collects, and cleans
  up repository-owned integrations. Keep that lifecycle rather than inventing a
  second plugin runner.
- RTK uses a Goose shell wrapper, Pi event handlers, an OpenCode plugin, and Claude
  hooks. The implementation differences remain real even when hidden from YAML.
- Claude's `--bare` profile skips hooks. Baseline parity requires an independent
  choice of launch profile, not changing the profile only when RTK is enabled.
- Scarfbench uses `/work` without conda; SWE-bench normally uses `/testbed` and its
  testbed conda environment. Native launchers now accept these settings, but the
  benchmark should eventually supply them rather than each example copying them.
- The existing Scarfbench RTK image provides Python and a pseudo-terminal utility.
  Its Python shim at a conda-looking path is transitional, not the desired runtime
  contract for future extensions.

## Resolution and defaults

Add a single pure resolver used by both preflight and execution. First resolve
experiment selections and harness settings without starting containers or calling
a model. Then resolve the Harbor task manifest and compose immutable plans per
task/harness with provider/runtime constraints. Validate known incompatibilities
across the selected matrix before measured execution. Preparation probes verify
runtime prerequisites before agent calls; unknown requirements remain pending in
`resolve` output. Execution consumes the result rather than merging dictionaries again.

Precedence for harness settings:

1. Versioned built-in harness defaults.
2. Harness registry settings.
3. Existing shared `overrides.harness`.
4. Optional `overrides.harnesses.<name>` for advanced per-harness tuning.

Harbor task workdir, user, OS, resource, and language requirements are validated
constraints, not overridable harness defaults. Incompatible explicit settings
fail clearly. Keep separate resolved environment plans for heterogeneous tasks.

Extension requirements validate the result; they must not silently overwrite an
explicit setting. Normalize shared extension selections, resolve the compatible
adapter, and reject unsupported version/architecture combinations up front.
Validate unknown names, option keys, duplicates, and exclusive-resource conflicts.
An incompatible selected harness fails preflight for the entire run; do not quietly
skip its extension or change its version.

Use reviewed exact versions as defaults for both baseline and treatment. Start
with Goose 1.50.1, Pi 0.84.3, OpenCode 1.18.22, Claude Code 2.1.241, and RTK 0.48.0,
subject to live compatibility gates. Goose 1.50.1 still needs the RTK live smoke
gate; inspecting its baseline binary is not that gate.

Choose a canonical isolated Claude launch profile independently of extension
enablement. Keep the legacy bare profile available explicitly, and reject RTK
with that profile. Introduce this default change through the new configuration
path and document migration; do not silently alter old run configurations.

## Catalog and adapter responsibilities

Ship a versioned repository-owned catalog as package data. For each extension it
declares its category, default version, option schema, supported harness versions
and architectures, artifact resolver, runtime prerequisites, exclusive resources,
and adapter identifier. The catalog selects implementation details; run YAML selects
the extension and meaningful options.

Separate extension-wide artifact/runtime logic from harness-specific activation.
Preserve the current typed `IntegrationActivation` until a concrete additional
adapter requires a more general type. Harnesses own final launch composition;
extensions contribute reviewed activation assets and environment additions.

Adding another extension requires a registered implementation, catalog entry,
compatibility evidence, and lifecycle tests. YAML does not load arbitrary Python
paths or execute install snippets. Initially keep implementations in the repository;
installed-package discovery can be a separate future feature if needed.

Maintain skills, MCP servers, execution integrations, and model middleware as
distinct categories internally. Shared top-level selections are resolved across
all selected harnesses. Identify components by category plus name: `skills/caveman`
and `extensions/caveman` are distinct, and may be selected together. Reuse the
existing skill and MCP lifecycles instead of implementing them as new extensions.

## Adding Caveman

Upstream distinguishes [response-style instructions](https://github.com/JuliusBrussee/caveman/blob/main/skills/caveman/SKILL.md)
from [context compression through a local runtime](https://github.com/JuliusBrussee/caveman/blob/main/docs/technical/product-model.md).
These affect different token channels and should be separate experiments. The
earlier suggestion that Caveman is simply middleware was incomplete.

### First delivery: shared Caveman skill

Register the pinned Caveman response skill in the skill catalog and reuse the
existing `SkillInstaller` and harness skill activation. One shared config enables
it alongside RTK:

```yaml
harness: [goose, pi, opencode, claude-code]
skills: [caveman]
extensions: [rtk]
```

The catalog owns the pinned instruction asset and its hash. No binary, global
installer, host hooks, browser, memory service, or upstream agent wrapper is needed
for this path. Deliver the same skill content through each harness's reviewed
skill mechanism and existing activation hints, preserving benchmark task
instructions. Check actual loading and compliance rather than equating installation
with activation. Any adapter-specific prompt contribution must be recorded.

Support an optional intensity setting (`lite`, `full`, `ultra`) only after checking
the pinned upstream rules. Recommend `lite` as the initial catalog default, clearly
recorded as a harness experiment choice rather than upstream's default. Preserve
code, exact errors, identifiers, and task requirements. Do not silently rewrite the
benchmark problem into terse language. The instruction changes assistant narration,
not shell results or source files; model compliance still needs testing.

Verification means the instruction was actually delivered at the intended priority
for all four harnesses, with its text/hash and any harness adaptation recorded.
Shorter answers alone do not prove correct activation or preserved task quality.
Measure added instruction input, generated prose, generated code/tool arguments,
model requests, cached reads/writes, task resolution, and total provider usage.
For longer tasks, shorter narration may also reduce later history size, but the
net benefit must include instruction overhead and any changed agent trajectory.

### Second delivery: context compression

Use `caveman` under `extensions` for the engine/proxy, distinguished from the
same name under `skills`. Selecting `skills: [caveman]` must not silently enable
the proxy or its bundled tools; selecting the extension must not silently enable
the response skill. Investigate the
pinned runtime's API and select a supported version before implementation; current
upstream documentation is discovery evidence, not a compatibility guarantee.

Proposed route:

```text
agent -> Caveman local proxy -> Praxis -> configured model
```

RTK, if selected, compresses shell results before they reach the agent. The Caveman
proxy would then operate on eligible content in the agent's outbound requests.
Praxis measures model usage after both transformations. Keep credentials, model
choice, Anthropic/OpenAI protocol, tool-call IDs, streaming, cancellation, and
provider cache metadata intact. Vertex routing remains Praxis's responsibility.
Start one isolated runtime/recovery store per measured instance and collect evidence
before cleanup. Do not use a global Caveman installation or scan host agent history.

The [engine's documented recovery contract](https://github.com/JuliusBrussee/caveman/blob/main/engine/README.md)
stores originals before lossy replacement and provides a retrieval handle. Verify
that contract under the pinned version. Every enabled harness needs a tested way
to retrieve the original inside its container; a handle printed in a summary is
insufficient. Any required tool or MCP registration is composed internally through
the harness adapter and recorded as part of the treatment's tool set.

Begin with a narrowly scoped, tested policy: routine successful logs and repetitive
data; preserve failed-test diagnostics and relevant source bodies. Do not assume
those switches exist upstream. If the pinned API cannot enforce the policy, narrow
the integration or leave that content unchanged. Test error preservation explicitly,
since our RTK investigation demonstrated agents repeatedly rerunning tests to recover
omitted errors instead of using recall. Measure retrieval input and extra requests.

Runtime availability failure is distinct from deliberate original-byte passthrough.
A required proxy that cannot start fails the run. Verified per-payload passthrough
can continue, but must be recorded and must not claim compression. A wrapper that
quietly launches direct cannot count as successful required extension activation.

### Composition and validation

Support RTK plus Caveman instructions first: shell activation and additive system
instructions occupy different resources. Define instruction order and reject
conflicting replacements of the same launch/config fields. Compose Claude settings
into one file if multiple extensions require them; do not overwrite settings.
RTK plus Caveman context compression needs a separate gate to test double
compression, recovery availability, critical diagnostics, and metadata preservation.
Do not turn this combination on by default.

Experiment arms, with matched exact harness versions and independent run IDs:

1. Baseline with neither extension.
2. RTK only.
3. Caveman response instructions only.
4. RTK plus Caveman response instructions.
5. Caveman context runtime only, after its compatibility gate.
6. RTK plus Caveman context runtime, after the composition gate.

For response style, add a plain concise-instruction control to distinguish Caveman
from generic brevity. For the runtime, add a no-transform/record control with the
same recovery tools and prompts, if supported, to isolate runtime/tool overhead.
Use repeated paired runs on the same instances. Test local Qwen and Haiku separately;
neither compression success nor model capability should be inferred from the other.
Report request count and resolution alongside tokens. Keep engine estimates labeled
as estimates, independent of provider-reported usage.

## Harbor lifecycle and service ownership

The custom Harbor agent bridge invokes existing harness/skill/MCP setup and
`IntegrationManager` installation, activation, verification, and service startup.
Use an environment transport for execute/stream/upload/download and service
operations on Docker and Podman; do not depend on Podman container names or pods.
Collect evidence and stop middleware before Harbor removes the relevant services,
including setup failures, timeout, cancellation, and separate-verifier transitions.
Harbor owns task lifecycle and grading; extension evidence is a separate result.

Praxis is a dedicated container per trial with a reusable image, fresh endpoint
configuration, and isolated logs. Caveman context, once verified, adds an isolated
container runtime/recovery store. Proxy attachment, health checks, configuration
transfer, flushing, and teardown must pass both backend gates. Keep credentials
out of agents and persisted non-secret configuration. Recovery tools/MCP services
run inside the trial's container environment, never against host agent history.

## Artifacts and container environment

Remove mandatory machine-specific RTK paths and hashes from normal run configs.
Resolve verified Linux ARM64/x86-64 artifacts into the run cache. Reuse the pinned
RTK source commit and locked build already used by `Dockerfile.rtk-smoke`, or
verified release artifacts with declared per-architecture checksums. Never use an
unverified moving release. Keep explicit local artifact overrides as an advanced
development path and record their identity.

Add a preparation command that resolves/builds required artifacts before generation.
The normal run command invokes the same preparation when necessary; offline mode
fails with an actionable missing-artifact message. Serialize cache population and
publish verified artifacts atomically for concurrent instances.

Have benchmarks declare working directory and language environment. Have extensions
declare prerequisites such as a Python interpreter. Compose these into a verified
generation image/runtime plan without enabling unrelated harness discovery or
permissions. Replace Claude's hardcoded interpreter dependency with a verified
runtime path supplied by activation. Keep JDK/Maven for Scarfbench and preserve
SWE-bench's test environment. Include the runtime image identity in baseline parity.

Keep verified asset caching as the first optimization, then add prepared images
where worthwhile. Key those images by task base/build identity, architecture,
harness version, prerequisite/asset identities, and preparation recipe revision.
Preserve task dependencies and grade semantics. Cache installation artifacts,
not mutable agent histories, task outputs, runtime recovery stores, or credentials.
Native settings, activation, and evidence paths are fresh per trial; warm caches
still require installed-version and loading checks. Baseline/treatment use matched
runtime images and launch profiles even when an extension is disabled.

## Visibility and evidence

Add `acb resolve --config <file>` to display the non-secret resolved plan without
starting containers or calling the model. It shows versions, adapters, interception
scope, launch profiles, runtime requirements, and missing artifacts.

Save requested and resolved configuration, catalog revision, image/binary/asset
hashes, generation settings, and adapter verification in every run. Keep preflight
evidence separate from measured agent activity. Distinguish installed, loaded,
intercepted, and compressed: OpenCode loading RTK without calling Bash must not
count as demonstrated compression.

Continue reporting model usage downstream of extensions and retain provider token
bucket semantics. The observed Vertex inclusive-input accounting issue needs its
own correction; this configuration refactor must not conceal it. Compare task
outcomes, model requests, and total usage rather than RTK command estimates alone.

## Delivery sequence

The sequence below describes extension dependencies. Integrate it through the
combined Harbor plan: shared resolution, Docker/Podman/Praxis spike, four-harness
runtime bridges, shared RTK/Caveman skill pilots, then legacy execution retirement.
Caveman context and RTK composition retain separate later compatibility gates.

1. **Shared resolver and schema.** Add top-level extension selections, optional
   per-harness overrides, exact defaults, and the resolve command. Centralize
   preflight/execution configuration. Retain legacy low-level fields through
   explicit normalization; reject ambiguous simultaneous legacy/new selections.
2. **RTK catalog and artifact preparation.** Resolve modes and assets internally,
   remove normal binary-path/hash requirements, and verify the Goose 1.50.1 gate.
3. **Benchmark runtime contract.** Supply `/work` versus `/testbed`, conda behavior,
   Python, and pseudo-terminal requirements from the resolved environment. Remove
   the Scarfbench Python shim workaround once the runtime contract replaces it.
4. **Examples and migration.** Replace the four Scarfbench files and orchestration
   script with one multi-harness example and one normal `acb run` command. Publish
   matched baseline/treatment examples with identical resolved environments.
5. **Shared skills and Caveman.** Reuse skill installation/activation with pinned
   catalog entries, per-harness composition checks, and loading/compliance tests.
   Provide one multi-harness Caveman skill config and one RTK-plus-Caveman skill config.
6. **Acceptance.** Run four controlled live integration gates and a fresh-checkout
   Scarfbench pilot across all four harnesses. Preserve complete reports and grade
   independently. Benchmark resolution and extension activation are separate results.
7. **Caveman context discovery and pilot.** Pin and verify the runtime/protocol,
   recovery path for every harness, failure-diagnostic preservation, and proxy
   metering. Start with runtime-only arms before enabling RTK composition.

## Required checks

- New and legacy selection parsing, merge precedence, and clear conflict errors.
- Different adapters/versions resolve correctly from one shared RTK selection.
- Baseline/treatment launch parity, including Claude isolation and runtime image.
- Unsupported harness/version/architecture fails before measured execution or model calls.
- Repeated resolution is deterministic and does not mutate registry settings.
- Linux ARM64/x86-64 artifact verification, cache races, offline preparation, and
  packaged resource availability outside the source checkout.
- Native hook composition, permissions, failure markers, and evidence collection.
- Scarfbench launches in `/work` without conda; SWE-bench defaults still work.
- Multi-harness output and parallel instances keep extension state per instance.

Complete means a fresh checkout can run one Scarfbench configuration selecting
all four harnesses, RTK, and the shared Caveman skill on Docker and Podman without
exposing adapter mechanics or host-specific
artifact paths, while retaining reproducible resolved settings. Verify container
service cleanup and task grading separately, with no host execution fallback. It does not mean
RTK must reduce tokens on every trajectory. Failure-diagnostic preservation and
recall guidance are separate experiments, not bundled into this refactor.
