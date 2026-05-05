"""
Load KGE training triples from Oxigraph (ADR-0009 §2).

Isolation guard: only <urn:tier0> ∪ <urn:tier2> may enter triples_factory.
Any tier-1 / predictions triple in the result is a bug → RuntimeError (fail-fast).
"""
from __future__ import annotations

from app.logging import get_logger

log = get_logger(__name__)

_PKM = "https://my-pkm.local/ontology#"

# Plumbing predicates that carry no semantic meaning for link prediction
_PLUMBING_PREDICATES = frozenset([
    "http://purl.org/dc/terms/created",
    "http://purl.org/dc/terms/modified",
    f"{_PKM}hasChunk",
    f"{_PKM}hasHeading",
    f"{_PKM}fromURL",
    f"{_PKM}provenance",
    f"{_PKM}confidence",
    f"{_PKM}source",
])

_TRAINING_SPARQL = f"""
SELECT ?s ?p ?o WHERE {{
  GRAPH ?g {{ ?s ?p ?o }}
  FILTER(?g IN (<urn:tier0>, <urn:tier2>))
  FILTER(isIRI(?o))
  FILTER(?p NOT IN (
    <http://purl.org/dc/terms/created>,
    <http://purl.org/dc/terms/modified>,
    <{_PKM}hasChunk>,
    <{_PKM}hasHeading>,
    <{_PKM}fromURL>,
    <{_PKM}provenance>,
    <{_PKM}confidence>,
    <{_PKM}source>
  ))
}}
"""

_FORBIDDEN_GRAPHS_SPARQL = """
SELECT ?s ?p ?o WHERE {
  GRAPH ?g { ?s ?p ?o }
  FILTER(?g IN (<urn:tier1>, <urn:tier1-pending>, <urn:tier1-rejected>, <urn:predictions>))
}
LIMIT 1
"""

_SPARSE_THRESHOLD = 2000


def load_training_triples(oxigraph) -> list[tuple[str, str, str]]:
    """Return (s, p, o) URI-only triples from tier0 ∪ tier2."""
    rows = oxigraph.sparql_select(_TRAINING_SPARQL)
    triples: list[tuple[str, str, str]] = []
    for row in rows:
        s, p, o = row.get("s", ""), row.get("p", ""), row.get("o", "")
        if s and p and o and p not in _PLUMBING_PREDICATES:
            triples.append((s, p, o))
    log.info("kge_triples_loaded", count=len(triples))
    return triples


def assert_isolation(triples: list[tuple[str, str, str]], oxigraph) -> None:
    """Fail-fast if any forbidden-graph triple leaked into the training set."""
    forbidden_rows = oxigraph.sparql_select(_FORBIDDEN_GRAPHS_SPARQL)
    if not forbidden_rows:
        return  # no forbidden triples in store at all

    training_set = set(triples)
    for row in forbidden_rows:
        t = (row.get("s", ""), row.get("p", ""), row.get("o", ""))
        if t in training_set:
            raise RuntimeError(
                f"KGE isolation violation: tier1/predictions triple found in training set: {t}. "
                "This is a bug — tier1 triples must never enter triples_factory (ADR-0009 §2)."
            )


def check_sparse_graph(triples: list[tuple[str, str, str]]) -> str | None:
    """Return warning string if graph is too sparse for meaningful RotatE training."""
    if len(triples) < _SPARSE_THRESHOLD:
        return (
            f"KGE training set has only {len(triples)} triples (<{_SPARSE_THRESHOLD}). "
            "RotatE may undertrain. Consider adding vault content or waiting for enrichment."
        )
    return None
