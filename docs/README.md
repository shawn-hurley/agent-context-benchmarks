# Documentation

Start with the [Quick Start](quick-start.md) to install ACB, check grading without
model calls, run one task and inspect its evidence. From a source checkout,
prefix CLI examples with `uv run`; installed packages expose `acb` directly.

## Set up and run experiments

| Guide | Use it for |
| --- | --- |
| [Quick Start](quick-start.md) | Container installation, the included repair task and a ScarfBench migration |
| [Starter templates](starter-templates.md) | Generate a quickstart or RH SWE-bench experiment with `acb init` |
| [Interactive setup](interactive-run.md) | Create/load YAML, select tasks, review settings and save without running |
| [Configuration reference](configuration.md) | Registries, model aliases, precedence, paths and per-harness settings |
| [CLI and operations](harbor-operations.md) | Commands, Compose prerequisites, exit status, offline operation and result files |
| [Task discovery](task-discovery.md) | List eligible IDs locally or explicitly download a listing |
| [Native benchmark setup](harbor-benchmark-migration.md) | SWE-bench/ScarfBench datasets, graders and execution boundaries |

## Workflows, treatments and results

| Guide | Use it for |
| --- | --- |
| [Staged workflows](workflows.md) | Author a workflow or use the bundled Kantra/Migiq workflows |
| [Skills and integrations](integrations.md) | Choose response instructions, RTK tool integration or Caveman middleware |
| [RTK](rtk.md) | Named selection, advanced options, paired pilots and compatibility checks |
| [Feature examples](../config.example/feature-tests/README.md) | Ready-to-edit configurations across harnesses and treatments |
| [Reports and comparisons](report-rendering.md) | Coverage, HTML/ZIP exports and renderer boundaries |
| [Maven caching](scarfbench-maven-cache.md) | ScarfBench download storage and isolation |

## Develop and maintain ACB

- [Architecture](architecture.md): current resolver, preparation, Harbor and reporting boundaries.
- [Runtime contract](harbor-runtime-contract.md): inspected task/image identity, isolation and asset delivery.
- [Maintenance checks](../scripts/README.md): regression, package and opt-in container checks.
- [Current scope and remaining work](harbor-cleanup.md): authoritative cleanup/status record.

## Validation and design history

[Quick Start validation](validation/quick-start-validation.md) and the
[CLI usability walkthrough](validation/cli-usability-review.md) record what was
tested and its limits. They are evidence for particular runs and revisions.

[Archived plans and checkpoints](archive/README.md) preserve earlier designs and
migration decisions. Use the guides above for current behavior and the cleanup
record for open work. Historical checklists do not create new acceptance gates.

Keep repository-wide usage/reference docs here, with README as the root entry
point. Example-specific instructions stay with `config.example/`; maintenance
commands stay with `scripts/`; packaged prompts and workflow skills stay with
their runtime assets.
