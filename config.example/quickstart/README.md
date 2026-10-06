# Quick-start configuration

Copy this directory to `config/quickstart` and follow the
[Quick Start](../../docs/quick-start.md). It has its own configuration, output,
and cache. It does not depend on previous runs or a local model server.

The bundled `smoke` task checks installation and execution; it is not a measure
of migration quality. Its oracle writes the correct answer (grade 1), while nop
leaves it broken (grade 0). A real agent must edit and check the file itself.

`scarfbench-cart.yaml` uses the corrected ScarfBench fork after its pinned
revision is published. Its native grader compiles and deploys the migrated
application and exercises the shared Cart behavior test.
