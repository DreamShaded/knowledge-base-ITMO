"""Unit tests for OpenIE 5-gate pipeline (phase-10, ADR-0008 §3)."""
from __future__ import annotations

import pytest

from app.services.openie.gates import (
    gate1_adaptive_confidence,
    gate2_predicate_whitelist,
    gate3_canonicalize,
    gate4_cross_source,
    gate5_sanity,
    _HARD_REJECT,
    _CANON_AUTO,
    _CANON_GREY_LOW,
)
from app.services.openie.schemas import CandidateTriple

_PKM = "https://my-pkm.local/ontology#"
_SKOS = "http://www.w3.org/2004/02/skos/core#"


def _triple(**kwargs) -> CandidateTriple:
    defaults = dict(
        subject_uri="urn:concept:a",
        predicate=f"{_PKM}mentions",
        object_uri_or_literal="urn:concept:b",
        evidence_chunk_id="urn:chunk:x:0",
        confidence_self_reported=0.90,
        claim_type="factual_general",
    )
    defaults.update(kwargs)
    return CandidateTriple(**defaults)


# ── Gate 1 ────────────────────────────────────────────────────────────────────

class TestGate1:
    def test_passes_above_threshold(self):
        t = _triple(predicate=f"{_PKM}mentions", confidence_self_reported=0.85)
        g = gate1_adaptive_confidence(t)
        assert g.passed

    def test_fails_below_predicate_threshold(self):
        t = _triple(predicate=f"{_SKOS}broader", confidence_self_reported=0.85)
        # skos:broader threshold = 0.90
        g = gate1_adaptive_confidence(t)
        assert not g.passed

    def test_hard_reject_below_0_60(self):
        t = _triple(confidence_self_reported=0.55)
        g = gate1_adaptive_confidence(t)
        assert not g.passed
        assert str(_HARD_REJECT) in g.reason or "hard_reject" in g.reason

    def test_new_entity_raises_threshold(self):
        # mentions threshold = 0.80; with new_entity bump becomes 0.85
        t = _triple(
            predicate=f"{_PKM}mentions",
            confidence_self_reported=0.82,
            is_new_subject=True,
        )
        g = gate1_adaptive_confidence(t)
        assert not g.passed  # 0.82 < 0.85

    def test_opinion_threshold_0_95(self):
        t = _triple(claim_type="opinion", confidence_self_reported=0.93)
        g = gate1_adaptive_confidence(t)
        assert not g.passed

    def test_framing_lower_threshold(self):
        # framing = 0.70; predicate mentions = 0.80 → effective = max(0.80, 0.70) = 0.80
        t = _triple(claim_type="framing", confidence_self_reported=0.75)
        g = gate1_adaptive_confidence(t)
        assert not g.passed  # 0.75 < 0.80

    def test_passes_exact_threshold(self):
        t = _triple(predicate=f"{_PKM}mentions", confidence_self_reported=0.80)
        g = gate1_adaptive_confidence(t)
        assert g.passed


# ── Gate 2 ────────────────────────────────────────────────────────────────────

class TestGate2:
    _WHITELIST = frozenset([
        f"{_PKM}mentions",
        f"{_SKOS}related",
    ])

    def test_allowed_predicate_passes(self):
        t = _triple(predicate=f"{_PKM}mentions")
        g = gate2_predicate_whitelist(t, self._WHITELIST)
        assert g.passed

    def test_unknown_predicate_fails(self):
        t = _triple(predicate=f"{_PKM}tagged")
        g = gate2_predicate_whitelist(t, self._WHITELIST)
        assert not g.passed
        assert "not whitelisted" in g.reason

    def test_empty_whitelist_rejects_all(self):
        t = _triple(predicate=f"{_PKM}mentions")
        g = gate2_predicate_whitelist(t, frozenset())
        assert not g.passed


# ── Gate 3 ────────────────────────────────────────────────────────────────────

class TestGate3:
    def test_auto_canonicalize_high_similarity(self):
        t = _triple()
        g = gate3_canonicalize(t, "urn:concept:existing", 0.95, None, 0.0)
        assert g.passed
        assert g.canonical_uri == "urn:concept:existing"

    def test_grey_zone_sends_to_pending(self):
        t = _triple()
        g = gate3_canonicalize(t, "urn:concept:similar", 0.80, None, 0.0)
        assert not g.passed  # grey zone → pending
        assert "grey_zone" in g.reason

    def test_below_grey_zone_is_new_entity(self):
        t = _triple()
        g = gate3_canonicalize(t, None, 0.60, None, 0.0)
        # no canonical URI, no grey zone → passes (new entity allowed)
        assert g.passed
        assert g.canonical_uri is None

    def test_object_grey_zone_also_pending(self):
        t = _triple()
        g = gate3_canonicalize(t, None, 0.0, "urn:concept:obj", 0.82)
        assert not g.passed


# ── Gate 4 ────────────────────────────────────────────────────────────────────

class TestGate4:
    def test_single_source_no_boost(self):
        t = _triple(confidence_self_reported=0.80)
        g = gate4_cross_source(t, n_sources=1)
        assert g.passed
        assert g.modified_confidence == pytest.approx(0.80)
        assert "single_source" in g.reason

    def test_two_sources_boost_0_10(self):
        t = _triple(confidence_self_reported=0.80)
        g = gate4_cross_source(t, n_sources=2)
        assert g.passed
        assert g.modified_confidence == pytest.approx(0.90)

    def test_boost_capped_at_1_0(self):
        t = _triple(confidence_self_reported=0.95)
        g = gate4_cross_source(t, n_sources=5)
        assert g.modified_confidence == pytest.approx(1.0)


# ── Gate 5 ────────────────────────────────────────────────────────────────────

class TestGate5:
    def test_self_loop_rejected(self):
        t = _triple(subject_uri="urn:a", object_uri_or_literal="urn:a")
        g = gate5_sanity(t)
        assert not g.passed
        assert "self_loop" in g.reason

    def test_stop_word_rejected(self):
        t = _triple(subject_uri="urn:concept:thing")
        g = gate5_sanity(t)
        assert not g.passed
        assert "stop_word" in g.reason

    def test_tautology_rdf_type_note_rejected(self):
        t = _triple(
            predicate="http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
            object_uri_or_literal="https://my-pkm.local/ontology#Note",
        )
        g = gate5_sanity(t)
        assert not g.passed
        assert "tautology" in g.reason

    def test_duplicate_rejected(self):
        t = _triple()
        g = gate5_sanity(t, duplicate_exists=True)
        assert not g.passed
        assert "duplicate" in g.reason

    def test_conflict_passes_with_flag(self):
        t = _triple()
        g = gate5_sanity(t, conflict_exists=True)
        assert g.passed
        assert "conflict" in g.reason

    def test_clean_triple_passes(self):
        t = _triple()
        g = gate5_sanity(t)
        assert g.passed
        assert g.reason == ""
