# LLM Wiki — Project Guide

This file guides the AI coding assistant (Claude Code, Claude in VS Code, or similar)
working on this project alongside the human developer. It describes what the project
is, how it is architected, the current state of development, and the conventions to
follow when writing code.

---

## What this project is

A Python pipeline that builds a personal knowledge wiki from a curated corpus of
source documents. The human drops source files into `raw/`. Running `python ingest.py`
processes them into structured markdown pages in `wiki/`. Running `python consolidate.py`
performs periodic synthesis across the accumulated wiki — finding patterns, connections,
and emergent insights that span multiple sources.

This is a personal research tool, not a production system. The corpus is small and
deliberately curated. Quality of synthesis matters more than throughput.

---

## What this project is NOT

- The LLM is not the agent. The Python pipeline is the agent.
- The Claude API is one tool the pipeline calls, like the Voyage API or the filesystem.
- This is not a RAG system. The wiki is a persistent, growing knowledge artifact —
  not a retrieval index over raw documents.
- Do not treat any LLM as having persistent memory or ongoing awareness across pipeline
  runs. Every API call is stateless. The wiki and the graph are the persistent state.

---

## Architecture

```
raw/                  ← human drops source files here (immutable)
wiki/                 ← pipeline writes here
  sources/            ← one summary page per raw source
  concepts/           ← idea and concept pages
  entities/           ← people, schools of thought, frameworks
  analyses/           ← syntheses and emergent insights (written by consolidate.py)
  index.md            ← master catalog
  log.md              ← append-only operation log
prompts/              ← all LLM prompt templates (see Prompt conventions below)
ingest.py             ← ingest pipeline
wiki_retrieval.py     ← Voyage AI semantic retrieval over wiki pages
wiki_graph.py         ← knowledge graph builder and analyser (to be built)
consolidate.py        ← consolidation pipeline (to be built)
.wiki_graph.json      ← knowledge graph (machine-written, gitignored)
.wiki_embeddings.json ← Voyage embedding cache (machine-written, gitignored)
.ingest_state.json    ← file hash state for skip-unchanged (machine-written, gitignored)
.api_audit.log        ← API call log (machine-written, gitignored)
.env.local            ← API keys (gitignored)
```

### ingest.py — middleware pipeline

Uses a Connect/Express-style middleware chain. Each middleware receives an
`IngestContext` dataclass and a `next()` callable.

Current pipeline:
```
mw_load_content → mw_plan → mw_write_pages → mw_update_embeddings
```

Planned addition (Ticket 2):
```
mw_load_content → mw_plan → mw_write_pages → mw_update_graph → mw_update_embeddings
```

To add a middleware: write a function `mw_foo(ctx: IngestContext, next)`, add it to
`ingest_pipeline = make_pipeline(...)`. Do not modify other middleware functions.

### Two-pass ingest

Pass 1 (`mw_plan`) — one cheap LLM call returns a JSON plan: which pages to create
or append to, with descriptions but no content. Bounded at 2048 output tokens.

Pass 2 (`mw_write_pages`) — one focused LLM call per page. Source content is placed
in the system prompt and cached by Anthropic across all page writes for the same
source, so subsequent pages cost ~10% of normal input token cost.

### wiki_retrieval.py

Maintains a Voyage AI embedding index over wiki pages. Called by `mw_plan` to retrieve
the top-K most semantically relevant wiki pages for a given source, replacing the
original naive alphabetical loading approach.

### wiki_graph.py (to be built — Ticket 1)

A standalone module with no LLM calls. Builds and maintains a knowledge graph of
relationships between wiki pages. JSON format, stored at `.wiki_graph.json`.
Used by `consolidate.py` to identify synthesis candidates, bridge nodes, and
consolidation candidates.

### consolidate.py (to be built — Tickets 4–7)

A separate script run periodically, not on every ingest. Three phases:

1. Survey — read full wiki + graph, one LLM call produces a consolidation plan
2. Synthesis — one LLM call per synthesis candidate, writes to `wiki/analyses/`
3. Consolidation — one LLM call per candidate page, rewrites append-accumulations
   as clean unified documents

