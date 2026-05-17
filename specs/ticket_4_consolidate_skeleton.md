# Ticket 4 — consolidate.py: skeleton, priority queue runner, CLI

## Context

The ingest pipeline (Tickets 0–3) populates the wiki and the knowledge graph.
This ticket creates `consolidate.py` — the periodic synthesis script that
improves the wiki by processing nodes in priority order.

No production LLM calls in this ticket. Rewrite and synthesis handlers are
stubbed — they print what they would do but do not call the API. Everything
else is fully implemented: CLI, queue building, pin logic, rewrite detection,
propagation, and resumability.

Tickets 5 and 6 replace the stubs with real LLM calls.

---

## New files

- `consolidate.py` — entry point and runner
- `rewrite.py` — shared rewrite utilities (partially stubbed, see below)

## Files modified

- `utils.py` — add `detect_append_sections()` helper
- `FUTURE_WORK.md` — no changes needed

## Files not touched

- `ingest.py`, `middleware.py`, `wiki_io.py`, `wiki_graph.py`, `wiki_retrieval.py`
- `prompts.py` — consolidation prompts added in Tickets 5 and 6

---

## CLI

```bash
python consolidate.py                          # run to default depth (5)
python consolidate.py --depth 10              # process top N nodes
python consolidate.py --survey                # show priority queue, write nothing
python consolidate.py --pin concepts/shadow.md  # pin one node to top
python consolidate.py --pin concepts/a.md --pin concepts/b.md  # pin multiple
python consolidate.py --depth 10 --pin concepts/shadow.md  # combined
```

Argument parsing using `argparse`. All flags optional.

---

## rewrite.py

A new shared module imported by both `consolidate.py` and future ingest
middleware. Contains utilities for detecting and performing page rewrites.

### `detect_append_sections(page_path: Path) -> list[str]`

Reads a wiki page and returns a list of append section headers found.
Append sections are identified by the pattern:

```
### From [[sources/slug]] (YYYY-MM-DD)
```

Returns a list of matched header strings, empty list if none found.
This is used to determine whether a page needs rewriting before synthesis.

```python
def detect_append_sections(page_path: Path) -> list[str]:
    """
    Return list of append section headers in a wiki page.
    Empty list means the page is clean (no pending appends).
    """
    if not page_path.exists():
        return []
    content = page_path.read_text(encoding="utf-8", errors="ignore")
    pattern = r"^### From \[\[sources/[^\]]+\]\] \(\d{4}-\d{2}-\d{2}\)"
    return re.findall(pattern, content, re.MULTILINE)
```

### `needs_rewrite(page_path: Path) -> bool`

```python
def needs_rewrite(page_path: Path) -> bool:
    """Return True if page has any pending append sections."""
    return len(detect_append_sections(page_path)) > 0
```

### `rewrite_page(page_path: Path, client: anthropic.Anthropic) -> bool`

**Stubbed in this ticket.** Prints what it would do, returns `True` to
simulate success. Ticket 5 replaces this with the real LLM call.

```python
def rewrite_page(page_path: Path, client: anthropic.Anthropic) -> bool:
    """
    Rewrite a page with pending append sections into a clean unified document.
    STUB — prints intent only, no API call made.
    Returns True on success, False on failure.
    """
    sections = detect_append_sections(page_path)
    print(f"  [STUB] rewrite_page: {page_path} ({len(sections)} append sections)")
    return True
```

---

## consolidate.py

### Imports and setup

```python
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
from wiki_graph import WikiGraph
from rewrite import needs_rewrite, rewrite_page

DEFAULT_DEPTH = 5
```

### `build_queue`

```python
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
```

### `pending_rewrites`

```python
def pending_rewrites(graph: WikiGraph) -> list[Path]:
    """
    Find all wiki pages that have pending append sections and need rewriting
    before synthesis can proceed.

    Only checks concept, entity, and analysis nodes — source pages are
    never rewritten.

    Returns list of page Paths sorted by path for determinism.
    """
    pending = []
    for key, node in graph.all_nodes().items():
        if node.type == "source":
            continue
        page_path = WIKI_DIR / key
        if needs_rewrite(page_path):
            pending.append(page_path)
    return sorted(pending)
```

