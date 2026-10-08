First read /work/PLAN.md and the Kantra violations summary using the available tools. Work
from the inspected source facts in that plan. Use Quarkus platform 3.30.5.

Make the minimum edits needed for a runnable migration. Edit the existing
Maven sections in place: never leave two `<plugins>` blocks under one `<build>`
or add an optional native profile before the JVM build works. After each POM
edit, run `mvn -q -DskipTests validate` and fix XML errors immediately.
Prioritize the mandatory EJB changes: replace `@Stateful` with a suitable CDI
scope, remove `@Remote`, and replace `@EJB` injection with `@Inject` where the
resource uses the cart service. Keep the REST paths and cart behavior.

Do not edit tests. Leave the changed project in /work for a
fresh verification session.
