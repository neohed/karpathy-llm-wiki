# LLM Wiki

A personal knowledge base that compounds. You drop sources in; an LLM builds and maintains a structured, interlinked wiki. Based on [Karpathy's LLM Wiki pattern](karpathy-llm-wiki.md).

## Prerequisites

```bash
pip install anthropic python-dotenv voyageai numpy
```

Get your API keys and add them to `.env.local`:

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

- Anthropic key: [console.anthropic.com](https://console.anthropic.com)
- Voyage key: [dash.voyageai.com](https://dash.voyageai.com) — used for semantic retrieval of relevant wiki pages. Without it the pipeline falls back to loading wiki pages in alphabetical order.

## Quick start

**1. Create the raw/ and wiki/ directories**

```bash
mkdir -p raw/papers raw/books raw/notes raw/assets
mkdir -p wiki/sources wiki/concepts wiki/entities wiki/analyses
```

**2. Add your first source**

Drop a markdown file into `raw/`:

```bash
cp ~/Downloads/attention-is-all-you-need.md raw/papers/
# or paste your notes into raw/notes/2026-04-28-first-note.md
```

**3. Ingest it**

```bash
# Single file
python ingest.py raw/papers/attention-is-all-you-need.md

# Or batch — ingests everything new under raw/
python ingest.py
```

The LLM will create/update pages under `wiki/`. Watch the output for what changed.

**4. Open in Obsidian**

Open this directory as an Obsidian vault. Key views:

- `wiki/index.md` — browse the full catalog
- `wiki/log.md` — see what's been ingested
- Graph view (`Ctrl+G`) — see the shape of your knowledge

## Configuration

Edit the constants at the top of `ingest.py`:

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `MAX_TOKENS` | `16384` | Max output tokens per ingest |

## Re-ingest

```bash
# Force re-ingest of a specific file (e.g. after editing CLAUDE.md)
python ingest.py --force raw/papers/foo.md

# Force re-ingest everything
python ingest.py --force
```

## Workflow

```
raw/         ← you put sources here (immutable, never edited by LLM)
wiki/        ← LLM writes everything here; you browse it
CLAUDE.md    ← the schema: tells the LLM how to structure the wiki
.env.local   ← your API key (gitignored)
```

The wiki is a git repo. Commit `wiki/` after each ingest session for version history.

## Prompt caching

`CLAUDE.md` is sent to the API with `cache_control: ephemeral`. After the first call, the schema tokens are served from cache at ~10% of the normal input cost — repeated ingests are significantly cheaper.

## Obsidian tips

- **Graph view** (`Ctrl+G`) — see hubs and orphans at a glance.
- **Obsidian Web Clipper** — browser extension that converts articles to markdown, ready to drop into `raw/`.
- **Download local images** — Settings → Files and links → set attachment folder to `raw/assets/`, then bind "Download attachments" to a hotkey.
- **Dataview plugin** — queries over frontmatter; the LLM adds `tags`, `sources`, `updated` to every page.

## Lint the wiki

Ask Claude Code (or any LLM with the wiki in context):

> "Lint the wiki: check for orphan pages, missing cross-references, stale claims, and important concepts that need their own page."

The LLM will inspect `wiki/` and append a lint entry to `log.md`.
