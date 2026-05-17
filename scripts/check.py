#!/usr/bin/env python3
"""
scripts/check.py — Bootstrap and environment check for the LLM Wiki pipeline.

Run from the project root:
    python scripts/check.py

Checks:
  1. Python version
  2. Required packages installed
  3. API keys present in .env.local
  4. All project modules import cleanly
  5. wiki_graph.py internal tests pass
  6. Directory structure is intact
  7. Raw source files found
  8. Graph and state file status (if they exist)

Exit code 0 = all checks passed. Non-zero = at least one failure.
"""

import sys
import os

# Must run from project root
if not os.path.exists("ingest.py"):
    print("ERROR: Run this script from the project root (where ingest.py lives).")
    sys.exit(1)

# Ensure project root is on sys.path so local modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath("ingest.py")))

PASS = "  ok"
FAIL = "  FAIL"
WARN = "  warn"

failures = []
warnings = []


def check(label: str, fn):
    """Run a check function, print result, record failures."""
    try:
        result = fn()
        msg = f"  {result}" if isinstance(result, str) else ""
        print(f"{PASS}  {label}{msg}")
        return True
    except AssertionError as e:
        detail = f": {e}" if str(e) else ""
        print(f"{FAIL}  {label}{detail}")
        failures.append(label)
        return False
    except Exception as e:
        print(f"{FAIL}  {label}: {e}")
        failures.append(label)
        return False


def warn(label: str, fn):
    """Run a check function but only warn on failure (not fatal)."""
    try:
        result = fn()
        msg = f"  {result}" if isinstance(result, str) else ""
        print(f"{PASS}  {label}{msg}")
        return True
    except Exception as e:
        print(f"{WARN}  {label}: {e}")
        warnings.append(label)
        return False


# ---------------------------------------------------------------------------
# 1. Python version
# ---------------------------------------------------------------------------
print("\n── Python ──────────────────────────────────────────────")

