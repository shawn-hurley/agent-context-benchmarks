Inspect the Quarkus application, including its build plugins, extensions, application properties, JAX-RS resources, CDI beans, and persistence code where present.

- Replace Quarkus-specific build setup with the Spring Boot dependencies, build plugin, and packaging needed by this application. Keep unrelated dependencies.
- Translate Quarkus configuration to Spring Boot properties while preserving application paths, persistence behavior, and feature settings. Reassess the listener port for the Spring runtime; the source framework's port is not automatically the target port.
- Convert JAX-RS/CDI components and Quarkus extension APIs to Spring MVC, dependency injection, data access, and security equivalents where needed. Provide a Spring Boot entrypoint.
- Check the module layout and packaging so the resulting Spring application can build and start. Compile or package it, then fix failures and probe the application when possible.
