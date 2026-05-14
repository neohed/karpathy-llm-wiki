# Ticket 0b — Extract inline prompts to WikiPrompts class

## Context

`ingest.py` currently contains all LLM prompt strings inline as f-strings scattered
across three functions. This ticket extracts them into a single `WikiPrompts` class
in a new `prompts.py` file. No new functionality. No change to behaviour. Pure
structural refactor.

This is the first step toward a prompt DAL. Later tickets may introduce Jinja2
templating or Pydantic validation, but YAGNI — keep it simple now.

---

## What to create

### `prompts.py`

A single file containing the `WikiPrompts` class. No external dependencies beyond
stdlib. No file I/O — prompts live as methods, not external template files.

```python
class WikiPrompts:
    """
    Prompt DAL for the LLM Wiki pipeline.

    Each prompt is a pair of methods:
      <name>_system(...) -> list[dict]   — Anthropic system blocks with cache_control
      <name>_user(...)   -> str          — user message content

    _update_index has no system method — it reuses the schema block from the
    call site and only needs a user method.
    """
```

---

## The three prompts to extract

### 1. Plan prompt

Currently in `call_llm_plan()`.

**System** — two blocks, first is cached:
- Block 1: `schema` string with `cache_control: ephemeral`
- Block 2: today's date + "Return ONLY valid JSON. No markdown fences, no explanation."

**User** — contains:
- Source path
- Source content (truncated to 100,000 chars)
- Wiki context (retrieved pages)
- The exact JSON structure Claude must return, including the rules list

Method signatures:
```python
def plan_system(self, schema: str, today: str) -> list[dict]: ...
def plan_user(
    self,
    source_path: str,
    source_content: str,
    wiki_context: str,
) -> str: ...
```

### 2. Write page prompt

Currently in `call_llm_write_page()`.

**System** — two blocks, both cached:
- Block 1: `schema` string with `cache_control: ephemeral`
- Block 2: source path + source content (truncated to 100,000 chars)
  with `cache_control: ephemeral`

**User** — branches on action:
- `CREATE`: page path + description + format instructions
- `APPEND`: page path + description + existing page content +
  instruction to begin with `### From [[sources/{source_slug}]] ({today})`

Method signatures:
```python
def write_page_system(
    self,
    schema: str,
    source_path: str,
    source_content: str,
) -> list[dict]: ...

def write_page_user(
    self,
    action: str,           # "CREATE" or "APPEND"
    page_path: str,
    description: str,
    source_slug: str,
    today: str,
    existing_content: Optional[str] = None,
) -> str: ...
```

### 3. Update index prompt

Currently in `_update_index()`. No system method — the call site passes the schema
block directly and this prompt only needs a user method.

**User** — contains:
- Instruction to update the wiki index
- Current index content
- JSON list of pages created or updated this ingest
- Source title
- Instruction to return the complete updated index.md following existing format

Method signature:
```python
def update_index_user(
    self,
    current_index: str,
    pages: list[dict],
    source_title: str,
) -> str: ...
```

---

## How to update ingest.py

Replace each inline prompt construction with a call to `WikiPrompts`. Instantiate
once in `main()` and pass to wherever it is needed, or instantiate at module level
— either is fine for now.

### call_llm_plan

```python
# Before
system = [
    {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": f"Today's date: {date.today().isoformat()}\n..."},
]
user = f"""Plan the wiki updates..."""

# After
prompts = WikiPrompts()
system = prompts.plan_system(schema, date.today().isoformat())
user = prompts.plan_user(str(source_path), source_content, wiki_context)
```

### call_llm_write_page

```python
# Before
system = [
    {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": f"Source being ingested: ...", "cache_control": ...},
]
user = f"""Write the full markdown..."""  # or APPEND variant

# After
system = prompts.write_page_system(schema, source_path, source_content)
user = prompts.write_page_user(action, page_path, description,
                                source_slug, date.today().isoformat(), existing_content)
```

### _update_index

```python
# Before
user = f"""Update the wiki index..."""

# After
user = prompts.update_index_user(current_index, pages, source_title)
```

---

## What must NOT change

- The Anthropic API call structure — `client.messages.create(...)` calls are unchanged
- The `cache_control` behaviour — caching must be preserved exactly as-is
- The content of the prompts — extract faithfully, do not rewrite or improve
- All other logic in `ingest.py` — this is purely a structural refactor
- `wiki_retrieval.py` — not touched by this ticket

---

## Verification

After the refactor, a full ingest run must produce identical output to before.
The audit log (`.api_audit.log`) token counts should be the same.

If the prompt content has been accidentally changed, the LLM output will differ —
use the audit log and a single-file test run to verify:

```bash
python ingest.py raw/notes/Shadow\ Work.md
```

---

## File summary

Files created:
- `prompts.py` — new file, `WikiPrompts` class

Files modified:
- `ingest.py` — replace inline prompt strings with `WikiPrompts` calls

Files not touched:
- `wiki_retrieval.py`
- `CLAUDE.md`
- `wiki_schema.md` (does not exist yet — Ticket 1)
