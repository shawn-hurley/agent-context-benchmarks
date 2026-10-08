# Vuln / deps / reachability workflow

**When:** OSV / CVE impact — “are we affected?”, “is it reachable?”, OpenVEX.

**Index for vuln scans (required before P4–P6):**

```bash
cd "$REPO" && rgctl discover . --with-cfg
# Prefer both when you need PDG-backed sink-first confidence:
cd "$REPO" && rgctl discover . --with-cfg --with-taint
```

- Run **`--with-cfg`** for any vulnerability scan that may reach sink-first taint, blast classify, or `vuln analyze` exploitability beyond deps-only.
- Add **`--with-taint`** when you need discover-time / PDG-backed taint confidence. Without CFG, `cfg_available=false`: deps / package / callers still work, but empty taint paths are **not** PDG proof.
- If `.rgctl/` exists from a plain `discover` (no CFG), **re-discover with `--with-cfg`** before P4–P6 — do not treat the warm index as sufficient for reachability/VEX.
- Discover-time taint remains **opt-in**; on-demand `taint --sink … --source external` still needs the CFG archive from `--with-cfg`.

**Pipeline:**

| Step | Command |
|------|---------|
| P−1 Index | `rgctl discover . --with-cfg` (+ `--with-taint` for PDG confidence) |
| P0 Normalize OSV | `rgctl -f json vuln triage --osv ./advisory.json` |
| P1 Deps match | `rgctl -r "$REPO" -f json deps check --osv ./advisory.json` (+ `--include-jars lib`) |
| P2 Package imports | `rgctl -r "$REPO" -f json find --package '<coords>' --type import` |
| P3 Facade callers | `rgctl -r "$REPO" -f json callers <Symbol> --package '<coords>' --methods readValue,…` |
| P4 Boundary blast | `rgctl -r "$REPO" -f json blast-radius <Symbol> --classify-boundary` |
| P5 Sink-first taint | `rgctl -r "$REPO" -f json taint --sink ObjectMapper.readValue --source external` |
| P6 Orchestrated VEX | `rgctl -r "$REPO" -f json vuln analyze --osv ./advisory.json --include-jars lib` |

**Verdicts:** deps `not_affected` | `affected_candidate`. Analyze `exploitability`: `not_affected` | `not_exploitable` | `exploitable` | `under_investigation`. OpenVEX statuses map accordingly; unresolved sinks alone MUST NOT force `not_affected` without caller evidence. With `cfg_available=false`, prefer wording grounded in caller/deps evidence — not “PDG confirmed no path.”

**Honesty:** OSV `versions[]` may be backport series; short Maven groups use the resolver table (no silent wrong guess). Bundled JAR / `node_modules` scans are **opt-in**. Zero imports ≠ library absent when `bundled_presence` / deps match. **Xalan dual path:** `xalan:*` resolves to Apache packages **and** JDK JAXP aliases (`javax.xml.transform`, `com.sun.org.apache.xalan.internal`) with `runtime_bundled=true` — Maven absence alone does not mean no XSLT engine.

**Multi-language:** same CLI; Maven/npm/Cargo/Go/PyPI/(NuGet/Ruby/Composer stubs) resolver; Java/Jakarta + Python web boundary catalogs; declarative taint packs (`TaintRuleSet` overlays for OSV methods — no hardcoded `detect_*`).
