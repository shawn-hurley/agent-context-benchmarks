/no_think

Read PLAN.md, .konveyor/kantra/violations-summary.md, and the installed rgctl skill. Execute the planned migration to Quarkus 3.30.5 while preserving the application's externally observable behavior.
Use bounded rgctl structural queries to check callers, injection and impact before changes when useful; save query evidence under .konveyor/rgctl. Re-index after substantial source edits before relying on graph results. Do not enable rgctl Kantra evaluation or semantic indexing; this binary provides structural analysis without ONNX.
Edit Maven sections in place and run `mvn -q -DskipTests validate` after POM changes. Keep module relationships valid. Replace unsupported APIs with working equivalents appropriate to this application, preserving endpoints and state/data behavior. Do not change existing tests or weaken checks. Leave the migrated project in /work for the verification session.
