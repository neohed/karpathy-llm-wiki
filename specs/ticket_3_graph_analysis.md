# Ticket 3 — Graph analysis utilities, staleness tracking, and priority queue

## Context

`wiki_graph.py` has a clean read/write API (Ticket 1) and is being populated
during ingest (Ticket 2). This ticket extends `wiki_graph.py` with the analysis
functions that `consolidate.py` will consume.

No LLM calls. No changes to `ingest.py`. No changes to `wiki_retrieval.py`.
Pure Python, all functions derivable from graph data.

The core design principle: **nothing is stored that can be calculated.** Staleness,
priority, and bridge status are all computed on demand from normalised graph data —
never cached as node fields. This keeps the graph clean and ensures the priority
queue always reflects true current state.

---

## Node schema extension

Add two fields to `GraphNode` (extend the dataclass from Ticket 1):

```python
@dataclass
class GraphNode:
    type: str
    label: str
    sources: list[str]
    created: str
    updated: str
    consolidated: Optional[str] = None   # ISO date of last consolidation run, or None
    consolidation_version: int = 0       # increments on each consolidation
```

`consolidated` is `None` if the node has never been consolidated.
`consolidation_version` allows tracking how many times a node has been rewritten.

These fields are already included in the node schema defined in Ticket 1.
If they were omitted, add them now.

Also add a method to `WikiGraph`:

```python
def mark_consolidated(self, key: str, today: str) -> None:
    """
    Record that a node has just been consolidated.
    Sets consolidated = today, increments consolidation_version, updates updated.
    """
    ...
```

---

## Staleness calculation

Staleness measures how much has changed since a node was last consolidated.
It is always calculated, never stored.

```python
def staleness(self, key: str) -> int:
    """
    Calculate staleness score for a node.

    Staleness = number of source edges added after last consolidation
              + number of directly connected nodes consolidated after this node

    A node that has never been consolidated has staleness = len(node.sources).
    A node with no new sources and no recently-consolidated neighbours has staleness = 0.
    """
    node = self.get_node(key)
    if node is None:
        return 0

    # Sources added after last consolidation
    if node.consolidated is None:
        new_sources = len(node.sources)
    else:
        new_sources = sum(
            1 for s in node.sources
            if self._source_added_after(s, node.consolidated)
        )

    # Neighbours consolidated more recently than this node
    # (their update may have implications for this node)
    dirty_neighbours = sum(
        1 for n in self.neighbours(key)
        if self._neighbour_consolidated_after(n, node.consolidated)
    )

    return new_sources + dirty_neighbours
```

### Helper methods (private)

```python
def _source_added_after(self, source_key: str, date_str: str) -> bool:
    """
    Return True if the edge from source_key to any node was created after date_str.
    Uses edge.created field.
    """
    ...

def _neighbour_consolidated_after(self, neighbour_key: str,
                                   date_str: Optional[str]) -> bool:
    """
    Return True if neighbour was consolidated after date_str.
    If date_str is None (node never consolidated), always returns True
    if neighbour has ever been consolidated.
    """
    ...
```

---

## Bridge factor

A bridge node connects otherwise separate clusters. It is identified by having
edges into at least two distinct clusters where a cluster is defined as a group
of nodes that share more edges with each other than with the rest of the graph.

For the current scale, use a pragmatic definition:

A node is a bridge if it has neighbours in at least two different `type` categories.
For example: a concept node that has edges to both `source` nodes from different
thematic groups AND edges to `entity` nodes counts as a bridge.

A more precise definition uses cluster detection — see `get_clusters()` below.

```python
def bridge_factor(self, key: str) -> float:
    """
    Return a multiplier reflecting how much this node bridges separate clusters.

    1.0  — node connects only within one cluster
    1.5  — node connects two distinct clusters  
    2.0  — node connects three or more distinct clusters

    Cluster membership is determined by get_clusters().
    """
    clusters = self.get_clusters()
    node_clusters = set()
    for neighbour in self.neighbours(key):
        for cluster_id, members in clusters.items():
            if neighbour in members:
                node_clusters.add(cluster_id)
    n = len(node_clusters)
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.5
    return 2.0
```

