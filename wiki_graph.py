#!/usr/bin/env python3
"""
wiki_graph.py — Knowledge graph data structure, read/write API, and analysis utilities.

No LLM calls. Standalone module. Persists to .wiki_graph.json.

Usage:
  python wiki_graph.py             # print graph report
  python wiki_graph.py --report    # print graph report (explicit)
  python wiki_graph.py --validate  # check integrity (dangling edges, etc.)
  python wiki_graph.py --test      # run smoke tests
"""

import json
import sys
import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    type: str                   # "source" | "concept" | "entity" | "analysis"
    label: str
    sources: list[str]          # source pages that contributed to this node
    created: str                # ISO date
    updated: str                # ISO date
    consolidated: Optional[str] = None  # ISO date of last consolidation, or None
    consolidation_version: int = 0      # increments on each consolidation run


@dataclass
class GraphEdge:
    from_node: str              # relative wiki path
    to_node: str                # relative wiki path
    type: str                   # edge type from vocabulary
    created: str                # ISO date
    synthesis: Optional[str] = None  # filepath to analyses/ doc, relative to wiki/


# ---------------------------------------------------------------------------
# WikiGraph
# ---------------------------------------------------------------------------

class WikiGraph:
    SCHEMA_VERSION = 1

    def __init__(self, graph_path: str = ".wiki_graph.json"):
        self._graph_path = graph_path
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, graph_path: str = ".wiki_graph.json") -> "WikiGraph":
        """Load from disk. Creates empty graph if file does not exist."""
        path = Path(graph_path)
        if not path.exists():
            return cls(graph_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls._from_dict(data, graph_path)

    def save(self) -> None:
        """Write to disk atomically (write temp, then rename)."""
        data = self._to_dict()
        path = Path(self._graph_path)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.rename(path)

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(
        self,
        key: str,
        type: str,
        label: str,
        source: Optional[str] = None,
    ) -> GraphNode:
        """
        Add a new node or update an existing one.
        If the node exists and source is provided, append source to node.sources
        (deduplicated). Update node.updated. Do not overwrite type or label.
        """
        today = date.today().isoformat()
        if key in self._nodes:
            node = self._nodes[key]
            if source and source not in node.sources:
                node.sources.append(source)
            node.updated = today
        else:
            sources = [source] if source else []
            node = GraphNode(
                type=type,
                label=label,
                sources=sources,
                created=today,
                updated=today,
            )
            self._nodes[key] = node
        return node

    def get_node(self, key: str) -> Optional[GraphNode]:
        return self._nodes.get(key)

    def has_node(self, key: str) -> bool:
        return key in self._nodes

    def mark_consolidated(self, key: str, today: str) -> None:
        """
        Record that a node has just been consolidated.
        Sets consolidated = today, increments consolidation_version, updates updated.
        """
        node = self._nodes.get(key)
        if node:
            node.consolidated = today
            node.consolidation_version += 1
            node.updated = today

    def all_nodes(self) -> dict[str, GraphNode]:
        return dict(self._nodes)

    def nodes_of_type(self, type: str) -> dict[str, GraphNode]:
        return {k: v for k, v in self._nodes.items() if v.type == type}

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(self, from_node: str, to_node: str, type: str) -> GraphEdge:
        """
        Add an edge. Silently deduplicates — if an edge with the same
        (from_node, to_node, type) already exists, do not add a duplicate.
        """
        for edge in self._edges:
            if edge.from_node == from_node and edge.to_node == to_node and edge.type == type:
                return edge
        edge = GraphEdge(
            from_node=from_node,
            to_node=to_node,
            type=type,
            created=date.today().isoformat(),
        )
        self._edges.append(edge)
        return edge

    def has_edge(self, from_node: str, to_node: str, type: str) -> bool:
        return any(
            e.from_node == from_node and e.to_node == to_node and e.type == type
            for e in self._edges
        )

    def edges_from(self, key: str) -> list[GraphEdge]:
        return [e for e in self._edges if e.from_node == key]

    def edges_to(self, key: str) -> list[GraphEdge]:
        return [e for e in self._edges if e.to_node == key]

    def neighbours(self, key: str) -> list[str]:
        """All nodes directly connected to key (either direction), deduplicated."""
        seen: set[str] = set()
        for e in self._edges:
            if e.from_node == key:
                seen.add(e.to_node)
            elif e.to_node == key:
                seen.add(e.from_node)
        return list(seen)

    # ------------------------------------------------------------------
    # Graph metrics
    # ------------------------------------------------------------------

    def degree(self, key: str) -> int:
        return sum(
            1 for e in self._edges if e.from_node == key or e.to_node == key
        )

    def in_degree(self, key: str) -> int:
        return sum(1 for e in self._edges if e.to_node == key)

    def out_degree(self, key: str) -> int:
        return sum(1 for e in self._edges if e.from_node == key)

    def edge_count(self) -> int:
        """Return total number of edges in the graph."""
        return len(self._edges)

    def shared_sources(self, key_a: str, key_b: str) -> list[str]:
        """
        Nodes that have edges to both key_a and key_b.
        """
        sources_a = {e.from_node for e in self._edges if e.to_node == key_a}
        sources_b = {e.from_node for e in self._edges if e.to_node == key_b}
        return list(sources_a & sources_b)

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

    # ------------------------------------------------------------------
    # Edge synthesis metadata
    # ------------------------------------------------------------------

    def set_edge_synthesis(
        self,
        from_node: str,
        to_node: str,
        edge_type: str,
        filepath: str,
    ) -> None:
        """
        Set the synthesis filepath on an existing edge.
        filepath is relative to wiki/ e.g. "analyses/shadow-buddhism.md"
        Raises KeyError if edge does not exist.
        """
        for edge in self._edges:
            if (edge.from_node == from_node
                    and edge.to_node == to_node
                    and edge.type == edge_type):
                edge.synthesis = filepath
                return
        raise KeyError(f"Edge not found: {from_node} -[{edge_type}]-> {to_node}")

    def get_edge_synthesis(
        self,
        from_node: str,
        to_node: str,
        edge_type: str,
    ) -> Optional[str]:
        """Return the synthesis filepath for an edge, or None if none exists."""
        for edge in self._edges:
            if (edge.from_node == from_node
                    and edge.to_node == to_node
                    and edge.type == edge_type):
                return edge.synthesis
        return None

    def edges_needing_synthesis(
        self,
        min_bridge_factor: float = 1.5,
    ) -> list[GraphEdge]:
        """
        Return edges that are candidates for synthesis.

        Criteria:
        - Both from_node and to_node are concept or entity nodes
        - At least one endpoint has bridge_factor >= min_bridge_factor
        - get_shared_sources(from_node, to_node) has >= 2 entries

        Returns edges sorted by combined priority score of endpoints descending.
        """
        candidates = []
        for edge in self._edges:
            node_a = self.get_node(edge.from_node)
            node_b = self.get_node(edge.to_node)
            if not node_a or not node_b:
                continue
            if node_a.type not in ("concept", "entity"):
                continue
            if node_b.type not in ("concept", "entity"):
                continue
            bf_a = self.bridge_factor(edge.from_node)
            bf_b = self.bridge_factor(edge.to_node)
            if bf_a < min_bridge_factor and bf_b < min_bridge_factor:
                continue
            shared = self.get_shared_sources(edge.from_node, edge.to_node)
            if len(shared) < 2:
                continue
            combined = self.priority_score(edge.from_node) + self.priority_score(edge.to_node)
            candidates.append((combined, edge))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in candidates]

    def edges_with_stale_synthesis(self) -> list[GraphEdge]:
        """
        Return edges that have an existing synthesis document but where
        at least one endpoint node has been updated since the synthesis
        was last written.

        Uses file modification time of the synthesis document compared
        to node.updated dates to determine staleness.
        """
        from datetime import datetime
        result = []
        wiki_dir = Path("wiki")
        for edge in self._edges:
            if not edge.synthesis:
                continue
            synth_path = wiki_dir / edge.synthesis
            if not synth_path.exists():
                continue
            synth_date = datetime.fromtimestamp(
                synth_path.stat().st_mtime
            ).date().isoformat()
            for node_key in (edge.from_node, edge.to_node):
                node = self.get_node(node_key)
                if node and node.updated > synth_date:
                    result.append(edge)
                    break
        return result

    # ------------------------------------------------------------------
    # Staleness and consolidation analysis
    # ------------------------------------------------------------------

    def staleness(self, key: str) -> int:
        """
        Calculate staleness score for a node.

        Staleness = number of source edges added after last consolidation
                  + number of directly connected nodes consolidated after this node

        A node that has never been consolidated has staleness = number of incoming
        source edges. A node with no new sources and no recently-consolidated
        neighbours has staleness = 0.
        """
        node = self.get_node(key)
        if node is None:
            return 0

        # Incoming edges from source-type nodes
        source_edges = [
            e for e in self.edges_to(key)
            if self.get_node(e.from_node) and self.get_node(e.from_node).type == "source"
        ]

        if node.consolidated is None:
            new_sources = len(source_edges)
        else:
            new_sources = sum(1 for e in source_edges if e.created > node.consolidated)

        # Neighbours consolidated more recently than this node
        dirty_neighbours = sum(
            1 for n in self.neighbours(key)
            if self._neighbour_consolidated_after(n, node.consolidated)
        )

        return new_sources + dirty_neighbours

    def _source_added_after(self, source_key: str, date_str: str) -> bool:
        """Return True if any edge from source_key was created after date_str."""
        return any(e.created > date_str for e in self.edges_from(source_key))

    def _neighbour_consolidated_after(
        self, neighbour_key: str, date_str: Optional[str]
    ) -> bool:
        """
        Return True if neighbour was consolidated after date_str.
        If date_str is None, returns True if neighbour has ever been consolidated.
        """
        neighbour = self.get_node(neighbour_key)
        if neighbour is None or neighbour.consolidated is None:
            return False
        if date_str is None:
            return True
        return neighbour.consolidated > date_str

    # ------------------------------------------------------------------
    # Cluster detection
    # ------------------------------------------------------------------

    def _connected_components(self) -> list[set[str]]:
        """Return list of connected components as sets of node keys."""
        visited: set[str] = set()
        components: list[set[str]] = []

        for start in self._nodes:
            if start in visited:
                continue
            component: set[str] = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                for neighbour in self.neighbours(node):
                    if neighbour not in visited:
                        stack.append(neighbour)
            components.append(component)

        return components

    def get_clusters(self) -> dict[str, set[str]]:
        """
        Detect clusters using connected components as a baseline, refined by
        source-node sub-clustering within components that contain multiple sources.

        Returns dict of {cluster_id: set of node keys}.
        cluster_id is the key of the highest-degree node in the cluster (or the
        source node key when sub-clustering by source).

        When a component contains multiple source nodes (which have no direct edges
        between themselves), each source becomes its own sub-cluster and non-source
        nodes are assigned to the source they share the most edges with.
        """
        components = self._connected_components()
        clusters: dict[str, set[str]] = {}

        for comp in components:
            source_nodes = sorted(k for k in comp if self._nodes[k].type == "source")

            if len(source_nodes) <= 1:
                cluster_id = max(comp, key=self.degree)
                clusters[cluster_id] = set(comp)
            else:
                # Each source node anchors its own sub-cluster
                sub: dict[str, set[str]] = {s: {s} for s in source_nodes}
                non_sources = [k for k in comp if self._nodes[k].type != "source"]

                for key in non_sources:
                    counts: Counter = Counter()
                    for e in self._edges:
                        if e.from_node in sub and e.to_node == key:
                            counts[e.from_node] += 1
                        elif e.to_node in sub and e.from_node == key:
                            counts[e.to_node] += 1
                    if counts:
                        # Deterministic tiebreak: highest count, then alphabetical
                        best = min(counts, key=lambda k: (-counts[k], k))
                        sub[best].add(key)
                    else:
                        sub[source_nodes[0]].add(key)

                clusters.update(sub)

        return dict(sorted(clusters.items()))

    # ------------------------------------------------------------------
    # Bridge factor and priority scoring
    # ------------------------------------------------------------------

    def bridge_factor(self, key: str) -> float:
        """
        Return a multiplier reflecting how much this node bridges separate clusters.

        1.0  — node connects only within one cluster
        1.5  — node connects two distinct clusters
        2.0  — node connects three or more distinct clusters
        """
        clusters = self.get_clusters()
        node_clusters: set[str] = set()
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

    def priority_score(self, key: str) -> float:
        """
        Calculate consolidation priority for a node.

        priority = staleness × degree × bridge_factor

        Only concept, entity, and analysis nodes are consolidation candidates.
        Source nodes are never consolidated.
        """
        node = self.get_node(key)
        if node is None or node.type == "source":
            return 0.0

        s = self.staleness(key)
        if s == 0:
            return 0.0

        return float(s * self.degree(key) * self.bridge_factor(key))

    def priority_queue(self, min_score: float = 0.0) -> list[tuple[float, str]]:
        """
        Return all consolidation candidates sorted by priority score descending.

        Each item is (score, node_key). Source nodes are always excluded.
        Nodes with score == 0 are excluded unless min_score == 0.
        """
        scores = []
        for key, node in self.all_nodes().items():
            if node.type == "source":
                continue
            score = self.priority_score(key)
            if score > min_score:
                scores.append((score, key))
        return sorted(scores, reverse=True)

    # ------------------------------------------------------------------
    # High-degree nodes
    # ------------------------------------------------------------------

    def get_high_degree_nodes(self, threshold: int = 3) -> list[tuple[int, str]]:
        """
        Return nodes with degree >= threshold, sorted by degree descending.
        Each item is (degree, node_key). Excludes source nodes.
        """
        result = []
        for key, node in self._nodes.items():
            if node.type == "source":
                continue
            d = self.degree(key)
            if d >= threshold:
                result.append((d, key))
        return sorted(result, reverse=True)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _to_dict(self) -> dict:
        return {
            "meta": {
                "version": self.SCHEMA_VERSION,
                "last_updated": date.today().isoformat(),
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
            },
            "nodes": {
                key: {
                    "type": node.type,
                    "label": node.label,
                    "sources": node.sources,
                    "created": node.created,
                    "updated": node.updated,
                    "consolidated": node.consolidated,
                    "consolidation_version": node.consolidation_version,
                }
                for key, node in self._nodes.items()
            },
            "edges": [
                {
                    "from": edge.from_node,
                    "to": edge.to_node,
                    "type": edge.type,
                    "created": edge.created,
                    "synthesis": edge.synthesis,
                }
                for edge in self._edges
            ],
        }

    @classmethod
    def _from_dict(cls, data: dict, graph_path: str) -> "WikiGraph":
        g = cls(graph_path)
        for key, nd in data.get("nodes", {}).items():
            raw_consolidated = nd.get("consolidated", None)
            # Migrate from old bool schema: True → updated date, False → None
            if raw_consolidated is True:
                consolidated: Optional[str] = nd.get("updated")
            elif raw_consolidated is False or raw_consolidated is None:
                consolidated = None
            else:
                consolidated = raw_consolidated
            g._nodes[key] = GraphNode(
                type=nd["type"],
                label=nd["label"],
                sources=nd.get("sources", []),
                created=nd["created"],
                updated=nd["updated"],
                consolidated=consolidated,
                consolidation_version=nd.get("consolidation_version", 0),
            )
        for ed in data.get("edges", []):
            g._edges.append(GraphEdge(
                from_node=ed["from"],
                to_node=ed["to"],
                type=ed["type"],
                created=ed["created"],
                synthesis=ed.get("synthesis"),
            ))
        return g

    # ------------------------------------------------------------------
    # Report / validation helpers
    # ------------------------------------------------------------------

    def report(self) -> str:
        lines = ["=== Wiki Graph Report ===", ""]

        # Node and edge summary
        type_counts = Counter(n.type for n in self._nodes.values())
        node_summary = ", ".join(
            f"{type_counts.get(t, 0)} {t}"
            for t in ["source", "concept", "entity", "analysis"]
        )
        lines.append(f"Nodes: {len(self._nodes)} total ({node_summary})")

        edge_type_counts = Counter(e.type for e in self._edges)
        edge_detail = ", ".join(
            f"{t}: {c}" for t, c in sorted(edge_type_counts.items())
        )
        lines.append(f"Edges: {len(self._edges)} total ({edge_detail})")

        # Priority queue (top 10)
        lines.append("")
        lines.append("--- Priority Queue (top 10) ---")
        queue = self.priority_queue()
        if not queue:
            lines.append("  (no consolidation candidates)")
        else:
            for i, (score, key) in enumerate(queue[:10], 1):
                s = self.staleness(key)
                d = self.degree(key)
                b = self.bridge_factor(key)
                lines.append(
                    f"  {i:2}. {key:<45} score={score:<6.1f} "
                    f"staleness={s}  degree={d}  bridge={b:.1f}x"
                )

        # Bridge nodes
        lines.append("")
        lines.append("--- Bridge Nodes ---")
        clusters = self.get_clusters()
        bridge_nodes = []
        for key, node in self._nodes.items():
            if node.type == "source":
                continue
            node_clusters: set[str] = set()
            for neighbour in self.neighbours(key):
                for cid, members in clusters.items():
                    if neighbour in members:
                        node_clusters.add(cid)
            if len(node_clusters) >= 2:
                bridge_nodes.append((len(node_clusters), key))
        bridge_nodes.sort(reverse=True)
        if not bridge_nodes:
            lines.append("  (none)")
        else:
            for n_clusters, key in bridge_nodes:
                lines.append(f"  {key}  (connects {n_clusters} clusters)")

        # Clusters
        lines.append("")
        lines.append("--- Clusters ---")
        if not clusters:
            lines.append("  (empty graph)")
        else:
            for cid, members in clusters.items():
                preview = ", ".join(sorted(members)[:5])
                suffix = ", ..." if len(members) > 5 else ""
                lines.append(f"  Cluster {cid} ({len(members)} nodes): {preview}{suffix}")

        # Never consolidated
        lines.append("")
        lines.append("--- Never Consolidated ---")
        never = [k for k, n in self._nodes.items() if n.consolidated is None and n.type != "source"]
        if not never:
            lines.append("  (all non-source nodes have been consolidated)")
        elif len(never) == len([k for k, n in self._nodes.items() if n.type != "source"]):
            lines.append(f"  {len(never)} nodes (all — no consolidation run yet)")
        else:
            lines.append(f"  {len(never)} nodes:")
            for key in sorted(never)[:10]:
                lines.append(f"    {key}")
            if len(never) > 10:
                lines.append(f"    ... and {len(never) - 10} more")

        # Shared sources (pairs sharing >= 2 sources, or all pairs sharing >= 1)
        lines.append("")
        lines.append("--- Shared Sources ---")
        non_source_keys = sorted(k for k, n in self._nodes.items() if n.type != "source")
        shared_pairs = []
        for i, a in enumerate(non_source_keys):
            for b in non_source_keys[i + 1:]:
                shared = self.get_shared_sources(a, b)
                if shared:
                    shared_pairs.append((len(shared), a, b, shared))
        shared_pairs.sort(reverse=True)

        # Show pairs with >= 2 shared sources, or top 10 if none qualify
        threshold_pairs = [p for p in shared_pairs if p[0] >= 2] or shared_pairs[:10]
        if not threshold_pairs:
            lines.append("  (no shared sources found)")
        else:
            for _, a, b, shared in threshold_pairs[:15]:
                lines.append(f"  {a} ↔ {b}:")
                lines.append(f"    [{', '.join(shared)}]")

        # Synthesis documents
        synth_edges = [e for e in self._edges if e.synthesis]
        lines.append("")
        lines.append("--- Synthesis Documents ---")
        if not synth_edges:
            lines.append("  (none)")
        else:
            for e in sorted(synth_edges, key=lambda x: x.synthesis):
                lines.append(f"  {e.from_node} -[{e.type}]-> {e.to_node}")
                lines.append(f"    {e.synthesis}")

        # Integrity
        issues = self.validate()
        if issues:
            lines.append("")
            lines.append("--- Integrity Issues ---")
            for issue in issues:
                lines.append(f"  [WARN] {issue}")
        else:
            lines.append("")
            lines.append("No integrity issues.")

        return "\n".join(lines)

    def validate(self) -> list[str]:
        """Return list of integrity issue strings (empty = clean)."""
        issues = []
        for edge in self._edges:
            if edge.from_node not in self._nodes:
                issues.append(f"Dangling edge from '{edge.from_node}' (node not found)")
            if edge.to_node not in self._nodes:
                issues.append(f"Dangling edge to '{edge.to_node}' (node not found)")
        return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_path = tf.name
    try:
        g = WikiGraph(graph_path=tmp_path)

        g.add_node("sources/shadow-work.md", type="source", label="Shadow Work")
        g.add_node("concepts/shadow.md", type="concept", label="Shadow",
                   source="sources/shadow-work.md")
        g.add_node("concepts/persona.md", type="concept", label="Persona",
                   source="sources/shadow-work.md")
        g.add_node("entities/carl-jung.md", type="entity", label="Carl Jung",
                   source="sources/shadow-work.md")

        g.add_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses")
        g.add_edge("sources/shadow-work.md", "concepts/persona.md", "discusses")
        g.add_edge("sources/shadow-work.md", "entities/carl-jung.md", "discusses")

        g.save()
        g2 = WikiGraph.load(tmp_path)

        assert g2.has_node("concepts/shadow.md"), "has_node failed"
        assert g2.has_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses"), "has_edge failed"
        assert g2.degree("sources/shadow-work.md") == 3, f"degree expected 3, got {g2.degree('sources/shadow-work.md')}"
        assert "concepts/shadow.md" in g2.neighbours("sources/shadow-work.md"), "neighbours failed"

        concept_nodes = g2.nodes_of_type("concept")
        assert "concepts/shadow.md" in concept_nodes, "nodes_of_type missing shadow"
        assert "concepts/persona.md" in concept_nodes, "nodes_of_type missing persona"
        assert "sources/shadow-work.md" not in concept_nodes, "nodes_of_type returned wrong type"

        # deduplication
        g2.add_edge("sources/shadow-work.md", "concepts/shadow.md", "discusses")
        assert len(g2.edges_from("sources/shadow-work.md")) == 3, \
            f"dedup failed: expected 3, got {len(g2.edges_from('sources/shadow-work.md'))}"

        # shared_sources
        g2.add_node("sources/buddhist-principles.md", type="source", label="Buddhist Principles")
        g2.add_edge("sources/buddhist-principles.md", "concepts/shadow.md", "parallels")
        shared = g2.shared_sources("concepts/shadow.md", "concepts/persona.md")
        assert "sources/shadow-work.md" in shared, f"shared_sources failed: {shared}"

        # mark_consolidated with date string
        today = date.today().isoformat()
        g2.mark_consolidated("concepts/shadow.md", today)
        node = g2.get_node("concepts/shadow.md")
        assert node.consolidated == today, "mark_consolidated: consolidated date wrong"
        assert node.consolidation_version == 1, "mark_consolidated: version not incremented"

        # in/out degree
        assert g2.in_degree("concepts/shadow.md") == 2, \
            f"in_degree expected 2, got {g2.in_degree('concepts/shadow.md')}"
        assert g2.out_degree("sources/shadow-work.md") == 3, \
            f"out_degree expected 3, got {g2.out_degree('sources/shadow-work.md')}"

        # validate — no dangling edges
        issues = g2.validate()
        assert not issues, f"Unexpected integrity issues: {issues}"

        print("Smoke tests passed.")
    finally:
        os.unlink(tmp_path)


