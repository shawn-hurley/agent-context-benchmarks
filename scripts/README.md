# Maintenance and live checks

The Python files named `check_*.py` are opt-in integration checks. They exercise
containers, cached harness binaries, local fixture services, or a prepared
Harbor plan and are intentionally excluded from the normal `pytest tests` run.
Each command requires a new output directory and retains a machine-readable
`check.json` where applicable.

Maintained capability checks include:

- Harbor accounting, cancellation, network phases, verifier isolation, service
  images, skill delivery, rgctl delivery, Caveman, and treatment composition.
- Dataset metrics in `check_harbor_dataset_metrics.py` and multi-step execution
  in `check_harbor_multistep.py`.
- Retained-runner Caveman behavior in `check_legacy_context.py` until that runner
  is retired.
- Download cancellation, provider-image caching, and Caveman store isolation.

The Phase 4 pilot runner and summarizer were removed after their frozen results
were recorded. Normal run configurations and reports now cover their ongoing
functionality. The smaller local-model `check_legacy_caveman.py` was also
removed because `check_legacy_context.py` covers the maintained lifecycle,
compression, failure, cancellation, and combined-extension contracts.

The shell scripts are user-facing smoke or example runners and remain supported.
