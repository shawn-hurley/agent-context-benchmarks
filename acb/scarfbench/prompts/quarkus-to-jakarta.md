Inspect the Quarkus application, including its build plugins, extensions, application properties, JAX-RS resources, CDI beans, and persistence code where present. The Jakarta EE target runs on Open Liberty.

- Replace Quarkus-specific build and packaging setup with Jakarta EE APIs and Open Liberty tooling appropriate to this application. Keep unrelated dependencies.
- Translate Quarkus runtime properties into Open Liberty server configuration and application-level settings while preserving application behavior. Reassess the listener port for Open Liberty; the source framework's port is not automatically the target port.
- Keep portable Jakarta APIs; replace Quarkus-specific extensions, annotations, Panache patterns, and bootstrapping with Jakarta EE or Liberty-compatible equivalents where needed.
- Check the module layout and deployable packaging. Compile or package the application, then fix failures and probe it when possible.
