# Staged workflows

ACB can run a user-authored sequence of agent stages over one Harbor task
workspace. Each stage has its own instruction and agent timeout. Harbor starts a
new agent session for each stage and keeps the task environment between stages.
ACB runs the benchmark's native grader at the final stage.

Select a workflow in a run file:

```yaml
schema_version: 2
run_id: my-migration
benchmark: scarfbench
harness: goose
model: my-local-model
workflow: ./workflows/my-migration
```

The path is relative to the run file. You can also select a bundled workflow by
name, such as `kantra-controller`. Workflows currently apply to ACB-exported
ScarfBench, SWE-bench, and SWE-bench Lite tasks. Runs without `workflow` keep the
existing single-step behavior.

Create `workflows/my-migration/workflow.yaml` and the instruction files it names:

```yaml
version: 1
name: my-migration
benchmarks: [scarfbench]
harnesses: [goose]
steps:
  - name: plan
    instruction: plan.md
    timeout_sec: 900
    gate:
      type: artifacts
      archive: [PLAN.md]
      required: [PLAN.md]
  - name: implement
    instruction: implement.md
    timeout_sec: 900
    gate:
      type: native
exclude_from_grading: [PLAN.md]
```

`benchmarks` and `harnesses` are optional compatibility lists. `native` must be
the final gate. An `artifacts` gate copies its declared paths from the agent
workspace to the run's verifier evidence and requires listed files to be
nonempty. Add a trailing slash to an `archive` entry to copy a directory. Use
`yaml_violations: path/to/output.yaml` to require a Kantra-style YAML list of
rulesets with at least one violation. A failed gate stops later stages.

The workflow can add image files:

```yaml
environment:
  assets: [scripts/helper.sh, skills/example/SKILL.md]
  skills_dir: /opt/acb/skills
  dockerfile_append: |
    COPY scripts/helper.sh /opt/helper.sh
    COPY skills/ /opt/acb/skills/
```

`environment.dockerfile` can replace the benchmark's default Dockerfile with
a file in the workflow directory. ACB copies the named assets into the task's
build context. For ScarfBench, ACB then copies the application source into
`/work`; SWE-bench workflows normally extend the benchmark's task image with
`dockerfile_append`. Workflow assets, instructions, and configuration are
included in each exported task snapshot. ACB checks their digest during
preparation, so a workflow edited after resolution must be resolved again.

`exclude_from_grading` lists workspace paths to omit from the final candidate.
The benchmark retains control of its native grader, including ScarfBench's
compile, deploy, and smoke-test evidence. The bundled
[`kantra-controller`](../acb/workflows/kantra-controller/workflow.yaml) is a
complete example with three stages, a Kantra image, a plan gate, and a final
native ScarfBench reward. Its local Qwen request adapter is specific to that
bundled pilot; user workflows do not need an agent adapter.

## ScarfBench migration comparisons

Two additional bundled workflows are available for `harness: goose`:

| Workflow | Analysis and migration process |
| --- | --- |
| `kantra-controller` | Existing Kantra baseline |
| `kantra-rgctl` | Kantra findings plus rgctl structural queries |
| `migiq` | migIQ analysis, requirements, planning, execution and reporting |

Both new treatments use rgctl **v0.4.18**, source commit
`a7f875d4e2e2f6c183c6cb8a946675f889dc5383`. migIQ is pinned to commit
`88438083945aed14cca53ec082ec00185e68fae0` (package version 0.2.3).
Each workflow includes `provenance.json` with upstream and packaged skill hashes.
Supporting text references are available offline; optional binary assets and
missing upstream references use links pinned to the source commit.

The images build rgctl natively on Linux ARM64 or AMD64 using Rust 1.99,
a bundled Cargo lockfile, checksum-verified source archives and the exact ruleset
submodule. The same `--no-default-features` build is used in both treatments:
structural queries, CFG, communities and migration hints are available; semantic
ONNX indexing is omitted. The first image preparation needs network access and
can be slow while Rust dependencies compile. Separate rgctl build caches do not
change the shared Maven cache policy.

`environment.skills_dir` names an absolute path inside the image. Harbor passes
this skill root to ACB, which validates and installs the bundled skills through
its existing harness-specific installer. Selecting either workflow supplies its
skills automatically; a separate configured `rgctl` skill is unnecessary and
would collide with the task-provided skill. The new local run configs explicitly
select `skills: []`. Existing workflows that omit `skills_dir` are unchanged.

Both workflows use three fresh agent sessions with 900 seconds each: plan,
execute, verify. migIQ's analysis, requirements and planning phases share the
plan session. Benchmark requirements supply the source/target, behavior contract
and authorization; Claude-specific invocation syntax is replaced by reading the
installed skills. External deployment and optional rgctl Kantra rule evaluation
are omitted. The Kantra treatment still runs the standalone Kantra analyzer.

The migration occurs in the original source tree. Existing tests may not be
weakened. Supplemental migIQ tests belong in its execution workspace. The
native ScarfBench build, deployment and shared behavior tests determine the
final reward. Graphs, planning workspaces and reports are excluded from grading.
Bounded graph evidence, plans and execution reports are archived at stage gates.
A native gate may also declare `archive` and `required` to capture final reports
before grading; artifact capture never replaces the native benchmark reward.

From the repository root, select the isolated local configurations with:

```sh
acb run --config config/local/scarfbench/kantra-rgctl-fixed-benchmark.yaml
acb run --config config/local/scarfbench/migiq-fixed-benchmark.yaml
```

They copy the existing fixed benchmark baseline's model, harness, dataset,
worker count and timeout settings and use distinct run IDs. No benchmark is
launched by adding these configurations. To inspect configuration without
running agents, use `acb resolve --config <config>`.

## Comparison identity

Workflow, harness, model, skill, tool, container image, resource, timeout and cache
changes are experimental treatments. They do not make matching benchmark results
incomparable. Reports show these differences as context alongside grade and token
deltas. Different attempt counts are also recorded: grades are means across
attempts and tokens are totals across attempts.

ACB records a separate benchmark contract in imported reports. For ScarfBench it
identifies the application source, migration conversion and frozen grading inputs.
Generated build output and reference deployment scaffolding (Dockerfiles,
Makefiles and metadata) are excluded from this behavioral identity. For SWE-bench
it identifies the repository, base commit, problem, test patch, test selections,
evaluation rules and frozen test assets. Grade definitions and native grader
identity remain part of compatibility. Changed application inputs or behavior
assertions are flagged instead of silently treated as the same benchmark.

For older reports, ACB recovers the contract from task snapshots listed in the
saved resolved plan without changing the run or rerunning validation. If those
snapshots are unavailable and no contract was recorded, matching frozen task
checksums or task revisions remain the fallback; task names alone are insufficient.
Incomplete telemetry suppresses token deltas only for the affected tasks.
