# Ticket 1 — wiki_graph.py: data structure and read/write API

## Context

The wiki pipeline currently has no awareness of relationships between wiki pages.
This ticket creates `wiki_graph.py` — a standalone module that defines the knowledge
graph schema and provides a clean read/write API over it.

No LLM calls. No changes to `ingest.py`. No changes to `wiki_retrieval.py`.
This module exists in isolation and is tested independently before anything depends on it.

The graph is persisted as `.wiki_graph.json` alongside the wiki. This file is
machine-written and gitignored. A graph database migration path is deliberately
left open — the API surface of this module is the abstraction layer that would
be replaced if we move to Neo4j or similar later.

---

## Graph schema

### JSON structure

```json
{
  "meta": {
    "version": 1,
    "created": "2026-05-12",
    "last_updated": "2026-05-12",
    "node_count": 4,
    "edge_count": 6
  },
  "nodes": {
    "concepts/buddhism.md": {
      "type": "concept",
      "label": "Buddhism",
      "sources": ["sources/buddhist-principles.md"],
      "created": "2026-05-12",
      "updated": "2026-05-12",
      "consolidated": false
    }
  },
  "edges": [
    {
      "from": "sources/buddhist-principles.md",
      "to": "concepts/buddhism.md",
      "type": "discusses",
      "created": "2026-05-12"
    }
  ]
}
```

### Node types

| Type | Description |
|------|-------------|
| `source` | Summary page of a raw source document |
| `concept` | An idea, principle, or domain of knowledge |
| `entity` | A person, school of thought, or framework |
| `analysis` | A synthesis or emergent insight (written by consolidate.py) |

### Edge types

| Type | Meaning |
|------|---------|
| `discusses` | Source page covers this concept or entity |
| `introduces` | Source is the primary origin of this concept in the wiki |
| `contradicts` | One page makes a claim that conflicts with another |
| `parallels` | Two concepts share structural similarity across domains |
| `synthesises` | An analysis page draws on these source or concept pages |
| `refines` | A later source adds nuance to an existing concept |

All node keys and edge `from`/`to` values are relative paths from the wiki/ root
(e.g. `concepts/buddhism.md`, `sources/atomic-habits.md`). They match the path
strings used in `ingest.py` action dicts and `wiki_retrieval.py` index keys.

---

## WikiGraph class

### Constructor and persistence

```python
@dataclass
class GraphNode:
    type: str                    # "source" | "concept" | "entity" | "analysis"
    label: str                   # human-readable name
    sources: list[str]           # source pages that contributed to this node
    created: str                 # ISO date
    updated: str                 # ISO date
    consolidated: bool = False   # True after consolidate.py rewrites this page


@dataclass
class GraphEdge:
    from_node: str    # relative wiki path
    to_node: str      # relative wiki path
    type: str         # edge type from vocabulary above
    created: str      # ISO date


class WikiGraph:
    def __init__(self, graph_path: str = ".wiki_graph.json"):
        ...

    @classmethod
    def load(cls, graph_path: str = ".wiki_graph.json") -> "WikiGraph":
        """Load from disk. Creates empty graph if file does not exist."""
        ...

    def save(self) -> None:
        """Write to disk atomically (write temp file, then rename)."""
        ...
```

### Node operations

```python
def add_node(
    self,
    key: str,           # relative wiki path e.g. "concepts/buddhism.md"
    type: str,
    label: str,
    source: Optional[str] = None,  # source page that created/updated this node
) -> GraphNode:
    """
    Add a new node or update an existing one.
    If the node exists and source is provided, append source to node.sources
    (deduplicated). Update node.updated. Do not overwrite type or label.
    Returns the node.
    """
    ...

def get_node(self, key: str) -> Optional[GraphNode]:
    ...

def has_node(self, key: str) -> bool:
    ...

def mark_consolidated(self, key: str) -> None:
    """Set node.consolidated = True and update node.updated."""
    ...

def all_nodes(self) -> dict[str, GraphNode]:
    """Return all nodes keyed by wiki path."""
    ...

def nodes_of_type(self, type: str) -> dict[str, GraphNode]:
    ...
```

### Edge operations

