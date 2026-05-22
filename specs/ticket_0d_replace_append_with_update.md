# Ticket 0d — Replace APPEND with UPDATE throughout the pipeline

## Context

The APPEND action was introduced to minimise output tokens by writing only new
sections to existing pages. This caused a correctness problem: re-ingesting a
source or ingesting multiple sources that mention the same entity produces
duplicate content on existing pages.

Analysis shows the token cost concern was unfounded at this scale — a full page
rewrite is well within Claude's context limits. UPDATE is simpler, correct, and
produces clean integrated pages after every ingest.

This ticket removes APPEND as a pipeline primitive and replaces it with UPDATE
everywhere. The pipeline simplifies. The wiki stays clean after every ingest.
The consolidation rewrite phase (housekeeping) is no longer needed.

---

## What changes

### Action vocabulary

Before: `CREATE` | `APPEND` | `UPDATE`
After:  `CREATE` | `UPDATE`

- `CREATE` — new file, page does not yet exist in wiki
- `UPDATE` — page exists, rewrite it cleanly integrating all information

### File writing

Both CREATE and UPDATE write the full page content to disk:

```python
path.write_text(content, encoding="utf-8")
```

No distinction at the filesystem level. The distinction is only in the LLM
prompt — CREATE writes from scratch, UPDATE integrates with existing content.

---

## Changes required

### prompts.py

**`plan_user`** — update the rules section:

Remove:
```
- APPEND for existing pages that have meaningful new information from this source
```

Replace with:
```
- UPDATE for existing pages that have meaningful new information from this source.
  For UPDATE actions, the existing page content will be provided — produce a
  complete rewritten page that integrates the existing content with new information
  from this source. Do not reproduce existing content unchanged where the new
  source adds nothing — only integrate where there is genuine new information.
```

Also update the example JSON in `plan_user` — replace the APPEND example with UPDATE:

```python
{{"action": "UPDATE", "path": "wiki/entities/name.md", "description": "what new information to integrate", "type": "entity", "label": "Entity Name", "edge_type": "discusses"}}
```

**`write_page_user`** — replace the APPEND branch with an UPDATE branch:

Remove the entire APPEND/else branch. Add a new UPDATE branch:

```python
def write_page_user(
    self,
    action: str,
    page_path: str,
    description: str,
    source_slug: str,
    today: str,
    existing_content: Optional[str] = None,
) -> str:
    if action == "CREATE":
        return f"""Write the full markdown content for this new wiki page.

Page: {page_path}
Description: {description}
Today's date (use for created/updated frontmatter): {today}

Follow the wiki schema conventions exactly (frontmatter, WikiLinks, etc.).
Return ONLY the markdown. No explanation, no JSON, no fences."""

    else:  # UPDATE
        return f"""Rewrite this wiki page, integrating new information from the
source document.

Page: {page_path}
Description of new information to integrate: {description}

Existing page content:
{existing_content or "(page is empty)"}

Instructions:
- Produce a complete, clean, unified page — not a page with appended sections
- Integrate new information from the source document naturally into the existing
  structure — do not add dated section headers like "### From [[sources/...]]"
- Preserve all existing content that remains accurate and relevant
- Where the source adds new detail to an existing section, expand that section
- Where the source introduces a genuinely new aspect not covered, add a new section
- Where the source contradicts existing content, resolve using the source as authority
  and note the update inline if significant
- Update frontmatter: set updated={today}, ensure this source is in sources: field
- Return ONLY the complete rewritten markdown. No explanation, no JSON, no fences."""
```

**`rewrite_user`** — keep as-is. The consolidation rewrite prompt remains valid
for future use even if it is no longer called during ingest.

**`update_index_user`** — ensure it ends with:
```
Return ONLY the markdown. No fences, no explanation.
```
If this line is missing, add it now.

---

### middleware.py

**`mw_write_pages`** — simplify the file writing block:

Remove the APPEND branch entirely. Both CREATE and UPDATE write the full content:

```python
for item in ctx.plan.get("pages", []):
    action = item["action"].upper()
    path   = Path(item["path"])
    desc   = item.get("description", "")

    # Load existing content for UPDATE actions
    existing = None
    if action == "UPDATE" and path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")

    print(f"  Writing {path}...")
    try:
        content = call_llm_write_page(
            ctx.client, schema,
            str(ctx.path), ctx.content,
            action, str(path), desc, source_slug, existing,
            plan_pages=ctx.plan.get("pages", []),
        )
    except Exception as e:
        print(f"  [WARN] Failed to write {path}: {e}")
        continue

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  [{action}] {path}")
    ctx.written_paths.append(str(path))
```

Note: the `path.write_text()` call is now identical for CREATE and UPDATE —
no branching on action type at the filesystem level.

