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
  assets: [scripts/helper.sh]
  dockerfile_append: |
    COPY scripts/helper.sh /opt/helper.sh
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
