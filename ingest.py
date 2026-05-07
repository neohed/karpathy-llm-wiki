#!/usr/bin/env python3
"""
ingest.py — LLM Wiki ingest tool (v4)

Usage:
  python ingest.py                    # batch: all new/changed files in raw/
  python ingest.py raw/papers/foo.md  # single file
  python ingest.py --force ...        # re-ingest everything

Requires:
  pip install anthropic python-dotenv
  ANTHROPIC_API_KEY in .env.local

Large files (> SPLIT_THRESHOLD bytes) are automatically split at heading
boundaries into a hidden .stem/ directory alongside the original file.
On subsequent runs the split parts are used instead of the original.
Convention: raw/notes/big-file.md → raw/notes/.big-file/part-01.md ...
"""

import sys
import json
import os
import re
import shutil
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv(".env.local")

import anthropic

try:
    from wiki_retrieval import WikiRetriever
    _RETRIEVAL_AVAILABLE = True
except ImportError:
    _RETRIEVAL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Audit logger  — one JSON object per line in .api_audit.log
# ---------------------------------------------------------------------------

def _setup_audit_logger(log_path: str = ".api_audit.log") -> logging.Logger:
    logger = logging.getLogger("api_audit")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger

_audit = _setup_audit_logger()


def _log(event: str, **fields):
    """Write one JSON audit entry to .api_audit.log."""
    entry = {"ts": datetime.now().isoformat(), "event": event, **fields}
    _audit.debug(json.dumps(entry, ensure_ascii=False))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LLM_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 32768
SPLIT_THRESHOLD = 40_000       # bytes; files larger than this are auto-split

RAW_DIR = Path("raw")
WIKI_DIR = Path("wiki")
SCHEMA_FILE = Path("CLAUDE.md")
STATE_FILE = Path(".ingest_state.json")

RAW_EXTENSIONS = {".md", ".txt", ".rst"}

# ---------------------------------------------------------------------------
# Pipeline context
# ---------------------------------------------------------------------------

@dataclass
class IngestContext:
    path: Path                          # file being ingested (may be a split part)
    client: anthropic.Anthropic
    retriever: Optional[object] = None  # WikiRetriever, if available
    original_path: Path = None          # the unsplit source file, when path is a part
    content: str = ""
    wiki_summary: str = ""
    llm_response: str = ""
    actions: list = field(default_factory=list)
    written_paths: list = field(default_factory=list)  # populated by mw_apply

    def __post_init__(self):
        if self.original_path is None:
            self.original_path = self.path

# ---------------------------------------------------------------------------
# Middleware runner  (Connect / Express pattern)
# ---------------------------------------------------------------------------

def make_pipeline(*middlewares):
    """Return a runner that executes middlewares in order.

    Each middleware: fn(ctx: IngestContext, next: Callable) -> None
    Call next() to pass control forward; omit it to stop the chain.
    """
    def run(ctx: IngestContext):
        def dispatch(i):
            if i < len(middlewares):
                middlewares[i](ctx, lambda: dispatch(i + 1))
        dispatch(0)
    return run

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def mw_load_content(ctx: IngestContext, next):
    ctx.content = ctx.path.read_text(errors="replace")
    next()


def mw_load_wiki(ctx: IngestContext, next):
    if ctx.retriever and ctx.retriever._index:
        ctx.wiki_summary = ctx.retriever.get_context_for_source(ctx.path)
    else:
        ctx.wiki_summary = load_wiki_context()
    next()


def mw_call_llm(ctx: IngestContext, next):
    schema = load_schema()
    user = build_user_prompt(
        source_path=str(ctx.path),
        source_content=ctx.content,
        wiki_summary=ctx.wiki_summary,
        original_path=str(ctx.original_path) if ctx.path != ctx.original_path else None,
    )
    print("  Calling LLM...")
    ctx.llm_response = call_llm(ctx.client, schema, user)
    next()


def mw_parse(ctx: IngestContext, next):
    print("  Parsing actions...")
    ctx.actions = parse_actions(ctx.llm_response)
    next()


def mw_apply(ctx: IngestContext, next):
    print(f"  Applying {len(ctx.actions)} action(s)...")
    apply_actions(ctx.actions)
    ctx.written_paths = [
        a["path"] for a in ctx.actions
        if isinstance(a, dict) and a.get("action", "").upper() in ("CREATE", "UPDATE", "APPEND")
        and "path" in a
    ]
    next()


