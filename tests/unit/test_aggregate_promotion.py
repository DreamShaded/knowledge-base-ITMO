"""Unit tests for child→parent aggregation and promotion (ADR-0002 §3)."""
import math

import pytest

from app.services.retrieval.aggregate import ScoredChunk, aggregate_children, _agg_score


def _chunk(
    chunk_id: str,
    parent_id: str,
    source_id: str = "note-1",
    chapter_id: str = "",
    source_type: str = "note",
    vault_id: str = "personal",
    score: float = 0.8,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        parent_id=parent_id,
        source_type=source_type,
        source_id=source_id,
        vault_id=vault_id,
        chapter_id=chapter_id,
        trust_tier="tier0",
        content=f"content-{chunk_id}",
        score=score,
    )


class TestAggScore:
    def test_single_child(self):
        score = _agg_score([0.8])
        expected = 0.8 + 0.1 * math.log(2)
        assert score == pytest.approx(expected)

    def test_three_children_higher_than_two(self):
        """Same-scored children: more matches → higher aggregate via log bonus."""
        s2 = _agg_score([0.8, 0.8])
        s3 = _agg_score([0.8, 0.8, 0.8])
        assert s3 > s2

    def test_formula(self):
        scores = [0.9, 0.7, 0.5]
        result = _agg_score(scores)
        expected = (0.9 + 0.7 + 0.5) / 3 + 0.1 * math.log(4)
        assert result == pytest.approx(expected)


class TestAggregation:
    def test_groups_by_parent_id(self):
        # Different source_ids prevent note-level promotion (needs 3+ per note).
        chunks = [
            _chunk("c1", "p1", source_id="note-1"),
            _chunk("c2", "p1", source_id="note-1"),
            _chunk("c3", "p2", source_id="note-2"),
        ]
        parents = aggregate_children(chunks)
        parent_ids = {p.parent_id for p in parents}
        assert "p1" in parent_ids
        assert "p2" in parent_ids

    def test_empty_input(self):
        assert aggregate_children([]) == []

    def test_child_matches_attached(self):
        chunks = [_chunk("c1", "p1"), _chunk("c2", "p1")]
        parents = aggregate_children(chunks)
        p = next(p for p in parents if p.parent_id == "p1")
        assert len(p.child_matches) == 2
        match_ids = {m.chunk_id for m in p.child_matches}
        assert match_ids == {"c1", "c2"}

    def test_sorted_by_score_descending(self):
        chunks = [
            _chunk("c1", "p1", score=0.5),
            _chunk("c2", "p2", score=0.9),
            _chunk("c3", "p3", score=0.7),
        ]
        parents = aggregate_children(chunks)
        scores = [p.score for p in parents]
        assert scores == sorted(scores, reverse=True)


class TestNotePromotion:
    def test_three_sections_promotes_to_note(self):
        """3+ children from same note → single note-level parent."""
        chunks = [
            _chunk("c1", "p1", source_id="note-A"),
            _chunk("c2", "p2", source_id="note-A"),
            _chunk("c3", "p3", source_id="note-A"),
        ]
        parents = aggregate_children(chunks)
        ids = [p.parent_id for p in parents]
        assert any(pid.startswith("note:") for pid in ids)
        # All children should be in the promoted note parent
        promoted = next(p for p in parents if p.parent_id.startswith("note:"))
        assert len(promoted.child_matches) == 3

    def test_two_sections_does_not_promote_note(self):
        """2 children from same note → regular parent, not note-level."""
        chunks = [
            _chunk("c1", "p1", source_id="note-A"),
            _chunk("c2", "p2", source_id="note-A"),
        ]
        parents = aggregate_children(chunks)
        ids = [p.parent_id for p in parents]
        assert not any(pid.startswith("note:") for pid in ids)


class TestChapterPromotion:
    def test_two_sections_same_chapter_promotes(self):
        """2+ children in same chapter → chapter-level parent."""
        chunks = [
            _chunk("c1", "p1", source_id="book-1", chapter_id="ch1", source_type="book_chapter"),
            _chunk("c2", "p2", source_id="book-1", chapter_id="ch1", source_type="book_chapter"),
        ]
        parents = aggregate_children(chunks)
        ids = [p.parent_id for p in parents]
        assert any(pid.startswith("chapter:") for pid in ids)

    def test_one_section_does_not_promote_chapter(self):
        chunks = [
            _chunk("c1", "p1", source_id="book-1", chapter_id="ch1", source_type="book_chapter"),
        ]
        parents = aggregate_children(chunks)
        ids = [p.parent_id for p in parents]
        assert not any(pid.startswith("chapter:") for pid in ids)

    def test_note_promotion_takes_precedence_over_chapter(self):
        """Note promotion (3+) should suppress chapter promotion."""
        chunks = [
            _chunk("c1", "p1", source_id="note-A", chapter_id="s1"),
            _chunk("c2", "p2", source_id="note-A", chapter_id="s1"),
            _chunk("c3", "p3", source_id="note-A", chapter_id="s1"),
        ]
        parents = aggregate_children(chunks)
        ids = [p.parent_id for p in parents]
        assert any(pid.startswith("note:") for pid in ids)
        assert not any(pid.startswith("chapter:") for pid in ids)