### `synthesise_node` (stub)

```python
def synthesise_node(
    node_key: str,
    graph: WikiGraph,
    client: anthropic.Anthropic,
) -> bool:
    """
    Synthesise or consolidate a single node.
    STUB — prints intent only, no API call made.
    Tickets 5 and 6 replace this with real LLM calls.

    Returns True on success, False on failure.
    """
    node = graph.get_node(node_key)
    page_path = WIKI_DIR / node_key

    score = graph.priority_score(node_key)
    bf = graph.bridge_factor(node_key)
    neighbours = graph.neighbours(node_key)

    print(f"  [STUB] synthesise_node: {node_key}")
    print(f"         type={node.type}  score={score:.1f}  "
          f"bridge={bf}x  neighbours={len(neighbours)}")
    print(f"         page_exists={page_path.exists()}")
    return True
```

### `run_consolidation`

```python
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

    # Step 1 — Rewrite all pages with pending append sections first
    rewrites = pending_rewrites(graph)
    if rewrites:
        print(f"Pending rewrites: {len(rewrites)} page(s)")
        for page_path in rewrites:
            if survey:
                print(f"  [SURVEY] would rewrite: {page_path}")
            else:
                success = rewrite_page(page_path, client)
                if success:
                    # Update graph to reflect the page has been consolidated
                    key = str(page_path.relative_to(WIKI_DIR))
                    graph.mark_consolidated(key, date.today().isoformat())
                    graph.save()
    else:
        print("No pending rewrites.")

    print()

    # Step 2 — Build priority queue
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
```

### `_write_consolidation_log`

```python
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
```

### `main`

```python
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

    client = anthropic.Anthropic()
    print(f"Model: {LLM_MODEL}")

    run_consolidation(
        depth=args.depth,
        pins=args.pins,
        survey=args.survey,
        client=client,
    )


if __name__ == "__main__":
    main()
```

---

## Propagation correctness note

After each node is processed, `build_queue` is called again to re-score
the remaining queue. This is correct because `graph.mark_consolidated()`
updates `node.consolidated`, which changes `staleness()` for all neighbours
via `_neighbour_consolidated_after()`. The re-scored queue naturally
reflects the propagation of the consolidation event.

The already-processed node filtering uses a set of processed keys to
ensure nodes are not re-processed in the same run even if they appear
in the re-scored queue.

---

## Import dependency layering

`consolidate.py` follows the same layering rules as `ingest.py`:

```
consolidate.py   ← imports from config, utils, wiki_graph, rewrite
rewrite.py       ← imports from config, utils (no wiki_graph dependency)
```

`rewrite.py` must not import from `middleware.py` or `context.py` —
it is shared infrastructure, not ingest-specific.

---

## Verification

With a populated wiki and graph from previous ingest runs:

```bash
# Survey mode — should print queue, write nothing
python consolidate.py --survey

# Default run — should process 5 nodes (stubs only)
python consolidate.py

# Depth override
python consolidate.py --depth 2

# Pin a specific node
python consolidate.py --survey --pin concepts/shadow.md

# Verify log entry was written
tail wiki/log.md
```

Expected stub output for each node:
```
→ [1/5] concepts/epistemic-humility.md (score=12.0)
  [STUB] synthesise_node: concepts/epistemic-humility.md
         type=concept  score=12.0  bridge=2.0x  neighbours=4
         page_exists=True
```

Verify that after each run, `consolidation_version` increments in
`.wiki_graph.json` for processed nodes and `consolidated` is set to today.

---

## File summary

Files created:
- `consolidate.py`
- `rewrite.py`

Files modified:
- `utils.py` — `detect_append_sections` is defined in `rewrite.py` not utils,
  no changes needed to utils in this ticket

Files not touched:
- `ingest.py`, `middleware.py`, `wiki_io.py`, `splitting.py`, `context.py`,
  `config.py`, `prompts.py`, `wiki_graph.py`, `wiki_retrieval.py`
