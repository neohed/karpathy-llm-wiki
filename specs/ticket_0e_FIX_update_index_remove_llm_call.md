# Fix — Remove LLM call from _update_index, fix _write_log APPEND reference

## Context

`_update_index` in `wiki_io.py` makes an LLM API call to update `wiki/index.md`.
This is unnecessary — the index is structured catalog data that Python can build
directly from `ctx.plan`. The LLM call:

- Is the largest prompt in the pipeline (grows with index size)
- Is the last call in a long sequential session — most vulnerable to connection drops
- Has caused two confirmed connection failures on the Burke-Hayek-Popper ingest
- Costs tokens unnecessarily for something deterministic

Additionally, `_write_log` still references `APPEND` which was removed in Ticket 0d.

Both are fixed in this ticket.

---

## Fix 1 — _write_log: fix stale APPEND reference

In `_write_log`, change:

```python
updated = [p["path"] for p in pages if p["action"].upper() == "APPEND"]
```

To:

```python
updated = [p["path"] for p in pages if p["action"].upper() == "UPDATE"]
```

---

## Fix 2 — _update_index: replace LLM call with Python

Replace the entire `_update_index` function with a pure Python implementation.
No API call. Reads existing index, parses current entries, merges new entries
from `ctx.plan`, writes clean index.

```python
def _update_index(ctx: "IngestContext"):
    """
    Update wiki/index.md from ctx.plan using pure Python.
    No LLM call — the index is structured catalog data, not prose.
    """
    index_path = WIKI_DIR / "index.md"
    today = date.today().isoformat()
    source_title = ctx.plan.get("source_title", ctx.path.stem)

    # Section name → dict of {path_key: entry_line}
    # Using dicts keyed by path ensures one entry per page (upsert behaviour)
    sections: dict[str, dict[str, str]] = {
        "Sources":  {},
        "Concepts": {},
        "Entities": {},
        "Analyses": {},
    }

    # Parse existing entries from current index to preserve pages
    # not touched in this ingest run
    if index_path.exists():
        current = index_path.read_text(encoding="utf-8", errors="replace")
        current_section = None
        for line in current.splitlines():
            stripped = line.strip()
            for section in sections:
                if stripped == f"## {section}":
                    current_section = section
                    break
            else:
                if current_section and stripped.startswith("- [["):
                    # Extract path key from "- [[path/slug]] — description"
                    m = re.match(r"- \[\[([^\]]+)\]\]", stripped)
                    if m:
                        key = m.group(1)
                        sections[current_section][key] = stripped

    # Map node type to index section
    type_to_section = {
        "source":   "Sources",
        "concept":  "Concepts",
        "entity":   "Entities",
        "analysis": "Analyses",
    }

    # Add or update entries from the current plan
    for item in ctx.plan.get("pages", []):
        node_type = item.get("type")
        section = type_to_section.get(node_type)
        if not section:
            continue

        raw_path = item.get("path", "")
        # Normalise to wiki-relative path without extension
        # e.g. "wiki/entities/carl-jung.md" → "entities/carl-jung"
        key = raw_path
        if key.startswith("wiki/"):
            key = key[len("wiki/"):]
        if key.endswith(".md"):
            key = key[:-3]

        label = item.get("label") or Path(raw_path).stem.replace("-", " ").title()
        desc  = item.get("description", "").split(".")[0]  # first sentence only
        entry = f"- [[{key}]] — {label}: {desc} ({today})"
        sections[section][key] = entry

    # Count total pages across all sections
    total = sum(len(v) for v in sections.values())

    # Rebuild index markdown
    lines = [
        "# Wiki Index",
        "",
        f"_Last updated: {today} — {total} pages_",
        "",
    ]

    for section, entries in sections.items():
        if entries:
            lines.append(f"## {section}")
            lines.extend(sorted(entries.values()))
            lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")
    print(f"  [UPDATE] {index_path}")
    ctx.written_paths.append(str(index_path))
```

Note: `re` must be imported at the top of `wiki_io.py`. Add if not already present:

```python
import re
```

---

## Remove update_index_user from prompts.py

The `update_index_user` method in `WikiPrompts` is no longer called anywhere.
Remove it to keep the prompt DAL clean.

Verify it is not called anywhere before removing:

```bash
grep -r "update_index_user" .
```

Should return only the definition in `prompts.py`. If any other file references
it, investigate before removing.

---

## Remove schema import from _update_index

The current `_update_index` imports `load_schema` for the LLM system prompt.
After this fix, schema is no longer needed in `_update_index`.

Check if `load_schema` is used anywhere else in `wiki_io.py`. If `_update_index`
was the only caller, the import can be removed from `wiki_io.py`:

```python
# Remove if no longer used:
from config import WIKI_DIR, SCHEMA_FILE, LLM_MODEL, MAX_TOKENS_PAGE
```

May simplify to:

```python
from config import WIKI_DIR
```

Check carefully — `load_schema` is also called from `middleware.py` but that
import is separate. Only remove from `wiki_io.py` if unused there.

---

## Verification

```bash
# Reset to clean state
./scripts/delete-wiki-files.sh

# Ingest Tragic Realism
python ingest.py "raw/notes/Tragic Realism: A Practical Training in Spotting Hubris and Utopian Thinking.md"

# Check index was written without LLM call
grep "update_index" .api_audit.log  # should return nothing

# Check index content looks correct
cat wiki/index.md

# Ingest Shadow Work — should UPDATE existing entries in index
python ingest.py "raw/notes/Shadow Work.md"

# Check index updated correctly — carl-jung entry should reflect today's date
grep "carl-jung" wiki/index.md

# Check log references UPDATE not APPEND
cat wiki/log.md
```

Expected `wiki/index.md` format after two ingests:

```markdown
# Wiki Index

_Last updated: 2026-05-22 — 28 pages_

## Sources
- [[sources/shadow-work]] — Shadow Work: Personal notes on Jungian shadow integration (2026-05-22)
- [[sources/tragic-realism-practical-training]] — Tragic Realism: ... (2026-05-22)

## Concepts
- [[concepts/acceptance-of-limits]] — ...
- [[concepts/carl-jung]] — ...
...

## Entities
...
```

---

## What must NOT change

- `_write_log` logic beyond the APPEND→UPDATE fix
- All other functions in `wiki_io.py`
- `middleware.py` — `_update_index` is called from `mw_write_pages`, signature unchanged
- `prompts.py` rewrite_*, synthesis_* methods — unaffected

---

## File summary

Files modified:
- `wiki_io.py` — replace `_update_index` LLM call with Python implementation,
  fix `_write_log` APPEND→UPDATE, add `import re`, simplify imports
- `prompts.py` — remove `update_index_user` method (no longer called)

Files not touched:
- `middleware.py`, `ingest.py`, `consolidate.py`
- `wiki_graph.py`, `wiki_retrieval.py`, `synthesis.py`, `rewrite.py`
- `splitting.py`, `utils.py`, `context.py`, `config.py`, `CLAUDE.md`
