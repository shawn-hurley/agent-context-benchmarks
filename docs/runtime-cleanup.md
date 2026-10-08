# Runtime cleanup decisions and implementation

Approved October 8, 2026. This is the implementation record for the comprehensive review of obsolete runtime paths, redundant configuration and bundled assets. All 32 findings were discussed and approved; F20 and F32 retain active behavior. YAML remains schema 2, while prepared worker protocol changes to 2. Re-prepare saved plans.

Implementation and acceptance checks are complete. No archived run directories or historical experiment results are rewritten. Model aliases and provider/pricing identities remain intact. The required native-grader Podman shim remains supported.

## Configuration and selection

### F01 — Superseded public benchmark.container_backend

**Decision:** Remove public setting.

Reject during resolution with execution.environment guidance; migrate maintained examples and tests; retain internal engine value used by native graders.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_harbor_benchmark_tasks.py.

### F02 — Nonfunctional benchmark.praxis_ai_repo

**Decision:** Remove; use custom image.

Reject the public setting; remove path normalization and obsolete acceptance coverage; correct examples; document benchmark.praxis_image as the custom-build selection route.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_provider_image_cache.py.

### F03 — Unused host executable settings and proxy configuration plumbing

**Decision:** Remove unused settings.

Remove public harness.binary, proxy.backends.praxis.binary, overrides.proxy and unused proxy_config/backend_config plumbing; update examples and tests. Preserve pinned harness versions, Praxis image customization, RTK binary_path and native grader executables.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_harbor_config.py.

### F04 — Accepted but unused harness model/tuning/interpreter fields

**Decision:** Reject unsupported fields.

Remove public harness fields model, provider, thinking, max_tokens, temperature and python_path; preserve RunConfig.model and extension-specific interpreter controls; add focused rejection coverage and correct documentation.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py.

### F05 — Obsolete benchmark schema fields and late rejection of removed Python override

**Decision:** Reject all four.

Remove public namespace, scarfbench_dir, git_url and python fields; reject during resolution; remove obsolete path normalization/docs/example acceptance. Preserve benchmark_cache_dir, supported dataset/path/task_repo options and swebench_python.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py.

### F06 — Three concurrency configuration locations

**Decision:** execution.max_workers only.

Make execution.max_workers the only public YAML concurrency setting with default four; remove benchmark.max_workers and top-level RunConfig.max_workers input; translate CLI --max-workers to execution; migrate examples, starters, interactive editor and tests. Retain one resolved global worker limit.

**Implementation:** Implemented. **Acceptance:** test_interactive_run.py; test_cli_usability.py.

### F07 — Single-value public runtime/proxy selectors

**Decision:** Remove public selectors.

Remove benchmark.execution_backend, RunConfig.proxy and CLI --proxy from public configuration; migrate catalogs, starters and examples; retain fixed Harbor/Praxis identities in resolved plans and reports; provide clear early diagnostics for removed selectors.

**Implementation:** Implemented. **Acceptance:** test_runner_retirement.py; test_runtime_cleanup.py.

### F08 — Two public extension interfaces with different validation

**Decision:** Named extensions only.

Reject user-supplied execution_integrations and model_middleware; migrate RTK/Caveman examples and active guides to named extensions/options; keep internal resolved integration categories; remove legacy selection-conflict branches. Additional supported version pairs require explicit tested catalog support.

**Implementation:** Implemented. **Acceptance:** test_harbor_config.py; test_execution_plan.py.

### F09 — Legacy inline skill/MCP definitions in harness configuration

**Decision:** Named selections only.

Require named selections/options for user-configured skills and MCP at every supported selection layer; move maintained custom definitions to skills.yaml/mcp.yaml; remove legacy inline parsing/conflict logic; preserve resolved full definitions, Harbor task-provided skills/MCP and installer validation.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_harbor_components.py.

### F10 — Two model registries and legacy proxy.yaml loading

**Decision:** models.yaml only.

Use models.yaml as the sole model registry for normal resolution/listing/editor/discovery; migrate maintained model definitions and preserve aliases/wire IDs; remove proxy.yaml registry loading, merge paths and unused model_spec helper. Review reset_config one-time legacy import separately.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_harbor_config.py; test_cli_features.py.

### F11 — Unused reports_cache model flag

**Decision:** Remove the flag.

