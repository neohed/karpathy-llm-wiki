"""
prompts.py — Prompt DAL for the LLM Wiki pipeline.
"""

from pathlib import Path
from typing import Optional


def _build_grounding_block(source_texts: list[tuple[str, str]]) -> str:
    """Concatenate source documents into a single grounding context block."""
    parts = [
        f"=== SOURCE: {source_path} ===\n{content[:40_000]}"
        for source_path, content in source_texts
    ]
    return "\n\n".join(parts)


class WikiPrompts:
    """
    Prompt DAL for the LLM Wiki pipeline.

    Each prompt is a pair of methods:
      <name>_system(...) -> list[dict]   — Anthropic system blocks with cache_control
      <name>_user(...)   -> str          — user message content

    _update_index has no system method — it reuses the schema block from the
    call site and only needs a user method.
    """

    def plan_system(self, schema: str, today: str) -> list[dict]:
        return [
            {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": (
                f"Today's date: {today}\n"
                "Return ONLY valid JSON. No markdown fences, no explanation."
            )},
        ]

    def plan_user(
        self,
        source_path: str,
        source_content: str,
        wiki_context: str,
    ) -> str:
        return f"""Plan the wiki updates needed to ingest this source.

## Source path
{source_path}

## Source content
{source_content[:100_000]}

## Current wiki (most relevant pages)
{wiki_context}

Return this exact JSON structure:
{{
  "source_title": "Human readable title",
  "summary": "One sentence describing what this source covers",
  "pages": [
    {{"action": "CREATE", "path": "wiki/sources/slug.md", "description": "what to write", "type": "source", "label": "Human Readable Title", "edge_type": "introduces"}},
    {{"action": "CREATE", "path": "wiki/concepts/name.md", "description": "what to write", "type": "concept", "label": "Concept Name", "edge_type": "introduces"}},
    {{"action": "UPDATE", "path": "wiki/entities/name.md", "description": "what new information to integrate", "type": "entity", "label": "Entity Name", "edge_type": "discusses"}}
  ]
}}

Rules:
- Always include wiki/sources/<slug>.md as the first CREATE
- CREATE for each concept or entity not yet in the wiki
- UPDATE for existing pages that have meaningful new information from this source.
  For UPDATE actions, the existing page content will be provided — produce a
  complete rewritten page that integrates the existing content with new information
  from this source. Do not reproduce existing content unchanged where the new
  source adds nothing — only integrate where there is genuine new information.
- Do NOT include wiki/index.md or wiki/log.md — those are handled separately
- Do NOT write page content — only paths and brief descriptions
- Slug: lowercase, hyphens only, no punctuation
- type: infer from path prefix — sources/→source, concepts/→concept, entities/→entity, analyses/→analysis
- label: human-readable name, not the slug (e.g. "Carl Jung" not "carl-jung")
- edge_type: "introduces" for CREATE, "discusses" or "refines" for UPDATE; use "contradicts" or "parallels" only when genuinely applicable
"""

    def write_page_system(
        self,
        schema: str,
        source_path: str,
        source_content: str,
        plan_pages: list[dict] = None,
    ) -> list[dict]:
        blocks = [
            {"type": "text", "text": schema,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text",
             "text": f"Source being ingested: {source_path}\n\n{source_content[:100_000]}",
             "cache_control": {"type": "ephemeral"}},
            {"type": "text",
             "text": (
                 "When creating [[WikiLinks]] to other wiki pages, use the exact path slugs "
                 "from the ingest plan. Do not invent slugs, do not use title-case variations, "
                 "do not use spaces. Examples: [[concepts/tragic-realism]] not [[Tragic Realism]], "
                 "[[entities/carl-jung]] not [[Carl Jung]] or [[carl jung]]."
             )},
        ]
        if plan_pages:
            skip = {"wiki/index.md", "wiki/log.md"}
            links = "\n".join(
                f"  [[{Path(p['path']).relative_to('wiki').with_suffix('')}]]"
                for p in plan_pages
                if p.get("path") not in skip
            )
            blocks.append({
                "type": "text",
                "text": (
                    "These are the exact WikiLinks for pages being created or updated "
                    "in this ingest run. Use these precisely when linking to any of "
                    "these pages — do not derive or guess:\n"
                    f"{links}"
                ),
            })
        return blocks

    def write_page_user(
        self,
        action: str,
        page_path: str,
        description: str,
        source_slug: str,
        today: str,
        existing_content: Optional[str] = None,
    ) -> str:
        if action == "CREATE":
            return f"""Write the full markdown content for this new wiki page.

Page: {page_path}
Description: {description}
Today's date (use for created/updated frontmatter): {today}

Follow CLAUDE.md conventions exactly (frontmatter, WikiLinks, etc.).
Return ONLY the markdown. No explanation, no JSON, no fences."""

        else:  # UPDATE
            return f"""Rewrite this wiki page, integrating new information from the source document.

Page: {page_path}
Description of new information to integrate: {description}

Existing page content:
{existing_content or "(page is empty)"}

Instructions:
- Produce a complete, clean, unified page — not a page with appended sections
- Integrate new information from the source document naturally into the existing
  structure — do not add dated section headers like "### From [[sources/...]]"
- Preserve all existing content that remains accurate and relevant
- Where the source adds new detail to an existing section, expand that section
- Where the source introduces a genuinely new aspect not covered, add a new section
- Where the source contradicts existing content, resolve using the source as authority
  and note the update inline if significant
- Update frontmatter: set updated={today}, ensure this source is in sources: field
- Return ONLY the complete rewritten markdown. No explanation, no JSON, no fences."""

    def rewrite_system(
        self,
        schema: str,
        source_texts: list[tuple[str, str]],  # [(source_path, content), ...]
    ) -> list[dict]:
        """
        System prompt for a page rewrite call.

        Block 1: wiki schema (cached)
        Block 2: grounding source documents (cached)
        """
        grounding = _build_grounding_block(source_texts)
        return [
            {
                "type": "text",
                "text": schema,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    "The following are the ORIGINAL SOURCE DOCUMENTS that contributed "
                    "to the wiki page you are about to rewrite. "
                    "These are primary sources — treat them as ground truth.\n"
                    "Where the current wiki page conflicts with these sources, "
                    "trust the sources.\n"
                    "Where the wiki page adds interpretation beyond what the sources "
                    "say, preserve it only if it is clearly reasonable inference, "
                    "not speculation.\n\n"
                    f"{grounding}"
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def synthesis_decide_system(
        self,
        schema: str,
        source_texts: list[tuple[str, str]],
    ) -> list[dict]:
        """
        System prompt for Phase 1 decision call.
        Same structure as rewrite_system — schema cached, sources cached.
        """
        return self.rewrite_system(schema, source_texts)

    def synthesis_decide_user(
        self,
        node_key_a: str,
        content_a: str,
        node_key_b: str,
        content_b: str,
    ) -> str:
        return f"""You are evaluating whether two wiki pages have a non-trivial,
worthwhile synthesis worth documenting.

## Page A: {node_key_a}
{content_a}

## Page B: {node_key_b}
{content_b}

A worthwhile synthesis:
- Reveals something genuinely surprising or non-obvious about the relationship
- Produces insight that neither page states on its own
- Would be practically or intellectually useful to a reader of both pages
- Is not merely "these two things are related" or "both discuss X"

A synthesis is NOT worthwhile if:
- The connection is trivially obvious (e.g. "both involve human behaviour")
- One page already fully explains the relationship to the other
- The relationship is purely definitional or taxonomic
- There is no meaningful intellectual tension, parallel, or emergent insight

Respond ONLY with valid JSON:
{{
  "worthwhile": true | false,
  "rationale": "one sentence explaining the decision",
  "proposed_title": "Title For The Synthesis Document"
}}

proposed_title only required when worthwhile is true.
No markdown fences, no explanation outside the JSON."""

    def synthesis_update_user(
        self,
        node_key_a: str,
        content_a: str,
        node_key_b: str,
        content_b: str,
        existing_synthesis: str,
    ) -> str:
        return f"""You are evaluating whether an existing synthesis document needs
updating given changes to the pages it synthesises.

## Page A: {node_key_a}
{content_a}

## Page B: {node_key_b}
{content_b}

## Existing synthesis document
{existing_synthesis}

The synthesis needs updating if:
- New information in either page materially changes the synthesis
- The existing synthesis contains claims now contradicted by updated pages
- Significant new connections have emerged that the synthesis misses

The synthesis does NOT need updating if:
- Changes to the pages are minor additions that don't affect the core insight
- The existing synthesis remains accurate and complete

Respond ONLY with valid JSON:
{{
  "needs_update": true | false,
  "rationale": "one sentence explaining the decision"
}}

No markdown fences, no explanation outside the JSON."""

    def synthesis_write_system(
        self,
        schema: str,
        source_texts: list[tuple[str, str]],
        content_a: str,
        content_b: str,
        node_key_a: str,
        node_key_b: str,
    ) -> list[dict]:
        """
        System prompt for Phase 2 write call.

        Block 1: schema (cached)
        Block 2: grounding source documents (cached)
        Block 3: current content of both nodes (cached)
        """
        grounding = _build_grounding_block(source_texts)
        nodes_block = (
            f"## {node_key_a}\n{content_a}\n\n"
            f"## {node_key_b}\n{content_b}"
        )
        return [
            {
                "type": "text",
                "text": schema,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    "The following are the ORIGINAL SOURCE DOCUMENTS that informed "
                    "the wiki pages you are synthesising. Treat them as ground truth. "
                    "Where wiki pages conflict with sources, trust the sources.\n\n"
                    f"{grounding}"
                ),
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    "The following are the current wiki pages being synthesised:\n\n"
                    f"{nodes_block}"
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def synthesis_write_user(
        self,
        node_key_a: str,
        node_key_b: str,
        proposed_title: str,
        today: str,
        existing_synthesis: Optional[str] = None,
    ) -> str:
        if existing_synthesis:
            instruction = (
                f"Rewrite this existing synthesis document incorporating the "
                f"updated information from both pages. Preserve the core insight "
                f"where it remains valid. Update or remove claims that are no longer "
                f"accurate. Add new connections that have emerged.\n\n"
                f"Existing synthesis:\n{existing_synthesis}"
            )
        else:
            instruction = (
                f"Write a new synthesis document exploring the non-trivial "
                f"connection between these two pages."
            )

        return f"""{instruction}

Title: {proposed_title}

The synthesis document should:
- Open with the core insight in one clear paragraph — what becomes visible
  when these two pages are read together that neither states alone
- Explore where the two concepts reinforce each other
- Explore where they create productive tension or apparent contradiction
- Note what questions remain open or unresolved
- Use [[WikiLinks]] to reference both pages and any related concepts
- Close with practical or intellectual implications for the reader

Frontmatter:
---
title: "{proposed_title}"
type: analysis
tags: []
sources: [{node_key_a}, {node_key_b}]
updated: {today}
---

Return ONLY the complete markdown document. No explanation, no fences."""

    def rewrite_user(
        self,
        page_path: str,
        current_content: str,
        today: str,
    ) -> str:
        """User prompt for a page rewrite call."""
        return f"""Rewrite this wiki page as a clean, unified document.

Page: {page_path}

Current content (may contain dated append sections to integrate):
{current_content}

Instructions:
- Integrate all information from the append sections into coherent prose
- Remove all "### From [[sources/...]] (YYYY-MM-DD)" section headers
- Preserve all factual content and cross-references ([[WikiLinks]])
- Update the frontmatter: set updated={today}, increment consolidation_version by 1,
  ensure all contributing sources are listed in the sources: field
- Write in a clear, skimmable style — bullet points over dense paragraphs
- Where sources agree, state the consensus clearly
- Where sources offer different perspectives, note both with attribution
- Where the append sections contradict the original page content,
  resolve the contradiction using the grounding source documents as authority
- Return ONLY the complete rewritten markdown. No explanation, no fences."""

