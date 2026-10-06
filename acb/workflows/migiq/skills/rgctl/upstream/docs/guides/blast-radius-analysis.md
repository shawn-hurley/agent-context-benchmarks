# Blast Radius Analysis

## Introduction

The `blast-radius` command measures the **upstream impact** of changing a function. It answers the question: "If I modify this function, what other parts of the codebase could be affected?" It does this by walking the call graph backwards from the target function, collecting every direct caller and the full transitive impact zone.

The result is a **blast-radius score** (0--100), a list of direct callers, and the complete impact zone -- everything you need to make an informed decision about the risk of a code change.

## Use Cases

- **Pre-change risk assessment.** Before modifying a function, check how many callers depend on it and how far the impact propagates.
- **Code review prioritization.** Focus review effort on changes to high-blast-radius functions.
- **Refactoring safety.** Identify all the code paths that exercise a function before extracting, renaming, or modifying it.
- **Migration planning.** Understand which functions are safe to extract into microservices (low blast radius) versus those that are deeply coupled (high blast radius).
- **CI guardrails.** Combine with `--policy-file` to fail builds when changes affect too many downstream consumers.

## Example Project

This guide uses the **CoolStore** (`example/coolstore`). Make sure you have run `discover` first:

CoolStore examples use `-l java` to index the Java backend only (skip Angular/bower).

```bash
rgctl -r example/coolstore discover -l java
```

## Step-by-Step

### 1. Basic Blast Radius (Text)

Check the impact of the `priceShoppingCart` method:

```bash
rgctl -r example/coolstore blast-radius priceShoppingCart
```

**Output:**

```
Blast radius for 'priceShoppingCart'
  Score: 40.6/100
  Direct callers: 6
  Impact zone: 12
  Callers: com.redhat.coolstore.service.ShoppingCartService.checkOutShoppingCart, com.redhat.coolstore.rest.CartEndpoint.delete, com.redhat.coolstore.rest.CartEndpoint.set, com.redhat.coolstore.rest.CartEndpoint.dedupeCartItems, com.redhat.coolstore.rest.CartEndpoint.add, com.redhat.coolstore.model.ShoppingCart.addShoppingCartItem
  Impact: com.redhat.coolstore.rest.CartEndpoint.dedupeCartItems, com.redhat.coolstore.rest.CartEndpoint.add, com.redhat.coolstore.model.ShoppingCart.addShoppingCartItem, com.redhat.coolstore.utils.Transformers.shoppingCartToJson, com.redhat.coolstore.service.ShoppingCartOrderProcessor.process, com.redhat.coolstore.service.ShoppingCartService.checkOutShoppingCart, com.redhat.coolstore.rest.CartEndpoint.checkout, com.redhat.coolstore.utils.Transformers.jsonToOrder, com.redhat.coolstore.service.OrderServiceMDB.onMessage, com.redhat.coolstore.service.InventoryNotificationMDB.onMessage, com.redhat.coolstore.rest.CartEndpoint.delete, com.redhat.coolstore.rest.CartEndpoint.set
```

**What this tells you:**

- **Score: 40.6/100** -- a moderate impact score. This function is called by several REST endpoints and the checkout flow.
- **6 direct callers** -- six functions call `priceShoppingCart` directly.
- **Impact zone: 12** -- the full transitive closure affects 12 functions, meaning a change here could ripple through 12 code paths.
- The callers span two classes: `ShoppingCartService` (internal) and `CartEndpoint` (REST API layer), showing that this function bridges the service and API layers.

### 2. JSON Output

For machine consumption or scripting, use `-f json`:

```bash
rgctl -r example/coolstore -f json blast-radius priceShoppingCart
```

**Output (truncated):**