**`mw_update_graph`** — update edge type inference:

The fallback edge type for UPDATE should be `"discusses"` not `"introduces"`:

```python
edge_type = item.get("edge_type") or (
    "introduces" if action == "CREATE" else "discusses"
)
```

This was previously `"introduces" if action == "CREATE" else "discusses"` for
APPEND — same logic, now applies to UPDATE. No change needed if already written
this way.

---

### consolidate.py

**`run_consolidation`** — remove the pending rewrites phase entirely:

Remove:
```python
# Step 1 — Rewrite all pages with pending append sections first
rewrites = pending_rewrites(graph)
if rewrites:
    ...
```

Replace with a comment explaining why it was removed:

```python
# Note: rewrite phase removed — ingest now uses UPDATE not APPEND,
# so pages are always clean after ingest. Consolidation focuses
# entirely on synthesis.
```

Remove the import of `needs_rewrite` and `rewrite_page` from `consolidate.py`
if they are no longer used anywhere in the file.

The `pending_rewrites` function can be removed from `consolidate.py` or kept
as dead code with a comment — your preference.

---

### rewrite.py

No changes required. Keep the file intact — `detect_append_sections`,
`needs_rewrite`, and `rewrite_page` may be useful for:
- Cleaning up existing wiki pages that accumulated append sections before this fix
- Future consolidation use cases
- The hallucination drift spike in FUTURE_WORK.md

Add a module-level comment noting the current status:

```python
"""
rewrite.py — Page rewrite utilities.

Note: as of Ticket 0d, the ingest pipeline uses UPDATE not APPEND, so
pages no longer accumulate append sections during normal ingest runs.
These utilities are retained for:
- Cleaning up legacy pages with append sections
- Potential future consolidation use cases
See FUTURE_WORK.md for the hallucination drift spike.
"""
```

---

### wiki_graph.py

No changes required. The graph records CREATE and UPDATE actions using the
same node/edge logic — the distinction is transparent to the graph.

---

### CLAUDE.md

Update the section that describes the builder's understanding of the pipeline.
In the two-pass ingest description, replace:

```
APPEND for existing pages that have meaningful new information
```

With:

```
UPDATE for existing pages — full rewrite integrating existing content
with new information from the source. Pages are always clean after ingest.
```

---

## Wiki state — cleanup before next ingest

The current wiki has pages with append sections from the pre-fix ingest runs.
Before ingesting further documents, clean the wiki to a known good state:

```bash
# Reset wiki, graph, embeddings, and state to clean slate
rm -rf wiki/
rm -f .wiki_graph.json .wiki_embeddings.json .ingest_state.json

# Recreate wiki directory structure
mkdir -p wiki/sources wiki/concepts wiki/entities wiki/analyses

# Re-ingest Tragic Realism cleanly (no --force needed on clean slate)
python ingest.py "raw/notes/Tragic Realism: A Practical Training in Spotting Hubris and Utopian Thinking.md"
```

Verify the output:
- `wiki/entities/carl-jung.md` exists with no `### From [[sources/...]]` sections
- `wiki/index.md` has no markdown fences
- `.wiki_graph.json` has correct nodes and edges
- `.api_audit.log` shows UPDATE calls for existing pages (none expected on first
  ingest of a clean wiki, but verify CREATE actions completed cleanly)

---

## Verification

After the fix, ingest the same document twice and confirm no duplication:

```bash
# First ingest
python ingest.py "raw/notes/Shadow Work.md"

# Second ingest of same file — should UPDATE cleanly, no duplication
python ingest.py "raw/notes/Shadow Work.md" --force

# Check carl-jung.md — should be clean, no dated section headers
grep "### From" wiki/entities/carl-jung.md  # should return nothing
```

Also verify the plan JSON in `.api_audit.log` uses UPDATE not APPEND for
existing pages on the second run.

---

## What must NOT change

- `synthesis.py` — unaffected, no APPEND references
- `wiki_retrieval.py` — unaffected
- `splitting.py`, `utils.py`, `context.py`, `config.py` — unaffected
- `prompts.py` rewrite_* and synthesis_* methods — unaffected

---

## File summary

Files modified:
- `prompts.py` — update `plan_user`, replace APPEND branch in `write_page_user`,
  add fence instruction to `update_index_user` if missing
- `middleware.py` — simplify `mw_write_pages`, remove APPEND file handling
- `consolidate.py` — remove pending rewrites phase, remove unused imports
- `rewrite.py` — add module-level status comment
- `CLAUDE.md` — update pipeline description

Files not touched:
- `wiki_graph.py`, `wiki_retrieval.py`, `synthesis.py`
- `splitting.py`, `utils.py`, `context.py`, `config.py`
