# Ticket 5 — Implement rewrite_page with real LLM call

## Context

`rewrite.py` currently has a stubbed `rewrite_page()` that prints intent but
makes no API call. This ticket replaces the stub with a real LLM call that
rewrites a wiki page with accumulated append sections into a clean, unified
document.

This is the simpler of the two consolidation LLM calls (rewrite vs synthesis).
One page in, one clean page out. Bounded context. No cross-document reasoning.

---

## The rewrite problem

After multiple ingest runs, a concept or entity page accumulates dated append
sections like this:

```markdown
---
title: "Shadow"
type: concept
...
---

# Shadow

The shadow is the unconscious part of the personality...

### From [[sources/shadow-work]] (2026-05-01)
- The shadow contains rejected aspects of the self
- Integration requires conscious acknowledgement

### From [[sources/buddhist-principles]] (2026-05-08)
- Buddhist concept of "near enemy" parallels the shadow
- Both traditions emphasise bringing the hidden into awareness

### From [[sources/jimmy-carr-philosophy]] (2026-05-12)
- Comedy as shadow integration — laughing at what we fear
- The persona/shadow split maps to public vs private self
```

A rewrite call reads this page plus the original source documents and produces
a clean unified page that integrates all the information coherently — no dated
section headers, no append-style bullet dumps, just good wiki prose with proper
provenance tracked in frontmatter.

---

## The grounding problem

Without the original source documents in context, the LLM rewrites from wiki
content alone. Wiki content is already one step removed from primary sources —
it was written by an LLM during ingest, may contain subtle inaccuracies, and
loses nuance with each rewrite cycle (hallucination drift, documented in
FUTURE_WORK.md).

Including original source documents as grounding context:
- Keeps the rewrite anchored to what the sources actually said
- Allows the LLM to notice when the append sections distorted the source
- Produces a more accurate and nuanced page without additional cost at this scale

---

## Prompt design

The rewrite call uses three context layers, each with a distinct role:

**Layer 1 — System: wiki schema (cached)**
The wiki page format conventions from `wiki_schema.md` (or `CLAUDE.md` until
`wiki_schema.md` is created). Tells the LLM how wiki pages should be structured.
Cached — stable across all rewrite calls in a consolidation run.

**Layer 2 — System: grounding sources (cached per page)**
The full text of every raw source document that contributed to this page.
Identified via `node.sources` in the graph — these are the source node keys
whose pages link to this concept/entity page.

This is the key grounding mechanism. Frame it explicitly in the prompt:

```
The following are the ORIGINAL SOURCE DOCUMENTS that contributed to the wiki
page you are rewriting. These are primary sources — treat them as ground truth.
Where the current wiki page conflicts with these sources, trust the sources.
Where the wiki page adds interpretation beyond what the sources say, preserve
it only if it is clearly reasonable inference, not speculation.
```

Cached with `cache_control: ephemeral` — stable for all pages that share the
same source documents, which is common (multiple concept pages often derive
from the same source).

**Layer 3 — User: the rewrite task**
The current page content plus the rewrite instruction. Not cached — specific
to each page.

### Method signatures to add to `WikiPrompts`

```python
def rewrite_system(
    self,
    schema: str,
    source_texts: list[tuple[str, str]],  # [(source_path, content), ...]
) -> list[dict]:
    """
    System prompt for a page rewrite call.

    Block 1: wiki schema (cached)
    Block 2: grounding source documents (cached)
    """
    ...

def rewrite_user(
    self,
    page_path: str,
    current_content: str,
    today: str,
) -> str:
    """
    User prompt for a page rewrite call.
    """
    ...
```

### `rewrite_system` implementation

