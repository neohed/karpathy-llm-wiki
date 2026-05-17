from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

from config import WIKI_DIR, SCHEMA_FILE, LLM_MODEL, MAX_TOKENS_PAGE
from utils import _log
from prompts import WikiPrompts

if TYPE_CHECKING:
    from context import IngestContext

_prompts = WikiPrompts()


def load_wiki_context(max_chars: int = 60_000) -> str:
    """Brute-force fallback: load index + log + pages alphabetically."""
    index_path = WIKI_DIR / "index.md"
    log_path   = WIKI_DIR / "log.md"

    parts = []
    if index_path.exists():
        parts.append("=== INDEX.md ===\n" + index_path.read_text(errors="replace"))
    if log_path.exists():
        parts.append("=== LOG.md (recent) ===\n" + log_path.read_text(errors="replace")[-8000:])

    budget = max_chars - sum(len(p) for p in parts)
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if p.name in ("index.md", "log.md"):
            continue
        text = p.read_text(errors="replace")
        if budget - len(text) < 0:
            break
        parts.append(f"=== {p} ===\n{text}")
        budget -= len(text)

    return "\n\n".join(parts) or "(Wiki is empty — first ingest)"


def load_schema() -> str:
    if not SCHEMA_FILE.exists():
        print(f"Warning: {SCHEMA_FILE} not found")
        return ""
    return SCHEMA_FILE.read_text()


def _update_index(ctx: "IngestContext"):
    index_path = WIKI_DIR / "index.md"
    current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    schema  = load_schema()
    pages   = ctx.plan.get("pages", [])

    user = _prompts.update_index_user(
        current, pages, ctx.plan.get("source_title", ctx.path.name)
    )

    _log("anthropic_request", call="update_index", model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PAGE)

    response = ctx.client.messages.create(
        model=LLM_MODEL,
        max_tokens=MAX_TOKENS_PAGE,
        system=[{"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )

    _log("anthropic_response", call="update_index",
         input_tokens=response.usage.input_tokens,
         output_tokens=response.usage.output_tokens)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(response.content[0].text.strip(), encoding="utf-8")
    print(f"  [UPDATE] {index_path}")
    ctx.written_paths.append(str(index_path))


def _write_log(ctx: "IngestContext"):
    log_path = WIKI_DIR / "log.md"
    pages   = ctx.plan.get("pages", [])
    created = [p["path"] for p in pages if p["action"].upper() == "CREATE"]
    updated = [p["path"] for p in pages if p["action"].upper() == "APPEND"]

    lines = [f"## [{date.today().isoformat()}] ingest | {ctx.plan.get('source_title', ctx.path.name)}"]
    lines.append(f"- Summary: {ctx.plan.get('summary', '')}")
    if created:
        lines.append(f"- Pages created: {', '.join(created)}")
    if updated:
        lines.append(f"- Pages updated: {', '.join(updated)}")
    entry = "\n".join(lines)

    existing_size = log_path.stat().st_size if log_path.exists() else 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        if existing_size > 0:
            f.write("\n\n")
        f.write(entry)
    print(f"  [APPEND] {log_path}")
    ctx.written_paths.append(str(log_path))
