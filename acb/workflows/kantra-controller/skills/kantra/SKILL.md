---
name: kantra
description: Analyze the local Java migration with Kantra and use its violations in PLAN.md.
---

# Kantra planning analysis

From `/work`, run `bash /opt/acb/kantra-plan.sh`. This runs Kantra locally
with `--mode source-only`, `--target quarkus`, and the bundled rulesets. It
writes `/work/.konveyor/kantra/output.yaml`, `violations-summary.md`, `analysis.ok`, and
`/work/.konveyor/kantra.log`. The command must exit successfully. If it fails,
report the error; do not invent findings or write a success marker.

Read `violations-summary.md` for matched findings; consult `output.yaml` for
details of a specific violation. The full YAML includes thousands of
unmatched rules, so avoid printing it in full. Inspect relevant incidents,
file locations, and advice. Explain in `PLAN.md` how those findings affect the
Jakarta to Quarkus migration. Include concrete file changes and build and
behavior checks. The same `/work` tree is passed to execute and verify.
