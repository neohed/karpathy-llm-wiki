# Ticket 0c — Refactor ingest.py into focused modules

## Context

`ingest.py` is currently 624 lines. This ticket splits it into focused modules
without changing any behaviour. No new functionality. No logic changes.
A full ingest run before and after must produce identical output.

This is a pure structural refactor. The audit log token counts and wiki output
should be byte-for-byte identical after this change.

---

## Motivation

- `consolidate.py` (Ticket 4) needs to import shared utilities without importing
  the entire ingest pipeline
- `utils.py` needs to exist as a home for the shared `rewrite` function (Ticket 5)
- Files over ~200 lines become hard to navigate; 624 lines is too long
- Each module should have a single clear responsibility

---

## Target file structure

```
config.py       ← constants, paths, model names
context.py      ← IngestContext dataclass + make_pipeline runner
middleware.py   ← all mw_* functions, pipeline definition, LLM call functions,
                  graph helper functions
splitting.py    ← file splitting logic
wiki_io.py      ← wiki file reading and writing (schema, context, index, log)
utils.py        ← audit logger, file hashing, state, slug, find_raw_files
ingest.py       ← imports, ingest_file, main() — ~60 lines
prompts.py      ← unchanged (already extracted in Ticket 0b)
wiki_graph.py   ← unchanged (Ticket 1)
wiki_retrieval.py ← unchanged
```

---

## config.py

Extract from ingest.py lines 77–87. All constants and Path objects:

```python
from pathlib import Path

LLM_MODEL        = "claude-sonnet-4-6"
MAX_TOKENS_PLAN  = 2048
MAX_TOKENS_PAGE  = 4096
SPLIT_THRESHOLD  = 40_000

RAW_DIR     = Path("raw")
WIKI_DIR    = Path("wiki")
SCHEMA_FILE = Path("CLAUDE.md")
STATE_FILE  = Path(".ingest_state.json")

RAW_EXTENSIONS = {".md", ".txt", ".rst"}
```

No imports beyond `pathlib`. No logic.

---

## context.py

Extract from ingest.py lines 93–121.

Contains:
- `IngestContext` dataclass
- `make_pipeline` function

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable
import anthropic

@dataclass
class IngestContext:
    ...

def make_pipeline(*middlewares):
    ...
```

No imports from other project modules — `context.py` must have zero internal
dependencies so it can be imported anywhere without circular imports.

---

## utils.py

Extract from ingest.py:
- Lines 57–71: `_setup_audit_logger` + `_log`
- Lines 446–475: `file_hash`, `load_state`, `save_state`, `find_raw_files`,
  `_path_to_slug`

```python
from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import RAW_DIR, RAW_EXTENSIONS, STATE_FILE

def _setup_audit_logger(log_path: str = ".api_audit.log") -> logging.Logger: ...
def _log(event: str, **fields): ...
def file_hash(path: Path) -> str: ...
def load_state() -> dict: ...
def save_state(state: dict): ...
def find_raw_files() -> list[Path]: ...
def _path_to_slug(path: Path) -> str: ...
```

`_log` must import `_audit` from module-level setup — initialise the logger at
module level in `utils.py` the same way it is currently done in `ingest.py`.

---

## wiki_io.py

Extract from ingest.py:
- `load_wiki_context` (lines 478–499)
- `load_schema` (lines 502–506)
- `_update_index` (lines 389–416)
- `_write_log` (lines 419–440)

```python
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
import anthropic

from config import WIKI_DIR, SCHEMA_FILE, LLM_MODEL, MAX_TOKENS_PAGE
from utils import _log
from prompts import WikiPrompts

if TYPE_CHECKING:
    from context import IngestContext

_prompts = WikiPrompts()

def load_wiki_context(max_chars: int = 60_000) -> str: ...
def load_schema() -> str: ...
def _update_index(ctx: "IngestContext"): ...
def _write_log(ctx: "IngestContext"): ...
```

Note: `_update_index` and `_write_log` take an `IngestContext` argument. Use
`TYPE_CHECKING` guard to avoid circular import — `context.py` has no internal
dependencies, but `wiki_io.py` referencing `IngestContext` for type hints would
otherwise create a cycle if `context.py` ever imports from `wiki_io.py`.

---

## splitting.py

Extract from ingest.py lines 512–552:
- `split_dir_for`
- `create_splits`
- `resolve_ingest_paths`

```python
from __future__ import annotations
import re
import shutil
from pathlib import Path

