from __future__ import annotations
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from config import LLM_MODEL, MAX_TOKENS_PLAN, MAX_TOKENS_PAGE
from context import IngestContext, make_pipeline
from utils import _log, _path_to_slug
from wiki_io import load_wiki_context, load_schema, _update_index, _write_log
from prompts import WikiPrompts
from wiki_graph import WikiGraph

_prompts = WikiPrompts()


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

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
# Middleware
# ---------------------------------------------------------------------------

def mw_load_content(ctx: IngestContext, next):
    ctx.content = ctx.path.read_text(errors="replace")
    next()


def mw_plan(ctx: IngestContext, next):
    """Pass 1: retrieve relevant context, ask LLM what pages to create/update."""
    if ctx.retriever and ctx.retriever.is_ready():
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
        edges_before = graph.edge_count()

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
        edges_after = graph.edge_count()
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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

ingest_pipeline = make_pipeline(
    mw_load_content,
    mw_plan,
    mw_write_pages,
    mw_update_graph,
    mw_update_embeddings,
)
