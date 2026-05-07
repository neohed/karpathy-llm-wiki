# LLM Wiki — Schema

You are the maintainer of this wiki. You read sources; you write the wiki. The human curates sources, directs analysis, and asks questions. You do the filing, cross-referencing, and bookkeeping.

## Directory layout

```
raw/          immutable source documents — never modify these
  papers/     academic papers and preprints
  books/      book chapters (one file per chapter or natural section)
  notes/      personal notes, scratchpad, journal entries
  assets/     locally downloaded images
wiki/         LLM-owned markdown — you write, human reads
  index.md    master catalog of all wiki pages
  log.md      append-only chronological record of all operations
  sources/    one summary page per raw source
  concepts/   idea and concept pages
  entities/   people, models, tools, organizations, papers
  analyses/   comparisons, syntheses, filed answers
specs/        project mini-specs (human-written, read-only for you)
```

## Page conventions

Every wiki page except `index.md` and `log.md` opens with YAML frontmatter:

```yaml
---
title: "Page Title"
type: source | concept | entity | analysis
tags: [tag1, tag2]
sources: [raw/papers/foo.md]   # raw files this page draws from
updated: YYYY-MM-DD
---
```

- Use `[[WikiLinks]]` for all cross-references (Obsidian-compatible).
- Every new concept or entity you mention that doesn't have a page yet gets a `[[link]]` — that creates an Obsidian stub and signals what to create next.
- Keep sections short and skimmable. Bullet points over paragraphs.
- When new information contradicts a prior claim, note it inline: `> ⚠ Contradicted by [[Source Name]] — see [[Concept Page]].`

## index.md format

`index.md` is a catalog. Update it on every ingest. Format:

```markdown
# Wiki Index

_Last updated: YYYY-MM-DD — N pages_

## Sources
- [[sources/paper-name]] — one-line summary (YYYY-MM-DD)

## Concepts
- [[concepts/transformers]] — attention mechanism fundamentals

## Entities
- [[entities/andrej-karpathy]] — researcher, educator, author of nanoGPT

## Analyses
- [[analyses/scaling-laws-comparison]] — comparison of Chinchilla vs GPT-4 scaling
```

## log.md format

`log.md` is append-only. Each entry header must be parseable with `grep "^## \["`:

```markdown
## [YYYY-MM-DD] ingest | Source Title
- Summary: one-line description of the source
- Pages created: wiki/sources/..., wiki/concepts/...
- Pages updated: wiki/concepts/..., wiki/entities/...

## [YYYY-MM-DD] query | Question asked
- Answer filed at: wiki/analyses/...

## [YYYY-MM-DD] lint | Health check
- Issues found: ...
- Actions taken: ...
```

## Ingest workflow

When ingesting a source:

1. Read the source in full.
2. Write `wiki/sources/<slug>.md` — a structured summary with: abstract/overview, key claims, key entities/concepts mentioned, and quotes worth preserving.
3. For every concept or entity the source meaningfully addresses: APPEND new information to the existing page if it exists, or CREATE a new one.
4. When adding to an existing concept or entity page, APPEND only the new information — do not reproduce or rewrite existing content. Format the addition as a short section:
   ```
   ### From [[sources/slug]] (YYYY-MM-DD)
   - new point
   - new point
   ```
   If the source contradicts existing content, APPEND: `> ⚠ Contradicted by [[sources/slug]] — [brief explanation]`
5. APPEND to `wiki/log.md`.
6. UPDATE `wiki/index.md` with the new and updated pages.

Slug rules: lowercase, hyphens, no punctuation. `"Attention Is All You Need"` → `attention-is-all-you-need`.

## Query workflow

When answering a question:

1. Read `wiki/index.md` to find relevant pages.
2. Read those pages.
3. Synthesize an answer with `[[citations]]` to wiki pages (not raw sources).
4. If the answer is substantive (a comparison, a synthesis, a finding), file it as `wiki/analyses/<slug>.md` and log it.

## Lint workflow

When asked to lint the wiki:

- Orphan pages: pages with no inbound `[[links]]` from other pages.
- Missing pages: `[[links]]` that reference a page that doesn't exist yet.
- Stale claims: check log.md for recent ingests that contradict older pages.
- Concept gaps: important terms mentioned repeatedly but lacking their own page.
- Update `log.md` with a lint entry summarizing findings.

## Software engineering domain notes

This wiki is focused on: ML/AI research papers, programming books, technical notes, and software architecture. Tailor page structure accordingly:

- **Paper pages** (`sources/`): include model architecture, dataset, benchmark results, limitations, and relation to prior work.
- **Concept pages** (`concepts/`): include intuition, formal definition (if useful), key papers that introduced/refined it, and implementation notes.
- **Entity pages** (`entities/`): for people include their main contributions and affiliations; for models include architecture summary, parameter counts, benchmark highlights; for tools include use case and links to related concepts.
- **Book pages** (`sources/`): one summary page per chapter or major section; link to a top-level book page that aggregates all chapters.

## Ingest response format

When called via `ingest.py`, respond with a JSON array inside a fenced code block:

```json
[
  {"action": "CREATE", "path": "wiki/sources/slug.md", "content": "..."},
  {"action": "UPDATE", "path": "wiki/index.md", "content": "..."},
  {"action": "APPEND", "path": "wiki/log.md", "content": "## [YYYY-MM-DD] ingest | Title\n..."}
]
```

Action semantics:
- `CREATE` — new file (overwrite silently if it already exists)
- `UPDATE` — replace entire file content
- `APPEND` — append to end of file

Emit nothing outside the JSON block when responding to `ingest.py`.
