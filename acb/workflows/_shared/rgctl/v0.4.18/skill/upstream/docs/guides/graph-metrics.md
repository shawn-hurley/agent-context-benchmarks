# Graph Metrics

## Introduction

The `metrics` command runs **network analytics** on your code knowledge graph: PageRank to find the most important functions, betweenness centrality to identify bridging nodes, and community detection to measure the modularity of your codebase. These are the same algorithms used to analyze web link graphs and social networks, applied to the structure of your code.

Metrics give you a quantitative view of your codebase's architecture -- which functions are critical hubs, which serve as bridges between modules, and how well the code separates into distinct clusters.

## Use Cases

- **Identify critical functions.** PageRank reveals the most structurally important functions -- the ones most depended upon.
- **Find architectural bottlenecks.** High betweenness centrality indicates functions that bridge otherwise disconnected parts of the codebase.
- **Measure modularity.** Community detection tells you how well your code separates into cohesive groups, quantified by a modularity score.
- **Prioritize refactoring.** Focus effort on high-PageRank, high-betweenness functions where changes have the largest structural impact.
- **Track architecture over time.** Run metrics on each release to detect modularity drift or emerging hotspots.

## Example Project

This guide uses the **CoolStore** (`example/coolstore`). Make sure you have run `discover` first:

CoolStore examples use `-l java` to index the Java backend only (skip Angular/bower).

```bash
rgctl -r example/coolstore discover -l java
```

## Step-by-Step

### 1. PageRank

PageRank ranks functions by structural importance -- how many other functions depend on them, directly or transitively. Functions called by many high-PageRank callers themselves receive a high score.

```bash
rgctl -r example/coolstore -f json metrics --pagerank
```

**Output (truncated):**

```json
{
  "pagerank": {
    "converged": true,
    "iterations": 9,
    "max_delta": 6.728148834490855e-07,
    "top": [
      {
        "node": "decbba50-f8fa-50b9-905c-e75d2a69cab5",
        "pagerank": 0.029448900943039962
      },
      {
        "node": "1f33cd28-e5f9-5c78-a39d-86d878b734fb",
        "pagerank": 0.007301250441888811
      },
      {
        "node": "8b0e2652-bbb9-575e-bf21-e35a79d53d13",
        "pagerank": 0.006971584463128407
      },
      {
        "node": "d942df07-720c-5947-9d4d-02fc9636a855",
        "pagerank": 0.006581553480124427
      },
      {
        "node": "7cdc70e2-72a4-59b7-bdd1-95b16f6ec016",
        "pagerank": 0.006539350370383329
      }
    ]
  },
  "schema_version": 1
}
```

**What this tells you:**

- **`converged: true`** -- PageRank converged in 9 iterations on this Java-only graph.
- **`top`** -- the highest-ranked functions by PageRank score. Top entries are node UUIDs (resolve with `find` / callers); ranks are stable after convergence.
- **`node`** -- the UUID of each function. Resolve with `rgctl find` by id or follow-up `blast-radius` JSON.

To increase iterations for better convergence:

```bash
rgctl -r example/coolstore -f json metrics --pagerank --iterations 50
```

### 2. Betweenness Centrality

Betweenness centrality measures how often a function lies on the shortest path between two other functions. High-betweenness functions are architectural bridges -- removing them would disconnect parts of the call graph.

```bash
rgctl -r example/coolstore -f json metrics --betweenness
```

**Output (truncated):**

```json
{
  "betweenness": [
    {
      "node": "778cc6fc-8ae3-40c8-9137-82e040e5b5d1",
      "score": 0.000021622996659875876
    },
    {
      "node": "7a576df2-e51b-405e-b5a1-6e14bca1c4c1",
      "score": 0.000021567926001973067
    }
  ],
  "schema_version": 1
}
```

**What this tells you:**

- Functions with the highest betweenness scores are the most critical bridges in the call graph.
- These are the functions where a bug or breaking change would propagate most widely across otherwise separate modules.
- Low betweenness means a function is "internal" to a single cluster.

### 3. Community Detection

Community detection partitions the graph into clusters of tightly connected functions using the Louvain algorithm. The `modularity` score (0--1) measures how well the code separates into distinct groups.

```bash
rgctl -r example/coolstore -f json metrics --communities
```

**Output:**

```json
{
  "communities": {
    "assignments": 14763,
    "count": 11303,
    "modularity": 0.3076728222682732
  },
  "schema_version": 1
}
```

**What this tells you:**

- **`count: 11303`** -- the algorithm identified 11,303 distinct communities.
- **`assignments: 14763`** -- all 14,763 nodes in the graph were assigned to a community.
- **`modularity: 0.3077`** -- a modularity score of ~0.31. Values above 0.3 indicate meaningful community structure; values above 0.5 indicate strong modularity. The CoolStore application has moderate modularity, consistent with its nature as a single-deployment application.

### 4. Combining Metrics

You can run multiple metrics in a single command:

```bash
rgctl -r example/coolstore -f json metrics --pagerank --betweenness --communities
```

This returns all three analyses in a single JSON response.

### 5. Resolving Node UUIDs

The metrics output uses node UUIDs. To find out which function a UUID refers to, query the graph:

```bash
rgctl -r example/coolstore -f json find priceShoppingCart --type function --exact
```

Or use `blast-radius` which resolves names automatically:

```bash
rgctl -r example/coolstore blast-radius priceShoppingCart
```

## Understanding the Algorithms

| Metric | What It Measures | High Score Means |
|--------|-----------------|-----------------|
| **PageRank** | Recursive importance via incoming edges | Many important callers depend on this function |
| **Betweenness** | Frequency on shortest paths between pairs | This function bridges otherwise disconnected modules |
| **Community modularity** | Quality of graph partitioning | The codebase has strong, well-separated functional clusters |

## Benefits

- **Quantitative architecture analysis.** Replace subjective assessments with concrete scores.
- **Identify hotspots.** High-PageRank and high-betweenness functions are where bugs hurt most and refactoring pays off most.
- **Measure modularity.** Track whether your codebase is becoming more or less modular over time.
- **Standard algorithms.** PageRank, betweenness centrality, and Louvain community detection are well-understood network science tools.
- **Fast.** All metrics run in-memory on the graph snapshot, typically completing in under a second.

## Related Guides

- [Discovering and Indexing a Codebase](discovering-and-indexing.md) -- must run `discover` before metrics
- [Community Detection](community-detection.md) -- dig deeper into community analysis
- [Blast Radius Analysis](blast-radius-analysis.md) -- per-function impact analysis that complements metrics
- [Migration Planning](migration-planning.md) -- metrics feed into migration ordering
