Inspect the Spring application, including its build files, Boot entrypoint, MVC controllers, dependency injection, data access, security, and application properties where present.

- Replace Spring Boot starters and build plugin with the Quarkus BOM, extensions, and build plugin needed by this application. Keep unrelated dependencies.
- Translate Spring configuration to Quarkus properties while preserving application paths, persistence behavior, and feature settings. Reassess the listener port for the Quarkus runtime; the source framework's port is not automatically the target port.
- Convert Spring MVC endpoints, beans, repositories, transactions, and lifecycle hooks to JAX-RS, CDI, Jakarta, or Quarkus equivalents where needed. Remove Spring Boot startup assumptions.
- Check the module layout and packaging so the resulting Quarkus application can build and start. Compile or package it, then fix failures and probe the application when possible.
