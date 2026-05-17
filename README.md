# LLM Wiki

A personal knowledge base that compounds. You drop sources in; an LLM builds and maintains a structured, interlinked wiki. Based on [Karpathy's LLM Wiki pattern](docs/karpathy-llm-wiki.md).

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

## Verify your setup

After installing dependencies and adding API keys, run the bootstrap check:

```bash
python scripts/check.py
```

This checks Python version, installed packages, API keys, all module imports, the graph self-tests, and directory structure. It exits 0 if everything is ready, non-zero with a list of failures otherwise. Run it any time something seems broken.

## Quick start

**1. Create the raw/ directory**

```bash
mkdir -p raw/papers raw/books raw/notes raw/assets
```

Wiki subdirectories (`wiki/sources/`, `wiki/concepts/`, etc.) are created automatically on first ingest.

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

Edit the constants in `config.py`:

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `MAX_TOKENS_PLAN` | `4096` | Max tokens for the planning call |
| `MAX_TOKENS_PAGE` | `4096` | Max tokens per page write |
| `SPLIT_THRESHOLD` | `40000` | Characters above which a source is split into chunks |

## Re-ingest

```bash
# Force re-ingest of a specific file (e.g. after editing CLAUDE.md)
python ingest.py --force raw/papers/foo.md

# Force re-ingest everything
python ingest.py --force
```

## Consolidation

After ingesting several sources, run consolidation periodically to synthesise cross-document insights and rewrite pages that have accumulated append sections:

```bash
# See what would be processed without writing anything
python consolidate.py --survey

# Run a consolidation pass (default depth: 5 nodes)
python consolidate.py

# Deeper pass
python consolidate.py --depth 10

# Force a specific node to the top of the queue
python consolidate.py --pin concepts/shadow.md
```

Consolidation writes synthesis documents to `wiki/analyses/` and rewrites accumulated append sections into clean unified pages.

## Workflow

```
raw/         ← you put sources here (immutable, never edited by LLM)
wiki/        ← LLM writes everything here; you browse it
CLAUDE.md    ← the schema: tells the LLM how to structure the wiki
config.py    ← pipeline configuration (model, token limits, paths)
.env.local   ← your API keys (gitignored)
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
