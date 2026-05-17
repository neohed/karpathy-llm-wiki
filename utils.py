from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import RAW_DIR, RAW_EXTENSIONS, STATE_FILE


def _setup_audit_logger(log_path: str = ".api_audit.log") -> logging.Logger:
    logger = logging.getLogger("api_audit")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


_audit = _setup_audit_logger()


def _log(event: str, **fields):
    entry = {"ts": datetime.now().isoformat(), "event": event, **fields}
    _audit.debug(json.dumps(entry, ensure_ascii=False))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def find_raw_files() -> list[Path]:
    """Find all raw source files, excluding hidden split directories."""
    if not RAW_DIR.exists():
        return []
    files = []
    for ext in RAW_EXTENSIONS:
        for f in RAW_DIR.rglob(f"*{ext}"):
            rel_parts = f.relative_to(RAW_DIR).parts
            if not any(part.startswith(".") for part in rel_parts[:-1]):
                files.append(f)
    return sorted(files)


def _path_to_slug(path: Path) -> str:
    name = path.stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")
