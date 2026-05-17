"""
synthesis.py — Two-phase synthesis between concept/entity node pairs.

Phase 1: Decision call — is synthesis worthwhile?
Phase 2: Write call — produce the synthesis document (only if Phase 1 says yes)

For existing synthesis documents, a lightweight update check replaces
Phase 1 to determine if the document needs rewriting.
"""

from __future__ import annotations
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from wiki_graph import WikiGraph, GraphEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _title_to_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _load_node_content(node_key: str, wiki_dir: Path) -> str:
    """Read current content of a wiki page. Returns empty string if not found."""
    page_path = wiki_dir / node_key
    if not page_path.exists():
        return ""
    return page_path.read_text(encoding="utf-8", errors="ignore")


def _load_grounding_sources(
    node_key_a: str,
    node_key_b: str,
    graph: WikiGraph,
    wiki_dir: Path,
    raw_dir: Path,
) -> list[tuple[str, str]]:
    """
    Load original source documents for both nodes combined.
    Deduplicates — if both nodes share a source, include it once.
    Returns list of (source_path, content) tuples.
    """
    from rewrite import _resolve_raw_path

    node_a = graph.get_node(node_key_a)
    node_b = graph.get_node(node_key_b)
    all_sources = list(set(
        (node_a.sources if node_a else []) +
        (node_b.sources if node_b else [])
    ))

    seen: set[str] = set()
    source_texts = []
    for source_key in all_sources:
        if source_key in seen:
            continue
        seen.add(source_key)
        wiki_source_page = wiki_dir / source_key
        raw_path = _resolve_raw_path(wiki_source_page, raw_dir)
        if raw_path and raw_path.exists():
            content = raw_path.read_text(encoding="utf-8", errors="ignore")
            source_texts.append((str(raw_path), content))

    return source_texts


def _extract_title(synth_path: Path) -> Optional[str]:
    """Extract title from synthesis document frontmatter."""
    if not synth_path.exists():
        return None
    try:
        content = synth_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Phase 1 — decision
# ---------------------------------------------------------------------------

