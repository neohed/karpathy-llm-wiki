"""
rewrite.py — Page rewrite utilities for the consolidation pipeline.

Shared by consolidate.py and future ingest middleware. No dependency on
middleware.py, context.py, or any ingest-specific module.
"""

from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic


def detect_append_sections(page_path: Path) -> list[str]:
    """
    Return list of append section headers in a wiki page.
    Empty list means the page is clean (no pending appends).
    """
    if not page_path.exists():
        return []
    content = page_path.read_text(encoding="utf-8", errors="ignore")
    pattern = r"^### From \[\[sources/[^\]]+\]\] \(\d{4}-\d{2}-\d{2}\)"
    return re.findall(pattern, content, re.MULTILINE)


def needs_rewrite(page_path: Path) -> bool:
    """Return True if page has any pending append sections."""
    return len(detect_append_sections(page_path)) > 0


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


def rewrite_page(
    page_path: Path,
    client: anthropic.Anthropic,
    graph,                           # WikiGraph — avoid circular import via late import
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

    # Warn if content is large — rewrite output may be truncated
    if len(current_content) > 8_000:
        print(f"  [WARN] {page_path.name} is {len(current_content):,} chars — "
              f"rewrite output may be truncated. Consider MAX_TOKENS_PAGE.")

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
