"""
Entity discovery: collect EntityCandidates from tier-1 graph, book metadata,
and high-frequency tags for Wikidata disambiguation (ADR-0008 §4 task 1).
Sources: pkm:mentions triples (tier-1), book authors (tier-0), top-K tags (tier-0).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.wikidata.schemas import EntityCandidate
from app.logging import get_logger

if TYPE_CHECKING:
    from app.stores.oxigraph import OxigraphStore

log = get_logger(__name__)

_PKM = "https://my-pkm.local/ontology#"

_TYPE_NORM: dict[str, str] = {
    "person": "Person", "human": "Person", "author": "Person",
    "software": "Software", "tool": "Software", "framework": "Software",
    "concept": "Concept", "term": "Concept",
    "book": "Book", "publication": "Book",
    "organization": "Organization", "org": "Organization", "company": "Organization",
    "event": "Event", "conference": "Event",
}


def discover_entities(oxigraph: "OxigraphStore", top_k: int = 50) -> list[EntityCandidate]:
    """
    Collect EntityCandidates from three sources.
    Scoped strictly to tier-0 and tier-1 — never reads tier-2
    (enforces 1-hop rule: discovered values are not re-enriched).
    """
    candidates: dict[str, EntityCandidate] = {}
    _collect_mentions(oxigraph, candidates)
    _collect_book_authors(oxigraph, candidates)
    _collect_highfreq_tags(oxigraph, candidates, top_k)
    log.info("wikidata_discovery", count=len(candidates))
    return list(candidates.values())


def _collect_mentions(oxigraph: "OxigraphStore", out: dict[str, EntityCandidate]) -> None:
    sparql = f"""
    SELECT ?label ?typeHint WHERE {{
        GRAPH <urn:tier1> {{
            ?subj <{_PKM}mentions> ?label .
            OPTIONAL {{ ?subj <{_PKM}typeHint> ?typeHint . }}
        }}
    }} LIMIT 500
    """
    try:
        for row in oxigraph.sparql_select(sparql):
            label = row.get("label", "").strip()
            if not label:
                continue
            key = label.lower()
            if key not in out:
                out[key] = EntityCandidate(
                    surface_form=label,
                    type_hint=_norm_type(row.get("typeHint", "")),  # type: ignore[arg-type]
                )
    except Exception as exc:
        log.warning("wikidata_collect_mentions_error", error=str(exc))


def _collect_book_authors(oxigraph: "OxigraphStore", out: dict[str, EntityCandidate]) -> None:
    sparql = f"""
    SELECT DISTINCT ?author WHERE {{
        GRAPH <urn:tier0> {{
            ?book <{_PKM}author> ?author .
        }}
    }} LIMIT 200
    """
    try:
        for row in oxigraph.sparql_select(sparql):
            name = row.get("author", "").strip()
            if not name:
                continue
            key = name.lower()
            if key not in out:
                out[key] = EntityCandidate(surface_form=name, type_hint="Person")
    except Exception as exc:
        log.warning("wikidata_collect_authors_error", error=str(exc))


def _collect_highfreq_tags(
    oxigraph: "OxigraphStore", out: dict[str, EntityCandidate], top_k: int
) -> None:
    sparql = f"""
    SELECT ?tag (COUNT(?tag) AS ?cnt) WHERE {{
        GRAPH <urn:tier0> {{
            ?chunk <{_PKM}tag> ?tag .
        }}
    }} GROUP BY ?tag ORDER BY DESC(?cnt) LIMIT {top_k}
    """
    try:
        for row in oxigraph.sparql_select(sparql):
            tag = row.get("tag", "").strip()
            if not tag:
                continue
            key = tag.lower()
            if key not in out:
                out[key] = EntityCandidate(surface_form=tag, type_hint="Concept")
    except Exception as exc:
        log.warning("wikidata_collect_tags_error", error=str(exc))


def _norm_type(raw: str) -> str:
    return _TYPE_NORM.get(raw.lower().strip(), "")
