"""
Verify Tier-1 isolation from KGE training set (ADR-0008 §5, ADR-0009 §2).

KGE training must use ONLY <urn:tier0> ∪ <urn:tier2>.
Tier-1 triples must never appear in triples_factory input.
"""
from __future__ import annotations

import pytest

_PKM = "https://my-pkm.local/ontology#"

# ── Fixtures ──────────────────────────────────────────────────────────────────

# Simulated Oxigraph store with triples across three named graphs
class _FakeOxigraph:
    def __init__(self, triples: dict[str, list[tuple]]):
        # triples: {graph_uri: [(s, p, o), ...]}
        self._triples = triples

    def sparql_select(self, query: str) -> list[dict]:
        # Minimal implementation: detect GRAPH clause and return matching triples
        results = []
        for graph_uri, rows in self._triples.items():
            # Only include graphs explicitly listed in the FROM/GRAPH clause
            if graph_uri in query:
                for s, p, o in rows:
                    results.append({"s": s, "p": p, "o": o, "g": graph_uri})
        return results


# ── KGE training set filter (mirrors phase-12 logic) ─────────────────────────

_KGE_TRAINING_GRAPHS = ("urn:tier0", "urn:tier2")

_KGE_INCLUDE_PREDICATES = frozenset([
    f"{_PKM}tagged",
    f"{_PKM}inProject",
    f"{_PKM}linksTo",
    f"{_PKM}hasChapter",
    f"{_PKM}authoredBy",
    "http://www.w3.org/2004/02/skos/core#related",
    "http://www.w3.org/2004/02/skos/core#broader",
    "http://www.w3.org/2004/02/skos/core#narrower",
])

_KGE_EXCLUDE_PREDICATES = frozenset([
    "http://purl.org/dc/terms/created",
    "http://purl.org/dc/terms/modified",
    f"{_PKM}hasChunk",
    f"{_PKM}hasHeading",
    f"{_PKM}fromURL",
    f"{_PKM}provenance",
    f"{_PKM}confidence",
    f"{_PKM}source",
])

_KGE_TRAINING_SPARQL = """
SELECT ?s ?p ?o WHERE {
  GRAPH ?g { ?s ?p ?o }
  FILTER(?g IN (<urn:tier0>, <urn:tier2>))
  FILTER(?p NOT IN (
    <http://purl.org/dc/terms/created>,
    <http://purl.org/dc/terms/modified>,
    <https://my-pkm.local/ontology#hasChunk>,
    <https://my-pkm.local/ontology#hasHeading>,
    <https://my-pkm.local/ontology#fromURL>,
    <https://my-pkm.local/ontology#provenance>,
    <https://my-pkm.local/ontology#confidence>,
    <https://my-pkm.local/ontology#source>
  ))
}
"""


def _build_training_triples(oxigraph) -> list[tuple[str, str, str]]:
    """Replicate the KGE training set filter from phase-12."""
    rows = oxigraph.sparql_select(_KGE_TRAINING_SPARQL)
    return [
        (r["s"], r["p"], r["o"])
        for r in rows
        if r["p"] not in _KGE_EXCLUDE_PREDICATES
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_tier1_triples_absent_from_training_set():
    """Core isolation: tier1 triples must never reach triples_factory."""
    tier0_triple = ("urn:note:v:a.md", f"{_PKM}linksTo", "urn:note:v:b.md")
    tier1_triple = ("urn:concept:x", f"{_PKM}mentions", "urn:concept:y")
    tier2_triple = ("urn:concept:x", "http://www.wikidata.org/prop/direct/P31", "urn:wd:Q5")

    oxigraph = _FakeOxigraph({
        "urn:tier0": [tier0_triple],
        "urn:tier1": [tier1_triple],
        "urn:tier2": [tier2_triple],
    })

    training = _build_training_triples(oxigraph)
    training_set = set(training)

    assert tier0_triple in training_set, "tier0 triple should be in training set"
    assert tier2_triple in training_set, "tier2 triple should be in training set"
    assert tier1_triple not in training_set, "tier1 triple must NOT be in training set"


def test_tier1_pending_absent_from_training_set():
    """Pending triples are also excluded."""
    pending_triple = ("urn:concept:p", f"{_PKM}explains", "urn:concept:q")

    oxigraph = _FakeOxigraph({
        "urn:tier0": [("urn:note:v:a.md", f"{_PKM}tagged", "urn:tag:python")],
        "urn:tier1-pending": [pending_triple],
    })

    training = _build_training_triples(oxigraph)
    assert pending_triple not in set(training)


def test_plumbing_predicates_excluded():
    """Internal predicates (hasChunk, hasHeading, etc.) are stripped."""
    plumbing = ("urn:note:v:a.md", f"{_PKM}hasChunk", "urn:chunk:sha:0")
    real = ("urn:note:v:a.md", f"{_PKM}linksTo", "urn:note:v:b.md")

    oxigraph = _FakeOxigraph({
        "urn:tier0": [plumbing, real],
    })

    training = _build_training_triples(oxigraph)
    training_set = set(training)

    assert plumbing not in training_set, "hasChunk is a plumbing predicate — excluded"
    assert real in training_set


def test_predictions_graph_excluded():
    """KGE predictions graph must never feed back into training (no self-reinforcement)."""
    pred_triple = ("urn:concept:a", f"{_PKM}predictedRelation", "urn:concept:b")

    oxigraph = _FakeOxigraph({
        "urn:tier0": [("urn:note:v:a.md", f"{_PKM}tagged", "urn:tag:ml")],
        "urn:predictions": [pred_triple],
    })

    training = _build_training_triples(oxigraph)
    assert pred_triple not in set(training)


def test_empty_tier0_and_tier2_yields_no_triples():
    oxigraph = _FakeOxigraph({
        "urn:tier1": [("urn:concept:x", f"{_PKM}mentions", "urn:concept:y")],
    })
    training = _build_training_triples(oxigraph)
    assert training == []
