"""
prompts.py — Prompt DAL for the LLM Wiki pipeline.
"""

import json
from typing import Optional


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
    {{"action": "APPEND", "path": "wiki/entities/name.md", "description": "what new information to add", "type": "entity", "label": "Entity Name", "edge_type": "discusses"}}
  ]
}}

Rules:
- Always include wiki/sources/<slug>.md as the first CREATE
- CREATE for each concept or entity not yet in the wiki
- APPEND for existing pages that have meaningful new information from this source
- Do NOT include wiki/index.md or wiki/log.md — those are handled separately
- Do NOT write page content — only paths and brief descriptions
- Slug: lowercase, hyphens only, no punctuation
- type: infer from path prefix — sources/→source, concepts/→concept, entities/→entity, analyses/→analysis
- label: human-readable name, not the slug (e.g. "Carl Jung" not "carl-jung")
- edge_type: "introduces" for CREATE, "discusses" or "refines" for APPEND; use "contradicts" or "parallels" only when genuinely applicable
"""

    def write_page_system(
        self,
        schema: str,
        source_path: str,
        source_content: str,
    ) -> list[dict]:
        return [
            {"type": "text", "text": schema,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text",
             "text": f"Source being ingested: {source_path}\n\n{source_content[:100_000]}",
             "cache_control": {"type": "ephemeral"}},
        ]

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

Follow CLAUDE.md conventions exactly (frontmatter, WikiLinks, etc.).
Return ONLY the markdown. No explanation, no JSON, no fences."""

        else:  # APPEND
            return f"""Write the new section to append to this wiki page.

Page: {page_path}
Description: {description}

Existing page content:
{existing_content or "(page is empty)"}

Return ONLY the new content to append. Begin with:
### From [[sources/{source_slug}]] ({today})

No explanation, no JSON, no fences."""

    def update_index_user(
        self,
        current_index: str,
        pages: list,
        source_title: str,
    ) -> str:
        return f"""Update the wiki index to reflect this ingest.

Current index:
{current_index}

Pages created or updated:
{json.dumps(pages, indent=2)}

Source ingested: {source_title}

Return the complete updated index.md. Follow the existing format exactly."""