def mw_update_index(ctx: IngestContext, next):
    if ctx.retriever and ctx.written_paths:
        ctx.retriever.update_index(ctx.written_paths)
    next()


# Default pipeline — add middleware here to extend the chain
ingest_pipeline = make_pipeline(
    mw_load_content,
    mw_load_wiki,
    mw_call_llm,
    mw_parse,
    mw_apply,
    mw_update_index,
)

# ---------------------------------------------------------------------------
# File splitting
# ---------------------------------------------------------------------------

def split_dir_for(path: Path) -> Path:
    """Return the hidden split directory for a raw source file.

    raw/notes/big-file.md  →  raw/notes/.big-file/
    """
    return path.parent / f".{path.stem}"


def create_splits(source: Path) -> list[Path]:
    """Split a large file at H1/H2 heading boundaries.

    Writes parts to .stem/ next to the original. Wipes any existing splits
    first so a re-ingest of a changed file starts clean.
    Returns the ordered list of part paths.
    """
    sdir = split_dir_for(source)
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir()

    content = source.read_text(errors="replace")

    # Split at every H1/H2 heading, keeping the heading with its section
    chunks = re.split(r'(?=^#{1,2} )', content, flags=re.MULTILINE)
    chunks = [c.strip() for c in chunks if c.strip()]

    # Fallback when there are no headings: split roughly in half at a paragraph break
    if len(chunks) <= 1:
        mid = len(content) // 2
        break_at = content.rfind('\n\n', 0, mid + 2000)
        if break_at == -1:
            break_at = mid
        chunks = [content[:break_at].strip(), content[break_at:].strip()]

    parts = []
    for i, chunk in enumerate(chunks, 1):
        p = sdir / f"part-{i:02d}.md"
        p.write_text(chunk, encoding="utf-8")
        parts.append(p)

    print(f"  [SPLIT] {source.name} → {len(parts)} parts in {sdir.name}/")
    return parts


def resolve_ingest_paths(source: Path) -> list[Path]:
    """Return the file(s) to actually ingest for a given raw source path.

    - If a .stem/ split directory already exists, return its parts.
    - If the file exceeds SPLIT_THRESHOLD, create splits and return them.
    - Otherwise return [source] unchanged.
    """
    sdir = split_dir_for(source)
    if sdir.exists():
        parts = sorted(sdir.glob("*.md"))
        if parts:
            print(f"  [SPLITS] {source.name} → using {len(parts)} pre-split parts")
            return parts
    if source.stat().st_size > SPLIT_THRESHOLD:
        return create_splits(source)
    return [source]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def find_raw_files() -> list[Path]:
    """Find all raw source files, excluding hidden split directories."""
    if not RAW_DIR.exists():
        return []
    files = []
    for ext in RAW_EXTENSIONS:
        for f in RAW_DIR.rglob(f"*{ext}"):
            # Exclude files whose parent directories include a hidden (.stem) dir
            rel_parts = f.relative_to(RAW_DIR).parts
            if not any(part.startswith('.') for part in rel_parts[:-1]):
                files.append(f)
    return sorted(files)


def load_wiki_context(max_chars: int = 60_000) -> str:
    """Load index + log + as many full wiki pages as fit within max_chars."""
    index_path = WIKI_DIR / "index.md"
    log_path = WIKI_DIR / "log.md"

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
            parts.append(f"=== {p} (truncated) ===\n{text[:budget]}")
            break
        parts.append(f"=== {p} ===\n{text}")
        budget -= len(text)

    return "\n\n".join(parts) or "(Wiki is empty — first ingest)"


def load_schema() -> str:
    if not SCHEMA_FILE.exists():
        print(f"Warning: {SCHEMA_FILE} not found — LLM will have no schema")
        return ""
    return SCHEMA_FILE.read_text()

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_user_prompt(
    source_path: str,
    source_content: str,
    wiki_summary: str,
    original_path: str = None,
) -> str:
    if len(source_content) > 200_000:
        source_content = source_content[:200_000] + "\n\n... [content truncated due to length]"

    source_header = source_path
    if original_path:
        source_header += f"\n(Part of: {original_path})"

    return f"""Ingest this source:

## Source
{source_header}

## Content
{source_content}

---

## Current Wiki State
{wiki_summary}

Produce the JSON actions now.
"""