check("Python >= 3.10", lambda: (
    None if sys.version_info >= (3, 10)
    else (_ for _ in ()).throw(AssertionError(f"got {sys.version_info.major}.{sys.version_info.minor}"))
) or f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


# ---------------------------------------------------------------------------
# 2. Required packages
# ---------------------------------------------------------------------------
print("\n── Packages ────────────────────────────────────────────")

def _import(pkg, import_name=None):
    import importlib
    mod = importlib.import_module(import_name or pkg)
    version = getattr(mod, "__version__", "?")
    return version

for pkg, import_name in [
    ("anthropic",   None),
    ("dotenv",      "dotenv"),
    ("voyageai",    "voyageai"),
    ("numpy",       "numpy"),
]:
    check(f"pip: {pkg}", lambda p=import_name or pkg: _import(p))


# ---------------------------------------------------------------------------
# 3. API keys
# ---------------------------------------------------------------------------
print("\n── API Keys (.env.local) ───────────────────────────────")

from dotenv import load_dotenv
load_dotenv(".env.local")

check(
    "ANTHROPIC_API_KEY",
    lambda: (
        None if os.environ.get("ANTHROPIC_API_KEY")
        else (_ for _ in ()).throw(AssertionError("not set — add to .env.local"))
    ) or f"sk-...{os.environ['ANTHROPIC_API_KEY'][-4:]}",
)
warn(
    "VOYAGE_API_KEY (needed for semantic retrieval)",
    lambda: (
        None if os.environ.get("VOYAGE_API_KEY")
        else (_ for _ in ()).throw(Exception("not set — semantic retrieval will fall back to brute-force"))
    ) or f"pa-...{os.environ['VOYAGE_API_KEY'][-4:]}",
)


# ---------------------------------------------------------------------------
# 4. Module imports
# ---------------------------------------------------------------------------
print("\n── Module Imports ──────────────────────────────────────")

modules = [
    ("config",        "from config import LLM_MODEL, WIKI_DIR, RAW_DIR"),
    ("context",       "from context import IngestContext, make_pipeline"),
    ("utils",         "from utils import _log, file_hash, find_raw_files"),
    ("splitting",     "from splitting import resolve_ingest_paths"),
    ("wiki_graph",    "from wiki_graph import WikiGraph, GraphEdge"),
    ("wiki_retrieval","from wiki_retrieval import WikiRetriever"),
    ("prompts",       "from prompts import WikiPrompts"),
    ("wiki_io",       "from wiki_io import load_schema, load_wiki_context"),
    ("middleware",    "from middleware import ingest_pipeline"),
    ("rewrite",       "from rewrite import needs_rewrite, rewrite_page, detect_append_sections"),
    ("synthesis",     "from synthesis import synthesise_edge"),
    ("consolidate",   "import consolidate"),
    ("ingest",        "import ingest"),
]

for name, stmt in modules:
    check(name, lambda s=stmt: exec(s) or "imported")  # noqa: S102


# ---------------------------------------------------------------------------
# 5. wiki_graph self-tests
# ---------------------------------------------------------------------------
print("\n── Graph Self-Tests ────────────────────────────────────")

def _run_graph_tests():
    import subprocess
    result = subprocess.run(
        [sys.executable, "wiki_graph.py", "--test"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip().replace("\n", ", ")

check("wiki_graph --test", _run_graph_tests)


# ---------------------------------------------------------------------------
# 6. Directory structure
# ---------------------------------------------------------------------------
print("\n── Directory Structure ─────────────────────────────────")

from pathlib import Path

required_dirs = ["raw", "wiki", "scripts", "specs"]
optional_dirs = ["wiki/concepts", "wiki/entities", "wiki/sources", "wiki/analyses"]

for d in required_dirs:
    check(f"dir: {d}/", lambda d=d: None if Path(d).is_dir()
          else (_ for _ in ()).throw(AssertionError("missing")))

for d in optional_dirs:
    warn(f"dir: {d}/ (created on first ingest)", lambda d=d: None if Path(d).is_dir()
         else (_ for _ in ()).throw(Exception("not yet created")))

required_files = [
    "ingest.py", "consolidate.py", "wiki_graph.py", "wiki_retrieval.py",
    "prompts.py", "rewrite.py", "synthesis.py",
    "config.py", "context.py", "utils.py", "splitting.py",
    "wiki_io.py", "middleware.py",
    "CLAUDE.md", ".env.local",
]
for f in required_files:
    check(f"file: {f}", lambda f=f: None if Path(f).exists()
          else (_ for _ in ()).throw(AssertionError("missing")))


# ---------------------------------------------------------------------------
# 7. Raw source files
# ---------------------------------------------------------------------------
print("\n── Raw Source Files ────────────────────────────────────")

def _count_raw():
    from utils import find_raw_files
    files = find_raw_files()
    if not files:
        raise AssertionError("no files found in raw/ — add source documents")
    return f"{len(files)} file(s) found"

check("raw/ contains source files", _count_raw)


# ---------------------------------------------------------------------------
# 8. State and graph status
# ---------------------------------------------------------------------------
print("\n── Runtime State ───────────────────────────────────────")

def _graph_status():
    from wiki_graph import WikiGraph
    g = WikiGraph.load()
    n = len(g.all_nodes())
    e = g.edge_count()
    if n == 0:
        raise Exception("graph is empty — run python ingest.py to populate")
    return f"{n} nodes, {e} edges"

def _state_status():
    state_path = Path(".ingest_state.json")
    if not state_path.exists():
        raise Exception("no ingest state yet — run python ingest.py first")
    import json
    state = json.loads(state_path.read_text())
    return f"{len(state)} file(s) tracked"

def _audit_status():
    log = Path(".api_audit.log")
    if not log.exists():
        raise Exception("no API calls logged yet")
    lines = log.read_text().strip().splitlines()
    return f"{len(lines)} API call(s) logged"

warn("knowledge graph (.wiki_graph.json)", _graph_status)
warn("ingest state (.ingest_state.json)", _state_status)
warn("API audit log (.api_audit.log)", _audit_status)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "─" * 57)
if failures:
    print(f"FAILED — {len(failures)} check(s) failed:")
    for f in failures:
        print(f"  ✗ {f}")
    if warnings:
        print(f"\n  {len(warnings)} warning(s) — non-fatal:")
        for w in warnings:
            print(f"  ~ {w}")
    sys.exit(1)
else:
    if warnings:
        print(f"READY  ({len(warnings)} warning(s) — non-fatal)")
        for w in warnings:
            print(f"  ~ {w}")
    else:
        print("READY  — all checks passed.")
    sys.exit(0)