def _run_analysis_tests() -> None:
    """Verification tests from Ticket 3 spec."""
    g = WikiGraph(graph_path="/tmp/test_graph_analysis.json")

    g.add_node("sources/shadow-work.md", type="source", label="Shadow Work")
    g.add_node("sources/buddhist-principles.md", type="source", label="Buddhist Principles")
    g.add_node("sources/burke-hayek-popper.md", type="source", label="Burke-Hayek-Popper")

    g.add_node("concepts/shadow.md", type="concept", label="Shadow",
               source="sources/shadow-work.md")
    g.add_node("concepts/epistemic-humility.md", type="concept",
               label="Epistemic Humility", source="sources/burke-hayek-popper.md")
    g.add_node("concepts/impermanence.md", type="concept", label="Impermanence",
               source="sources/buddhist-principles.md")
    g.add_node("entities/carl-jung.md", type="entity", label="Carl Jung",
               source="sources/shadow-work.md")

    g.add_edge("sources/shadow-work.md", "concepts/shadow.md", "introduces")
    g.add_edge("sources/shadow-work.md", "entities/carl-jung.md", "discusses")
    g.add_edge("sources/buddhist-principles.md", "concepts/impermanence.md", "introduces")
    g.add_edge("sources/buddhist-principles.md", "concepts/epistemic-humility.md", "discusses")
    g.add_edge("sources/burke-hayek-popper.md", "concepts/epistemic-humility.md", "discusses")
    g.add_edge("sources/shadow-work.md", "concepts/epistemic-humility.md", "parallels")

    g.save()

    # Staleness — nothing consolidated yet
    s_shadow = g.staleness("concepts/shadow.md")
    assert s_shadow == 1, f"staleness(shadow) expected 1, got {s_shadow}"

    s_ep = g.staleness("concepts/epistemic-humility.md")
    assert s_ep == 3, f"staleness(epistemic-humility) expected 3, got {s_ep}"

    # Priority — epistemic-humility should rank highest
    queue = g.priority_queue()
    assert queue, "priority_queue is empty"
    assert queue[0][1] == "concepts/epistemic-humility.md", \
        f"expected epistemic-humility at top, got {queue[0][1]}"

    # Bridge factor — epistemic-humility connects all three source clusters
    bf = g.bridge_factor("concepts/epistemic-humility.md")
    assert bf >= 1.5, f"bridge_factor(epistemic-humility) expected >= 1.5, got {bf}"

    # Shared sources
    shared = g.get_shared_sources("concepts/epistemic-humility.md",
                                   "concepts/impermanence.md")
    assert "sources/buddhist-principles.md" in shared, \
        f"get_shared_sources missing buddhist-principles: {shared}"

    # Mark consolidated and verify staleness drops
    g.mark_consolidated("concepts/shadow.md", date.today().isoformat())
    s_after = g.staleness("concepts/shadow.md")
    assert s_after == 0, f"staleness(shadow) after consolidation expected 0, got {s_after}"

    # After consolidating shadow, epistemic-humility's staleness should be >= 3
    s_ep_after = g.staleness("concepts/epistemic-humility.md")
    assert s_ep_after >= 3, \
        f"staleness(epistemic-humility) after shadow consolidation expected >= 3, got {s_ep_after}"

    # consolidation_version incremented
    node = g.get_node("concepts/shadow.md")
    assert node.consolidation_version == 1, \
        f"consolidation_version expected 1, got {node.consolidation_version}"

    import os
    try:
        os.unlink("/tmp/test_graph_analysis.json")
    except OSError:
        pass

    print("Analysis tests passed.")


def main():
    parser = argparse.ArgumentParser(description="WikiGraph CLI")
    parser.add_argument("--report", action="store_true", help="Print graph report")
    parser.add_argument("--validate", action="store_true", help="Check graph integrity")
    parser.add_argument("--test", action="store_true", help="Run smoke tests")
    parser.add_argument("--graph", default=".wiki_graph.json", help="Path to graph file")
    args = parser.parse_args()

    if args.test:
        _run_tests()
        _run_analysis_tests()
        return

    g = WikiGraph.load(args.graph)

    if args.validate:
        issues = g.validate()
        if issues:
            for issue in issues:
                print(f"[WARN] {issue}")
            sys.exit(1)
        else:
            print("Graph is valid.")
    else:
        print(g.report())


if __name__ == "__main__":
    main()
