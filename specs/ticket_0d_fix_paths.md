**Fix: Pass plan page list into write call to eliminate slug mismatches**

This is a two-part change — `prompts.py` and `middleware.py`. No other files touched.

**Part 1 — `prompts.py`**

Add `plan_pages` parameter to `write_page_system`:

```python
def write_page_system(
    self,
    schema: str,
    source_path: str,
    source_content: str,
    plan_pages: list[dict] = None,
) -> list[dict]:
```

Build the blocks list explicitly so the optional fourth block can be appended cleanly:

```python
blocks = [
    {
        "type": "text",
        "text": schema,
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": f"Source being ingested: {source_path}\n\n{source_content[:100_000]}",
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": (
            "When creating [[WikiLinks]] to other wiki pages, use the exact path slugs "
            "from the ingest plan. Do not invent slugs, do not use title-case variations, "
            "do not use spaces. Examples: [[concepts/tragic-realism]] not [[Tragic Realism]], "
            "[[entities/carl-jung]] not [[Carl Jung]] or [[carl jung]]."
        ),
    },
]

if plan_pages:
    skip = {"wiki/index.md", "wiki/log.md", "index.md", "log.md"}
    slugs = "\n".join(
        f"  {p['path']}"
        for p in plan_pages
        if p.get("path") not in skip
    )
    blocks.append({
        "type": "text",
        "text": (
            "These are the exact page paths being created or updated in this "
            "ingest run. Use these slugs precisely when linking to any of "
            "these pages — do not derive or guess:\n"
            f"{slugs}"
        ),
    })

return blocks
```

**Part 2 — `middleware.py`**

Update `call_llm_write_page` signature to accept `plan_pages`:

```python
def call_llm_write_page(
    client: anthropic.Anthropic,
    schema: str,
    source_path: str,
    source_content: str,
    action: str,
    page_path: str,
    description: str,
    source_slug: str,
    existing_content: Optional[str] = None,
    plan_pages: list[dict] = None,   # ← new
) -> str:
```

Pass it through to `write_page_system`:

```python
system = _prompts.write_page_system(
    schema, source_path, source_content, plan_pages
)
```

In `mw_write_pages`, pass the plan pages at the call site:

```python
content = call_llm_write_page(
    ctx.client, schema,
    str(ctx.path), ctx.content,
    action, str(path), desc, source_slug, existing,
    plan_pages=ctx.plan.get("pages", []),   # ← new
)
```

**Verification:**

```bash
python -c "
from prompts import WikiPrompts
p = WikiPrompts()
pages = [
    {'path': 'wiki/sources/test.md'},
    {'path': 'wiki/concepts/shadow.md'},
    {'path': 'wiki/index.md'},  # should be excluded
]
blocks = p.write_page_system('schema', 'path', 'content', plan_pages=pages)
print(len(blocks), 'blocks')  # should print 4
print(blocks[-1]['text'])     # should show test.md and shadow.md but not index.md
"
```

Then re-ingest Tragic Realism with `--force` and check that WikiLinks in the generated pages use exact slugs from the plan.
