# Introduction to rgctl

**What rgctl is** and how a **code knowledge graph** works — concepts before commands.

**Hands-on:** [Installation](installation.md) · [Guides](guides/README.md) (CoolStore) · [User Guide](user-guide.md) (ecommerce-java).  
**Agents:** [agent-skill](guides/agent-skill.md) · [USER_AGENTS_TEMPLATE](agents/USER_AGENTS_TEMPLATE.md) · `rgctl install --skill`.  
**Contribute to rgctl:** [AGENTS.md](../AGENTS.md). **JSON:** [json-api.md](json-api.md).

---

## What problem does rgctl solve?

Modern codebases are too large to hold in your head — or in an LLM context window. Changing a function raises reachability questions: who calls it, what depends on it, are security-sensitive paths involved, where is complexity concentrated?

**rgctl turns the repository into a structured graph** — functions, types, calls, imports, docs, and more — so you ask structural questions and get deterministic answers instead of grepping and guessing. Built in **Rust** for speed and predictable memory on large repos. Primary consumer output is compact **`-f json`** for agents and scripts.

---

## What is a code knowledge graph?

| Everyday idea | In rgctl |
|---------------|--------------|
| Places on the map | **Nodes** — functions, classes, files, modules, headings, … |
| Roads | **Edges** — typed relations (`CALLS`, `CONTAINS`, `IMPORTS`, `VIOLATES`, …) |
| The map file | Artifacts under **`{repo}/.rgctl/`** after `discover` |

**Reachability** (who can reach whom along call paths) is pre-computed and stored compactly — that is why **blast-radius** stays fast on large graphs.

You do not need graph theory to use the CLI: **indexing builds the map; commands query the map.**

---

## How the pieces fit

```text
  Your repo (source)
      │
      │  discover  (cd repo && rgctl discover .   — or  rgctl -r PATH discover)
      ▼
  artifact root ({repo}/.rgctl/)
      │
      ├── find / callers / relations / blast-radius / metrics / communities / cpg / slice / inspect
      ├── check / pr-check / diff          (CI + snapshot compare)
      ├── semantic index + query           (opt-in embedder)
      ├── export                           (JSON, GraphML, Mermaid, Obsidian, …)
      └── serve                            (optional HTTP dashboard + semantic API)
```

1. **Once** (or after large changes): `discover` from the repo you mean to index — see [Discovering and indexing](guides/discovering-and-indexing.md) for `-r` vs `.` pitfalls.  
2. **Many times:** query commands read `{repo}/.rgctl/`. Prefer **`-f json`** and never scrape stderr ([JSON API](json-api.md)).  
3. **Agents:** install the pack (`rgctl install --skill --tools …`) — single skill `rgctl` ([agent-skill](guides/agent-skill.md)).  
4. **Dashboard:** optional UI after `discover --with-dashboard` + `serve` — not required for structural answers.

Capability designs for contributors: [design/](design/README.md).

### Discover depth (common flags)

| Flags | Use |
|-------|-----|
| (default) | Fast graph + metrics caches |
| `--with-cfg` | CFG/PDG archive (needed for `slice`, `inspect`, `cpg`, taint) |
| `--with-taint` | Discover-time taint (with CFG) |
| `--with-kantra` | Konveyor Kantra rule eval (Java-oriented; `VIOLATES` edges) |
| `--full` | Full pipeline (large corpora / Gate B style runs) |
| `--with-dashboard` / `--export-migration-hints` | Opt-in UI bundle / migration JSON |

```bash
rgctl discover . -l java,kotlin,python --with-cfg
rgctl discover . -e node_modules,target,.git,vendor
```

---

## Capability map (concepts only)

Step-by-step how-tos: **[Guides](guides/README.md)**. Full CLI walkthrough: **[User Guide](user-guide.md)**.

| Capability | Intent |
|------------|--------|
| **discover** | Index repo → graph + analytics caches under `.rgctl/` |
| **find / callers / relations / inventory** | Structured graph queries (agent-facing) |
| **blast-radius** | Upstream impact / reachability for a symbol |
| **slice / taint** | Statement-level data/control dependence; source→sink |
| **inspect** | CFG / PDG / dominance for one function |
| **cpg** | Hybrid CALL + CFG/PDG façade (mutations, flows) |
| **metrics / communities** | PageRank, betweenness, label-propagation clusters |
| **semantic** | Opt-in natural-language / keyword search over functions |
| **export** | Subgraph / projection export (JSON, GraphML, Mermaid, Obsidian, …) |
| **check / pr-check** | CI policy on blast-radius; temporal PR gate (base/head snapshots) |
| **diff** | Compare two columnar snapshots (digest + `diff_snapshots`) |
| **migration hints** | Package roadmap JSON (`--export-migration-hints`) |
| **Kantra** | Migration-rule findings during discover (`--with-kantra`) |
| **install** | Bundle agent skills / optional policy into IDEs |
| **serve** | Foreground HTTP dashboard + semantic HTTP API for one repository |

**Markdown / docs:** `discover` indexes `.md` / `.mdx` by default (headings, links, frontmatter). Use `find` / `relations` on `module` nodes and `references` edges; function semantic search stays separate. See [guide](guides/markdown-context-graph.md).

---

## Languages

Tier 1 support is **custom tree-sitter plugins**. What each plugin handles is declared in  
`crates/rgctl-lang-*/{id}-ast-coverage.json` (grammar pin + named-kind → handler). Extensions and aliases live in [`languages.toml`](../languages.toml).

The website **`/docs/languages/`** pages are generated from those JSON files at build time — do not maintain parallel hand-written coverage tables. Pointers for contributors: [languages/README.md](languages/README.md) · [tier-1-language-support.md](tier-1-language-support.md).

Current Tier 1 ids include C, C++, C#, Go, Groovy, Java, JavaScript, Kotlin, PHP, Puppet, Python, Ruby, Rust, TypeScript (plus markdown as a doc plugin).

---

## Where to go next

| You want… | Go to |
|-----------|--------|
| Install / verify the binary | [Installation](installation.md) |
| Feature how-tos on CoolStore | [Guides](guides/README.md) |
| Full CLI + ecommerce-java | [User Guide](user-guide.md) |
| Agent pack (skills) | [agent-skill](guides/agent-skill.md) |
| Paste into *another* repo | [USER_AGENTS_TEMPLATE](agents/USER_AGENTS_TEMPLATE.md) |
| Language support matrix | [languages/README.md](languages/README.md) (JSON SSOT → website) |
| JSON fields / `schema_version` | [json-api.md](json-api.md) |
| HTTP `serve` API | [HTTP Server and Dashboard](guides/http-server-and-dashboard.md) |
| Latest release notes | [v0.4.17](releases/v0.4.17.md) |
| Contribute / cold profiles | [AGENTS.md](../AGENTS.md) · [docs hub — For contributors](README.md#for-contributors) |
| Research map | [further-reading.md](further-reading.md) |
