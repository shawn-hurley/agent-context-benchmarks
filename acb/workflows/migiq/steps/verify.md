/no_think

Continue migIQ verification and reporting for the migrated application in /work. Read mig-execute-workspace/EXECUTION_REPORT.md and the plan. Run `mvn -q test`, resolve application failures and rerun affected checks. Check startup and existing behavior when feasible. Do not edit or weaken existing tests. Supplementary generated tests do not replace native benchmark validation.
Write migiq-workspace/MIGRATION_REPORT.md with changes, checks actually performed, results and unresolved issues; record that native ScarfBench grading follows and do not claim its result in advance. Update the orchestration log. Keep generated graph, planning and report workspaces out of the application submission. Native ScarfBench build, deployment and shared behavior validation follows this stage.
