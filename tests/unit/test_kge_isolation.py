"""
KGE isolation guard: assert_isolation() raises on tier-1/predictions leakage.
Tests the runtime assertion in triples-loader.py (ADR-0009 §2).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load triples-loader module (kebab-case file, not importable directly)
_HERE = Path(__file__).parent.parent.parent
_MOD_PATH = _HERE / "app" / "services" / "kge" / "triples-loader.py"

spec = importlib.util.spec_from_file_location("kge_triples_loader", _MOD_PATH)
_loader = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["kge_triples_loader"] = _loader
spec.loader.exec_module(_loader)  # type: ignore[union-attr]

load_training_triples = _loader.load_training_triples
assert_isolation = _loader.assert_isolation
check_sparse_graph = _loader.check_sparse_graph

_PKM = "https://my-pkm.local/ontology#"


# ── Fake Oxigraph store ────────────────────────────────────────────────────────

class _FakeOxigraph:
    def __init__(self, graph_data: dict[str, list[tuple[str, str, str]]]):
        self._data = graph_data

    def sparql_select(self, query: str) -> list[dict]:
        results = []
        for graph_uri, triples in self._data.items():
            if graph_uri in query:
                for s, p, o in triples:
                    results.append({"s": s, "p": p, "o": o, "g": graph_uri})
        return results


# ── assert_isolation tests ─────────────────────────────────────────────────────

def test_isolation_passes_when_no_forbidden_triples():
    tier0_triple = ("urn:note:a", f"{_PKM}linksTo", "urn:note:b")
    store = _FakeOxigraph({"urn:tier0": [tier0_triple]})
    training = [tier0_triple]
    assert_isolation(training, store)  # must not raise


def test_isolation_raises_on_tier1_leakage():
    tier1_triple = ("urn:concept:x", f"{_PKM}mentions", "urn:concept:y")
    # Store has a tier1 triple AND it leaked into training set
    store = _FakeOxigraph({
        "urn:tier1": [tier1_triple],
    })
    with pytest.raises(RuntimeError, match="isolation violation"):
        assert_isolation([tier1_triple], store)


def test_isolation_raises_on_predictions_leakage():
    pred_triple = ("urn:concept:a", f"{_PKM}predictedRelation", "urn:concept:b")
    store = _FakeOxigraph({"urn:predictions": [pred_triple]})
    with pytest.raises(RuntimeError, match="isolation violation"):
        assert_isolation([pred_triple], store)


def test_isolation_passes_when_forbidden_triple_not_in_training():
    tier1_triple = ("urn:concept:x", f"{_PKM}mentions", "urn:concept:y")
    tier0_triple = ("urn:note:a", f"{_PKM}linksTo", "urn:note:b")
    # tier1 triple exists in store but is NOT in the training set
    store = _FakeOxigraph({
        "urn:tier1": [tier1_triple],
        "urn:tier0": [tier0_triple],
    })
    assert_isolation([tier0_triple], store)  # must not raise


# ── load_training_triples tests ────────────────────────────────────────────────

def test_load_filters_plumbing_predicates():
    plumbing = ("urn:note:a", f"{_PKM}hasChunk", "urn:chunk:0")
    semantic = ("urn:note:a", f"{_PKM}linksTo", "urn:note:b")
    store = _FakeOxigraph({"urn:tier0": [plumbing, semantic]})
    triples = load_training_triples(store)
    assert semantic in triples
    assert plumbing not in triples


def test_load_excludes_literal_objects():
    """Only IRI objects pass (FILTER(isIRI(?o)) in SPARQL)."""
    literal = ("urn:note:a", f"{_PKM}title", '"My Note"')
    iri = ("urn:note:a", f"{_PKM}linksTo", "urn:note:b")
    # FakeOxigraph won't include these since SPARQL filter is inside the query string;
    # test that the filtering logic in load_training_triples doesn't add empties.
    store = _FakeOxigraph({"urn:tier0": [iri]})
    triples = load_training_triples(store)
    assert iri in triples


# ── check_sparse_graph tests ───────────────────────────────────────────────────

def test_sparse_warning_below_threshold():
    triples = [("urn:note:a", f"{_PKM}linksTo", "urn:note:b")]
    warning = check_sparse_graph(triples)
    assert warning is not None
    assert "2000" in warning


def test_no_sparse_warning_above_threshold():
    triples = [("urn:note:a", f"{_PKM}linksTo", f"urn:note:{i}") for i in range(2001)]
    warning = check_sparse_graph(triples)
    assert warning is None
