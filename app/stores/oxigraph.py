"""
Oxigraph store init, health, and basic SPARQL query helper (ADR-0011).
Named graphs: <urn:ontology>, <urn:tier0>, <urn:tier1>, <urn:tier1-pending>,
              <urn:tier2>, <urn:predictions>.
Phase-01: init, health, SPARQL read stub. Triple writes in phase-04.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.logging import get_logger

log = get_logger(__name__)

NAMED_GRAPHS = [
    "urn:ontology",
    "urn:tier0",
    "urn:tier1",
    "urn:tier1-pending",
    "urn:tier2",
    "urn:predictions",
]


@dataclass
class OxigraphStore:
    path: str
    _store: Any = field(default=None, init=False, repr=False)

    def init(self) -> None:
        import pyoxigraph
        Path(self.path).mkdir(parents=True, exist_ok=True)
        self._store = pyoxigraph.Store(self.path)
        log.info("opened", path=self.path)

    def health(self) -> dict:
        if self._store is None:
            return {"status": "not_initialized"}
        try:
            # Count triples across all named graphs as a liveness check
            result = list(self._store.query("SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"))
            val = result[0]["n"] if result else None
            # pyoxigraph Literal has .value; fall back to str parsing
            count = int(val.value if hasattr(val, "value") else str(val)) if val is not None else 0
            return {"status": "ok", "triple_count": count}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def sparql_select(self, query: str) -> list[dict[str, str]]:
        """Execute a read-only SPARQL SELECT and return rows as dicts."""
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        solutions = self._store.query(query)
        variables = [v.value for v in solutions.variables]
        rows = []
        for sol in solutions:
            rows.append({var: (sol[i].value if sol[i] is not None else "") for i, var in enumerate(variables)})
        return rows

    def load_ttl(self, turtle_str: str, graph_uri: str) -> None:
        """Load Turtle-encoded triples into a named graph."""
        import pyoxigraph
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        self._store.load(
            turtle_str.encode(),
            format=pyoxigraph.RdfFormat.TURTLE,
            to_graph=pyoxigraph.NamedNode(graph_uri),
        )

    def add_quads(self, quads: list) -> None:
        """Bulk-add pyoxigraph Quad objects (phase-04 tier0 writes)."""
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        self._store.extend(quads)

    def sparql_update(self, update: str) -> None:
        """Execute a SPARQL 1.1 UPDATE atomically."""
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        self._store.update(update)

    def delete_note_triples(self, note_uri: str) -> None:
        """Atomically delete all Tier-0 triples for a note (subject + its headings)."""
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        pkm = "https://my-pkm.local/ontology#"
        # Delete heading nodes first, then the note's own triples
        self._store.update(f"""
            DELETE {{ GRAPH <urn:tier0> {{ ?h ?hp ?ho }} }}
            WHERE  {{ GRAPH <urn:tier0> {{ <{note_uri}> <{pkm}hasHeading> ?h . ?h ?hp ?ho }} }}
        """)
        self._store.update(
            f"DELETE WHERE {{ GRAPH <urn:tier0> {{ <{note_uri}> ?p ?o }} }}"
        )

    @property
    def store(self):
        if self._store is None:
            raise RuntimeError("OxigraphStore not initialized — call init() first")
        return self._store
