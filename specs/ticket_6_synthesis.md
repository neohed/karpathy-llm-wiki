# Ticket 6 — Implement synthesis with two-phase LLM calls and edge metadata

## Context

`synthesise_node()` in `consolidate.py` is currently stubbed. This ticket
implements synthesis — the process of identifying and articulating non-trivial
cross-domain connections between concept and entity nodes.

Key architectural decisions established before this ticket:
- Synthesis documents live on **graph edges as filepath metadata**, not as
  independent graph nodes. One synthesis document per edge, enforced by structure.
- Synthesis only between concept/entity nodes — never between analysis nodes,
  never between source nodes. Hard constraint enforced in code.
- **Two-phase LLM call**: Phase 1 decides if synthesis is worthwhile (cheap).
  Phase 2 writes the document (only if Phase 1 says yes).
- Existing synthesis documents are updated only if Phase 1 determines the update
  is material — otherwise staleness is cleared without touching the document.
- Synthesis documents are grounded in original source documents, same pattern
  as `rewrite_page()` in Ticket 5.

---

## Graph schema extension

Add optional `synthesis` field to `GraphEdge`:

```python
@dataclass
class GraphEdge:
    from_node: str
    to_node: str
    type: str
    created: str
    synthesis: Optional[str] = None   # filepath to analyses/ doc, if one exists
```

Update `WikiGraph`:

### `set_edge_synthesis(from_node, to_node, edge_type, filepath)`

```python
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
    ...
```

### `get_edge_synthesis(from_node, to_node, edge_type)`

```python
def get_edge_synthesis(
    self,
    from_node: str,
    to_node: str,
    edge_type: str,
) -> Optional[str]:
    """
    Return the synthesis filepath for an edge, or None if none exists.
    """
    ...
```

### `edges_needing_synthesis(min_bridge_factor)`

```python
def edges_needing_synthesis(
    self,
    min_bridge_factor: float = 1.5,
) -> list[GraphEdge]:
    """
    Return edges that are candidates for synthesis.

    Criteria:
    - Both from_node and to_node are concept or entity nodes (not source, not analysis)
    - At least one endpoint has bridge_factor >= min_bridge_factor
    - Edge has been touched by at least 2 distinct source documents
      (i.e. shared_sources(from_node, to_node) has >= 2 entries)

    Returns edges sorted by combined priority score of endpoints descending.
    """
    ...
```

### `edges_with_stale_synthesis()`

```python
def edges_with_stale_synthesis(self) -> list[GraphEdge]:
    """
    Return edges that have an existing synthesis document but where
    at least one endpoint node has been updated since the synthesis
    was last written.

    Uses the file modification time of the synthesis document compared
    to node.updated dates to determine staleness.
    """
    ...
```

Update JSON serialisation/deserialisation to include the `synthesis` field.
Existing edges without `synthesis` deserialise with `synthesis=None`.

---

## New file: synthesis.py

Create `synthesis.py` alongside `rewrite.py`. Contains the synthesis logic.

```python
"""
synthesis.py — Two-phase synthesis between concept/entity node pairs.

Phase 1: Decision call — is synthesis worthwhile?
Phase 2: Write call — produce the synthesis document (only if Phase 1 says yes)

For existing synthesis documents, a lightweight update check replaces
Phase 1 to determine if the document needs rewriting.
"""
```

### `synthesise_edge`

```python
def synthesise_edge(
    edge: GraphEdge,
    graph: WikiGraph,
    client: anthropic.Anthropic,
    wiki_dir: Path = None,
    raw_dir: Path = None,
) -> bool:
    """
    Run synthesis for a single graph edge.

    If the edge has no existing synthesis document:
        Phase 1 — decide if worthwhile
        Phase 2 — write document (if worthwhile)

    If the edge has an existing synthesis document:
        Phase 1 — decide if update is needed
        Phase 2 — rewrite document (if update needed)

    Updates the edge synthesis filepath in the graph on success.
    Returns True if the edge was processed successfully (even if synthesis
    was judged not worthwhile — that is a valid outcome, not a failure).
    """
    ...
```

