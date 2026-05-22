#!/usr/bin/env python3
"""
consolidate.py — LLM Wiki consolidation tool

Processes wiki nodes in priority order, rewriting accumulated append sections
into clean unified documents and synthesising cross-document insights.

Consolidation is never complete — it is an iterative process with a
configurable depth cutoff. Run periodically after ingest.

Usage:
  python consolidate.py                    # run to default depth (5)
  python consolidate.py --depth 10        # deeper pass
  python consolidate.py --survey          # show queue, write nothing
  python consolidate.py --pin concepts/shadow.md  # force node to top

Requires:
  ANTHROPIC_API_KEY in .env.local
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(".env.local")

import anthropic

from config import WIKI_DIR, LLM_MODEL
from utils import _log
from wiki_graph import WikiGraph, GraphEdge

DEFAULT_DEPTH = 5


# ---------------------------------------------------------------------------
# Queue building
# ---------------------------------------------------------------------------

def build_queue(
    graph: WikiGraph,
    pins: list[str],
) -> list[tuple[float, str]]:
    """
    Build the consolidation priority queue.

    Pinned nodes appear first in reverse pin order (last pinned = top).
    Remaining nodes sorted by priority_score descending.
    Nodes with score 0 and not pinned are excluded.

    Returns list of (score, node_key) tuples.
    """
    queue = graph.priority_queue()

    # Remove pinned nodes from the scored queue (they go to the top)
    pinned_keys = set(pins)
    queue = [(score, key) for score, key in queue if key not in pinned_keys]

    # Validate pins exist in graph
    pin_items = []
    for pin in reversed(pins):   # reversed so last --pin arg ends up at top
        if graph.has_node(pin):
            pin_items.append((float("inf"), pin))
        else:
            print(f"  [WARN] Pinned node not found in graph: {pin}")

    return pin_items + queue


# ---------------------------------------------------------------------------
# Pending rewrites
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def synthesise_node(
    node_key: str,
    graph: WikiGraph,
    client: anthropic.Anthropic,
) -> bool:
    """
    Run synthesis for all eligible edges connected to this node.

    Finds edges where:
    - This node is one endpoint
    - The other endpoint is a concept or entity node
    - The edge meets synthesis eligibility criteria

    For each eligible edge, calls synthesise_edge().
    Returns True if all edge syntheses completed without fatal error.
    """
    from synthesis import synthesise_edge
    from config import WIKI_DIR, RAW_DIR

    node = graph.get_node(node_key)
    if node is None:
        return False

    # Hard constraint — no synthesis from/to analysis or source nodes
    if node.type in ("source", "analysis"):
        print(f"  [SKIP] synthesise_node: {node_key} is type={node.type}, skipping")
        return True

    # Find eligible edges (new synthesis candidates)
    eligible = [
        e for e in graph.edges_needing_synthesis()
        if e.from_node == node_key or e.to_node == node_key
    ]

    # Also check existing syntheses that may need updating
    stale = [
        e for e in graph.edges_with_stale_synthesis()
        if e.from_node == node_key or e.to_node == node_key
    ]

    # Deduplicate by (from, to, type) — stale takes precedence over eligible
    all_edges: dict[tuple, GraphEdge] = {}
    for e in eligible:
        all_edges[(e.from_node, e.to_node, e.type)] = e
    for e in stale:
        all_edges[(e.from_node, e.to_node, e.type)] = e

    if not all_edges:
        print(f"  No eligible edges for synthesis from {node_key}")
        return True

    print(f"  {len(all_edges)} edge(s) to evaluate for synthesis")
    success = True

    for edge in all_edges.values():
        ok = synthesise_edge(edge, graph, client, WIKI_DIR, RAW_DIR)
        if not ok:
            success = False

    return success


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

def _write_consolidation_log(processed: int, pins: list[str]) -> None:
    """Append a consolidation entry to wiki/log.md."""
    log_path = WIKI_DIR / "log.md"
    lines = [
        f"## [{date.today().isoformat()}] consolidate | {processed} node(s) processed",
        f"- Depth: {processed}",
    ]
    if pins:
        lines.append(f"- Pinned: {', '.join(pins)}")

    entry = "\n".join(lines)
    existing_size = log_path.stat().st_size if log_path.exists() else 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        if existing_size > 0:
            f.write("\n\n")
        f.write(entry)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_consolidation(
    depth: int,
    pins: list[str],
    survey: bool,
    client: anthropic.Anthropic,
) -> None:
    """Main consolidation runner."""
    print(f"\n{'='*60}")
    print(f"Consolidation run — {date.today().isoformat()}")
    print(f"depth={depth}  pins={pins}  survey={survey}")
    print(f"{'='*60}\n")

    graph = WikiGraph.load()

    if not graph.all_nodes():
        print("Graph is empty — run ingest first.")
        return

    # Note: rewrite phase removed — ingest now uses UPDATE not APPEND,
    # so pages are always clean after ingest. Consolidation focuses
    # entirely on synthesis.

    # Step 1 — Build priority queue
    queue = build_queue(graph, pins)

    if not queue:
        print("Priority queue is empty — nothing to consolidate.")
        return

    # Step 3 — Survey mode: print queue and exit
    if survey:
        print(f"Priority queue ({len(queue)} nodes):\n")
        for rank, (score, key) in enumerate(queue, 1):
            node = graph.get_node(key)
            staleness = graph.staleness(key)
            degree = graph.degree(key)
            bf = graph.bridge_factor(key)
            score_str = "pinned" if score == float("inf") else f"{score:.1f}"
            print(f"  {rank:3}. {key:<50} score={score_str:<8} "
                  f"staleness={staleness}  degree={degree}  bridge={bf}x")
        return

    # Step 4 — Process nodes up to depth
    print(f"Processing top {min(depth, len(queue))} node(s):\n")
    processed = 0

    for score, node_key in queue[:depth]:
        node = graph.get_node(node_key)
        score_str = "pinned" if score == float("inf") else f"{score:.1f}"
        print(f"\n→ [{processed + 1}/{min(depth, len(queue))}] {node_key} "
              f"(score={score_str})")

        success = synthesise_node(node_key, graph, client)

        if success:
            graph.mark_consolidated(node_key, date.today().isoformat())
            graph.save()
            processed += 1

            # Propagation: re-score queue after each node
            # Neighbours may have changed staleness due to this consolidation
            queue = build_queue(graph, pins)
            # Skip already-processed nodes
            processed_keys = {k for _, k in queue[:processed]}
            queue = [(s, k) for s, k in queue if k not in processed_keys]
        else:
            print(f"  [WARN] synthesise_node failed for {node_key}, skipping")

    print(f"\nConsolidation complete — {processed} node(s) processed.")
    _write_consolidation_log(processed, pins)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LLM Wiki consolidation tool"
    )
    parser.add_argument(
        "--depth", type=int, default=DEFAULT_DEPTH,
        help=f"Maximum nodes to process (default: {DEFAULT_DEPTH})"
    )
    parser.add_argument(
        "--survey", action="store_true",
        help="Show priority queue without making any changes"
    )
    parser.add_argument(
        "--pin", action="append", dest="pins", default=[],
        metavar="NODE_KEY",
        help="Pin a node to the top of the queue (repeatable)"
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set in .env.local")
        sys.exit(1)

    client = anthropic.Anthropic(max_retries=0)
    print(f"Model: {LLM_MODEL}")

    run_consolidation(
        depth=args.depth,
        pins=args.pins,
        survey=args.survey,
        client=client,
    )


if __name__ == "__main__":
    main()
