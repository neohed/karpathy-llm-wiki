Good choice. Voyage is actually Anthropic's dedicated embedding partner — excellent quality. Let me read your skill file first, then write the retrieval module.Good — the current recommended model is actually `voyage-3.5` (not voyage-3). Now I have everything I need. Let me build the semantic retrieval module.Here's what to do with it:

**Setup:**
```bash
pip install voyageai numpy
export VOYAGE_API_KEY="your-key-from-dash.voyageai.com"
```

**Replace your ingest logic with two lines:**
```python
from wiki_retrieval import WikiRetriever

retriever = WikiRetriever(wiki_dir="wiki", cache_path=".wiki_embeddings.json")
retriever.build_index()  # fast when warm, embeds only new/changed pages

# Where you currently call load_wiki_context(), use this instead:
wiki_context = retriever.get_context_for_source("raw/random-notes.md", top_k=12)
```

**After Claude updates wiki pages, re-embed only what changed:**
```python
retriever.update_index(["wiki/concepts/jung.md", "wiki/entities/freud.md"])
```

**Key design decisions worth knowing:**

- The cache is a plain `.wiki_embeddings.json` file — embeddings persist across runs, so you only pay for re-embedding when a page actually changes (detected by SHA-256 hash)
- `top_k=12` is a good starting point — at ~2-3KB per wiki page that's roughly 30KB of context, well inside your budget with room for the source itself
- Voyage uses `input_type="query"` vs `input_type="document"` — the source being ingested is treated as a query, wiki pages as documents. This matters for retrieval quality; Voyage optimises the embeddings differently based on this hint
- The current recommended model is `voyage-3.5`, not voyage-3 as you specified — I've used the latest

You can also smoke-test it standalone before integrating: `python wiki_retrieval.py raw/random-notes.md`