from config import SPLIT_THRESHOLD

def split_dir_for(path: Path) -> Path: ...
def create_splits(source: Path) -> list[Path]: ...
def resolve_ingest_paths(source: Path) -> list[Path]: ...
```

No LLM calls, no wiki reads, no state. Pure filesystem operations.

---

## middleware.py

Extract from ingest.py:
- All `mw_*` functions (lines 125–292)
- `ingest_pipeline` definition
- `call_llm_plan` (lines 298–340)
- `call_llm_write_page` (lines 347–382)
- Graph helper functions: `_should_skip_graph`, `_graph_key`,
  `_infer_node_type`, `_infer_label` (lines 193–216)

```python
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

# mw_* functions ...
# call_llm_plan, call_llm_write_page ...
# graph helpers ...

ingest_pipeline = make_pipeline(
    mw_load_content,
    mw_plan,
    mw_write_pages,
    mw_update_graph,
    mw_update_embeddings,
)
```

`middleware.py` is the most import-heavy module — this is correct and expected.
It is the integration layer that orchestrates all other modules.

---

## ingest.py (after refactor)

~60 lines. Entry point only:

```python
#!/usr/bin/env python3
"""
ingest.py — LLM Wiki ingest tool

Usage:
  python ingest.py                    # batch: all new/changed files in raw/
  python ingest.py raw/notes/foo.md   # single file
  python ingest.py --force ...        # re-ingest everything

Requires:
  pip install anthropic python-dotenv voyageai numpy
  ANTHROPIC_API_KEY and VOYAGE_API_KEY in .env.local
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(".env.local")

import anthropic

from config import WIKI_DIR, LLM_MODEL
from context import IngestContext
from middleware import ingest_pipeline
from splitting import resolve_ingest_paths
from utils import file_hash, load_state, save_state, find_raw_files
from wiki_io import load_schema

try:
    from wiki_retrieval import WikiRetriever
    _RETRIEVAL_AVAILABLE = True
except ImportError:
    _RETRIEVAL_AVAILABLE = False


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
```

---

## Import dependency graph

To prevent circular imports, follow this strict layering — modules may only
import from modules below them in this list:

```
ingest.py        ← top level, imports from all
middleware.py    ← imports from context, config, utils, wiki_io, prompts, wiki_graph
wiki_io.py       ← imports from config, utils, prompts
splitting.py     ← imports from config only
utils.py         ← imports from config only
context.py       ← no internal imports
config.py        ← no internal imports
prompts.py       ← no internal imports (already extracted)
wiki_graph.py    ← no internal imports (standalone)
wiki_retrieval.py ← no internal imports (standalone)
```

Do not add imports that violate this layering. If a function seems to need
an import that would create a cycle, move the function to a higher layer.

---

## Verification

After the refactor, run a single file ingest and confirm:

```bash
python ingest.py raw/notes/Shadow\ Work.md
```

Expected: identical output to before the refactor. Check:
1. Same wiki pages written to disk
2. Same graph updates in `.wiki_graph.json`
3. Same token counts in `.api_audit.log`
4. No import errors on startup

Also verify that importing individual modules works cleanly:

```bash
python -c "from utils import file_hash; print('utils ok')"
python -c "from wiki_io import load_schema; print('wiki_io ok')"
python -c "from middleware import ingest_pipeline; print('middleware ok')"
python -c "from splitting import resolve_ingest_paths; print('splitting ok')"
```

---

## What must NOT change

- All function signatures — identical to current ingest.py
- All prompt content — `prompts.py` is not touched
- All pipeline behaviour — same middleware, same order, same logic
- `wiki_graph.py`, `wiki_retrieval.py`, `prompts.py` — not touched

---

## File summary

Files created:
- `config.py`
- `context.py`
- `middleware.py`
- `splitting.py`
- `wiki_io.py`
- `utils.py`

Files modified:
- `ingest.py` — reduced to ~60 lines, entry point only

Files not touched:
- `prompts.py`
- `wiki_graph.py`
- `wiki_retrieval.py`
- `CLAUDE.md`
