from pathlib import Path

LLM_MODEL       = "claude-sonnet-4-6"
MAX_TOKENS_PLAN = 4096    # plan output; larger sources can generate many pages
MAX_TOKENS_PAGE = 4096    # one wiki page at a time, always fits
SPLIT_THRESHOLD = 40_000  # bytes; files larger than this are auto-split

RAW_DIR     = Path("raw")
WIKI_DIR    = Path("wiki")
SCHEMA_FILE = Path("CLAUDE.md")
STATE_FILE  = Path(".ingest_state.json")

RAW_EXTENSIONS = {".md", ".txt", ".rst"}
