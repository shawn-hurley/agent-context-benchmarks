# Pinned Harbor API inventory

ACB requires Harbor 0.23.0. Private dependencies are confined to provider
inspection, Compose process ownership, file transfer and dataset metric binding.
Re-run these contracts before changing the pin. Broader typed worker/result
boundaries and the Harbor agent options model remain separate work.

| ACB boundary | Harbor dependency | Relied-on behavior | Acceptance |
| --- | --- | --- | --- |
| `contracts.py` | environment `_platform._resolve_service_container`, `_enable_egress_control`, `_EGRESS_CONTROL_SERVICE_NAME` | Resolve main/provider/task service containers; include enforced egress service identity without storing secrets | `test_harbor_api_contracts.py`, `test_harbor_contracts.py` |
| `environment.py` | `_docker_compose_paths`, `_run_docker_compose_command`, `_platform._upload_file_with_tar`, `_upload_dir_with_tar` | Frozen image overlays retain task Compose settings; tar transfer works when Compose cp is unavailable; noninteractive execution preserves bytes | `test_harbor_api_contracts.py`, `test_harbor_contracts.py`, `test_harbor_runtime.py`, `test_harbor_transfer.py` |
| `processes.py` | `_sanitize_docker_compose_project_name`, `_compose_env_vars`, `_collect_buffered_output`, `_collect_streamed_output` | Select the same owned project/environment as Harbor; preserve streamed callbacks; reap client and engine descendants on cancellation | `test_harbor_api_contracts.py`, real process tests in `test_harbor_transfer.py` |
| `metrics.py` | `job._metrics`, uv-script metric `_script_path` | Bind frozen custom scripts to controller-owned grading containers, retain built-in aggregation semantics and missing rewards | `test_harbor_metrics.py` |

Harness adapters use the explicit `EnvironmentTransport` protocol. Raw container
names no longer provide an alternate execution or copy route. Opt-in RTK tests
use ACB's Harbor Podman provider and independently check fixture-owned container
teardown. Existing Docker/Podman lifecycle evidence is recorded in the
[runtime contract](harbor-runtime-contract.md); this cleanup does not claim a new
live Docker matrix or provider-effectiveness result.
