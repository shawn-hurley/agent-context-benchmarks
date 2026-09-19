Inspect the Jakarta EE application and its Open Liberty configuration, including `pom.xml`, `server.xml`, JAX-RS resources, CDI beans, persistence, and MicroProfile settings where present.

- Replace Open Liberty build and deployment setup with the Quarkus BOM, extensions, and build plugin needed by this application. Keep unrelated dependencies.
- Translate runtime configuration to Quarkus properties while preserving application paths, persistence behavior, and feature settings. Reassess the listener port for the Quarkus runtime; the source framework's port is not automatically the target port.
- Keep Jakarta APIs that Quarkus supports; replace Liberty-specific bootstrapping or integrations with Quarkus-compatible implementations.
- Check the module layout and packaging so the resulting Quarkus application can build and start. Compile or package it, then fix failures and probe the application when possible.
