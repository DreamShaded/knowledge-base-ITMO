"""
Chunker package — imports from kebab-case modules via importlib.
External code imports from this package; never directly from hyphenated files.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).parent


def _load(stem: str):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required for @dataclass __module__ lookup
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Re-export core models
from app.services.chunker.base import Chunk, ChunkedDocument, ParentChunk  # noqa: E402

# Re-export from promotion (single-word, importable normally)
from app.services.chunker.promotion import (  # noqa: E402
    NOTE_SECTION_THRESHOLD,
    BOOK_SECTION_THRESHOLD,
    should_promote_note,
    should_promote_book_chapter,
)

# Lazy loaders for kebab-case modules
def get_tokenizer(model_id: str = "Qwen/Qwen3-Embedding-0.6B", use_fallback: bool = False):
    mod = _load("qwen3-tokenizer")
    return mod.Qwen3Tokenizer(model_id=model_id, use_fallback=use_fallback)


def get_chunker(tokenizer=None):
    mod = _load("markdown-parent-child")
    return mod.MarkdownParentChildChunker(tokenizer=tokenizer)


__all__ = [
    "Chunk",
    "ChunkedDocument",
    "ParentChunk",
    "NOTE_SECTION_THRESHOLD",
    "BOOK_SECTION_THRESHOLD",
    "should_promote_note",
    "should_promote_book_chapter",
    "get_tokenizer",
    "get_chunker",
]
