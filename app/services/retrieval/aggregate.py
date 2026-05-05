"""Child-to-parent aggregation and promotion logic (ADR-0002 §3)."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.chunker.promotion import should_promote_note, should_promote_book_chapter
from app.services.retrieval.schemas import ChildMatch, Parent, SourceMeta


@dataclass
class ScoredChunk:
    """Internal representation of a retrieved child chunk."""
    chunk_id: str
    parent_id: str
    source_type: str
    source_id: str
    vault_id: str
    chapter_id: str
    trust_tier: str
    content: str
    score: float
    tags: list[str] = field(default_factory=list)


def _agg_score(scores: list[float]) -> float:
    """parent_score = mean(child_scores) + 0.1 * log(matched_count + 1)"""
    n = len(scores)
    return sum(scores) / n + 0.1 * math.log(n + 1)


def _source_to_channel(source_type: str) -> str:
    return {
        "note": "notes",
        "book_chapter": "books",
        "web_saved": "web_saved",
        "web_live": "web",
    }.get(source_type, source_type)


def _make_parent(
    parent_id: str,
    chunks: list[ScoredChunk],
    content: str,
) -> Parent:
    sample = chunks[0]
    return Parent(
        parent_id=parent_id,
        content=content,
        score=_agg_score([c.score for c in chunks]),
        source_meta=SourceMeta(
            source_type=sample.source_type,
            source_id=sample.source_id,
            vault_id=sample.vault_id,
            trust_tier=sample.trust_tier,
            channel=_source_to_channel(sample.source_type),
        ),
        child_matches=[
            ChildMatch(chunk_id=c.chunk_id, score=c.score, content=c.content)
            for c in chunks
        ],
    )


def aggregate_children(chunks: list[ScoredChunk]) -> list[Parent]:
    """Group children by parent_id; apply chapter and note-level promotion.

    Promotion thresholds (ADR-0002):
      - 3+ children in same note source → promote to note-level parent
      - 2+ children in same book chapter → promote to chapter-level parent
    """
    if not chunks:
        return []

    by_parent: dict[str, list[ScoredChunk]] = defaultdict(list)
    by_note: dict[str, list[ScoredChunk]] = defaultdict(list)
    by_chapter: dict[tuple[str, str], list[ScoredChunk]] = defaultdict(list)

    for c in chunks:
        by_parent[c.parent_id].append(c)
        by_note[c.source_id].append(c)
        if c.chapter_id:
            by_chapter[(c.source_id, c.chapter_id)].append(c)

    promoted_sources: set[str] = set()
    promoted_chapters: set[tuple[str, str]] = set()
    parents: list[Parent] = []

    # Note-level promotion: 3+ sections matched in same note
    for source_id, note_chunks in by_note.items():
        if should_promote_note(len(note_chunks)):
            promoted_sources.add(source_id)
            content = "\n\n".join(c.content for c in note_chunks)
            parents.append(_make_parent(f"note:{source_id}", note_chunks, content))

    # Chapter-level promotion: 2+ sections matched in same book chapter
    for (source_id, chapter_id), ch_chunks in by_chapter.items():
        if source_id in promoted_sources:
            continue
        if should_promote_book_chapter(len(ch_chunks)):
            promoted_chapters.add((source_id, chapter_id))
            content = "\n\n".join(c.content for c in ch_chunks)
            parents.append(
                _make_parent(f"chapter:{source_id}:{chapter_id}", ch_chunks, content)
            )

    # Regular section-level parents for everything else
    for parent_id, p_chunks in by_parent.items():
        sample = p_chunks[0]
        if sample.source_id in promoted_sources:
            continue
        key = (sample.source_id, sample.chapter_id)
        if sample.chapter_id and key in promoted_chapters:
            continue
        # content placeholder — pipeline fills real parent content from Qdrant
        content = p_chunks[0].content
        parents.append(_make_parent(parent_id, p_chunks, content))

    return sorted(parents, key=lambda p: p.score, reverse=True)
