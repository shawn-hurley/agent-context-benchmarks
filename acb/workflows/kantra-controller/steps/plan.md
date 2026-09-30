/no_think

Plan the Jakarta to Quarkus migration in /work. Use Quarkus platform 3.30.5.
First run `bash /opt/acb/kantra-plan.sh` with the shell tool and wait for it.
Then read the existing root and module POMs and the Java files containing
`@Stateful`, `@Remote`, and `@EJB`; use shell for reads. The Kantra summary
names the affected paths. Check the REST resource and source test.sh for
observable behavior. Base the plan on those files, not only Kantra advice.

Write /work/PLAN.md in at most 500 words. Give ordered, file-specific edits,
the matched findings behind them, endpoint and data behavior to preserve,
and build/check commands. Specify CDI replacements for EJB annotations and
injection. For Maven, describe the minimum changes needed for a valid Quarkus
build; do not paste XML examples or optional native-build boilerplate.
Finish after saving PLAN.md. Do not edit application code in this stage.
