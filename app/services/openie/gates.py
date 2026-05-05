"""
5-gate auto-promotion checks for Tier-1 OpenIE (ADR-0008 §3).

All gates are pure functions — no I/O. Testable in isolation.
Context-dependent inputs (n_sources, duplicate_exists, etc.) are injected by the caller.
"""
from __future__ import annotations

from app.services.openie.schemas import CandidateTriple, GateResult

_PKM = "https://my-pkm.local/ontology#"
_SKOS = "http://www.w3.org/2004/02/skos/core#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# ── Gate 1: Adaptive confidence ───────────────────────────────────────────────

# Per-predicate confidence floors (ADR-0008 §3 table)
_PREDICATE_THRESHOLDS: dict[str, float] = {
    f"{_PKM}mentions":    0.80,
    f"{_PKM}defines":     0.85,
    f"{_PKM}explains":    0.85,
    f"{_PKM}cites":       0.85,
    f"{_PKM}exemplifies": 0.85,
    f"{_PKM}contradicts": 0.85,
    f"{_PKM}precedes":    0.85,
    f"{_PKM}causes":      0.85,
    f"{_PKM}dependsOn":   0.85,
    f"{_PKM}authoredBy":  0.90,
    f"{_SKOS}related":    0.85,
    f"{_SKOS}broader":    0.90,
    f"{_SKOS}narrower":   0.90,
}

# Per-claim-type floors (phase-10 spec)
_CLAIM_TYPE_THRESHOLDS: dict[str, float] = {
    "factual_specific": 0.85,
    "factual_general":  0.80,
    "framing":          0.70,
    "opinion":          0.95,
}

_NEW_ENTITY_BUMP = 0.05   # threshold raised when subject or object is new
_HARD_REJECT = 0.60       # hard reject regardless of predicate/claim type


def gate1_adaptive_confidence(triple: CandidateTriple) -> GateResult:
    """Confidence must meet the effective threshold for its predicate + claim type."""
    pred_t = _PREDICATE_THRESHOLDS.get(triple.predicate, 0.85)
    claim_t = _CLAIM_TYPE_THRESHOLDS.get(triple.claim_type, 0.85)
    effective = max(pred_t, claim_t)
    if triple.is_new_subject or triple.is_new_object:
        effective = min(effective + _NEW_ENTITY_BUMP, 1.0)

    conf = triple.confidence_self_reported
    if conf < _HARD_REJECT:
        return GateResult(
            passed=False,
            gate_id=1,
            reason=f"conf {conf:.2f} < hard_reject {_HARD_REJECT}",
            modified_confidence=conf,
        )

    passed = conf >= effective
    return GateResult(
        passed=passed,
        gate_id=1,
        reason="" if passed else f"conf {conf:.2f} < threshold {effective:.2f}",
        modified_confidence=conf,
    )


# ── Gate 2: Predicate whitelist ───────────────────────────────────────────────

def gate2_predicate_whitelist(triple: CandidateTriple, allowed: frozenset[str]) -> GateResult:
    """Predicate must be in the curated Tier-1 whitelist (ADR-0008 §6)."""
    passed = triple.predicate in allowed
    return GateResult(
        passed=passed,
        gate_id=2,
        reason="" if passed else f"predicate not whitelisted: {triple.predicate!r}",
    )


# ── Gate 3: Embedding canonicalization ───────────────────────────────────────

# From ADR-0008 §3 + config TIER1_CANONICALIZATION_GREY_ZONE
_CANON_AUTO = 0.92      # ≥ 0.92 → auto-reuse existing URI
_CANON_GREY_LOW = 0.75  # 0.75–0.92 → grey zone → pending


def gate3_canonicalize(
    triple: CandidateTriple,
    subject_canonical: str | None,
    subject_similarity: float,
    object_canonical: str | None,
    object_similarity: float,
) -> GateResult:
    """Embedding dedup: grey-zone matches send triple to pending instead of auto-promote."""
    in_grey = (
        (subject_canonical is not None and _CANON_GREY_LOW <= subject_similarity < _CANON_AUTO)
        or (object_canonical is not None and _CANON_GREY_LOW <= object_similarity < _CANON_AUTO)
    )
    # Best canonical URI for subject if auto-matched
    canon_uri = (
        subject_canonical
        if subject_canonical and subject_similarity >= _CANON_AUTO
        else None
    )
    return GateResult(
        passed=not in_grey,
        gate_id=3,
        reason="grey_zone_canonicalization" if in_grey else "",
        canonical_uri=canon_uri,
    )


# ── Gate 4: Cross-source agreement boost ─────────────────────────────────────

_CROSS_SOURCE_BOOST = 0.10


def gate4_cross_source(triple: CandidateTriple, n_sources: int) -> GateResult:
    """Boost confidence for triples seen in ≥2 different source chunks."""
    boosted = min(
        triple.confidence_self_reported + _CROSS_SOURCE_BOOST * (n_sources - 1),
        1.0,
    )
    return GateResult(
        passed=True,  # gate 4 never rejects — only modifies confidence
        gate_id=4,
        reason="" if n_sources >= 2 else "single_source_no_boost",
        modified_confidence=boosted,
    )


# ── Gate 5: Sanity checks ─────────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "thing", "something", "someone", "anything", "anyone",
    "entity", "object", "concept", "item", "instance", "example",
    "one of", "some of",
})

_PKM_NOTE_URI = f"{_PKM}Note"
_RDF_TYPE = f"{_RDF}type"


def gate5_sanity(
    triple: CandidateTriple,
    *,
    duplicate_exists: bool = False,
    conflict_exists: bool = False,
) -> GateResult:
    """Self-loops, stop-words, tautologies, exact duplicates → reject. Conflict → pending."""
    subj_label = _label_from_uri(triple.subject_uri)
    obj_label = _label_from_uri(triple.object_uri_or_literal)

    if triple.subject_uri == triple.object_uri_or_literal:
        return GateResult(passed=False, gate_id=5, reason="self_loop")

    if subj_label in _STOP_WORDS or obj_label in _STOP_WORDS:
        return GateResult(
            passed=False,
            gate_id=5,
            reason=f"stop_word: {subj_label!r}/{obj_label!r}",
        )

    # Tautology: ?s rdf:type pkm:Note comes from Tier-0
    if triple.predicate == _RDF_TYPE and triple.object_uri_or_literal == _PKM_NOTE_URI:
        return GateResult(passed=False, gate_id=5, reason="tautology_rdf_type_note")

    if duplicate_exists:
        return GateResult(passed=False, gate_id=5, reason="exact_duplicate_in_graph")

    # Conflict: pass gate but mark for pending + conflict flag
    if conflict_exists:
        return GateResult(passed=True, gate_id=5, reason="conflict_flagged")

    return GateResult(passed=True, gate_id=5)


def _label_from_uri(uri: str) -> str:
    """Extract last fragment, path segment, or URN segment and lowercase it."""
    part = uri.rstrip("/")
    if "#" in part:
        fragment = part.split("#")[-1]
    elif "/" in part:
        fragment = part.split("/")[-1]
    else:
        fragment = part.split(":")[-1]  # URN: urn:concept:thing → "thing"
    return fragment.lower().replace("-", " ").replace("_", " ")
