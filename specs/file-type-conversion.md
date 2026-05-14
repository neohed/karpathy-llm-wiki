# Implementation Prompt: File Extraction Middleware

## Context

This project is an LLM-powered personal knowledge wiki based on Andrej Karpathy's LLM Wiki
concept. The pipeline is implemented in `ingest.py` using a middleware chain pattern modelled
on Express.js/Connect. Each middleware receives an `IngestContext` dataclass and a `next()`
callable, does its work, and calls `next()` to pass control forward.

The current pipeline chain is:

```
mw_load_content → mw_load_wiki → mw_call_llm → mw_parse → mw_apply → mw_update_index
```

`mw_load_content` currently reads the raw file into `ctx.content` with no transformation.
It only handles plain text formats (`.md`, `.txt`, `.rst`) defined in `RAW_EXTENSIONS`.

## What needs to be built

Add support for ingesting **PDF**, **HTML**, and **OpenOffice/LibreOffice** files by
implementing a **strategy pattern** for text extraction, plugged in as a new middleware
step immediately after `mw_load_content`.

---

## Architecture

### New middleware: `mw_extract_text`

Insert this into the pipeline immediately after `mw_load_content`:

```
mw_load_content → mw_extract_text → mw_load_wiki → mw_call_llm → ...
```

`mw_extract_text` checks the file extension, looks up the appropriate strategy in a
registry, runs it, and overwrites `ctx.content` with the extracted plain text. For file
types not in the registry (already plain text), it is a no-op.

```python
def mw_extract_text(ctx: IngestContext, next):
    suffix = ctx.path.suffix.lower()
    extractor = TEXT_EXTRACTORS.get(suffix)
    if extractor:
        result = extractor(ctx.path)
        ctx.content = result.text
        ctx.metadata.update(result.metadata)
    next()
```

### Strategy registry

```python
TEXT_EXTRACTORS = {
    ".pdf":  extract_pdf,
    ".html": extract_html,
    ".htm":  extract_html,
    ".odt":  extract_openoffice,
    ".docx": extract_openoffice,
}
```

### ExtractionResult

Each strategy returns an `ExtractionResult` dataclass:

```python
@dataclass
class ExtractionResult:
    text: str
    metadata: dict = field(default_factory=dict)
```

`metadata` carries file-level information the LLM can use when writing wiki pages —
things like document title, author, creation date. This avoids the LLM having to infer
metadata it could be given directly.

### IngestContext changes

Add a `metadata` field to `IngestContext`:

```python
@dataclass
class IngestContext:
    ...
    metadata: dict = field(default_factory=dict)  # populated by mw_extract_text
```

Weave the metadata into the user prompt in `build_user_prompt()` when present:

```python
if ctx.metadata:
    metadata_block = "\n".join(f"{k}: {v}" for k, v in ctx.metadata.items())
    # Include as a ## Metadata section above ## Content in the user prompt
```

---

## Extraction strategies

### PDF — `extract_pdf`

Use `pdfplumber` (not `pypdf`). It handles layout and tables better.

- Extract text page by page, joining with double newlines between pages
- Extract metadata: `Title`, `Author`, `CreationDate` from `pdf.metadata` if present
- Strip null bytes and other artefacts that pdfplumber occasionally produces
- Do NOT attempt OCR — if a PDF yields no text (scanned), return a clear message in
  `text` like `"[PDF contained no extractable text — may be scanned]"` so the LLM
  can create a minimal wiki page noting the source exists but is not readable

```
pip install pdfplumber
```

### HTML — `extract_html`

Use `trafilatura`. It is specifically designed to extract article body content from
real-world HTML, stripping navigation, headers, footers, sidebars, and ads.

- Use `trafilatura.extract()` with `include_metadata=True` to get title and author
- Fall back to `trafilatura.extract()` with `favor_recall=True` if the first pass
  returns None or very short text (under 200 characters)
- If trafilatura returns nothing, fall back to BeautifulSoup stripping all tags as
  a last resort
- Populate metadata with: `title`, `author`, `date` if available from trafilatura

```
pip install trafilatura
```

### OpenOffice/LibreOffice and Word — `extract_openoffice`

- For `.docx` use `mammoth`. It converts to clean markdown output which is ideal
  since the rest of the pipeline expects markdown-friendly prose. Use
  `mammoth.convert_to_markdown()`.
- For `.odt` use the `odfpy` library (`odf.opendocument` + `odf.text`). Walk the
  text elements and join them.
- Populate metadata with `title` and `author` from document properties where available.

```
pip install mammoth odfpy
```

---

## RAW_EXTENSIONS update

Extend the set to include the new supported types:

```python
RAW_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".html", ".htm", ".odt", ".docx"}
```

---

## Error handling

Each strategy should catch its own exceptions and return a graceful `ExtractionResult`
rather than letting errors propagate up and kill the pipeline run. Pattern:

```python
def extract_pdf(path: Path) -> ExtractionResult:
    try:
        ...
        return ExtractionResult(text=text, metadata=metadata)
    except Exception as e:
        return ExtractionResult(
            text=f"[Extraction failed for {path.name}: {e}]",
            metadata={}
        )
```

This keeps the pipeline resumable — a bad file produces a minimal wiki note rather
than crashing the batch.

---

## File splitting interaction

The existing large-file splitting logic in `resolve_ingest_paths()` splits on H1/H2
markdown headings. This is only appropriate for `.md` files. The new extraction
middleware should run **before** splitting is considered, or alternatively the splitting
logic should be skipped for non-markdown files. Recommended approach: in
`resolve_ingest_paths()`, only attempt heading-based splitting when
`source.suffix.lower() in {".md", ".txt", ".rst"}`. For PDF and other binary formats,
pass the file through unsplit regardless of size — the extraction strategy handles
whatever the file contains.

---

## What does NOT need to change

- The middleware chain runner (`make_pipeline`, `dispatch`)
- `mw_load_wiki`, `mw_call_llm`, `mw_parse`, `mw_apply`, `mw_update_index`
- `wiki_retrieval.py` — entirely unaffected
- `CLAUDE.md` schema — the LLM already handles varied source content
- `.ingest_state.json` hashing — already hashes raw bytes, works for any file type

---

## Suggested implementation order

1. Add `ExtractionResult` dataclass and `metadata` field to `IngestContext`
2. Update `build_user_prompt()` to include metadata block when present
3. Implement `extract_pdf` and test with a real PDF
4. Implement `extract_html` and test with a saved HTML file
5. Implement `extract_openoffice` for `.docx` first, then `.odt`
6. Wire up `TEXT_EXTRACTORS` registry and `mw_extract_text` middleware
7. Insert `mw_extract_text` into `ingest_pipeline`
8. Update `RAW_EXTENSIONS` and `resolve_ingest_paths()` splitting guard
9. End-to-end test: one file of each new type through the full pipeline

---

## Dependencies summary

```
pip install pdfplumber trafilatura mammoth odfpy
```

All are pure Python, no system dependencies required except that `trafilatura` benefits
from `lxml` being present (usually already installed as a transitive dependency).
