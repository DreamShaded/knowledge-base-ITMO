"""Unit tests for Wikidata disambiguation scoring (ADR-0008 §4 task 2, phase-11)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.wikidata.disambiguation import (
    _context_overlap,
    _score,
    _string_match,
    _type_match,
    disambiguate,
)
from app.services.wikidata.schemas import EntityCandidate, WikidataSearchResult


# ── string_match ──────────────────────────────────────────────────────────────

class TestStringMatch:
    def test_exact_match_returns_one(self):
        assert _string_match("Eric Evans", "Eric Evans") == pytest.approx(1.0)

    def test_case_insensitive(self):
        assert _string_match("eric evans", "Eric Evans") == pytest.approx(1.0)

    def test_partial_match_below_one(self):
        score = _string_match("Eric Evans", "Eric")
        assert 0.0 < score < 1.0

    def test_no_overlap_near_zero(self):
        score = _string_match("Eric Evans", "Python")
        assert score < 0.5

    def test_empty_surface_empty_label(self):
        assert _string_match("", "") == pytest.approx(1.0)

    def test_substring_match_scores_reasonably(self):
        score = _string_match("Domain-Driven Design", "Domain-Driven Design book")
        assert score > 0.7


# ── type_match ────────────────────────────────────────────────────────────────

class TestTypeMatch:
    def test_person_hint_matches_human_description(self):
        score = _type_match("Person", "American software engineer and author")
        assert score == pytest.approx(1.0)

    def test_person_hint_misses_software_description(self):
        score = _type_match("Person", "open-source software framework")
        assert score < 0.5

    def test_software_hint_matches_framework(self):
        score = _type_match("Software", "programming framework for Java")
        assert score == pytest.approx(1.0)

    def test_no_type_hint_neutral(self):
        score = _type_match("", "anything here")
        assert score == pytest.approx(0.5)

    def test_concept_hint_neutral(self):
        score = _type_match("Concept", "some description")
        assert score == pytest.approx(0.5)

    def test_book_hint_matches_publication(self):
        score = _type_match("Book", "influential publication on software design")
        assert score == pytest.approx(1.0)


# ── context_overlap ───────────────────────────────────────────────────────────

class TestContextOverlap:
    def test_no_context_chunks_returns_neutral(self):
        score = _context_overlap([], "American software engineer")
        assert score == pytest.approx(0.5)

    def test_empty_description_returns_neutral(self):
        score = _context_overlap(["domain driven design"], "")
        assert score == pytest.approx(0.5)

    def test_high_overlap_scores_above_neutral(self):
        ctx = ["domain driven design patterns aggregates bounded context"]
        desc = "author of domain driven design book about bounded context"
        score = _context_overlap(ctx, desc)
        assert score > 0.5

    def test_zero_overlap_stays_low(self):
        ctx = ["cooking recipes pasta carbonara"]
        desc = "American software engineer at ThoughtWorks"
        score = _context_overlap(ctx, desc)
        assert score < 0.5


# ── composite score ───────────────────────────────────────────────────────────

class TestCompositeScore:
    def _candidate(self, surface: str, type_hint: str = "", ctx: list[str] | None = None):
        return EntityCandidate(surface_form=surface, type_hint=type_hint, context_chunks=ctx or [])

    def _hit(self, label: str, desc: str = "") -> WikidataSearchResult:
        return WikidataSearchResult(qid="Q1", label=label, description=desc)

    def test_perfect_match_near_one(self):
        c = self._candidate("Eric Evans", "Person", ["domain driven design"])
        h = self._hit("Eric Evans", "American software engineer and author")
        assert _score(c, h) > 0.75

    def test_mismatch_scores_low(self):
        c = self._candidate("Eric Evans", "Person")
        h = self._hit("Python programming language", "high-level software language")
        assert _score(c, h) < 0.4

    def test_string_match_dominates(self):
        c = self._candidate("Eric Evans", "")
        exact = _score(c, self._hit("Eric Evans"))
        partial = _score(c, self._hit("Eric"))
        assert exact > partial


# ── disambiguate (integration, mocked network) ────────────────────────────────

class TestDisambiguate:
    @pytest.mark.asyncio
    async def test_auto_link_above_threshold(self):
        candidate = EntityCandidate(surface_form="Eric Evans", type_hint="Person")
        mock_results = [{
            "id": "Q1374524",
            "label": "Eric Evans",
            "description": "American software engineer and author",
            "aliases": [],
        }]
        with patch(
            "app.services.wikidata.disambiguation.wd_client.search_entities",
            new=AsyncMock(return_value=mock_results),
        ):
            result = await disambiguate(candidate)

        assert result is not None
        assert result.qid == "Q1374524"
        assert result.method == "auto"
        assert result.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_no_results_returns_none(self):
        candidate = EntityCandidate(surface_form="Xyzzy Nonexistent")
        with patch(
            "app.services.wikidata.disambiguation.wd_client.search_entities",
            new=AsyncMock(return_value=[]),
        ):
            result = await disambiguate(candidate)
        assert result is None

    @pytest.mark.asyncio
    async def test_low_confidence_without_ollama_returns_none(self):
        candidate = EntityCandidate(surface_form="Eric", type_hint="")
        mock_results = [{"id": "Q999", "label": "Totally Different Name", "description": "", "aliases": []}]
        with patch(
            "app.services.wikidata.disambiguation.wd_client.search_entities",
            new=AsyncMock(return_value=mock_results),
        ):
            result = await disambiguate(candidate, ollama_host=None)
        assert result is None
