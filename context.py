from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

import anthropic


@dataclass
class IngestContext:
    path: Path                           # file being ingested (may be a split part)
    client: anthropic.Anthropic
    retriever: Optional[object] = None   # WikiRetriever, if available
    original_path: Path = None           # unsplit source path when path is a part
    content: str = ""
    wiki_summary: str = ""
    plan: dict = field(default_factory=dict)
    written_paths: list = field(default_factory=list)

    def __post_init__(self):
        if self.original_path is None:
            self.original_path = self.path


def make_pipeline(*middlewares):
    """Each middleware: fn(ctx: IngestContext, next: Callable) -> None"""
    def run(ctx: IngestContext):
        def dispatch(i):
            if i < len(middlewares):
                middlewares[i](ctx, lambda: dispatch(i + 1))
        dispatch(0)
    return run