# ---------------------------------------------------------------------------
# LLM call + parsing
# ---------------------------------------------------------------------------

def call_llm(client: anthropic.Anthropic, schema: str, user: str) -> str:
    system = [
        {
            "type": "text",
            "text": schema,
            "cache_control": {"type": "ephemeral"},  # CLAUDE.md is stable across ingests
        },
        {
            "type": "text",
            "text": (
                f"Today's date: {date.today().isoformat()}\n\n"
                "Respond ONLY with a ```json code block containing the array of actions.\n"
                "No explanations, no extra text."
            ),
        },
    ]

    request_body = {
        "model": LLM_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    _log("anthropic_request", **request_body)

    with client.messages.stream(**request_body) as stream:
        message = stream.get_final_message()

    usage = message.usage
    _log("anthropic_response",
         model=message.model,
         stop_reason=message.stop_reason,
         input_tokens=usage.input_tokens,
         output_tokens=usage.output_tokens,
         cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
         cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

    return message.content[0].text


def parse_actions(text: str) -> list[dict]:
    patterns = [
        r"```json\s*(.*?)\s*```",
        r"```(?:json)?\s*(.*?)\s*```",
        r"(\[\s*\{.*\}\s*\])",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                continue
    raise ValueError(f"Could not parse JSON from response. Preview:\n{text[:800]}")


def apply_actions(actions: list[dict]):
    for action in actions:
        if not isinstance(action, dict):
            print(f"  [WARN] Skipping unexpected item in actions: {str(action)[:80]}")
            continue
        if "path" not in action or "action" not in action:
            print(f"  [WARN] Skipping malformed action (missing keys): {str(action)[:80]}")
            continue
        act = action.get("action", "").upper()
        path = Path(action["path"])
        content = action.get("content", "")

        path.parent.mkdir(parents=True, exist_ok=True)

        if act in ("CREATE", "UPDATE"):
            path.write_text(content, encoding="utf-8")
            print(f"  [{act}] {path}")
        elif act == "APPEND":
            with open(path, "a", encoding="utf-8") as f:
                if path.stat().st_size > 0:
                    f.write("\n\n")
                f.write(content)
            print(f"  [APPEND] {path}")
        else:
            print(f"  [WARN] Unknown action '{act}' for {path}")

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_file(client: anthropic.Anthropic, source_path: Path, retriever=None):
    print(f"\n→ Ingesting: {source_path}")
    parts = resolve_ingest_paths(source_path)
    for part in parts:
        if len(parts) > 1:
            print(f"  → Part: {part.name}")
        ctx = IngestContext(
            path=part,
            client=client,
            retriever=retriever,
            original_path=source_path if part != source_path else None,
        )
        ingest_pipeline(ctx)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    client = anthropic.Anthropic()
    print(f"Model: {LLM_MODEL}")

    # Set up semantic retriever if voyageai is installed and key is present
    retriever = None
    if _RETRIEVAL_AVAILABLE and os.environ.get("VOYAGE_API_KEY"):
        retriever = WikiRetriever(wiki_dir=str(WIKI_DIR), cache_path=".wiki_embeddings.json")
        retriever.build_index()
    else:
        if not _RETRIEVAL_AVAILABLE:
            print("Semantic retrieval: unavailable (pip install voyageai numpy)")
        else:
            print("Semantic retrieval: unavailable (VOYAGE_API_KEY not set)")
        print("Falling back to brute-force wiki context loading.")

    state = load_state()

    if args:                                  # Single file mode
        target = Path(" ".join(args))         # join handles unquoted paths with spaces
        if not target.exists():
            print(f"Error: {target} not found")
            sys.exit(1)
        ingest_file(client, target, retriever)
        state[str(target)] = file_hash(target)
        save_state(state)

    else:                                     # Batch mode
        files = find_raw_files()
        if not files:
            print("No files found in raw/")
            return

        pending = files if force else [f for f in files if state.get(str(f)) != file_hash(f)]

        print(f"Found {len(files)} files, {len(pending)} pending.")

        for f in pending:
            ingest_file(client, f, retriever)
            state[str(f)] = file_hash(f)
            save_state(state)

        print(f"\nFinished ingesting {len(pending)} file(s).")


if __name__ == "__main__":
    main()