---

## Priority score

```python
def priority_score(self, key: str) -> float:
    """
    Calculate consolidation priority for a node.

    priority = staleness × degree × bridge_factor

    Higher score = should be consolidated sooner.
    Score of 0 means node is fully up to date.

    Only concept, entity, and analysis nodes are consolidation candidates.
    Source nodes are never consolidated — they are summaries of immutable raw files.
    """
    node = self.get_node(key)
    if node is None or node.type == "source":
        return 0.0

    s = self.staleness(key)
    if s == 0:
        return 0.0

    return float(s * self.degree(key) * self.bridge_factor(key))
```

---

## Priority queue

```python
def priority_queue(self, min_score: float = 0.0) -> list[tuple[float, str]]:
    """
    Return all consolidation candidates sorted by priority score descending.

    Each item is (score, node_key).
    Nodes with score == 0 are excluded unless min_score == 0.
    Source nodes are always excluded.

    Usage:
        queue = graph.priority_queue()
        top_10 = queue[:10]
    """
    scores = []
    for key, node in self.all_nodes().items():
        if node.type == "source":
            continue
        score = self.priority_score(key)
        if score > min_score:
            scores.append((score, key))
    return sorted(scores, reverse=True)
```

---

## Cluster detection

Clusters are groups of nodes more connected to each other than to the rest of
the graph. Use a simple label propagation approach suitable for small graphs:

```python
def get_clusters(self) -> dict[str, set[str]]:
    """
    Detect clusters using connected components as a baseline, refined by
    edge density within groups.

    Returns dict of {cluster_id: set of node keys}.
    cluster_id is the key of the highest-degree node in the cluster.

    For graphs under ~500 nodes, connected components with type-weighting
    is sufficient. Does not need to be perfect — it feeds bridge_factor
    which uses a float multiplier, not a binary decision.
    """
    ...
```

Implementation approach:
1. Start with connected components (nodes reachable from each other via any edge)
2. Within each component, sub-cluster by dominant node type if the component is
   large (> 10 nodes) — concepts cluster together, entities cluster together
3. Return the result as a stable dict (sort by cluster_id for determinism)

For the current corpus size (< 100 nodes expected) connected components alone
is sufficient. Add the sub-clustering refinement but keep it simple.

---

## High degree nodes

```python
def get_high_degree_nodes(self, threshold: int = 3) -> list[tuple[int, str]]:
    """
    Return nodes with degree >= threshold, sorted by degree descending.
    Each item is (degree, node_key).
    Excludes source nodes.
    """
    ...
```

---

## Shared sources

```python
def get_shared_sources(self, key_a: str, key_b: str) -> list[str]:
    """
    Return source node keys that have edges to both key_a and key_b.
    Used to identify which documents connect two concepts.
    """
    sources_a = {
        e.from_node for e in self.edges_to(key_a)
        if self.get_node(e.from_node) and self.get_node(e.from_node).type == "source"
    }
    sources_b = {
        e.from_node for e in self.edges_to(key_b)
        if self.get_node(e.from_node) and self.get_node(e.from_node).type == "source"
    }
    return sorted(sources_a & sources_b)
```

---

## Graph report

Extend the existing `--report` CLI output to include analysis data:

```
=== Wiki Graph Report ===

Nodes: 24 total (8 source, 12 concept, 4 entity, 0 analysis)
Edges: 31 total (18 discusses, 8 introduces, 3 refines, 2 contradicts)

--- Priority Queue (top 10) ---
  1. concepts/epistemic-humility.md       score=12.0  staleness=4  degree=3  bridge=2.0x
  2. concepts/shadow.md                   score=9.0   staleness=3  degree=3  bridge=1.0x
  3. entities/carl-jung.md               score=6.0   staleness=2  degree=3  bridge=1.0x
  ...

--- Bridge Nodes ---
  concepts/epistemic-humility.md  (connects 3 clusters)
  concepts/impermanence.md        (connects 2 clusters)

--- Clusters ---
  Cluster A (8 nodes): concepts/shadow, concepts/persona, entities/carl-jung ...
  Cluster B (6 nodes): concepts/epistemic-humility, entities/hayek ...
  Cluster C (4 nodes): concepts/habit-loop, concepts/identity ...

--- Never Consolidated ---
  24 nodes (all — no consolidation run yet)

--- Shared Sources ---
  concepts/shadow.md ↔ concepts/impermanence.md: [sources/shadow-work.md,
                                                   sources/buddhist-principles.md]
```

