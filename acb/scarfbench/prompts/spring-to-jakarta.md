Inspect the Spring application, including its build files, Boot entrypoint, MVC controllers, dependency injection, data access, security, and application properties where present. The Jakarta EE target runs on Open Liberty.

- Replace Spring Boot build and executable-jar setup with Jakarta EE APIs, Open Liberty tooling, and deployable packaging appropriate to this application. Keep unrelated dependencies.
- Translate Spring properties and runtime configuration to Open Liberty server settings and Jakarta application configuration while preserving application behavior. Reassess the listener port for Open Liberty; the source framework's port is not automatically the target port.
- Convert Spring MVC endpoints, beans, repositories, transactions, and lifecycle hooks to JAX-RS, CDI, Jakarta Persistence, and other Jakarta APIs where needed.
- Check the module layout and deployable packaging. Compile or package the application, then fix failures and probe it when possible.