```python
def rewrite_system(
    self,
    schema: str,
    source_texts: list[tuple[str, str]],
) -> list[dict]:
    # Build grounding block
    grounding_parts = []
    for source_path, content in source_texts:
        grounding_parts.append(
            f"=== SOURCE: {source_path} ===\n{content[:40_000]}"
        )
    grounding = "\n\n".join(grounding_parts)

    return [
        {
            "type": "text",
            "text": schema,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "The following are the ORIGINAL SOURCE DOCUMENTS that contributed "
                "to the wiki page you are about to rewrite. "
                "These are primary sources — treat them as ground truth.\n"
                "Where the current wiki page conflicts with these sources, "
                "trust the sources.\n"
                "Where the wiki page adds interpretation beyond what the sources "
                "say, preserve it only if it is clearly reasonable inference, "
                "not speculation.\n\n"
                f"{grounding}"
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]
```

### `rewrite_user` implementation

```python
def rewrite_user(
    self,
    page_path: str,
    current_content: str,
    today: str,
) -> str:
    return f"""Rewrite this wiki page as a clean, unified document.

Page: {page_path}

Current content (may contain dated append sections to integrate):
{current_content}

Instructions:
- Integrate all information from the append sections into coherent prose
- Remove all "### From [[sources/...]] (YYYY-MM-DD)" section headers
- Preserve all factual content and cross-references ([[WikiLinks]])
- Update the frontmatter: set updated={today}, increment consolidation_version by 1,
  ensure all contributing sources are listed in the sources: field
- Write in a clear, skimmable style — bullet points over dense paragraphs
- Where sources agree, state the consensus clearly
- Where sources offer different perspectives, note both with attribution
- Where the append sections contradict the original page content,
  resolve the contradiction using the grounding source documents as authority
- Return ONLY the complete rewritten markdown. No explanation, no fences."""
```

---

## `rewrite_page` implementation in rewrite.py

Replace the stub with the real implementation:

```python
def rewrite_page(
    page_path: Path,
    client: anthropic.Anthropic,
    graph: WikiGraph,
    wiki_dir: Path = None,
    raw_dir: Path = None,
) -> bool:
    """
    Rewrite a wiki page with pending append sections into a clean unified document.

    Loads original source documents as grounding context.
    Returns True on success, False on failure.
    """
    from config import WIKI_DIR, RAW_DIR, LLM_MODEL, MAX_TOKENS_PAGE
    from utils import _log
    from prompts import WikiPrompts
    from wiki_io import load_schema

    if wiki_dir is None:
        wiki_dir = WIKI_DIR
    if raw_dir is None:
        raw_dir = RAW_DIR

    if not page_path.exists():
        print(f"  [WARN] rewrite_page: {page_path} does not exist")
        return False

    sections = detect_append_sections(page_path)
    if not sections:
        print(f"  [WARN] rewrite_page: {page_path} has no append sections, skipping")
        return False

    print(f"  Rewriting {page_path} ({len(sections)} append sections)...")

    # Load current page content
    current_content = page_path.read_text(encoding="utf-8", errors="ignore")

    # Load grounding source documents via graph
    node_key = str(page_path.relative_to(wiki_dir))
    node = graph.get_node(node_key)
    source_texts = []

    if node and node.sources:
        for source_key in node.sources:
            # source_key is like "sources/shadow-work.md"
            # Find the corresponding raw file via the wiki source page frontmatter
            wiki_source_page = wiki_dir / source_key
            raw_path = _resolve_raw_path(wiki_source_page, raw_dir)
            if raw_path and raw_path.exists():
                content = raw_path.read_text(encoding="utf-8", errors="ignore")
                source_texts.append((str(raw_path), content))
            else:
                print(f"  [WARN] Could not resolve raw source for {source_key}")

    if not source_texts:
        print(f"  [WARN] No grounding sources found for {page_path.name} "
              f"— rewriting from wiki content only")

    # Build prompt and call LLM
    prompts = WikiPrompts()
    schema = load_schema()
    system = prompts.rewrite_system(schema, source_texts)
    user = prompts.rewrite_user(
        str(page_path), current_content, date.today().isoformat()
    )

    _log("anthropic_request", call="rewrite_page", model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PAGE, path=str(page_path),
         n_sources=len(source_texts))

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS_PAGE,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        usage = response.usage
        _log("anthropic_response", call="rewrite_page",
             path=str(page_path),
             input_tokens=usage.input_tokens,
             output_tokens=usage.output_tokens,
             cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
             cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

        rewritten = response.content[0].text.strip()

        # Write atomically
        tmp = page_path.with_suffix(".tmp")
        tmp.write_text(rewritten, encoding="utf-8")
        tmp.rename(page_path)

        print(f"  [REWRITE] {page_path}")
        return True

    except Exception as e:
        print(f"  [WARN] rewrite_page failed for {page_path}: {e}")
        _log("anthropic_error", call="rewrite_page",
             path=str(page_path), error=str(e))
        return False
```