Remove reports_cache from ModelSpec and accepted model fields/examples; rely on captured response metrics for cache accounting; retain local OpenAI/MLX and Anthropic cache normalization tests; correct cloud-only documentation.

**Implementation:** Implemented. **Acceptance:** test_usage.py; test_runtime_cleanup.py.

## Runtime and artifacts

### F12 — Raw Podman adapter execution alongside Harbor transport

**Decision:** Use Harbor transport throughout.

Require EnvironmentTransport for harness/integration/skill adapter command/upload/download operations; migrate opt-in RTK smoke checks to Harbor-managed environments and unit fixtures to transport doubles; remove container-name fallbacks and raw subprocess streaming/heartbeat implementation while preserving ANSI filtering used by Harbor. Preserve controller-side native-grader engine commands and all deterministic paired smoke assertions.

**Implementation:** Implemented. **Acceptance:** test_harness_workdir.py; test_rtk_claude.py; test_rtk_native.py; opt-in test_rtk_native_live.py.

### F13 — Retired pod lifecycle code and active pod terminology in terminal UI

**Decision:** Remove handlers and rename.

Remove unused cleanup_all_pods/setup_interrupt_handler and orphaned pod-specific tracker helpers/state; rename active pod_name fields/parameters/log labels/column to trial_name/Trial; preserve Harbor worker cancellation and current progress semantics.

**Implementation:** Implemented. **Acceptance:** test_harbor_progress.py.

### F14 — Unused standalone recording proxy and stale reference claims

**Decision:** Retire it.

Remove acb.proxy.record_server and its orphaned parser-only test; retain maintained Praxis/usage/cache normalization coverage; extend retirement/package absence checks and remove unsupported reference/parity claims.

**Implementation:** Implemented. **Acceptance:** test_runner_retirement.py; installed wheel.

### F15 — Empty and unimplemented runtime placeholders

**Decision:** Remove runtime placeholders.

Remove empty acb.harnesses.stubs, unregistered nonfunctional acb.integrations.tamp, LiveCodeBench stub/class/import/benchmark registry/example entry; retain roadmap intentions outside runtime package if needed; extend installed-package/retirement checks.

**Implementation:** Implemented. **Acceptance:** test_runner_retirement.py; installed wheel.

### F16 — Legacy bare Claude Code launch mode and conflicting defaults

**Decision:** Remove bare entirely.

Standardize Claude Code launches on the current isolated-hooks behavior; remove bare branches/defaults/tests and public profile choice after migrating maintained explicit settings; retain profile identity in resolved execution/report provenance and matched RTK baseline/treatment behavior. Reject old bare inputs with clear guidance; do not reinterpret archived experiment results.

**Implementation:** Implemented. **Acceptance:** test_rtk_claude.py; test_harbor_components.py.

### F17 — Legacy instances result aliases and missing-trial path mismatch

**Decision:** Direct trial directories only.

Use a shared canonical <harness>/<trial-id> path for views/imported evidence/missing placeholders/job summaries/report loading; stop writing instances compatibility aliases for new runs; migrate tests and active documentation; preserve native grader's own internal staging until separately reviewed and leave archived run directories unchanged. Migrate report.py aggregation, per-trial metrics updates and integration manifest enumeration to canonical direct directories; aggregate only scheduled/imported trial identities and keep harness-level logs/report outputs.

**Implementation:** Implemented. **Acceptance:** test_harbor_trial_view.py; test_harbor_results.py; test_harbor_execution_status.py.

### F18 — Old-report identity recovery from unverified external task files

**Decision:** Use saved provenance only.

Remove HarnessReport.benchmark_contracts external snapshot backfill and comparator enrichment; use embedded current contracts/revision/checksum identities only; show non-comparable results when identity is insufficient; replace legacy recovery coverage with missing-identity and saved-provenance behavior checks.

**Implementation:** Implemented. **Acceptance:** test_benchmark_contract.py; test_comparison.py.

### F19 — ScarfBench grading API retains retired runner batch/discovery/tracker behavior

**Decision:** Use single-run grading API.

Introduce explicit ScarfBench.grade_run(instance_id, run_dir, artifact_dir) -> bool; migrate Harbor grading child and parity fixtures; remove evaluate batch/per-instance discovery and tracker branches, unused run_id/Prediction scaffolding where no callers remain. Preserve conversion format, native scarf validate behavior, compile/deploy/smoke outcomes, Maven instrumentation, immutable images/logs and process-group cancellation.

