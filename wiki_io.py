from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from config import WIKI_DIR, SCHEMA_FILE

if TYPE_CHECKING:
    from context import IngestContext


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
    """
    Update wiki/index.md from ctx.plan using pure Python.
    No LLM call — the index is structured catalog data, not prose.
    """
    index_path = WIKI_DIR / "index.md"
    today = date.today().isoformat()

    # Section name → dict of {path_key: entry_line}
    # Dict keying on path gives upsert behaviour — one entry per page, latest date wins
    sections: dict[str, dict[str, str]] = {
        "Sources":  {},
        "Concepts": {},
        "Entities": {},
        "Analyses": {},
    }

    # Parse existing entries to preserve pages not touched in this ingest run
    if index_path.exists():
        current = index_path.read_text(encoding="utf-8", errors="replace")
        current_section = None
        for line in current.splitlines():
            stripped = line.strip()
            found_section = False
            for section in sections:
                if stripped == f"## {section}":
                    current_section = section
                    found_section = True
                    break
            if not found_section and current_section and stripped.startswith("- [["):
                m = re.match(r"- \[\[([^\]]+)\]\]", stripped)
                if m:
                    key = m.group(1)
                    sections[current_section][key] = stripped

    type_to_section = {
        "source":   "Sources",
        "concept":  "Concepts",
        "entity":   "Entities",
        "analysis": "Analyses",
    }

    for item in ctx.plan.get("pages", []):
        section = type_to_section.get(item.get("type"))
        if not section:
            print(f"  [WARN] _update_index: no type for {item.get('path', '?')}, skipping")
            continue

        raw_path = item.get("path", "")
        key = raw_path
        if key.startswith("wiki/"):
            key = key[len("wiki/"):]
        if key.endswith(".md"):
            key = key[:-3]

        label = item.get("label") or Path(raw_path).stem.replace("-", " ").title()
        desc  = item.get("description", "").split(".")[0]
        sections[section][key] = f"- [[{key}]] — {label}: {desc} ({today})"

    total = sum(len(v) for v in sections.values())

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


def _write_log(ctx: "IngestContext"):
    log_path = WIKI_DIR / "log.md"
    pages   = ctx.plan.get("pages", [])
    created = [p["path"] for p in pages if p["action"].upper() == "CREATE"]
    updated = [p["path"] for p in pages if p["action"].upper() == "UPDATE"]

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
