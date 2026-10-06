/no_think

Run `cd /work && mvn -q test`. Resolve application failures and rerun the affected checks. If a POM is malformed, inspect and fix it, then parse all POMs with Python before retrying Maven. Check startup and the existing application behavior when feasible, using PLAN.md and the source test.sh as context. Do not edit existing tests or weaken behavior checks.
If code changes make a structural query stale, re-index before relying on it. Keep .rgctl and analysis evidence separate from application changes. Native ScarfBench build, deployment and shared behavior validation follows this stage.
