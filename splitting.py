from __future__ import annotations
import re
import shutil
from pathlib import Path

from config import SPLIT_THRESHOLD


def split_dir_for(path: Path) -> Path:
    return path.parent / f".{path.stem}"


def create_splits(source: Path) -> list[Path]:
    sdir = split_dir_for(source)
    if sdir.exists():
        shutil.rmtree(sdir)
    sdir.mkdir()

    content = source.read_text(errors="replace")
    chunks  = re.split(r"(?=^#{1,2} )", content, flags=re.MULTILINE)
    chunks  = [c.strip() for c in chunks if c.strip()]

    if len(chunks) <= 1:
        mid      = len(content) // 2
        break_at = content.rfind("\n\n", 0, mid + 2000)
        if break_at == -1:
            break_at = mid
        chunks = [content[:break_at].strip(), content[break_at:].strip()]

    parts = []
    for i, chunk in enumerate(chunks, 1):
        p = sdir / f"part-{i:02d}.md"
        p.write_text(chunk, encoding="utf-8")
        parts.append(p)

    print(f"  [SPLIT] {source.name} → {len(parts)} parts in {sdir.name}/")
    return parts


def resolve_ingest_paths(source: Path) -> list[Path]:
    sdir = split_dir_for(source)
    if sdir.exists():
        parts = sorted(sdir.glob("*.md"))
        if parts:
            print(f"  [SPLITS] {source.name} → using {len(parts)} pre-split parts")
            return parts
    if source.stat().st_size > SPLIT_THRESHOLD:
        return create_splits(source)
    return [source]
