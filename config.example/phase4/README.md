# Matched Phase 4 configurations

These templates retain the inputs used for the historical Phase 4 pilots.
For current feature coverage, including combined treatments, use the
[feature configurations](../feature-tests/README.md).

Copy this directory to an experiment directory. Edit `config/models.yaml` for
your model and `config/machine.yaml` for the provider/cache. Paths are relative
to their declaring YAML files; no machine-specific paths or binary checksums
are required. Harbor accepts Podman and Docker.

The `swebench.*` files run SWE-bench Lite through Harbor; the `harbor.*`
files select an existing Harbor task bundle. See
[benchmark migration](../../docs/harbor-benchmark-migration.md).

Each benchmark configuration has four independent arms with the same harnesses, model, task
selection, 300-second budget and one worker:

| File suffix | Treatment |
| --- | --- |
| `baseline.yaml` | No extensions or response skill |
| `response-skill.yaml` | Caveman response instructions only |
| `rtk.yaml` | Automatic verified RTK preparation |
| `context.yaml` | Caveman context compression and recovery |

`skills: [caveman]` and `extensions: [caveman]` select different features.
These pilot arms keep the treatments separate; current feature examples also
include combined RTK/context and response-skill selections.

## Harbor input

Place a prepared RH task bundle under `tasks/rh-swe-bench/task-0000/`. The catalog
retains the pinned RH revision and its `testbed` conda profile even though the
task is loaded locally. Keep the instruction, environment, solution and tests
directories together. Alternatively, remove the local `path` entry from
`config/benchmarks.yaml` to fetch the original pinned RH dataset.

The original RH task declares a disk quota that this Podman host cannot enforce.
Phase 4's local pilot used a separately approved copy with only that quota
removed. Preparation still rejects unsupported quotas by default. Use a reviewed
local task copy for such an experiment; these run files do not silently weaken
resource requirements.

```sh
acb resolve --config harbor.context.yaml
acb prepare --config harbor.context.yaml
acb run --config harbor.baseline.yaml
acb run --config harbor.response-skill.yaml
acb run --config harbor.rtk.yaml
acb run --config harbor.context.yaml
```

Run arms sequentially when using one local model server. These examples do not
clear the provider's model cache; compare fresh and cached token buckets.

For the registry pilots, use the same four Harbor arms and change only the
benchmark and subset in every arm. Put the reviewed task copy in the matching
relative task directory. The original registry tasks also declare disk quotas;
the recorded local pilots use separately approved quota deviations. Terminal
also has a separately approved native-image pilot, described below.

| Benchmark | Subset | Pinned upstream revision |
| --- | --- | --- |
| `terminal-bench` | `openssl-selfsigned-cert` | `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c` |
| `aider-polyglot` | `polyglot_python_list-ops` | `f30b14415dd733c83627204bad0af69a89ceb46f` |

Aider's inspected task image is ARM64 and includes all four harnesses. The
Terminal-Bench task's published image is amd64, so its original pilot omitted
Claude under the accepted emulation limitation. Its verifier also crashed under
emulation. The separate native pilot removes `environment.docker_image` from
the approved task copy, builds the unchanged upstream Dockerfile on ARM64,
and freezes that inspected image for all four arms and harnesses. Native
no-op/oracle controls pass with rewards 0/1. This image change is an explicitly
approved experiment deviation, not an automatic fallback. Neither task's code
or tests was changed.

## SWE-bench Lite

The `swebench.*.yaml` files use the same named treatments. Native grader and
dataset/image prerequisites apply. ScarfBench supports these selections too;
ACB exports its tasks during preparation.

```sh
acb resolve --config swebench.rtk.yaml
acb run --config swebench.baseline.yaml
acb run --config swebench.response-skill.yaml
acb run --config swebench.rtk.yaml
acb run --config swebench.context.yaml
```

Add `claude-code` when its binary can run in the selected task image. The local
ARM host's emulated amd64 Claude failure remains an accepted upstream limitation.
All four harnesses have native ARM functional evidence. Full dependency locking
remains deferred; effectiveness experiments are user-owned. See the
[current status](../../docs/harbor-cleanup.md) for scope and evidence limits.