```python
def add_edge(
    self,
    from_node: str,
    to_node: str,
    type: str,
) -> GraphEdge:
    """
    Add an edge. Silently deduplicates — if an edge with the same
    (from_node, to_node, type) already exists, do not add a duplicate.
    Returns the edge (existing or new).
    """
    ...

def has_edge(self, from_node: str, to_node: str, type: str) -> bool:
    ...

def edges_from(self, key: str) -> list[GraphEdge]:
    """All edges where from_node == key."""
    ...

def edges_to(self, key: str) -> list[GraphEdge]:
    """All edges where to_node == key."""
    ...

def neighbours(self, key: str) -> list[str]:
    """
    All nodes directly connected to key (in either direction).
    Returns deduplicated list of node keys.
    """
    ...
```

### Graph metrics

```python
def degree(self, key: str) -> int:
    """Total number of edges touching this node (in + out)."""
    ...

def in_degree(self, key: str) -> int:
    """Number of edges pointing TO this node."""
    ...

def out_degree(self, key: str) -> int:
    """Number of edges pointing FROM this node."""
    ...

def shared_sources(self, key_a: str, key_b: str) -> list[str]:
    """
    Source nodes that have edges to both key_a and key_b.
    Used to identify which documents connect two concepts.
    """
    ...
```

### Serialisation helpers (private)

```python
def _to_dict(self) -> dict:
    """Serialise full graph to JSON-compatible dict."""
    ...

@classmethod
def _from_dict(cls, data: dict, graph_path: str) -> "WikiGraph":
    """Deserialise from dict loaded from JSON."""
    ...
```

---

## Atomic file writes

All saves must be atomic to prevent corruption on crash mid-write:

```python
import tempfile

def save(self) -> None:
    data = self._to_dict()
    path = Path(self._graph_path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)
```

---

## Smoke test / CLI

Add a `__main__` block so the module can be run standalone:

```bash
python wiki_graph.py             # print graph report to stdout
python wiki_graph.py --validate  # check graph integrity (no dangling edges, etc.)
```

The report should print:
- Total nodes by type
- Total edges by type
- Top 5 nodes by degree
- Any integrity issues (edges referencing nodes that don't exist)

---

## What must NOT be built in this ticket

- No LLM calls
- No analysis functions (bridge nodes, clusters, shortest path) — those are Ticket 3
- No changes to ingest.py — Ticket 2 wires this into the pipeline
- No changes to wiki_retrieval.py
- No changes to CLAUDE.md

---

## Verification

Create a small standalone test at the bottom of `wiki_graph.py` or in a separate
`test_wiki_graph.py`. It should exercise the full API without touching the filesystem
(use a temp file):

```python
# Create graph
g = WikiGraph(graph_path="/tmp/test_graph.json")

# Add nodes
g.add_node("sources/shadow-work.md", type="source", label="Shadow Work")
g.add_node("concepts/shadow.md", type="concept", label="Shadow",
           source="sources/shadow-work.md")
g.add_node("concepts/persona.md", type="concept", label="Persona",
           source="sources/shadow-work.md")
g.add_node("entities/carl-jung.md", type="entity", label="Carl Jung",
           source="sources/shadow-work.md")

# Add edges
g.add_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses")
g.add_edge("sources/shadow-work.md", "concepts/persona.md", "discusses")
g.add_edge("sources/shadow-work.md", "entities/carl-jung.md", "discusses")

# Save and reload
g.save()
g2 = WikiGraph.load("/tmp/test_graph.json")

# Verify round-trip
assert g2.has_node("concepts/shadow.md")
assert g2.has_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses")
assert g2.degree("sources/shadow-work.md") == 3
assert "concepts/shadow.md" in g2.neighbours("sources/shadow-work.md")
assert g2.nodes_of_type("concept") == {
    "concepts/shadow.md": ...,
    "concepts/persona.md": ...,
}

# Test deduplication
g2.add_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses")
assert len(g2.edges_from("sources/shadow-work.md")) == 3  # not 4

# Test shared sources
g2.add_node("sources/buddhist-principles.md", type="source",
            label="Buddhist Principles")
g2.add_edge("sources/buddhist-principles.md", "concepts/shadow.md", "parallels")
shared = g2.shared_sources("concepts/shadow.md", "concepts/persona.md")
assert "sources/shadow-work.md" in shared

print("All assertions passed.")
```

---

## File summary

Files created:
- `wiki_graph.py` — new file, `WikiGraph` class + dataclasses + CLI

Files not touched:
- `ingest.py`
- `wiki_retrieval.py`
- `prompts.py` (being created in Ticket 0b)
- `CLAUDE.md`