---

## Ticket plan

Current state: Ticket 0a (this file) in progress.

```
0a  — Rewrite CLAUDE.md as builder guide                    ← current
0b  — Extract inline prompt strings to prompts/ module
1   — wiki_graph.py: data structure and read/write API
2   — mw_update_graph middleware in ingest.py
3   — wiki_graph.py: analysis utilities (bridge nodes, clusters, etc.)
4   — consolidate.py skeleton + survey phase
5   — consolidate.py synthesis phase (writes wiki/analyses/)
6   — consolidate.py consolidation phase (rewrites accumulated pages)
7   — consolidate.py structural suggestions (optional, conservative)
```

Separate pending work:
```
P1  — File extraction middleware (pdf, html, odt, docx)
      See: PROMPT_file_extraction_middleware.md
```

---

## Prompt conventions

All LLM prompt templates live in `prompts/`. No prompt strings inline in Python code.

```
prompts/
  wiki_schema.md             ← wiki page format conventions (sent as context, not persona)
  ingest_plan.md             ← Pass 1: planning prompt
  ingest_write_page.md       ← Pass 2: page writing prompt
  ingest_update_index.md     ← index update prompt
  consolidate_survey.md      ← consolidation survey prompt
  consolidate_synthesise.md  ← synthesis phase prompt
  consolidate_rewrite.md     ← page rewrite/consolidation prompt
```

### Loading prompts

Use a simple loader. No hardcoded prompt paths outside the loader:

```python
def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from prompts/ and interpolate kwargs."""
    path = Path("prompts") / f"{name}.md"
    template = path.read_text(encoding="utf-8")
    return template.format(**kwargs) if kwargs else template
```

### wiki_schema.md

Contains wiki page format conventions — frontmatter structure, page types, WikiLink
conventions, analyses page format, graph edge vocabulary. Sent as a system prompt
context block on every API call that writes wiki content. Reference material only —
no persona framing, no "you are" language.

---

## Coding conventions

- Python 3.10+. Type hints on all function signatures.
- Dataclasses for context objects. No plain dicts passed between pipeline stages.
- All file I/O uses `pathlib.Path`, not `os.path`.
- All API calls logged via `_log()` to `.api_audit.log` (one JSON object per line).
- Errors in per-page or per-file operations: catch, log with `[WARN]`, continue.
  Errors in setup (missing keys, missing dirs): fatal, `sys.exit(1)` with clear message.
- State files written atomically — write to temp file, then rename.
- No prompt strings in Python files. Use `load_prompt()`.
- Git is the undo mechanism. Destructive operations (page rewrites, structural
  changes) must log what they changed to `wiki/log.md` before writing.

---

## Environment

```
ANTHROPIC_API_KEY   — Claude API (required)
VOYAGE_API_KEY      — Voyage AI embeddings (required for semantic retrieval)
```

Both loaded from `.env.local` via `python-dotenv`.

```
pip install anthropic python-dotenv voyageai numpy
```

---

## Current corpus

Small and deliberately curated. Personal intellectual development across philosophy,
psychology, political thought, and practical self-development:

```
raw/books/    Atomic Habits (reduced)
raw/notes/    Shadow Work
              Jimmy Carr Philosophy
              Burke–Hayek–Popper Triad
              Buddhist Principles
              Framework for anti-ideological thinking
              Therapy — Grok, Survivor Tips
              Tragic Realism
```

Chosen for conceptual synergies across documents. The goal is a wiki that surfaces
emergent connections — particularly bridge concepts that appear across multiple
intellectual domains.

---

## What good looks like

- A new middleware can be added without modifying existing middleware.
- A prompt can be tuned without touching Python code.
- A pipeline run that fails mid-batch resumes cleanly from where it stopped.
- Every destructive operation is logged and reversible via git.
- The graph and the wiki are always in sync after a completed run.
- `consolidate.py --survey` shows a human-readable plan before any writes happen.