The report is the human-readable interface to the graph before running consolidation.
`python wiki_graph.py --report` should give a clear picture of what consolidation
will prioritise and why.

---

## Verification

```python
# Build a test graph that represents a realistic small corpus
g = WikiGraph(graph_path="/tmp/test_graph_analysis.json")

# Three source nodes
g.add_node("sources/shadow-work.md", type="source", label="Shadow Work")
g.add_node("sources/buddhist-principles.md", type="source", label="Buddhist Principles")
g.add_node("sources/burke-hayek-popper.md", type="source", label="Burke-Hayek-Popper")

# Concept nodes
g.add_node("concepts/shadow.md", type="concept", label="Shadow",
           source="sources/shadow-work.md")
g.add_node("concepts/epistemic-humility.md", type="concept",
           label="Epistemic Humility", source="sources/burke-hayek-popper.md")
g.add_node("concepts/impermanence.md", type="concept", label="Impermanence",
           source="sources/buddhist-principles.md")

# Entity nodes
g.add_node("entities/carl-jung.md", type="entity", label="Carl Jung",
           source="sources/shadow-work.md")

# Edges — epistemic-humility is a bridge: touched by all three sources
g.add_edge("sources/shadow-work.md", "concepts/shadow.md", "introduces")
g.add_edge("sources/shadow-work.md", "entities/carl-jung.md", "discusses")
g.add_edge("sources/buddhist-principles.md", "concepts/impermanence.md", "introduces")
g.add_edge("sources/buddhist-principles.md", "concepts/epistemic-humility.md", "discusses")
g.add_edge("sources/burke-hayek-popper.md", "concepts/epistemic-humility.md", "discusses")
g.add_edge("sources/shadow-work.md", "concepts/epistemic-humility.md", "parallels")

g.save()

# Staleness — nothing consolidated yet, all nodes stale
assert g.staleness("concepts/shadow.md") == 1       # one source
assert g.staleness("concepts/epistemic-humility.md") == 3  # three sources

# Priority — epistemic-humility should rank highest
queue = g.priority_queue()
assert queue[0][1] == "concepts/epistemic-humility.md"

# Bridge factor — epistemic-humility connects all three source clusters
assert g.bridge_factor("concepts/epistemic-humility.md") >= 1.5

# Shared sources
shared = g.get_shared_sources("concepts/epistemic-humility.md",
                               "concepts/impermanence.md")
assert "sources/buddhist-principles.md" in shared

# Mark consolidated and verify staleness drops
g.mark_consolidated("concepts/shadow.md", "2026-05-14")
assert g.staleness("concepts/shadow.md") == 0

# After consolidating shadow, epistemic-humility's staleness should increase
# because a neighbour was consolidated after it
assert g.staleness("concepts/epistemic-humility.md") >= 3

print("All assertions passed.")
```

---

## What must NOT be built in this ticket

- No LLM calls
- No changes to `ingest.py`
- No changes to `wiki_retrieval.py`
- `consolidate.py` — Ticket 4
- Structural suggestion analysis (split/merge recommendations) — Ticket 7

---

## File summary

Files modified:
- `wiki_graph.py` — add staleness, bridge_factor, priority_score, priority_queue,
  get_clusters, get_high_degree_nodes, get_shared_sources, mark_consolidated,
  extend --report CLI output

Files not touched:
- `ingest.py`
- `prompts.py`
- `wiki_retrieval.py`
- `CLAUDE.md`
