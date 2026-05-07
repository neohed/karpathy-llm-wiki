# Folder Structure

```
karpathy-llm-wiki/
├── CLAUDE.md                   # wiki schema & LLM instructions (the key config file)
├── README.md                   # quick start
├── ingest.py                   # ingest tool
├── folder-structure.md         # this file
├── .ingest_state.json          # tracks ingested file hashes (auto-created, git-ignore)
│
├── raw/                        # immutable source documents — YOU write here, LLM reads
│   ├── papers/                 # academic papers, preprints (markdown or plain text)
│   ├── books/                  # book chapters — one .md file per chapter/section
│   ├── notes/                  # personal notes, journal entries, scratch
│   └── assets/                 # images downloaded locally (Obsidian Web Clipper)
│
├── wiki/                       # LLM-owned markdown — LLM writes, you read
│   ├── index.md                # master catalog: every page listed with a one-line summary
│   ├── log.md                  # append-only history of ingests, queries, lint passes
│   ├── sources/                # one summary page per raw source
│   │   └── attention-is-all-you-need.md
│   ├── concepts/               # idea and concept pages
│   │   ├── transformer.md
│   │   ├── scaling-laws.md
│   │   └── attention.md
│   ├── entities/               # people, models, tools, organizations
│   │   ├── andrej-karpathy.md
│   │   ├── gpt-4.md
│   │   └── pytorch.md
│   └── analyses/               # comparisons, syntheses, filed answers
│       └── scaling-chinchilla-vs-gpt.md
│
└── specs/                      # project mini-specs (human-written)
    └── 001-initial-setup.md
```

## What goes where

| You have... | Put it in... |
|---|---|
| A PDF paper | Convert to markdown, put in `raw/papers/` |
| A book chapter | One `.md` file per chapter in `raw/books/` |
| A web article | Clip with Obsidian Web Clipper, save to `raw/notes/` or `raw/papers/` |
| A personal note | `raw/notes/YYYY-MM-DD-topic.md` |
| An image from an article | `raw/assets/` (Obsidian can auto-download these) |

## .gitignore additions

```
.ingest_state.json
.venv/
__pycache__/
raw/assets/*.png
raw/assets/*.jpg
```

The wiki itself should be committed — it's the compounding artifact. Raw sources are up to you (they may be large or copyrighted).
