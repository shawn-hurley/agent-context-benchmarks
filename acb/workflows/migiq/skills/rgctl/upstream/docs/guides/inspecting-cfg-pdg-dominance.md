# Inspecting CFG, PDG, and Dominance

## Introduction

The `inspect` command lets you examine the raw **Control-Flow Graph (CFG)**, **Program Dependence Graph (PDG)**, and **Dominator Tree** for any function in your codebase. These are the foundational data structures of program analysis -- the CFG shows how execution flows through a function, the PDG shows data and control dependencies between statements, and the dominator tree shows which basic blocks dominate others.

While higher-level commands like `slice` and `cpg flows` build on these structures, `inspect` gives you direct access to the raw graphs when you need to understand the internal structure of a function at the deepest level.

## Use Cases

- **Understand complex control flow.** Visualize how branches, loops, and exceptions create execution paths through a function.
- **Debug program analysis.** When a slice or flow result is unexpected, inspect the underlying CFG/PDG to understand why.
- **Academic and research use.** Access standard program analysis data structures for experimentation.
- **Security analysis.** Examine control-flow and data-dependency structures for potential vulnerabilities.
- **Compiler-style optimization analysis.** Use dominator trees and dominance frontiers for SSA-related analysis.

## Example Project

This guide uses the **CoolStore** (`example/coolstore`). Make sure you have run `discover` with `--with-cfg`:

CoolStore examples use `-l java` to index the Java backend only (skip Angular/bower).

```bash
rgctl -r example/coolstore discover -l java --with-cfg
```

## Step-by-Step

### 1. Control-Flow Graph (CFG)

Inspect the CFG for `priceShoppingCart`:

```bash
rgctl -r example/coolstore -f json inspect priceShoppingCart cfg
```

**Output (truncated):**

```json
{
  "layer": "cfg",
  "symbol": "priceShoppingCart",
  "schema_version": 1,
  "pruned": false,
  "node_count": 18,
  "edge_count": 22,
  "nodes_sample": [
    {
      "id": "block_0"
    },
    {
      "id": "block_1"
    },
    {
      "id": "block_2"
    },
    {
      "id": "block_3"
    }
  ]
}
```

**What this tells you:**

- **`idom`** -- the immediate dominator for each basic block. Block 5 (the entry block) dominates most other blocks, as expected.
- **`frontiers`** -- dominance frontiers, used for SSA construction. In this example, no explicit frontiers are shown (use `--frontiers` for detailed output).
- The dominator tree is a tree structure rooted at the entry block; each block's immediate dominator is the closest block that must execute before it on every path.

### 4. Dominance Frontiers

Request dominance frontiers explicitly:

```bash
rgctl -r example/coolstore -f json inspect priceShoppingCart dom --frontiers
```

Dominance frontiers identify the join points in the CFG where phi functions would be needed in SSA form.

### 5. Pruned CFG

For a cleaner view, prune unreachable blocks:

```bash
rgctl -r example/coolstore -f json inspect priceShoppingCart cfg --prune
```

### 6. PDG with Edge Layer Filtering

Filter PDG edges by layer:

```bash
rgctl -r example/coolstore -f json inspect priceShoppingCart pdg --edge-layer data
```

This shows only data-dependency edges, filtering out control dependencies for a cleaner view.

```bash
rgctl -r example/coolstore -f json inspect priceShoppingCart pdg --def-use
```

The `--def-use` flag adds def-use chain information to the edges.

## Inspect Subcommands

| Subcommand | Description | Key Options |
|------------|-------------|-------------|
| `cfg` | Control-flow graph | `--prune` (remove unreachable blocks) |
| `pdg` | Program dependence graph | `--edge-layer` (filter by data/control), `--def-use` |
| `dom` | Dominator tree | `--frontiers` (show dominance frontiers) |

## Benefits

- **Full transparency.** See exactly the data structures that power slicing, flow analysis, and the CPG.
- **Standard representations.** CFG, PDG, and dominator trees are well-understood program analysis structures with extensive academic literature.
- **Debugging aid.** When higher-level commands produce unexpected results, inspect the underlying graphs to understand why.
- **Multi-format output.** Use `-f json` for programmatic access or `-f graphviz` for visualization.
- **Function-level precision.** Each graph is scoped to a single function, keeping the output manageable.

## Related Guides

- [Discovering and Indexing a Codebase](discovering-and-indexing.md) -- `discover --with-cfg` is required
- [Program Slicing](program-slicing.md) -- slicing operates on the PDG that `inspect` exposes
- [Hybrid CPG](hybrid-cpg.md) -- the CPG facade that combines these graphs with the call graph
- [Blast Radius Analysis](blast-radius-analysis.md) -- function-level analysis that complements statement-level inspection
