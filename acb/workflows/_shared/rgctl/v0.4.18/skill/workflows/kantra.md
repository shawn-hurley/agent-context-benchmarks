# Kantra workflow

**Primary output:** `.rgctl/kantra_findings.json` and `KantraRule` / `VIOLATES` in the graph.

**This workflow is not migration roadmap export.** Do not present `migration_plan.json` as the main Kantra deliverable.

Native evaluation of [Konveyor Kantra](https://github.com/konveyor/kantra) rules against the rgctl graph and source cache. Release builds embed Konveyor `stable/java` (~2.6k rules); no external Kantra CLI required.

### Default Kantra discover

**User intent:** *"Run Konveyor migration rules on this Java codebase"*

```bash
rgctl discover . -l java --with-kantra
# violations: .rgctl/kantra_findings.json

# Post-index (when snapshot already exists):
rgctl -f json rules run ./rules/ --target quarkus
# rules in graph: find --type kantrarule / inventory --by type
```

Report `catalog_id`, `evaluated_rules`, violation count, sample hits (`rule_id`, `file`, `line`, `matched_by`), and top `skipped_rules` reasons.

### Target-filtered eval

**User intent:** *"What Quarkus migration rules apply?" / "Audit for Spring Boot 3+"*

```bash
rgctl discover . -l java --with-kantra --kantra-target quarkus
# or: --kantra-target spring-boot3+
```

`target_filter` appears in `kantra_findings.json`. Only rules with `konveyor.io/target=<NAME>` labels are evaluated.

### Rules inventory

**User intent:** *"List migration rules indexed in the graph" / "How many Kantra rules?"*

```bash
rgctl -f json find --type kantrarule --limit 50
rgctl -f json inventory --by type   # KantraRule / KantraRuleset counts
# after full eval: rule → code links
rgctl -f json relations --edge violates --from-type kantrarule --limit 50
```

Line-level detail and enrichment live in `kantra_findings.json` (preferred over edge dumps for violations).

### Fixture / CI override

**User intent:** *"Run a small custom ruleset in CI"*

```bash
rgctl discover . --with-kantra --kantra-rules tests/fixtures/kantra-rules
```

Mutually exclusive with `--kantra-catalog`. Embedded catalog is the default when neither override is set.

### Index only

**User intent:** *"Index rules into the graph without running eval"*

```bash
rgctl discover . --with-kantra --kantra-index-only
```

Useful when you only need structured rule inventory (`find --type kantrarule`). Eval stage is skipped; `kantra_findings.json` is not written.

**Pitfalls:**

- Does **not** require `--with-cfg`
- Many upstream Konveyor rules use unsupported providers (`builtin.xml`, `java.dependency`) or Windup-style regex — expect a large `skipped_rules` list with full catalog
- Re-run discover after rule/catalog changes; kantra index rewrites `graph.snapshot.bin` at end of pipeline

**See:** [User guide — Kantra](../upstream/docs/user-guide.md#kantra-migration-rules---with-kantra), [JSON API](../upstream/docs/json-api.md#kantra_findingsjson), [KANTRA_ARCHITECTURE_OPTIONS.md](https://github.com/sshaaf/rgctl/blob/a7f875d4e2e2f6c183c6cb8a946675f889dc5383/KANTRA_ARCHITECTURE_OPTIONS.md)
