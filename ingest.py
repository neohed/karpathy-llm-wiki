#!/usr/bin/env python3
"""
ingest.py — LLM Wiki ingest tool (v5)

Two-pass architecture:
  Pass 1 (plan)  : one LLM call returns list of pages to create/update — no content
  Pass 2 (write) : one focused LLM call per page — bounded output, never truncates
  Post-write     : index.md via LLM, log.md generated locally

Source content is placed in the system prompt for Pass 2, so it is cached by
Anthropic after the first page write — subsequent pages for the same source
pay ~10% of normal input token cost.

Large files (> SPLIT_THRESHOLD bytes) are auto-split at heading boundaries.
Semantic retrieval (Voyage AI) selects the most relevant wiki pages for context.

Usage:
  python ingest.py                    # batch: all new/changed files in raw/
  python ingest.py raw/notes/foo.md   # single file
  python ingest.py --force ...        # re-ingest everything

Requires:
  pip install anthropic python-dotenv voyageai numpy
  ANTHROPIC_API_KEY and VOYAGE_API_KEY in .env.local
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
from prompts import WikiPrompts

_prompts = WikiPrompts()

from wiki_graph import WikiGraph

try:
    from wiki_retrieval import WikiRetriever
    _RETRIEVAL_AVAILABLE = True
except ImportError:
    _RETRIEVAL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Audit logger — one JSON object per line in .api_audit.log
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
    entry = {"ts": datetime.now().isoformat(), "event": event, **fields}
    _audit.debug(json.dumps(entry, ensure_ascii=False))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LLM_MODEL       = "claude-sonnet-4-6"
MAX_TOKENS_PLAN = 2048    # plan is just paths + descriptions, always small
MAX_TOKENS_PAGE = 4096    # one wiki page at a time, always fits
SPLIT_THRESHOLD = 40_000  # bytes; files larger than this are auto-split

RAW_DIR    = Path("raw")
WIKI_DIR   = Path("wiki")
SCHEMA_FILE = Path("CLAUDE.md")
STATE_FILE  = Path(".ingest_state.json")

RAW_EXTENSIONS = {".md", ".txt", ".rst"}

# ---------------------------------------------------------------------------
# Pipeline context
# ---------------------------------------------------------------------------

@dataclass
class IngestContext:
    path: Path                           # file being ingested (may be a split part)
    client: anthropic.Anthropic
    retriever: Optional[object] = None   # WikiRetriever, if available
    original_path: Path = None           # unsplit source path when path is a part
    content: str = ""
    wiki_summary: str = ""
    plan: dict = field(default_factory=dict)
    written_paths: list = field(default_factory=list)

    def __post_init__(self):
        if self.original_path is None:
            self.original_path = self.path

# ---------------------------------------------------------------------------
# Middleware runner  (Connect / Express pattern)
# ---------------------------------------------------------------------------

def make_pipeline(*middlewares):
    """Each middleware: fn(ctx: IngestContext, next: Callable) -> None"""
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


def mw_plan(ctx: IngestContext, next):
    """Pass 1: retrieve relevant context, ask LLM what pages to create/update."""
    if ctx.retriever and ctx.retriever._index:
        ctx.wiki_summary = ctx.retriever.get_context_for_source(ctx.path)
    else:
        ctx.wiki_summary = load_wiki_context()

    print("  Planning...")
    ctx.plan = call_llm_plan(ctx.client, ctx.path, ctx.content, ctx.wiki_summary)

    pages = ctx.plan.get("pages", [])
    print(f"  Plan: {len(pages)} page(s)")
    for p in pages:
        print(f"    [{p['action']}] {p['path']}")
    next()


def mw_write_pages(ctx: IngestContext, next):
    """Pass 2: one focused LLM call per page, then update index and log."""
    schema = load_schema()
    source_slug = _path_to_slug(ctx.original_path)

    for item in ctx.plan.get("pages", []):
        action = item["action"].upper()
        path   = Path(item["path"])
        desc   = item.get("description", "")

        existing = None
        if action == "APPEND" and path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace")

        print(f"  Writing {path}...")
        try:
            content = call_llm_write_page(
                ctx.client, schema,
                str(ctx.path), ctx.content,
                action, str(path), desc, source_slug, existing,
            )
        except Exception as e:
            print(f"  [WARN] Failed to write {path}: {e}")
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        if action == "CREATE":
            path.write_text(content, encoding="utf-8")
            print(f"  [CREATE] {path}")
        elif action == "APPEND":
            existing_size = path.stat().st_size if path.exists() else 0
            with open(path, "a", encoding="utf-8") as f:
                if existing_size > 0:
                    f.write("\n\n")
                f.write(content)
            print(f"  [APPEND] {path}")

        ctx.written_paths.append(str(path))

    _update_index(ctx)
    _write_log(ctx)
    next()


def _should_skip_graph(path: str) -> bool:
    return Path(path).name in {"index.md", "log.md"}


def _graph_key(path: str) -> str:
    """Strip wiki/ prefix from plan path to get the graph node key."""
    return path[len("wiki/"):] if path.startswith("wiki/") else path


def _infer_node_type(path: str) -> Optional[str]:
    key = _graph_key(path)
    for prefix, node_type in [
        ("sources/", "source"),
        ("concepts/", "concept"),
        ("entities/", "entity"),
        ("analyses/", "analysis"),
    ]:
        if key.startswith(prefix):
            return node_type
    return None


def _infer_label(path: str) -> str:
    return Path(path).stem.replace("-", " ").title()


def mw_update_graph(ctx: IngestContext, next):
    """Update the knowledge graph from the completed ingest plan."""
    try:
        pages = ctx.plan.get("pages", [])
        if not pages:
            print("  [WARN] mw_update_graph: no pages in plan, skipping")
            next()
            return

        graph = WikiGraph.load()
        nodes_before = len(graph.all_nodes())
        edges_before = len(graph._edges)

        # Locate the source page item
        source_item = None
        for p in pages:
            if p.get("type") == "source" or _graph_key(p["path"]).startswith("sources/"):
                source_item = p
                break

        source_key = None
        if source_item:
            source_key = _graph_key(source_item["path"])
            source_label = source_item.get("label") or _infer_label(source_item["path"])
            graph.add_node(source_key, type="source", label=source_label)

        for item in pages:
            path = item["path"]
            if _should_skip_graph(path):
                continue
            key = _graph_key(path)
            if key == source_key:
                continue

            node_type = item.get("type") or _infer_node_type(path)
            if not node_type:
                print(f"  [WARN] mw_update_graph: cannot infer type for {path}, skipping")
                continue

            label = item.get("label") or _infer_label(path)
            action = item.get("action", "CREATE").upper()
            edge_type = item.get("edge_type") or ("introduces" if action == "CREATE" else "discusses")

            graph.add_node(key, type=node_type, label=label, source=source_key)
            if source_key:
                graph.add_edge(source_key, key, edge_type)

        graph.save()

        nodes_after = len(graph.all_nodes())
        edges_after = len(graph._edges)
        print(f"  [GRAPH] {nodes_after} nodes, {edges_after} edges "
              f"(+{nodes_after - nodes_before} nodes, +{edges_after - edges_before} edges this ingest)")

    except Exception as e:
        print(f"  [WARN] mw_update_graph failed: {e}")

    next()


def mw_update_embeddings(ctx: IngestContext, next):
    """Re-embed only the pages that changed."""
    if ctx.retriever and ctx.written_paths:
        ctx.retriever.update_index(ctx.written_paths)
    next()


ingest_pipeline = make_pipeline(
    mw_load_content,
    mw_plan,
    mw_write_pages,
    mw_update_graph,
    mw_update_embeddings,
)

# ---------------------------------------------------------------------------
# LLM — Pass 1: planning
# ---------------------------------------------------------------------------

def call_llm_plan(
    client: anthropic.Anthropic,
    source_path: Path,
    source_content: str,
    wiki_context: str,
) -> dict:
    schema = load_schema()
    system = _prompts.plan_system(schema, date.today().isoformat())
    user = _prompts.plan_user(str(source_path), source_content, wiki_context)

    _log("anthropic_request", call="plan", model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PLAN, source=str(source_path))

    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=MAX_TOKENS_PLAN,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    usage = response.usage
    _log("anthropic_response", call="plan",
         input_tokens=usage.input_tokens,
         output_tokens=usage.output_tokens,
         cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
         cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

    text = response.content[0].text.strip()
    # Strip markdown fences if the model wrapped the JSON anyway
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    print(f"  [WARN] Could not parse plan. Preview: {text[:300]}")
    return {"source_title": source_path.stem, "summary": "", "pages": []}


# ---------------------------------------------------------------------------
# LLM — Pass 2: write one page
# ---------------------------------------------------------------------------

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
) -> str:
    # Source content in the system prompt — cached across all page writes for this source
    system = _prompts.write_page_system(schema, source_path, source_content)
    user = _prompts.write_page_user(
        action, page_path, description, source_slug, date.today().isoformat(), existing_content
    )

    _log("anthropic_request", call="write_page", model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PAGE, action=action, path=page_path)

    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=MAX_TOKENS_PAGE,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    usage = response.usage
    _log("anthropic_response", call="write_page",
         action=action, path=page_path,
         input_tokens=usage.input_tokens,
         output_tokens=usage.output_tokens,
         cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
         cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Post-write: index and log
# ---------------------------------------------------------------------------

def _update_index(ctx: IngestContext):
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


def _write_log(ctx: IngestContext):
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path_to_slug(path: Path) -> str:
    name = path.stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


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
            rel_parts = f.relative_to(RAW_DIR).parts
            if not any(part.startswith(".") for part in rel_parts[:-1]):
                files.append(f)
    return sorted(files)


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

# ---------------------------------------------------------------------------
# File splitting
# ---------------------------------------------------------------------------

def split_dir_for(path: Path) -> Path:
    return path.parent / f".{path.stem}"


def create_splits(source: Path) -> list[Path]:
    sdir = split_dir_for(source)
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir()

    content = source.read_text(errors="replace")
    chunks  = re.split(r"(?=^#{1,2} )", content, flags=re.MULTILINE)
    chunks  = [c.strip() for c in chunks if c.strip()]

    if len(chunks) <= 1:
        mid      = len(content) // 2
        break_at = content.rfind("\n\n", 0, mid + 2000)
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
    args  = sys.argv[1:]
    force = "--force" in args
    args  = [a for a in args if a != "--force"]

    client = anthropic.Anthropic()
    print(f"Model: {LLM_MODEL}")

    retriever = None
    if _RETRIEVAL_AVAILABLE and os.environ.get("VOYAGE_API_KEY"):
        retriever = WikiRetriever(wiki_dir=str(WIKI_DIR), cache_path=".wiki_embeddings.json")
        retriever.build_index()
    else:
        reason = "pip install voyageai numpy" if not _RETRIEVAL_AVAILABLE else "VOYAGE_API_KEY not set"
        print(f"Semantic retrieval unavailable ({reason}) — falling back to brute-force context.")

    state = load_state()

    if args:
        target = Path(" ".join(args))
        if not target.exists():
            print(f"Error: {target} not found")
            sys.exit(1)
        ingest_file(client, target, retriever)
        state[str(target)] = file_hash(target)
        save_state(state)

    else:
        files = find_raw_files()
        if not files:
            print("No files found in raw/")
            return

        pending = files if force else [
            f for f in files if state.get(str(f)) != file_hash(f)
        ]
        print(f"Found {len(files)} files, {len(pending)} pending.")

        for f in pending:
            ingest_file(client, f, retriever)
            state[str(f)] = file_hash(f)
            save_state(state)

        print(f"\nFinished ingesting {len(pending)} file(s).")


if __name__ == "__main__":
    main()