**Implementation:** Implemented. **Acceptance:** test_harbor_benchmark_tasks.py; test_scarfbench_validation.py.

### F20 — ScarfBench validation-harness compatibility is active current support

**Decision:** Keep active adapter support.

Retain generation of missing native validator inputs and repair of ACB-owned generated Makefiles, with upstream files taking precedence; preserve current native CLI 0.1.2 compatibility, immutable-image grading and benchmark test semantics; clarify active adapter role in documentation.

**Implementation:** Retained with clarified scope. **Acceptance:** test_scarfbench_validation.py.

## Assets and maintenance

### F21 — Duplicate root and packaged Praxis Rust/build assets

**Decision:** One packaged copy.

Keep acb/assets/praxis as canonical tracked source; remove root Containerfile and root praxis-vertex-anthropic duplicate crate; update canonical developer build/test commands and cleanup guide acceptance to installed-resource checks without duplicated-source drift machinery.

**Implementation:** Implemented. **Acceptance:** 16 canonical Rust tests; installed wheel.

### F22 — Config reset imports retired layouts and historical prerequisite assets

**Decision:** Current inputs only.

Keep reset utility, whole-tree archival, validation-before-install and rollback. Preserve current models.yaml/machine environment/costs.yaml and valid current external ScarfBench prerequisite path. Remove proxy.yaml and phase6 registry imports, historical RH pilot/deviation bundle lookup and hard-coded rgctl-version cache lookup. Document explicit prerequisite paths/assets; test no dependency on old layouts/historical runs and lossless backup.

**Implementation:** Implemented. **Acceptance:** test_feature_configs.py.

## Validation and remaining compatibility

### F23 — Harness-specific controls are accepted and ignored by other adapters

**Decision:** Reject mismatches.

Validate applicable public fields per selected harness at all registry/inline/override layers. max_budget_usd is Claude Code only; max_tool_repetitions is Goose only. Shared overrides must be supported by every selected harness; error directs users to overrides.harnesses. Keep working universal controls and supported version behavior. Remove obsolete fields first per F04/F16, add side-effect-free acceptance/rejection and mixed-harness tests.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py.

### F24 — Normal harness cache reuse accepts legacy entries without checksum manifests

**Decision:** Require manifest.

Treat harness entries missing .acb-cache.json as not ready, repopulate through existing locked atomic download/extraction online, and fail offline with preparation guidance. Preserve current corruption errors for invalid/existing manifests, version/startup verification, publication rollback and no deletion of unrelated caches. Test all four harnesses: old format online repopulates once and publishes manifest; offline old format never downloads; valid manifest reuses; corrupt manifests/files fail.

**Implementation:** Implemented. **Acceptance:** test_harness_cache.py.

### F26 — Duplicated shared helpers, lockfiles and pinned rgctl workflow bundles

**Decision:** Share pinned assets.

Keep canonical shared helper sources and a versioned pinned rgctl bundle; bundled workflow resolution/export materializes declared destination paths into complete task snapshots. Shared source bytes participate in resolved workflow hashes, preparation drift validation, installed-wheel package checks, skill provenance and link checks. Retain distinct workflow image recipes and instructions; preserve custom workflow asset path validation. F28 supersedes sharing no-think-proxy: remove it entirely rather than extracting it.

**Implementation:** Implemented. **Acceptance:** test_migration_workflows.py; test_runtime_cleanup.py; installed wheel.

### F27 — Unused rgctl developer build recipe and skill override directory

**Decision:** Remove them.

Delete Dockerfile.rgctl-build and acb/skills-overrides/rgctl; keep distinct active bundled workflow rgctl build stages and supported local skill registry mechanism. Update any maintained links if found; preserve archived historical evidence.

**Implementation:** Implemented. **Acceptance:** test_runner_retirement.py; installed wheel.

### F28 — Workflow/model-specific Qwen pilot request and tool adapter

**Decision:** Remove pilot adapter.

Remove workflow agent_adapter public field and bundled selections, hard-coded Qwen model triggers, local proxy startup/readiness/teardown, request-adapter artifact generation, no-think-proxy.py copies and workflow image COPY/assets. Remove pilot-specific /no_think prompt directives so no implicit suppression remains; preserve benchmark/workflow requirements and other experimental modes. All workflows route through standard HarborPraxis and configured integrations; no new extension added. Test bundled exports and agent routing/lifecycle without pilot proxy, and early rejection of old agent_adapter field. Remove pilot-only shell/write/edit and no-todo restrictions from bundled prompts while keeping substantive stage/application requirements.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_migration_workflows.py; installed wheel.