---

## Resolving raw source paths

The graph stores source node keys like `sources/shadow-work.md`. The wiki
source page at `wiki/sources/shadow-work.md` contains a frontmatter `sources:`
field pointing to the original raw file path.

Add this helper to `rewrite.py`:

```python
def _resolve_raw_path(wiki_source_page: Path, raw_dir: Path) -> Optional[Path]:
    """
    Read the frontmatter of a wiki source page and return the raw file path.

    Wiki source pages have frontmatter like:
        sources: [raw/notes/shadow-work.md]

    Returns the Path if it exists, None otherwise.
    """
    if not wiki_source_page.exists():
        return None
    try:
        content = wiki_source_page.read_text(encoding="utf-8", errors="ignore")
        # Extract sources: field from YAML frontmatter
        m = re.search(r"^sources:\s*\[([^\]]+)\]", content, re.MULTILINE)
        if m:
            # Take first source if multiple listed
            raw_path_str = m.group(1).split(",")[0].strip().strip("\"'")
            return Path(raw_path_str)
    except Exception:
        pass
    return None
```

---

## Update consolidate.py

`rewrite_page` now requires `graph` as a parameter. Update the call site in
`pending_rewrites` processing within `run_consolidation`:

```python
# Before (stub signature)
success = rewrite_page(page_path, client)

# After (real signature)
success = rewrite_page(page_path, client, graph)
```

Also update the import at the top of `consolidate.py`:

```python
from rewrite import needs_rewrite, rewrite_page
```

No other changes to `consolidate.py`.

---

## Token budget note

`MAX_TOKENS_PAGE` (4096) is sufficient for most concept and entity page rewrites.
If a page is very large after many appends, the rewrite may be truncated.

Add a pre-flight check in `rewrite_page`:

```python
# Warn if current content is large
if len(current_content) > 8_000:
    print(f"  [WARN] {page_path.name} is {len(current_content):,} chars — "
          f"rewrite output may be truncated. Consider MAX_TOKENS_PAGE.")
```

The constant `MAX_TOKENS_PAGE` in `config.py` can be raised if needed.
For the current corpus this should not be an issue.

---

## Verification

After running ingest on at least two documents that share a concept page
(so the page has append sections):

```bash
# Confirm the page has append sections
grep "### From" wiki/concepts/some-concept.md

# Run consolidation (rewrite phase only if no high-priority synthesis candidates)
python consolidate.py --depth 3

# Confirm the page was rewritten — no more append section headers
grep "### From" wiki/concepts/some-concept.md  # should return nothing

# Confirm the rewrite is grounded — check the content still reflects sources
# Confirm consolidation_version incremented in graph
python wiki_graph.py --report
```

Also verify `.api_audit.log` shows a `rewrite_page` call with non-zero
`cache_read_tokens` on the second rewrite call of the same run (source
content should be cached).

---

## File summary

Files modified:
- `rewrite.py` — replace stub with real implementation, add `_resolve_raw_path`
- `prompts.py` — add `rewrite_system()` and `rewrite_user()` methods
- `consolidate.py` — update `rewrite_page` call signature

Files not touched:
- `ingest.py`, `middleware.py`, `wiki_io.py`, `splitting.py`
- `context.py`, `config.py`, `utils.py`
- `wiki_graph.py`, `wiki_retrieval.py`
- `CLAUDE.md`
