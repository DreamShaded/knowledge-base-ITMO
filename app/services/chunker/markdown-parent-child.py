"""
Markdown-aware parent-child chunker (ADR-0002).
Splits markdown sources into section-level parents and leaf children (≤512 tok, overlap=64).
Applies promotion thresholds: note≥3 sections → whole-note parent; book≥2 → whole-chapter.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from app.services.chunker.base import Chunk, ChunkedDocument, ParentChunk
from app.services.chunker.promotion import should_promote_book_chapter, should_promote_note

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

CHILD_MAX_TOKENS = 512
CHILD_OVERLAP_TOKENS = 64


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _point_id(source_id: str, index: int) -> str:
    raw = f"{source_id}|{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class _Section:
    heading: str
    level: int
    content: str  # full section text including heading line


def _parse_sections(text: str) -> list[_Section]:
    """Split markdown text into sections delimited by headings."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [_Section(heading="", level=0, content=text)]

    sections: list[_Section] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(_Section(
            heading=m.group(2).strip(),
            level=len(m.group(1)),
            content=text[start:end].strip(),
        ))

    # Text before first heading
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.insert(0, _Section(heading="", level=0, content=preamble))
    return sections


class MarkdownParentChildChunker:
    def __init__(self, tokenizer=None) -> None:
        if tokenizer is None:
            import sys as _sys
            import importlib.util as _ilu
            from pathlib import Path as _P
            _name = "qwen3_tokenizer"
            _spec = _ilu.spec_from_file_location(_name, _P(__file__).parent / "qwen3-tokenizer.py")
            _mod = _ilu.module_from_spec(_spec)
            _sys.modules[_name] = _mod
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
            tokenizer = _mod.Qwen3Tokenizer()
        self._tok = tokenizer

    def chunk(
        self,
        text: str,
        source_id: str,
        source_type: str,
        vault_id: str = "",
        chapter_id: str = "",
        project: str = "",
        tags: Optional[list[str]] = None,
        trust_tier: str = "tier1",
        modified_at: str = "",
    ) -> ChunkedDocument:
        tags = tags or []
        sections = _parse_sections(text)
        is_book = source_type == "book_chapter"

        promote = (
            should_promote_book_chapter(len(sections)) if is_book
            else should_promote_note(len(sections))
        )

        doc = ChunkedDocument()
        child_idx = 0
        parent_idx = 0

        # Whole-document parent (created when promotion threshold is met)
        whole_parent_id: Optional[str] = None
        if promote:
            pid = _point_id(source_id, parent_idx)
            parent_idx += 1
            whole_parent_id = pid
            doc.parents.append(ParentChunk(
                chunk_id=pid,
                parent_id="",
                source_type=source_type,
                source_id=source_id,
                vault_id=vault_id,
                chapter_id=chapter_id,
                project=project,
                tags=tags,
                trust_tier=trust_tier,
                content=text,
                content_hash=_content_hash(text),
                chunk_index=0,
                modified_at=modified_at,
            ))

        for section in sections:
            section_text = section.content
            if not section_text.strip():
                continue

            # Section-level parent (always created, unless promoted away)
            sec_pid = _point_id(source_id, parent_idx)
            parent_idx += 1
            effective_parent_id = whole_parent_id or ""
            doc.parents.append(ParentChunk(
                chunk_id=sec_pid,
                parent_id=effective_parent_id,
                source_type=source_type,
                source_id=source_id,
                vault_id=vault_id,
                chapter_id=chapter_id,
                project=project,
                tags=tags,
                trust_tier=trust_tier,
                content=section_text,
                content_hash=_content_hash(section_text),
                chunk_index=parent_idx - 1,
                modified_at=modified_at,
            ))

            # Child chunks split from this section
            child_texts = self._tok.split_with_overlap(
                section_text, CHILD_MAX_TOKENS, CHILD_OVERLAP_TOKENS
            )
            for ct in child_texts:
                if not ct.strip():
                    continue
                cid = _point_id(source_id, 10000 + child_idx)
                doc.children.append(Chunk(
                    chunk_id=cid,
                    parent_id=sec_pid,
                    source_type=source_type,
                    source_id=source_id,
                    vault_id=vault_id,
                    chapter_id=chapter_id,
                    project=project,
                    tags=tags,
                    trust_tier=trust_tier,
                    content=ct,
                    content_hash=_content_hash(ct),
                    chunk_index=child_idx,
                    modified_at=modified_at,
                ))
                child_idx += 1

        return doc