```json
{
  "metrics": {
    "direct_callers_count": 6,
    "impact_zone_size": 12,
    "score": 40.6
  },
  "schema_version": 2,
  "target": {
    "canonical_fqn": "ShoppingCartService::priceShoppingCart",
    "class_context": "ShoppingCartService",
    "file_path": "src/main/java/com/redhat/coolstore/service/ShoppingCartService.java",
    "id": "cac00c48-70f7-56ca-bdbc-667d7f51e916",
    "language": "java",
    "symbol": "priceShoppingCart"
  },
  "topology": {
    "direct_callers": [
      {
        "file_path": "src/main/java/com/redhat/coolstore/service/ShoppingCartService.java",
        "fqn": "com.redhat.coolstore.service.ShoppingCartService.checkOutShoppingCart",
        "id": "e3d4f4a9-86c7-57af-88cd-6f8f97816e68"
      },
      {
        "file_path": "src/main/java/com/redhat/coolstore/rest/CartEndpoint.java",
        "fqn": "com.redhat.coolstore.rest.CartEndpoint.delete",
        "id": "96716012-e50a-57e6-b4bc-d51d8a8f8fc0"
      },
      {
        "file_path": "src/main/java/com/redhat/coolstore/rest/CartEndpoint.java",
        "fqn": "com.redhat.coolstore.rest.CartEndpoint.set",
        "id": "1d3e30cb-4a5b-57b9-adb1-9f93c22b350a"
      }
    ],
    "impact_zone": [
      {
        "file_path": "src/main/java/com/redhat/coolstore/rest/CartEndpoint.java",
        "fqn": "com.redhat.coolstore.rest.CartEndpoint.dedupeCartItems",
        "id": "19fb0367-1cad-5749-9bba-bba302903dde"
      },
      {
        "file_path": "src/main/java/com/redhat/coolstore/rest/CartEndpoint.java",
        "fqn": "com.redhat.coolstore.rest.CartEndpoint.add",
        "id": "19861464-9c28-5c96-bc3e-3963f9b0e4a1"
      },
      {
        "file_path": "src/main/java/com/redhat/coolstore/model/ShoppingCart.java",
        "fqn": "com.redhat.coolstore.model.ShoppingCart.addShoppingCartItem",
        "id": "80333090-4ac0-54f9-9650-8df523c35205"
      }
    ]
  }
}
```

The JSON output provides full detail: the target function's signature, file path, language, and unique ID; each caller and impact-zone member with their FQNs and file paths; and the SCC (strongly connected component) ID for cycle detection.

### 3. Limiting Depth

By default, blast radius computes the full transitive closure. To limit analysis to a specific number of call hops:

```bash
rgctl -r example/coolstore blast-radius getShoppingCart --depth 3
```

**Output:**

```
Blast radius for 'getShoppingCart'
  Score: 40.5/100
  Direct callers: 6
  Impact zone: 10
  Callers: com.redhat.coolstore.service.ShoppingCartService.checkOutShoppingCart,
           com.redhat.coolstore.rest.CartEndpoint.getCart,
           com.redhat.coolstore.rest.CartEndpoint.add,
           com.redhat.coolstore.rest.CartEndpoint.dedupeCartItems,
           com.redhat.coolstore.rest.CartEndpoint.delete,
           com.redhat.coolstore.rest.CartEndpoint.set
```

The `--depth 3` flag limits the impact zone to at most 3 levels of callers, useful for large graphs where the full closure is too expensive or noisy.

### 4. Low Blast Radius Example

Compare with a constructor, which typically has zero callers:

```bash
rgctl -r example/coolstore blast-radius ShoppingCartService
```

**Output:**

```
Blast radius for 'ShoppingCartService'
  Score: 0.0/100
  Direct callers: 0
  Impact zone: 0
```

A score of 0 means this function (the constructor) has no upstream callers in the graph -- it is safe to modify in isolation.

### 5. Blast Radius with Policy

Use `--policy-file` to enforce thresholds:

```bash
rgctl -r example/coolstore -f json blast-radius priceShoppingCart \
  --policy-file example/coolstore/policy.json
```

The policy file (`policy.json`) defines rules like maximum impact zone size. If `priceShoppingCart` violates any rule, the `violations` array in the output will be populated and the exit code will be non-zero.

## Understanding the Score

The blast-radius score (0--100) combines:

- **Direct callers count** -- how many functions call the target directly
- **Impact zone size** -- how many functions are in the full transitive closure
- **Graph centrality** -- the target's position in the overall call graph

A score of 0 means the function is isolated. Scores above 50 indicate high-impact functions that should be changed with caution.

## Benefits

- **Quantified risk.** Replace intuition with a concrete score before making changes.
- **Full topology.** See not just who calls a function, but the entire upstream ripple effect.
- **Multi-format output.** Text for quick checks, JSON for CI pipelines and agent workflows.
- **Depth limiting.** Control the analysis scope for large codebases.
- **Policy integration.** Combine with `check` to enforce blast-radius thresholds in CI.

## Related Guides

- [Discovering and Indexing a Codebase](discovering-and-indexing.md) -- must run `discover` before blast-radius
- [CI Policy Checks](ci-policy-checks.md) -- enforce blast-radius policies in CI
- [Structured graph queries](structured-query.md) -- `callers`, `callees`, and `relations --edge calls`
- [Hybrid CPG](hybrid-cpg.md) -- combine blast-radius with per-function CPG analysis
- [Migration Planning](migration-planning.md) -- blast-radius scores feed into migration ordering
