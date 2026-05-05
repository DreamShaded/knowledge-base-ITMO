"""Unit tests for trust multipliers (ADR-0004 §1)."""
import pytest

from app.services.retrieval.schemas import Parent, SourceMeta
from app.services.retrieval.trust import apply_trust_multipliers, _MULTIPLIERS


def _parent(source_type: str, score: float = 1.0) -> Parent:
    return Parent(
        parent_id="p1",
        content="x",
        score=score,
        source_meta=SourceMeta(
            source_type=source_type,
            source_id="s1",
            vault_id="v1",
            trust_tier="tier0",
            channel="notes",
        ),
    )


class TestTrustMultipliers:
    def test_note_multiplier_is_one(self):
        result = apply_trust_multipliers([_parent("note", score=0.8)])
        assert result[0].score == pytest.approx(0.8)

    def test_book_chapter_multiplier(self):
        result = apply_trust_multipliers([_parent("book_chapter", score=1.0)])
        assert result[0].score == pytest.approx(_MULTIPLIERS["book_chapter"])

    def test_web_saved_multiplier(self):
        result = apply_trust_multipliers([_parent("web_saved", score=1.0)])
        assert result[0].score == pytest.approx(_MULTIPLIERS["web_saved"])

    def test_tier1_multiplier(self):
        result = apply_trust_multipliers([_parent("tier1", score=1.0)])
        assert result[0].score == pytest.approx(_MULTIPLIERS["tier1"])

    def test_unknown_source_uses_default(self):
        result = apply_trust_multipliers([_parent("unknown_source", score=1.0)])
        assert result[0].score == pytest.approx(0.70)

    def test_preserves_relative_ordering(self):
        """After multipliers, notes should still score higher than books on equal input."""
        notes_p = _parent("note", score=1.0)
        books_p = _parent("book_chapter", score=1.0)
        result = apply_trust_multipliers([notes_p, books_p])
        assert result[0].score > result[1].score

    def test_does_not_mutate_input(self):
        p = _parent("note", score=0.9)
        apply_trust_multipliers([p])
        assert p.score == pytest.approx(0.9)

    def test_empty_list(self):
        assert apply_trust_multipliers([]) == []

    def test_ordering_matches_trust_hierarchy(self):
        """note > book_chapter > web_saved > tier1 > unknown."""
        types = ["note", "book_chapter", "web_saved", "tier1", "unknown_type"]
        parents = [_parent(t, score=1.0) for t in types]
        result = apply_trust_multipliers(parents)
        scores = [r.score for r in result]
        assert scores == sorted(scores, reverse=True)
