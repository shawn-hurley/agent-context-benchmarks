# Matched Phase 4 configurations

Copy this directory to an experiment directory. Edit `config/models.yaml` for
your model and `config/machine.yaml` for the provider/cache. Paths are relative
to their declaring YAML files; no machine-specific paths or binary checksums
are required. The retained runner uses Podman. Harbor also accepts Docker.

Each backend has four independent arms with the same harnesses, model, task
selection, 300-second budget and one worker:

| File suffix | Treatment |
| --- | --- |
| `baseline.yaml` | No extensions or response skill |
| `response-skill.yaml` | Caveman response instructions only |
| `rtk.yaml` | Automatic verified RTK preparation |
| `context.yaml` | Caveman context compression and recovery |

`skills: [caveman]` and `extensions: [caveman]` select different features.
Combined RTK/context experiments belong to Phase 6.

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

## Existing runner

The `legacy.*.yaml` files use the retained SWE-bench Lite runner and the same
named treatments. Its normal dataset/image prerequisites still apply. ScarfBench
can use these selections in its existing run configs too; no Harbor export is
needed.

```sh
acb resolve --config legacy.rtk.yaml
acb run --config legacy.baseline.yaml
acb run --config legacy.response-skill.yaml
acb run --config legacy.rtk.yaml
acb run --config legacy.context.yaml
```

Add `claude-code` when its binary can run in the selected task image. The local
ARM host's emulated amd64 Claude failure remains an accepted upstream limitation.
All four harnesses have native ARM functional evidence. Full dependency locking,
MCP and benchmark-runner retirement remain deferred.