### F25 — Checkout-local Docker shim fallback overlaps current per-run native grader shim

**Decision:** Unify grader setup; keep required Podman shim.

Centralize native-grader environment setup, use current per-run docker-to-podman shim in runtime and official/bridge parity script, remove _REPO_BIN PATH injection and checkout dependency. Select shim from resolved engine, preserve DOCKER_HOST overrides/SDK connectivity and Podman credential-store workaround. Repeated child environment setup must preserve the intended shim; Docker uses actual CLI. Test installed-package operation and inherited PATH precedence.

**Implementation:** Implemented. **Acceptance:** test_harbor_benchmark_tasks.py; test_runtime_cleanup.py.

### F29 — execution.cache_policy is descriptive text masquerading as an operational control

**Decision:** Remove input.

Reject execution.cache_policy; derive and report implemented provider-managed/no-reset cache behavior internally. Preserve genuine Maven/download/offline controls and comparison context; migrate tests asserting arbitrary cache policies to actual conditions or provenance fixtures. No new public annotation field.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_comparison.py.

### F30 — Benchmark schema accepts native options for unrelated benchmark/source modes

**Decision:** Reject mismatches.

Define supported public field sets for native ScarfBench, native SWE-bench/SWE-bench Lite, local Harbor tasks, pinned RH dataset and Harbor registry/package tasks. Validate explicit user fields in all layers according to selected loading/grading route; retain truly shared execution/provenance/cache/image-architecture checks. Reject native export/grader fields when path supplies existing Harbor tasks. Normalize route-relevant built-in defaults without treating inactive defaults as explicit user controls; test representative cross-benchmark and path/registry errors before side effects.

**Implementation:** Implemented. **Acceptance:** test_runtime_cleanup.py; test_feature_configs.py.

### F31 — Legacy scalar/reversed tool telemetry readers and duplicate producer fields

**Decision:** Current shape only.

Use tools and tool_results arrays as the sole Praxis/report tool identity format. Remove report_metrics.tool_identities scalar fallback and reversed command-name/detail interpretation, and redundant Rust RequestClassification/metric tool_name/tool_detail fields. Preserve derived report display labels, RTK hook/decision event schemas, upstream OpenAI/Anthropic wire fields, multi-call pairing, scoped IDs, unmatched events and token accounting. Migrate Python/Rust/rendering fixtures and reject unsupported old telemetry at explicit typed boundary where relevant without fabricating tool identities.

**Implementation:** Implemented. **Acceptance:** test_tool_interactions.py; test_html_rendering.py; 16 canonical Rust tests.

### F32 — Unused Rust benchmark_metrics.max_body_bytes option retained for YAML compatibility

**Decision:** Keep compatibility option.

Keep this filter's accepted unused option and storage as explicitly requested. Clarify that it does not enforce a body limit; preserve actual max_body_bytes controls in Vertex preparation and request-classifier filters. Do not include removal or new body-limit behavior in this cleanup.

**Implementation:** Retained with clarified scope. **Acceptance:** canonical Rust tests; scripts/README.md.

## Verification and limits

Completed acceptance checks:

- Full Python suite: **832 passed, 4 skipped**. The four skips are opt-in live RTK checks.
- Canonical Praxis Rust suite: **16 passed**; `cargo fmt --check` passes.
- Rebuilt wheel: installed imports, packaged resources, all three workflow exports and offline ScarfBench export passed.
- `git diff --check` and maintained documentation links passed.

The Python suite, canonical Praxis Rust suite and installed-wheel checks cover these changes. Opt-in live RTK checks now use Harbor-managed providers with the same deterministic baseline/treatment assertions and fixture-owned teardown checks; they require local images/binaries and are not run as part of the ordinary regression. No paid model or live container run is required for this cleanup.

C11's broader typed boundaries, C12's new report charts, C13's agent options schema, full dependency locking, amd64 emulation repair and the full Docker harness matrix remain separate work. C15's relevant private API dependencies are documented in [the Harbor API inventory](harbor-api-contracts.md). [The configuration guide](configuration.md#applicable-controls-and-migration) describes YAML migration; [maintenance commands](../scripts/README.md#canonical-praxis-and-workflow-sources) use the canonical sources.
