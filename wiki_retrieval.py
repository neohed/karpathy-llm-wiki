"""
wiki_retrieval.py
-----------------
Drop-in semantic retrieval layer for the LLM Wiki pipeline.

Replaces the blind load_wiki_context() approach with:
  1. Embed all wiki pages on first run, cache to disk
  2. On ingest, embed the source file and retrieve only the top-K
     most semantically similar wiki pages
  3. Re-embed only changed/new pages (hash-based cache invalidation)

Requirements:
    pip install voyageai numpy

Environment variables:
    VOYAGE_API_KEY  — your Voyage AI key (https://dash.voyageai.com)

Usage in your pipeline:
    from wiki_retrieval import WikiRetriever

    retriever = WikiRetriever(wiki_dir="wiki", cache_path=".wiki_embeddings.json")

    # Call this once at startup (fast if cache is warm)
    retriever.build_index()

    # Replace load_wiki_context() with this:
    context = retriever.get_context_for_source("raw/my-notes.md", top_k=12)

    # Call after you've written new/updated wiki pages:
    retriever.update_index(changed_paths=["wiki/concepts/jung.md"])
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import voyageai

# Shares the "api_audit" logger configured by ingest.py; falls back to no-op if standalone.
_audit = logging.getLogger("api_audit")


def _log(event: str, **fields):
    if _audit.handlers:
        entry = {"ts": datetime.now().isoformat(), "event": event, **fields}
        _audit.debug(json.dumps(entry, ensure_ascii=False))


def _embed_with_retry(vo, texts, model, input_type, max_retries=6):
    """Call vo.embed() with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return vo.embed(texts, model=model, input_type=input_type)
        except voyageai.error.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 20 * (2 ** attempt)   # 20s, 40s, 80s, ...
            print(f"  [WARN] Voyage rate limit — retrying in {wait}s "
                  f"(attempt {attempt + 1}/{max_retries}). "
                  f"Add a payment method at dashboard.voyageai.com to unlock higher limits.")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "voyage-3.5"          # Anthropic's recommended general-purpose model
INPUT_TYPE_DOC = "document"   # Use when embedding wiki pages (the "corpus")
INPUT_TYPE_QUERY = "query"    # Use when embedding the source being ingested
BATCH_SIZE = 64               # Voyage allows up to 1 000 per call; 64 is safe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    """SHA-256 of file contents — used to detect changes and skip re-embeds."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cosine_similarities(query_vec: np.ndarray, doc_matrix: np.ndarray) -> np.ndarray:
    """
    Voyage embeddings are already L2-normalised, so dot-product == cosine sim.
    query_vec  : (dim,)
    doc_matrix : (N, dim)
    returns    : (N,) similarity scores in [-1, 1]
    """
    return doc_matrix @ query_vec


# ---------------------------------------------------------------------------
# WikiRetriever
# ---------------------------------------------------------------------------

