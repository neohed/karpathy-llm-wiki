# TODO

## Known limitations

### Large file ingestion (> 32K tokens)
Files exceeding the model's context window are currently truncated at 28,000
characters in `ingest.py`. Long documents silently lose their tail content.

Planned: structure-aware splitting on markdown headings/section boundaries,
with each chunk ingested as its own source page and an optional synthesis pass
to produce a top-level summary page linking the chunks together.

Workaround for now: split large files manually before dropping into `raw/`.
Books should already be one file per chapter; long papers can be split at
major section boundaries.
