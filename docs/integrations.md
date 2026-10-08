# Skills and harness integrations

ACB supports three distinct treatment mechanisms:

| Selection | What it changes | Shipped example |
| --- | --- | --- |
| `skills` | Instructions available to the agent | Caveman response style, local/task skills and bundled workflow skills |
| `extensions` → execution integration | Tool execution/output before the model sees it | RTK shell integration for all four harnesses |
| `extensions` → model middleware | Traffic between the harness and measurement proxy | Caveman record/compress service |

Use named selections in ordinary run YAML. The resolver chooses the reviewed
adapter mode and version; preparation obtains/verifies runtime assets.

## Select treatments

Response instructions:

```yaml
skills:
  - name: caveman
    options:
      intensity: lite
extensions: []
```

RTK tool-output integration:

```yaml
skills: []
extensions: [rtk]
```

Caveman context middleware:

```yaml
extensions:
  - name: caveman
    options:
      mode: compress
```

The middleware defaults to `record`; use `compress` explicitly for compression.
Response skill intensity accepts `lite`, `full` or `ultra`. Selecting the Caveman
extension does not activate its response skill. Enable both explicitly to combine
them; `extensions: [rtk, {name: caveman, options: {mode: compress}}]` combines RTK
and middleware. Recovery returns the bytes captured after RTK, rather than
reconstructing pre-RTK output.

Shared lists apply to all selected harnesses. Per-harness overrides can replace
lists; empty lists disable selections. Only named selections are accepted; internal integration arrays are worker inputs.
See [configuration precedence](configuration.md#precedence-and-component-selections).

Resolve and prepare each arm, then run baseline and treatment separately against
the same inputs, model and cache policy. Use actual reserved output paths with
`acb compare`. Installation, observed interception, compression and model-level
savings are distinct evidence; selection alone does not establish effectiveness.

Use the [feature examples](../config.example/feature-tests/README.md) for concrete
configurations. The [RTK guide](rtk.md) describes versions, profiles and advanced
binary options. [Operations](harbor-operations.md#shared-treatment-preparation)
covers prepared assets and Caveman checks.

## Adapter lifecycle and evidence

[`IntegrationManager`](../acb/integrations/manager.py) registers `rtk` and
`caveman`. Each adapter validates options, installs assets, activates reviewed
harness settings, verifies setup and collects evidence before cleanup. Middleware
also starts its service, checks readiness and returns a local `ModelEndpoint`.
Ordered middleware starts downstream first and stops upstream first, including
after partial startup failure. It preserves the API surface and forwards through
Praxis for provider measurement.

Per-trial `agent/acb/integrations/NAME/` holds manifests and adapter evidence in
the native trial; imported harness trial directories retain that evidence under
`integrations/NAME/`. Manifests record versions, hashes, loading/activity checks
and failures. Required export or cleanup failures stay visible rather than
silently turning a treatment into a baseline. Provider credential values are not
written into integration manifests.

To add an adapter, implement the contract in
[`acb/integrations/base.py`](../acb/integrations/base.py), register it with the
manager and add resolver/catalog support. Declare exclusive resources to detect
launch-setting conflicts. Run YAML selects registered adapters; it cannot import
arbitrary Python files. Validate composition, failures and evidence with
[maintenance checks](../scripts/README.md).
