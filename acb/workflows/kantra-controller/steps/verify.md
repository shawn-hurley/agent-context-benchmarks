/no_think

First run `cd /work && mvn -q test`. Fix only failures in the migrated
application and rerun the failing check. If Maven reports malformed XML,
inspect the reported POM once, fix the whole invalid block, and check all
POMs with Python's XML parser before rerunning Maven. Do not repeatedly read
the same lines without making a change.

Before finishing, search source for remaining `@EJB`, `@Remote`, and
`@Stateful` uses; replace them with CDI equivalents. Check startup and the
existing REST paths when feasible. Do not edit test files or write todo files.
The native ScarfBench compile, deploy, and smoke validation follows this stage.