### `_load_node_content`

```python
def _load_node_content(node_key: str, wiki_dir: Path) -> str:
    """Read current content of a wiki page. Returns empty string if not found."""
    page_path = wiki_dir / node_key
    if not page_path.exists():
        return ""
    return page_path.read_text(encoding="utf-8", errors="ignore")
```

### `_load_grounding_sources`

```python
def _load_grounding_sources(
    node_key_a: str,
    node_key_b: str,
    graph: WikiGraph,
    wiki_dir: Path,
    raw_dir: Path,
) -> list[tuple[str, str]]:
    """
    Load original source documents for both nodes combined.
    Deduplicates — if both nodes share a source, include it once.
    Returns list of (source_path, content) tuples.

    Reuses _resolve_raw_path from rewrite.py.
    """
    from rewrite import _resolve_raw_path

    seen = set()
    source_texts = []

    node_a = graph.get_node(node_key_a)
    node_b = graph.get_node(node_key_b)
    all_sources = list(set(
        (node_a.sources if node_a else []) +
        (node_b.sources if node_b else [])
    ))

    for source_key in all_sources:
        if source_key in seen:
            continue
        seen.add(source_key)
        wiki_source_page = wiki_dir / source_key
        raw_path = _resolve_raw_path(wiki_source_page, raw_dir)
        if raw_path and raw_path.exists():
            content = raw_path.read_text(encoding="utf-8", errors="ignore")
            source_texts.append((str(raw_path), content))

    return source_texts
```

### `_phase1_decide`

```python
def _phase1_decide(
    node_key_a: str,
    node_key_b: str,
    content_a: str,
    content_b: str,
    source_texts: list[tuple[str, str]],
    existing_synthesis: Optional[str],
    client: anthropic.Anthropic,
    prompts: WikiPrompts,
) -> dict:
    """
    Phase 1 LLM call — decide if synthesis is worthwhile.

    If existing_synthesis is None: ask if synthesis should be created.
    If existing_synthesis is provided: ask if existing synthesis needs updating.

    Returns parsed JSON dict with keys:
        worthwhile: bool  (or needs_update: bool for existing syntheses)
        rationale: str
        proposed_title: str  (only when worthwhile=True and no existing synthesis)
    """
    ...
```

### `_phase2_write`

```python
def _phase2_write(
    node_key_a: str,
    node_key_b: str,
    content_a: str,
    content_b: str,
    source_texts: list[tuple[str, str]],
    proposed_title: str,
    existing_path: Optional[str],
    wiki_dir: Path,
    client: anthropic.Anthropic,
    prompts: WikiPrompts,
) -> Optional[str]:
    """
    Phase 2 LLM call — write or rewrite the synthesis document.

    Returns the filepath of the written document relative to wiki_dir,
    or None on failure.

    Filename slug derived from proposed_title if creating new,
    or existing_path if updating.
    """
    ...
```

---

## Prompts — add to WikiPrompts in prompts.py

### `synthesis_decide_system`

```python
def synthesis_decide_system(
    self,
    schema: str,
    source_texts: list[tuple[str, str]],
) -> list[dict]:
    """
    System prompt for Phase 1 decision call.
    Same structure as rewrite_system — schema cached, sources cached.
    """
    ...
```

### `synthesis_decide_user` — new synthesis

```python
def synthesis_decide_user(
    self,
    node_key_a: str,
    content_a: str,
    node_key_b: str,
    content_b: str,
) -> str:
    return f"""You are evaluating whether two wiki pages have a non-trivial,
worthwhile synthesis worth documenting.

## Page A: {node_key_a}
{content_a}

## Page B: {node_key_b}
{content_b}

A worthwhile synthesis:
- Reveals something genuinely surprising or non-obvious about the relationship
- Produces insight that neither page states on its own
- Would be practically or intellectually useful to a reader of both pages
- Is not merely "these two things are related" or "both discuss X"

A synthesis is NOT worthwhile if:
- The connection is trivially obvious (e.g. "both involve human behaviour")
- One page already fully explains the relationship to the other
- The relationship is purely definitional or taxonomic
- There is no meaningful intellectual tension, parallel, or emergent insight

Respond ONLY with valid JSON:
{{
  "worthwhile": true | false,
  "rationale": "one sentence explaining the decision",
  "proposed_title": "Title For The Synthesis Document"
}}

proposed_title only required when worthwhile is true.
No markdown fences, no explanation outside the JSON."""
```

