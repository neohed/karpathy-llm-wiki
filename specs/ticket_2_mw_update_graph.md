# Ticket 2 — mw_update_graph middleware + plan prompt extension

## Context

`wiki_graph.py` now exists with a clean read/write API (Ticket 1).
This ticket wires it into the ingest pipeline in two parts:

1. Extend the plan prompt so Claude returns richer page metadata
2. Add `mw_update_graph` middleware that reads `ctx.plan` and updates the graph

No new LLM calls. No changes to Pass 2. No changes to `wiki_retrieval.py`.

---

## Part 1 — Extend the plan prompt in prompts.py

### New fields required in each page item

The plan prompt currently asks Claude to return:

```json
{
  "source_title": "Shadow Work",
  "summary": "One-line description",
  "pages": [
    {
      "action": "CREATE",
      "path": "concepts/shadow.md",
      "description": "Jungian concept of the rejected unconscious self"
    }
  ]
}
```

Extend each page item with three new fields:

```json
{
  "action": "CREATE",
  "path": "concepts/shadow.md",
  "description": "Jungian concept of the rejected unconscious self",
  "type": "concept",
  "label": "Shadow",
  "edge_type": "introduces"
}
```

### Field definitions

**`type`** — node type, must be one of:
- `source` — always used for pages under `sources/`
- `concept` — pages under `concepts/`
- `entity` — pages under `entities/`
- `analysis` — pages under `analyses/`

Claude should infer this from the path prefix. It will always be unambiguous.

**`label`** — human-readable name for the node. For concepts and entities this is
the proper name, not the slug. Examples:
- `concepts/carl-jung.md` → `"Carl Jung"`
- `concepts/shadow.md` → `"Shadow"`
- `concepts/burke-hayek-popper-triad.md` → `"Burke-Hayek-Popper Triad"`

**`edge_type`** — the relationship from the source being ingested to this page.
Must be a single string from this vocabulary:

| Value | When to use |
|-------|-------------|
| `introduces` | CREATE action — this source is the first to bring this concept into the wiki |
| `discusses` | APPEND action — this source adds to an existing concept or entity page |
| `refines` | APPEND action — this source adds nuance, qualification, or correction |
| `contradicts` | Either action — this source conflicts with claims on an existing page |
| `parallels` | Either action — this source draws a structural analogy to an existing concept |

For most CREATE actions: `introduces`.
For most APPEND actions: `discusses` or `refines`.
`contradicts` and `parallels` should only be used when Claude genuinely identifies
the relationship — do not use as defaults.

### How to update prompts.py

Update `plan_user()` in `WikiPrompts` to include the new field definitions and
an example in the JSON schema section of the prompt. The existing prompt structure
does not change — only the page item schema within it.

Add the new fields to the example JSON in the prompt. Make the field descriptions
concise — this is a planning call with a 2048 token output budget.

Also update `plan_system()` if the schema block references the page item structure.

---

## Part 2 — mw_update_graph middleware

### New middleware function

```python
def mw_update_graph(ctx: IngestContext, next):
    """Update the knowledge graph from the completed ingest plan."""
    ...
    next()
```

Insert into the pipeline between `mw_write_pages` and `mw_update_embeddings`:

```python
ingest_pipeline = make_pipeline(
    mw_load_content,
    mw_plan,
    mw_write_pages,
    mw_update_graph,       # ← new
    mw_update_embeddings,
)
```

### What mw_update_graph does

1. Load the graph from disk (`WikiGraph.load()`)
2. Determine the source node key from `ctx.plan`:
   - Find the page item where `type == "source"` — that is the source node
   - Its `path` field is the source node key e.g. `"sources/shadow-work.md"`
   - Its `label` field is the source node label
3. Add the source node to the graph
4. For every other page item in `ctx.plan["pages"]`:
   - Add or update the node using `type`, `label`, `path`, and the source node key
   - Add an edge from the source node to this node using `edge_type`
   - Skip `index.md` and `log.md` — these are housekeeping files, not knowledge nodes
5. Save the graph atomically

### Defensive handling

- If `ctx.plan` is empty or has no pages, log `[WARN]` and return without saving
- If a page item is missing `type`, `label`, or `edge_type`, infer defensively:
  - `type`: infer from path prefix (`sources/` → `source`, `concepts/` → `concept`,
    `entities/` → `entity`, `analyses/` → `analysis`). If path doesn't match any
    prefix, skip the item with a `[WARN]`.
  - `label`: derive from filename stem, title-cased, hyphens replaced with spaces.
    e.g. `carl-jung` → `"Carl Jung"`
  - `edge_type`: default to `"discusses"` for APPEND, `"introduces"` for CREATE
- Wrap the entire middleware in try/except — a graph update failure must not kill
  the pipeline. Log `[WARN]` and call `next()`.

### Import

Add to the imports at the top of `ingest.py`:

```python
from wiki_graph import WikiGraph
```

`WikiGraph` is instantiated fresh inside `mw_update_graph` on each call via
`WikiGraph.load()` — do not hold a long-lived instance in module scope, since
multiple pipeline runs in the same process would otherwise share stale state.

---

## Skipping index.md and log.md

These paths must be excluded from graph construction:

```python
GRAPH_SKIP_PATHS = {"wiki/index.md", "wiki/log.md", "index.md", "log.md"}

def _should_skip_graph(path: str) -> bool:
    return Path(path).name in {"index.md", "log.md"}
```

---

## Graph path convention

Page items in `ctx.plan` use paths relative to the project root:
`"sources/shadow-work.md"`, `"concepts/shadow.md"`.

`WikiGraph` node keys use the same convention — relative to `wiki/` root.
These match directly. Do not prepend `wiki/` to the path when calling `add_node()`
or `add_edge()` — the plan paths are already in the correct format.

Verify this matches how `wiki_graph.py` was implemented in Ticket 1. If there is
a mismatch, the graph module is the authority — adjust here to match it.

---

## Logging

Add a summary log line after the graph is saved:

```
  [GRAPH] 4 nodes, 3 edges (+2 nodes, +3 edges this ingest)
```

Track delta by comparing node/edge counts before and after the update.

---

## Verification

Run a single file ingest and then inspect the graph:

```bash
python ingest.py raw/notes/Shadow\ Work.md
python wiki_graph.py --report
```

Expected report output should show:
- Source node for shadow-work
- Concept and entity nodes created during that ingest
- Edges connecting the source to each concept/entity with correct edge types
- No entries for index.md or log.md

Also verify the plan JSON in `.api_audit.log` contains the new `type`, `label`,
and `edge_type` fields on every page item.

---

## What must NOT change

- Pass 2 write logic — `mw_write_pages` is not touched
- `wiki_retrieval.py` — not touched
- `CLAUDE.md` — not touched
- The plan JSON top-level structure (`source_title`, `summary`, `pages`) — unchanged
- Existing page item fields (`action`, `path`, `description`) — unchanged, only
  new fields added

---

## File summary

Files modified:
- `prompts.py` — extend `plan_user()` (and `plan_system()` if needed) with new
  field definitions and updated example JSON
- `ingest.py` — add `mw_update_graph` middleware, insert into pipeline,
  add `WikiGraph` import, add `_should_skip_graph()` helper

Files not touched:
- `wiki_graph.py`
- `wiki_retrieval.py`
- `CLAUDE.md`