class WikiRetriever:
    """
    Maintains a persistent embedding index over your wiki/ directory.

    Cache format (.wiki_embeddings.json):
    {
        "relative/path/to/page.md": {
            "hash": "<sha256>",
            "embedding": [0.123, -0.456, ...]
        },
        ...
    }
    """

    def __init__(
        self,
        wiki_dir: str = "wiki",
        cache_path: str = ".wiki_embeddings.json",
        voyage_api_key: Optional[str] = None,
    ):
        self.wiki_dir = Path(wiki_dir)
        self.cache_path = Path(cache_path)
        self._vo = voyageai.Client(
            api_key=voyage_api_key or os.environ["VOYAGE_API_KEY"]
        )
        # In-memory index: {relative_path_str -> {"hash": str, "embedding": list}}
        self._index: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self) -> None:
        """
        Load the cache from disk, then embed any new or changed wiki pages.
        Safe to call every time your pipeline starts — it's a no-op when warm.
        """
        self._load_cache()

        pages = list(self.wiki_dir.rglob("*.md"))
        to_embed: list[tuple[str, Path]] = []  # (rel_key, path)

        for page in pages:
            key = str(page.relative_to(self.wiki_dir))
            current_hash = _file_hash(page)
            cached = self._index.get(key)
            if cached is None or cached["hash"] != current_hash:
                to_embed.append((key, page))

        # Remove index entries for deleted pages
        existing_keys = {str(p.relative_to(self.wiki_dir)) for p in pages}
        stale = [k for k in self._index if k not in existing_keys]
        for k in stale:
            del self._index[k]

        if to_embed:
            print(f"[WikiRetriever] Embedding {len(to_embed)} new/changed wiki pages…")
            self._embed_and_store(to_embed)
            self._save_cache()
        else:
            print(f"[WikiRetriever] Index is up to date ({len(self._index)} pages).")

    def is_ready(self) -> bool:
        """Return True if the index has been built and contains at least one entry."""
        return len(self._index) > 0

    def update_index(self, changed_paths: list[str | Path]) -> None:
        """
        Re-embed specific pages after an ingest run that modified them.
        Pass the paths exactly as they exist on disk.

        Example:
            retriever.update_index(["wiki/concepts/jung.md", "wiki/entities/freud.md"])
        """
        to_embed = []
        for p in changed_paths:
            path = Path(p)
            if path.exists():
                key = str(path.relative_to(self.wiki_dir))
                to_embed.append((key, path))
            else:
                # Page was deleted — remove from index
                key = str(path.relative_to(self.wiki_dir))
                self._index.pop(key, None)

        if to_embed:
            self._embed_and_store(to_embed)
            self._save_cache()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_context_for_source(
        self,
        source_path: str | Path,
        top_k: int = 12,
        char_budget: int = 60_000,
    ) -> str:
        """
        Embed the source file being ingested, find the top-K most similar
        wiki pages, and return their content concatenated as a context string.

        This is a direct replacement for your current load_wiki_context().

        Args:
            source_path : path to the raw source file being ingested
            top_k       : max number of wiki pages to include
            char_budget : hard character cap (safety net, rarely hit with top_k)

        Returns:
            A string of the form:
                === wiki/concepts/jung.md ===
                <content>

                === wiki/entities/freud.md ===
                <content>
                ...
        """
        if not self._index:
            raise RuntimeError(
                "Index is empty. Call build_index() before get_context_for_source()."
            )

        source_text = Path(source_path).read_text(encoding="utf-8", errors="ignore")

        # Embed the source as a *query* (Voyage optimises differently for queries vs docs)
        _log("voyage_request",
             endpoint="embed",
             model=MODEL,
             input_type=INPUT_TYPE_QUERY,
             n_texts=1,
             source=str(source_path),
             text_chars=len(source_text))
        result = _embed_with_retry(self._vo, [source_text], MODEL, INPUT_TYPE_QUERY)
        _log("voyage_response",
             endpoint="embed",
             n_embeddings=1,
             total_tokens=getattr(result, "total_tokens", None))
        query_vec = np.array(result.embeddings[0])

        # Build matrix of all doc embeddings
        keys = list(self._index.keys())
        doc_matrix = np.array([self._index[k]["embedding"] for k in keys])

        similarities = _cosine_similarities(query_vec, doc_matrix)
        ranked_indices = np.argsort(similarities)[::-1]  # highest first

        parts = []
        used_chars = 0

        for idx in ranked_indices[:top_k]:
            key = keys[idx]
            page_path = self.wiki_dir / key
            if not page_path.exists():
                continue
            content = page_path.read_text(encoding="utf-8", errors="ignore")
            chunk = f"=== {key} ===\n{content}\n"
            if used_chars + len(chunk) > char_budget:
                break
            parts.append(chunk)
            used_chars += len(chunk)

        print(
            f"[WikiRetriever] Retrieved {len(parts)} wiki pages "
            f"({used_chars:,} chars) for: {Path(source_path).name}"
        )
        return "\n".join(parts)

    def get_relevant_page_paths(
        self,
        source_path: str | Path,
        top_k: int = 12,
    ) -> list[str]:
        """
        Like get_context_for_source() but returns a list of relative page paths
        instead of their content. Useful if you want to load/filter them yourself.
        """
        if not self._index:
            raise RuntimeError("Call build_index() first.")

        source_text = Path(source_path).read_text(encoding="utf-8", errors="ignore")
        result = _embed_with_retry(self._vo, [source_text], MODEL, INPUT_TYPE_QUERY)
        query_vec = np.array(result.embeddings[0])

        keys = list(self._index.keys())
        doc_matrix = np.array([self._index[k]["embedding"] for k in keys])
        similarities = _cosine_similarities(query_vec, doc_matrix)
        ranked_indices = np.argsort(similarities)[::-1][:top_k]

        return [keys[i] for i in ranked_indices if (self.wiki_dir / keys[i]).exists()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_and_store(self, items: list[tuple[str, Path]]) -> None:
        """Embed pages in batches and store results in self._index."""
        for batch_start in range(0, len(items), BATCH_SIZE):
            batch = items[batch_start : batch_start + BATCH_SIZE]
            texts = [
                p.read_text(encoding="utf-8", errors="ignore") for _, p in batch
            ]
            _log("voyage_request",
                 endpoint="embed",
                 model=MODEL,
                 input_type=INPUT_TYPE_DOC,
                 n_texts=len(texts),
                 keys=[k for k, _ in batch])
            result = _embed_with_retry(self._vo, texts, MODEL, INPUT_TYPE_DOC)
            _log("voyage_response",
                 endpoint="embed",
                 n_embeddings=len(result.embeddings),
                 total_tokens=getattr(result, "total_tokens", None))
            for (key, path), embedding in zip(batch, result.embeddings):
                self._index[key] = {
                    "hash": _file_hash(path),
                    "embedding": embedding,
                }

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            with self.cache_path.open() as f:
                self._index = json.load(f)
            print(f"[WikiRetriever] Loaded cache: {len(self._index)} entries.")
        else:
            self._index = {}

    def _save_cache(self) -> None:
        with self.cache_path.open("w") as f:
            json.dump(self._index, f)
        print(f"[WikiRetriever] Cache saved: {len(self._index)} entries → {self.cache_path}")


# ---------------------------------------------------------------------------
# Quick smoke-test  (python wiki_retrieval.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python wiki_retrieval.py <path/to/raw/source.md>")
        sys.exit(1)

    source = sys.argv[1]
    retriever = WikiRetriever()
    retriever.build_index()

    print("\n--- Top relevant wiki pages ---")
    paths = retriever.get_relevant_page_paths(source, top_k=10)
    for p in paths:
        print(f"  {p}")

    print("\n--- Context string preview (first 500 chars) ---")
    ctx = retriever.get_context_for_source(source, top_k=10)
    print(ctx[:500])