### `synthesis_decide_user` — update check

```python
def synthesis_update_user(
    self,
    node_key_a: str,
    content_a: str,
    node_key_b: str,
    content_b: str,
    existing_synthesis: str,
) -> str:
    return f"""You are evaluating whether an existing synthesis document needs
updating given changes to the pages it synthesises.

## Page A: {node_key_a}
{content_a}

## Page B: {node_key_b}
{content_b}

## Existing synthesis document
{existing_synthesis}

The synthesis needs updating if:
- New information in either page materially changes the synthesis
- The existing synthesis contains claims now contradicted by updated pages
- Significant new connections have emerged that the synthesis misses

The synthesis does NOT need updating if:
- Changes to the pages are minor additions that don't affect the core insight
- The existing synthesis remains accurate and complete

Respond ONLY with valid JSON:
{{
  "needs_update": true | false,
  "rationale": "one sentence explaining the decision"
}}

No markdown fences, no explanation outside the JSON."""
```

### `synthesis_write_system`

```python
def synthesis_write_system(
    self,
    schema: str,
    source_texts: list[tuple[str, str]],
    content_a: str,
    content_b: str,
    node_key_a: str,
    node_key_b: str,
) -> list[dict]:
    """
    System prompt for Phase 2 write call.

    Block 1: schema (cached)
    Block 2: grounding source documents (cached)
    Block 3: current content of both nodes (cached)
    """
    grounding = _build_grounding_block(source_texts)
    nodes_block = (
        f"## {node_key_a}\n{content_a}\n\n"
        f"## {node_key_b}\n{content_b}"
    )
    return [
        {
            "type": "text",
            "text": schema,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "The following are the ORIGINAL SOURCE DOCUMENTS that informed "
                "the wiki pages you are synthesising. Treat them as ground truth. "
                "Where wiki pages conflict with sources, trust the sources.\n\n"
                f"{grounding}"
            ),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "The following are the current wiki pages being synthesised:\n\n"
                f"{nodes_block}"
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]
```

### `synthesis_write_user`

```python
def synthesis_write_user(
    self,
    node_key_a: str,
    node_key_b: str,
    proposed_title: str,
    today: str,
    existing_synthesis: Optional[str] = None,
) -> str:
    if existing_synthesis:
        instruction = (
            f"Rewrite this existing synthesis document incorporating the "
            f"updated information from both pages. Preserve the core insight "
            f"where it remains valid. Update or remove claims that are no longer "
            f"accurate. Add new connections that have emerged.\n\n"
            f"Existing synthesis:\n{existing_synthesis}"
        )
    else:
        instruction = (
            f"Write a new synthesis document exploring the non-trivial "
            f"connection between these two pages."
        )

    return f"""{instruction}

Title: {proposed_title}

The synthesis document should:
- Open with the core insight in one clear paragraph — what becomes visible
  when these two pages are read together that neither states alone
- Explore where the two concepts reinforce each other
- Explore where they create productive tension or apparent contradiction
- Note what questions remain open or unresolved
- Use [[WikiLinks]] to reference both pages and any related concepts
- Close with practical or intellectual implications for the reader

Frontmatter:
---
title: "{proposed_title}"
type: analysis
tags: []
sources: [{node_key_a}, {node_key_b}]
updated: {today}
---

Return ONLY the complete markdown document. No explanation, no fences."""
```

---

## Update synthesise_node in consolidate.py

Replace the stub with a real implementation:

```python
def synthesise_node(
    node_key: str,
    graph: WikiGraph,
    client: anthropic.Anthropic,
) -> bool:
    """
    Run synthesis for all eligible edges connected to this node.

    Finds edges where:
    - This node is one endpoint
    - The other endpoint is a concept or entity node (not source, not analysis)
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

    # Find eligible edges
    eligible = [
        e for e in graph.edges_needing_synthesis()
        if e.from_node == node_key or e.to_node == node_key
    ]

    # Also check existing syntheses that may need updating
    stale = [
        e for e in graph.edges_with_stale_synthesis()
        if e.from_node == node_key or e.to_node == node_key
    ]

    all_edges = {(e.from_node, e.to_node, e.type): e
                 for e in eligible + stale}

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
```

---

## Synthesis document naming

Filename is derived from the proposed title returned by Phase 1:

```python
def _title_to_slug(title: str) -> str:
    import re
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")

# e.g. "Shadow and Buddhist Non-Self" -> "shadow-and-buddhist-non-self"
# filepath: "analyses/shadow-and-buddhist-non-self.md"
```

If a synthesis document for this edge already exists at a different path
(because the title changed), the old document is not deleted — the edge
`synthesis` field is updated to the new path and the old file is left for
git cleanup. Log a `[WARN]` in this case.

---

## Atomic writes

Same pattern as `rewrite_page` — write to `.tmp` then rename:

```python
tmp = synthesis_path.with_suffix(".tmp")
tmp.write_text(content, encoding="utf-8")
tmp.rename(synthesis_path)
```

---

## Logging

After each synthesis decision log to audit:

```python
_log("synthesis_decision",
     edge=f"{edge.from_node}->{edge.to_node}",
     worthwhile=decision["worthwhile"],
     rationale=decision["rationale"])
```

After each synthesis write:

```python
_log("synthesis_write",
     edge=f"{edge.from_node}->{edge.to_node}",
     path=str(synthesis_path),
     input_tokens=...,
     output_tokens=...)
```

---

## Import layering

```
consolidate.py   ← imports synthesis, rewrite, wiki_graph, config, utils
synthesis.py     ← imports rewrite (_resolve_raw_path), wiki_graph, config,
                   utils, prompts
rewrite.py       ← imports config, utils, prompts, wiki_graph
```

`synthesis.py` must not import from `middleware.py` or `context.py`.

---

## Verification

After running ingest on at least three documents that share concept pages:

```bash
# Survey to see synthesis candidates
python consolidate.py --survey

# Run with a specific bridge node pinned
python consolidate.py --depth 3 --pin concepts/epistemic-humility.md

# Verify Phase 1 decisions in audit log
grep "synthesis_decision" .api_audit.log

# Verify synthesis documents created
ls wiki/analyses/

# Verify edge synthesis field set in graph
python wiki_graph.py --report

# Run again — verify existing syntheses trigger update check not creation
python consolidate.py --depth 3 --pin concepts/epistemic-humility.md
grep "synthesis_decision" .api_audit.log | tail -5
```

Expected behaviour:
- Phase 1 returns `worthwhile: false` for trivial connections — no document written
- Phase 1 returns `worthwhile: true` for genuine cross-domain insight — document written
- Second run triggers update check — `needs_update: false` if nothing changed,
  document not rewritten
- Graph `--report` shows edge synthesis filepaths for edges that produced documents

---

## File summary

Files created:
- `synthesis.py`

Files modified:
- `wiki_graph.py` — add `synthesis` field to `GraphEdge`, add
  `set_edge_synthesis`, `get_edge_synthesis`, `edges_needing_synthesis`,
  `edges_with_stale_synthesis`
- `prompts.py` — add `synthesis_decide_system`, `synthesis_decide_user`,
  `synthesis_update_user`, `synthesis_write_system`, `synthesis_write_user`
- `consolidate.py` — replace `synthesise_node` stub with real implementation,
  add `from synthesis import synthesise_edge`

Files not touched:
- `ingest.py`, `middleware.py`, `wiki_io.py`, `splitting.py`
- `context.py`, `config.py`, `rewrite.py`
- `wiki_retrieval.py`, `CLAUDE.md`
