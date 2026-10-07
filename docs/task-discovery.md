# Discover task IDs

Use the same run YAML and registries as execution:

```sh
acb tasks --config experiment/run.yaml
acb tasks --config experiment/run.yaml --json
acb tasks --config experiment/run.yaml --download
```

From a source checkout, prefix commands with `uv run`. `--config-dir` overrides
registry discovery. A valid run configuration and model definition are required
to resolve the experiment; task discovery never contacts the model provider.

The default command reads local or cached IDs without network requests. It does
not export task snapshots, build images, inspect/start containers, run agents
or grade tasks. `--download` explicitly permits remote listing/data downloads
and caches the resulting IDs. It conflicts with `execution.offline: true`.

| Task source | Discovery behavior |
| --- | --- |
| Local Harbor directory | Reads directory names containing `task.toml`; does not snapshot/copy tasks |
| ScarfBench | Discovers configured migrations from the local `benchmark_cache_dir`, retaining canonical `layer/app/source-to-target` IDs; populate the application tree using the [Quick Start](quick-start.md) first |
| Local SWE-bench JSON/JSONL | Reads instance IDs from the dataset, preserving dataset order |
| Remote SWE-bench | `--download` loads dataset metadata/data using the optional datasets extra; later discovery can reuse cached IDs or prepared-plan task metadata |
| RH SWE-bench | `--download` lists task names at the immutable configured revision; cached subset bundles are labeled partial |
| Harbor registry/package dataset | `--download` reads task configuration metadata without downloading task environments; subsequent calls can reuse the cached listing |

Remote SWE-bench discovery requires the datasets extra (`uv sync --extra
datasets` in a source checkout). Cached metadata is specific to the benchmark
configuration; changing dataset revision does not reuse a different revision's
listing. Use `--download` to refresh remote metadata. A cached set of previously
prepared tasks is labeled partial because it may cover only a subset.

Listings include every known eligible ID after exclusions supported by the
native ScarfBench/SWE-bench adapters, mark the current `subset`/`limit`
selection, and state whether coverage is complete. Local Harbor selection uses
its task IDs and limit; native adapter exclusion fields do not filter it.
When partial coverage makes the task-limit selection uncertain, the text marks
it `?` and JSON uses `selected: null` rather than guessing which task will run.
Missing requested IDs are reported separately. **Complete listing** means the
ID list is complete for those discovery inputs; runtime compatibility, local
files, grading and measurements still require preparation/execution checks.

Put the displayed IDs in the run YAML:

```yaml
subset: [smoke]
limit: 1
```

For ScarfBench, use its slash-separated ID, such as
`business_domain/cart/jakarta-to-quarkus`, even though exported task directories
use normalized names. Discovery preserves the selection order used by each
adapter for complete listings, so a limit chooses the same available IDs as execution.

The [interactive editor](interactive-run.md#discover-and-select-tasks) offers
manual entry, **Browse local/cached tasks**, and **Download task listing** under
**Task IDs**. No checked IDs means all eligible tasks; the task limit still
applies. Preparation's `manifest.tasks` remains the definitive frozen execution
manifest, including checksums and runtime requirements.
