# Languages

Per-language markdown guides here were removed. **Single source of truth** for what each Tier 1 plugin handles:

| Artifact | Role |
|----------|------|
| `crates/rgctl-lang-*/{id}-ast-coverage.json` | Named tree-sitter kinds → handlers (`Symbol`, `Relation`, `CfgStatement`, `AstSkeleton`, `Literal`, `Skip`) + grammar pin |
| [`languages.toml`](../../languages.toml) | Extensions, aliases, plugin / grammar crate metadata |

The **website** builds `/docs/languages/` (and `/docs/languages/{id}/`) from those JSON files at `prebuild` / `predev` via `website/scripts/copy-lang-coverage.mjs`. Do not reintroduce hand-written coverage tables here.

## Related

- [Tier 1 language support](../tier-1-language-support.md) — Layers A–F contributor bar
- [Markdown context](https://github.com/sshaaf/rgctl/blob/a7f875d4e2e2f6c183c6cb8a946675f889dc5383/markdown-context.md) — doc markup plugin (also has a coverage JSON)
- Honesty notes (where present): `docs/*-extract-honesty.md`
- Langfeature / structured-query smoke harnesses under `rgctl-tests/` (per-language ecommerce fixtures).
