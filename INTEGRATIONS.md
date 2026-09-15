# Harness integrations

Skills provide agent instructions, execution integrations alter tool execution,
and model middleware transforms model traffic. Python adapters share installation,
activation, verification, managed-service startup/health checks, artifact collection,
and shutdown. Only registered adapters can be selected; configuration cannot load
arbitrary Python files.

The first concrete adapter is experimental RTK for Goose. Native RTK adapters for
OpenCode, Pi, and Claude Code, and Caveman's proxy, are not registered yet.

## Experimental RTK with Goose

Use a predownloaded Linux RTK executable matching the benchmark architecture and
ABI. Supply its verified SHA-256 and exact version, plus an exact Goose version:

```yaml
overrides:
  harness:
    version: "1.50.0" # example; validate the chosen release in your environment
    execution_integrations:
      - name: rtk
        version: "<exact-rtk-release>"
        binary_path: "/absolute/path/to/linux/rtk"
        sha256: "<binary-sha256>"
        mode: shell-wrapper
        experimental: true
```

An empty list preserves current runs. Installation verifies the copied RTK bytes,
its executable version, and Goose's version. Wrapper preflight exercises RTK using
separate tracking storage from generation. It does not establish agent-level
compatibility, which is explicitly recorded as unverified. Complete a controlled
agent-tool smoke run before relying on benchmark results.

The wrapper receives Goose's shell command, asks RTK to rewrite it, then executes
it with Bash. Unsupported commands pass through unchanged. RTK's denial and error
statuses never fall back to running the original. Rewrite status 3 means the host
must authorize execution; the wrapper runs only after Goose authorizes the shell
call. Other Bash invocations pass through, including login PATH probes.

The adapter installs under `/opt/acb/rtk` in the ephemeral container and does not
initialize the host. Decision logs, tracking data, gain exports, versions, hashes,
and verification results are collected under `instances/<instance>/integrations/rtk`.
`report.json` includes these manifests. Export/cleanup failures are recorded and
successful generation cannot silently lose required integration evidence.

## Model middleware

`model_middleware` is an ordered list of registered adapters. No concrete middleware
adapter is shipped yet. `[A, B]` means harness -> A -> B -> Praxis -> provider, so
Praxis measures traffic after compression. Services start downstream first, undergo
health checks before generation, and stop upstream first even after partial failure.
Adapters return a local `ModelEndpoint`, preserving the API surface. Provider keys
are not written to integration manifests.

Caveman's skill belongs in existing skill configuration; its proxy needs a concrete
middleware adapter after API, streaming, recovery, usage reporting, and runtime
compatibility have been checked. Its general installer should not auto-enable extra
skills or modify host configuration during a benchmark treatment.
