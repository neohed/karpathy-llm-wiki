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

    client = anthropic.Anthropic(max_retries=0)
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