def _phase1_decide(
    node_key_a: str,
    node_key_b: str,
    content_a: str,
    content_b: str,
    source_texts: list[tuple[str, str]],
    existing_synthesis: Optional[str],
    client: anthropic.Anthropic,
    prompts,
) -> dict:
    """
    Phase 1 LLM call — decide if synthesis is worthwhile.

    If existing_synthesis is None: ask if synthesis should be created.
    If existing_synthesis is provided: ask if existing synthesis needs updating.

    Returns parsed JSON dict with keys:
        worthwhile: bool  (or needs_update: bool for existing syntheses)
        rationale: str
        proposed_title: str  (only when worthwhile=True and no existing synthesis)
    """
    from config import LLM_MODEL, MAX_TOKENS_PLAN
    from utils import _log
    from wiki_io import load_schema

    schema = load_schema()
    system = prompts.synthesis_decide_system(schema, source_texts)

    if existing_synthesis is None:
        user = prompts.synthesis_decide_user(node_key_a, content_a, node_key_b, content_b)
        call_type = "synthesis_decide"
    else:
        user = prompts.synthesis_update_user(
            node_key_a, content_a, node_key_b, content_b, existing_synthesis
        )
        call_type = "synthesis_update_check"

    _log("anthropic_request", call=call_type, model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PLAN,
         edge=f"{node_key_a}->{node_key_b}")

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS_PLAN,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        usage = response.usage
        _log("anthropic_response", call=call_type,
             edge=f"{node_key_a}->{node_key_b}",
             input_tokens=usage.input_tokens,
             output_tokens=usage.output_tokens,
             cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
             cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

        text = response.content[0].text.strip()
        # Strip markdown fences if present
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
        print(f"  [WARN] Could not parse Phase 1 decision: {text[:200]}")
        return {"worthwhile": False, "rationale": "parse error", "needs_update": False}

    except Exception as e:
        _log("anthropic_error", call=call_type,
             edge=f"{node_key_a}->{node_key_b}", error=str(e))
        print(f"  [WARN] Phase 1 failed: {e}")
        return {"worthwhile": False, "rationale": str(e), "needs_update": False}


# ---------------------------------------------------------------------------
# Phase 2 — write
# ---------------------------------------------------------------------------

def _phase2_write(
    node_key_a: str,
    node_key_b: str,
    content_a: str,
    content_b: str,
    source_texts: list[tuple[str, str]],
    proposed_title: str,
    existing_path: Optional[str],
    wiki_dir: Path,
    client: anthropic.Anthropic,
    prompts,
) -> Optional[str]:
    """
    Phase 2 LLM call — write or rewrite the synthesis document.

    Returns the filepath relative to wiki_dir, or None on failure.
    Filename slug derived from proposed_title if creating new,
    or existing_path if updating.
    """
    from config import LLM_MODEL, MAX_TOKENS_PAGE
    from utils import _log
    from wiki_io import load_schema

    # Determine output path
    if existing_path:
        synthesis_path = wiki_dir / existing_path
        rel_path = existing_path
    else:
        slug = _title_to_slug(proposed_title)
        rel_path = f"analyses/{slug}.md"
        synthesis_path = wiki_dir / rel_path

    # Warn on path collision with a different edge (new synthesis only)
    if not existing_path and synthesis_path.exists():
        print(f"  [WARN] synthesis path {rel_path} already exists — may belong to another edge")

    # Load existing content for rewrite prompt
    existing_content: Optional[str] = None
    if existing_path and synthesis_path.exists():
        existing_content = synthesis_path.read_text(encoding="utf-8", errors="ignore")

    schema = load_schema()
    system = prompts.synthesis_write_system(
        schema, source_texts, content_a, content_b, node_key_a, node_key_b
    )
    user = prompts.synthesis_write_user(
        node_key_a, node_key_b, proposed_title, date.today().isoformat(), existing_content
    )

    _log("anthropic_request", call="synthesis_write", model=LLM_MODEL,
         max_tokens=MAX_TOKENS_PAGE,
         edge=f"{node_key_a}->{node_key_b}",
         path=rel_path)

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS_PAGE,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        usage = response.usage
        _log("synthesis_write",
             edge=f"{node_key_a}->{node_key_b}",
             path=rel_path,
             input_tokens=usage.input_tokens,
             output_tokens=usage.output_tokens,
             cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
             cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0))

        content = response.content[0].text.strip()

        synthesis_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = synthesis_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(synthesis_path)

        action = "[REWRITE]" if existing_path else "[SYNTHESIS]"
        print(f"  {action} {synthesis_path}")
        return rel_path

    except Exception as e:
        print(f"  [WARN] Phase 2 write failed: {e}")
        _log("anthropic_error", call="synthesis_write",
             edge=f"{node_key_a}->{node_key_b}", path=rel_path, error=str(e))
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def synthesise_edge(
    edge: GraphEdge,
    graph: WikiGraph,
    client: anthropic.Anthropic,
    wiki_dir: Path = None,
    raw_dir: Path = None,
) -> bool:
    """
    Run synthesis for a single graph edge.

    If the edge has no existing synthesis document:
        Phase 1 — decide if worthwhile
        Phase 2 — write document (if worthwhile)

    If the edge has an existing synthesis document:
        Phase 1 — decide if update is needed
        Phase 2 — rewrite document (if update needed)

    Updates the edge synthesis filepath in the graph on success.
    Returns True if the edge was processed successfully (even if synthesis
    was judged not worthwhile — that is a valid outcome, not a failure).
    """
    from config import WIKI_DIR, RAW_DIR
    from utils import _log
    from prompts import WikiPrompts

    if wiki_dir is None:
        wiki_dir = WIKI_DIR
    if raw_dir is None:
        raw_dir = RAW_DIR

    node_key_a = edge.from_node
    node_key_b = edge.to_node

    # Hard constraint — only concept/entity endpoints
    node_a = graph.get_node(node_key_a)
    node_b = graph.get_node(node_key_b)
    if not node_a or not node_b:
        print(f"  [WARN] synthesise_edge: missing node for {node_key_a}->{node_key_b}")
        return False
    if node_a.type not in ("concept", "entity") or node_b.type not in ("concept", "entity"):
        print(f"  [SKIP] synthesise_edge: non-concept/entity endpoints "
              f"{node_key_a}({node_a.type})->{node_key_b}({node_b.type})")
        return True

    content_a = _load_node_content(node_key_a, wiki_dir)
    content_b = _load_node_content(node_key_b, wiki_dir)
    source_texts = _load_grounding_sources(node_key_a, node_key_b, graph, wiki_dir, raw_dir)

    # Check for existing synthesis document on this edge
    existing_synth_path = graph.get_edge_synthesis(edge.from_node, edge.to_node, edge.type)
    existing_synth_content: Optional[str] = None
    if existing_synth_path:
        synth_file = wiki_dir / existing_synth_path
        if synth_file.exists():
            existing_synth_content = synth_file.read_text(encoding="utf-8", errors="ignore")
        else:
            print(f"  [WARN] synthesis file missing: {synth_file} — treating as new")
            existing_synth_path = None

    prompts = WikiPrompts()

    # Phase 1: decide
    print(f"  Phase 1: evaluating {node_key_a} ↔ {node_key_b}...")
    decision = _phase1_decide(
        node_key_a, node_key_b,
        content_a, content_b,
        source_texts,
        existing_synth_content,
        client,
        prompts,
    )

    # Determine which key signals a positive outcome
    if existing_synth_content is not None:
        proceed = decision.get("needs_update", False)
        verdict_key = "needs_update"
    else:
        proceed = decision.get("worthwhile", False)
        verdict_key = "worthwhile"

    _log("synthesis_decision",
         edge=f"{node_key_a}->{node_key_b}",
         **{verdict_key: proceed},
         rationale=decision.get("rationale", ""))

    print(f"  Phase 1 result: {verdict_key}={proceed} — {decision.get('rationale', '')}")

    if not proceed:
        return True  # Not worthwhile or no update needed — valid outcome

    # Phase 2: write
    if existing_synth_path:
        synth_file = wiki_dir / existing_synth_path
        proposed_title = (
            _extract_title(synth_file)
            or Path(existing_synth_path).stem.replace("-", " ").title()
        )
    else:
        proposed_title = decision.get(
            "proposed_title",
            f"{Path(node_key_a).stem.title()} and {Path(node_key_b).stem.title()}",
        )

    print(f"  Phase 2: writing synthesis '{proposed_title}'...")
    written_path = _phase2_write(
        node_key_a, node_key_b,
        content_a, content_b,
        source_texts,
        proposed_title,
        existing_synth_path,
        wiki_dir,
        client,
        prompts,
    )

    if written_path:
        if existing_synth_path and existing_synth_path != written_path:
            print(f"  [WARN] synthesis path changed: {existing_synth_path} → {written_path}")
        graph.set_edge_synthesis(edge.from_node, edge.to_node, edge.type, written_path)
        graph.save()
        return True

    return False
