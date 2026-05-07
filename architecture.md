# Architecture

How the LLM Wiki pipeline works, end to end.

---

## What the system does

You drop source files into `raw/`. Running `python ingest.py` reads each new file, sends it to Claude along with relevant context from the existing wiki, and Claude writes structured markdown pages into `wiki/`. Over time the wiki accumulates and cross-links knowledge from every source you've added.

```
raw/notes/shadow-work.md   →   ingest.py   →   wiki/sources/shadow-work.md
                                                wiki/concepts/shadow.md
                                                wiki/concepts/persona.md  (updated)
                                                wiki/entities/carl-jung.md (updated)
                                                wiki/index.md             (updated)
                                                wiki/log.md               (appended)
```

---

## The pipeline

Each file goes through a middleware chain — similar to Express/Connect in Node.js. Each step receives a context object, does its work, and calls `next()` to pass control forward.

```
mw_load_content → mw_load_wiki → mw_call_llm → mw_parse → mw_apply → mw_update_index
```

---

## Step by step: ingesting `raw/notes/shadow-work.md`

### Step 1 — mw_load_content

Reads the raw source file into `ctx.content`. Nothing else.

---

### Step 2 — mw_load_wiki

This is where **semantic retrieval** comes in.

Without it, the old approach loaded every wiki page alphabetically until hitting a 60KB budget. Jung would make the cut; vagus-nerve.md would too, whether relevant or not. Pages that happened to sort late would be silently dropped.

With semantic retrieval (`wiki_retrieval.py`):

1. The source file is embedded as a **query vector** using Voyage AI's `voyage-3.5` model
2. Every wiki page has been pre-embedded as a **document vector** (cached in `.wiki_embeddings.json`)
3. Cosine similarity is computed between the query and all document vectors
4. The top 12 most similar pages are returned — Jung, persona, shadow, authenticity — not vagus-nerve

This means the LLM always sees the pages most likely to be relevant, regardless of wiki size.

**Example result for `shadow-work.md`:**
```
=== concepts/shadow.md ===
[content]

=== entities/carl-jung.md ===
[content]

=== concepts/persona.md ===
[content]

=== concepts/authenticity.md ===
[content]

... (8 more by similarity score)
```

The embedding cache is hash-based — a page is only re-embedded when its content changes. On a warm run, `build_index()` is essentially instant.

---

### Step 3 — mw_call_llm

Assembles the full API call and sends it to Claude:

**System prompt (two blocks):**

| Block | Content | Cached? |
|---|---|---|
| Block 1 | Full contents of `CLAUDE.md` — the wiki schema and instructions | Yes — stable, served from Anthropic's prompt cache after the first call |
| Block 2 | Today's date + response format instruction | No — changes daily |

**User prompt:**

```
Ingest this source:

## Source
raw/notes/shadow-work.md

## Content
[full text of shadow-work.md]

---

## Current Wiki State
=== concepts/shadow.md ===
[content]

=== entities/carl-jung.md ===
[content]

... (top 12 retrieved pages)

Produce the JSON actions now.
```

**Approximate token counts:**

| Part | Tokens |
|---|---|
| CLAUDE.md schema | ~1,300 (cached after first call) |
| Date + instructions | ~50 |
| Source file content | ~500–8,000 |
| Wiki context (top 12 pages) | ~3,000–8,000 |
| **Total input** | **~5,000–17,000** |

---

### Step 4 — mw_parse

The LLM responds with a JSON block. This step extracts and parses it:

```json
[
  {
    "action": "CREATE",
    "path": "wiki/sources/shadow-work.md",
    "content": "---\ntitle: Shadow Work\n..."
  },
  {
    "action": "CREATE",
    "path": "wiki/concepts/shadow-integration.md",
    "content": "---\ntitle: Shadow Integration\n..."
  },
  {
    "action": "APPEND",
    "path": "wiki/concepts/shadow.md",
    "content": "### From [[sources/shadow-work]] (2026-05-06)\n- new point\n- new point\n"
  },
  {
    "action": "APPEND",
    "path": "wiki/entities/carl-jung.md",
    "content": "### From [[sources/shadow-work]] (2026-05-06)\n- new point\n"
  },
  {
    "action": "UPDATE",
    "path": "wiki/index.md",
    "content": "..."
  },
  {
    "action": "APPEND",
    "path": "wiki/log.md",
    "content": "## [2026-05-06] ingest | Shadow Work\n..."
  }
]
```

**Three action types:**

| Action | Behaviour | Used for |
|---|---|---|
| `CREATE` | Write new file (overwrites if exists) | New source pages, new concept/entity pages |
| `UPDATE` | Replace entire file | `index.md` — always rewritten as a clean catalog |
| `APPEND` | Add to end of file | Existing concept/entity pages, `log.md` |

---

### Step 5 — mw_apply

Writes every action to disk. Directories are created if they don't exist.

---

### Step 6 — mw_update_index

Re-embeds only the pages that were just written, so the retrieval index stays current for the next ingest. Unchanged pages are not re-embedded.

---

## Why APPEND for existing pages?

This is the key architectural decision. When a new source has something to add to `wiki/concepts/shadow.md`, there are two options:

**UPDATE (old approach):**
- LLM reads the full current page in the input
- LLM reproduces the full page in the output with additions woven in
- Output tokens = entire page size × number of pages touched
- As the wiki grows, output tokens grow. With 50 pages touching 15 per ingest → 32K+ tokens output → hits model ceiling

**APPEND (current approach):**
- LLM reads the full current page in the input
- LLM outputs only the new section (a few bullet points)
- Output tokens = new content only, regardless of existing page size
- Output stays flat as the wiki grows

The tradeoff: pages accumulate dated sections over time rather than staying as clean unified documents. A periodic **consolidation** step (on demand, not automatic) rewrites a single page cleanly — one focused API call, one page, run when it looks cluttered.

---

## Large file splitting

Files over 40KB are automatically split at H1/H2 heading boundaries before ingestion.

```
raw/books/atomic-habits-reduced.md   (48KB)
    ↓
raw/books/.atomic-habits-reduced/
    part-01.md   ← preamble
    part-02.md   ← # Chapter 1
    part-03.md   ← # Chapter 2
    ...
```

`find_raw_files()` excludes hidden directories (those starting with `.`), so only the original file appears in the file list. Its hash is tracked in `.ingest_state.json`. If the original changes, the splits are wiped and recreated.

Each part is ingested as a separate pipeline run, with `(Part of: atomic-habits-reduced.md)` added to the user prompt so the LLM names wiki pages consistently across parts.

---

## File layout

```
raw/              ← you write here (immutable, never touched by LLM)
wiki/             ← LLM writes here, you read here
  sources/        ← one summary page per raw source
  concepts/       ← idea and concept pages
  entities/       ← people, models, tools, orgs
  analyses/       ← comparisons, syntheses, answers
  index.md        ← master catalog (always UPDATEd)
  log.md          ← append-only chronological record
CLAUDE.md         ← wiki schema and LLM instructions (you edit)
ingest.py         ← the pipeline
wiki_retrieval.py ← semantic retrieval module
.env.local        ← API keys (gitignored)
.wiki_embeddings.json  ← embedding cache (gitignored, rebuilt automatically)
.ingest_state.json     ← file hashes to skip unchanged sources (gitignored)
```
