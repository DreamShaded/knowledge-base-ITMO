"""
Loads docs/ontology.ttl into Oxigraph named graph <urn:ontology> at startup.
Validator: warns if any ontology predicate is absent from the store
(predicates will be populated in phase-04; warning is informational in phase-01).
"""
from __future__ import annotations

from pathlib import Path

from app.logging import get_logger
from app.stores.oxigraph import OxigraphStore

log = get_logger(__name__)

ONTOLOGY_GRAPH = "urn:ontology"

# Tier-0/1/2 predicates declared in ontology.ttl that should eventually be used
_EXPECTED_PREDICATES = [
    "https://my-pkm.local/ontology#tagged",
    "https://my-pkm.local/ontology#inProject",
    "https://my-pkm.local/ontology#inVault",
    "https://my-pkm.local/ontology#linksTo",
    "https://my-pkm.local/ontology#hasChapter",
    "https://my-pkm.local/ontology#hasSection",
    "https://my-pkm.local/ontology#hasChunk",
    "https://my-pkm.local/ontology#authoredBy",
    "https://my-pkm.local/ontology#mentions",
    "https://my-pkm.local/ontology#defines",
    "https://my-pkm.local/ontology#explains",
    "https://my-pkm.local/ontology#cites",
]


def load_ontology(store: OxigraphStore, ontology_path: Path) -> None:
    """Parse ontology.ttl and load into <urn:ontology> named graph."""
    if not ontology_path.exists():
        log.warning("file_not_found", path=str(ontology_path))
        return

    ttl = ontology_path.read_text(encoding="utf-8")
    store.load_ttl(ttl, ONTOLOGY_GRAPH)
    log.info("loaded", graph=ONTOLOGY_GRAPH, path=str(ontology_path))

    _validate_predicates(store)


def _validate_predicates(store: OxigraphStore) -> None:
    """Warn about predicates defined in ontology but not yet used in Tier-0/1/2 graphs."""
    used_query = """
    SELECT DISTINCT ?p WHERE {
        GRAPH ?g { ?s ?p ?o }
        FILTER(?g != <urn:ontology>)
    }
    """
    try:
        used = {row["p"] for row in store.sparql_select(used_query)}
    except Exception as exc:
        log.warning("validation_skipped", error=str(exc))
        return

    missing = [p for p in _EXPECTED_PREDICATES if p not in used]
    if missing:
        # Expected in phase-01: all predicates unused until phase-04
        log.debug(
            "predicates_not_yet_used",
            count=len(missing),
            note="will be populated from phase-04 onwards",
        )
