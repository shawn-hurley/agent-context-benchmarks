# Phase 2/3 scope and evidence audit

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](../harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

> Subsequent September 17 decisions supersede the remaining list in this audit:
> the user waived live Docker testing in favor of interface compatibility and bug
> reports. Offline support is interpreted as disconnected operation with natural
> engine failures, not a guarantee against every network attempt while connected.
> The package/CLI/evidence review is now complete (282 passed, 4 skipped).
> See [current operations](../harbor-operations.md) and the [checklist](harbor-migration-tasks.md).
> Claude amd64 emulation is now an accepted [known issue](../harbor-operations.md#known-issue-claude-code-on-emulated-amd64-benchmarks);
> further debugging is deferred. Phases 2/3 are complete under the revised scope.

September 17, 2026. Implementation is paused for this audit. No runtime code was
changed and no container jobs or test suite were rerun. Evidence below was checked
against saved artifacts, source, tests and the written delivery-phase criteria.
The latest recorded broad regression result is 282 passed, 4 skipped.

## Finding

The work has made substantive progress, but status tracking allowed scope to
expand and made completed work appear unfinished. The concern about moving goals
is justified. The main causes were:

1. Entire-migration verification requirements were promoted to immediate phase
   2/3 gates. Phase 4 explicitly completes artifact preparation; phase 6 explicitly
   adds separate verifiers, multi-step/provider capabilities and Caveman context.
2. Broad parent tasks stayed open as successive implementation details were found.
   Managed Python, RTK, provider images, remote skills and harness archives are
   distinct caches, but repeatedly calling each pass "preparation reliability"
   obscured what had finished and never set a stopping point.
3. Historical status documents still listed accounting, skills/MCP and image
   identity as unresolved. Their results and instructions were not current.
4. "Other live failure gates" and "broader network coverage" had no finite
   acceptance definition. Passing tests were followed by further unspecified tests.
5. Docker acceptance was repeatedly listed but not advanced. Extra Podman cache
   hardening cannot satisfy that original backend requirement.

The technical fixes were useful. They were not all necessary before finishing
phase 2/3. Test-count growth is not a phase-completion measure.

## Original phase scope versus current evidence

Source: HARBOR_PLAN.md, delivery phases 2 and 3. The plan is a working document,
not an immutable original revision. The user's later scope/ordering decisions
apply: MCP and full dependency locking are deferred; rgctl architecture is assumed
usable; phases 2/3 precede remaining phase 1 work; Claude emulation is handled last.

| Requirement | Evidence/status | Remaining work |
| --- | --- | --- |
| Isolated Harbor worker, dispatch and transport | Implemented with Harbor 0.23.0; all four native ARM64 harnesses run | Final package/CLI smoke only |
| Four harnesses with Praxis, OpenAI/Anthropic paths | Saved four-harness reward-1 checks on Podman | Docker execution |
| Exact request accounting and no-traffic/error reporting | `harbor-accounting-check` reconciles all four; `harbor-partial-job-check` accounts for missing trials | Preserve in final acceptance; no new accounting implementation backlog |
| Task workdir/user/language requirements | Prepared runtime contracts, startup checks and RH testbed conda inspection | No identified open defect for the declared supported profile |
| Timeout/cancellation and cleanup | Tool, setup, install, upload and stream cancellation artifacts pass; download interruption also covered | Docker execution |
| Missing/malformed rewards, verifier failure/timeout | Native results and imported evaluations in `harbor-lifecycle-fixtures` report errors and unknown rewards | Final review of existing cases, not an unspecified new fixture program |
| Agent failure and incomplete metrics | RH startup failure, real timeout evidence, stream interruption; truncated metrics tested at collection boundary | Preserve scope distinctions; no requirement to duplicate every parser test as a live experiment |
| Skills through the transport | All four read task/configured skills; cached rgctl v0.4.12 installs, hash matches, discovers and queries a function | No identified Podman skill-delivery blocker |
| RH grading controls | Native Harbor results show no-op 0 and oracle 1 on Podman | Docker controls; real-model pilot remains phase 4 |
| Both Docker and Podman | Podman has live evidence; Docker implementation lacks equivalent acceptance | Original, unresolved gate |

These results use controlled fixtures, with real-model RH diagnostics identified
separately. They do not prove ScarfBench/SWE-bench migration parity or a completed
real-model RH treatment pilot.

## Work already done beyond the initial phase scope

Keep these implementations and their evidence; they do not create further phase
2/3 requirements:

- Separate-verifier image contracts and synthetic isolation checks:
  `harbor-verifier-contract-check` passes and records no remaining trial containers.
- Caveman record-mode provider image verification:
  `harbor-caveman-image-check` passes all four harnesses. Compression effectiveness
  and composition remain phase 6.
- Public/no-network policy and hostname allowlist transitions:
  `harbor-network-phase-check-4` passes. Exhaustive wildcard/CIDR/TLS combinations
  are broader capability work, not an open-ended phase 2/3 gate.
- Managed-runtime, RTK, provider, remote-skill and harness cache hardening:
  preserve current tests. Do not require a new dependency-locking project or
  retrospective integrity guarantees for every legacy cache.

The main runtime design still matches the intended boundaries: resolver →
preparation → Harbor lifecycle → adapters/integrations → Praxis/results. The
new cache utilities add maintenance cost, and verifier inspection uses private
Harbor APIs despite the plan's preference to avoid them. These are documented
limitations for the pinned dependency, not grounds for another architecture rewrite.

## Bounded remaining list

This replaces the previous six broad, expandable areas. Existing checklist IDs
remain for traceability.

1. **Offline behavior — H03-13.** The worker does not pass an offline restriction
   to task environment startup, and initial inspection can run before frozen task
   images exist. Therefore the end-to-end no-download claim is not established.
   Close this by ensuring missing task/service images or required builds fail
   before pull/build/network work in offline preparation. A conservative explicit
   rejection is acceptable; comprehensive offline task building is not required.
   Verify a warm supported path and a missing-image rejection. No additional cache
   framework or dependency locking belongs in this item.
2. **Docker acceptance — H03-20.** Establish a real Docker engine and run the
   existing four-harness fixture/accounting, representative failure/cancellation,
   and RH no-op/oracle checks. Podman behind a Docker-compatible socket is not
   Docker evidence. This remains the largest original outstanding gate. Creating
   or choosing the runtime is still an execution decision, not work completed by
   this audit.
3. **One final acceptance/package review — H03-16/21/22.** Reconcile the existing
   positive/negative evidence, build/install the current wheel outside the checkout,
   smoke the supported CLI path and run the regression suite once at the final
   code state. Publish a concise supported-profile/commands/limitations summary.
   Full portable benchmark guides/examples remain later-phase work. Repeat tests
   only for an identified regression or changed behavior, not merely because a
   prior result is older.
4. **Claude amd64 emulation — preserved user ordering.** Handle last, per the
   user's instruction. It is a blocker to the four-harness RH pilot; the native
   ARM64 controlled bridge already passes. The original real-model RH rollout is
   phase 4, so do not describe this as a broken core bridge. Do not claim it solved
   without evidence or silently remove it from the user's requested sequence.

Broader network/provider capabilities move back to phase 6. Complete extension
preparation and real-model pilots remain phase 4. Resolver/path completion remains
phase 1, next in the user's ordering. MCP and full dependency locking remain deferred.

## Tracking rules after this audit

- This audit defines the current scope correction; the task checklist carries
  implementation status. Older checkpoint docs are historical, not resume lists.
- A completed item stays completed unless a concrete regression is identified.
- A proposed new gate must identify its original requirement or be labeled later
  work. It must not silently extend the list above.
- Preserve failed attempts as diagnostics without treating them as the latest
  state when a later successful run supersedes them.
- Migration changes are still in the working tree, with substantial untracked
  code/docs/tests. Saved runs are local evidence, not portable release artifacts.
  No commit, merge, deployment, or phase-completion claim is made by this audit.

## Conclusion

Phase 2 is not complete because Docker acceptance is missing. Phase 3's core
Podman bridge is implemented and has substantial live evidence; it is not waiting
for another round of general feature expansion. Finish the bounded items above,
then return to the intended configuration UX/code-structure review and phase 1.
