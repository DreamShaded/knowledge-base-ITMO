"""
L3 fetcher: lazy runtime enrichment, RAG-time only (ADR-0008 §4 task 5).
Fetches extended description + up to 2-hop P279 hierarchy.
NOT written to graph — returned as runtime context only.
Cache: 7 days in SQLite.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.services.wikidata import client as wd_client
from app.services.wikidata.schemas import WikidataL3
from app.logging import get_logger

if TYPE_CHECKING:
    from app.services.wikidata.cache import WikidataCache

log = get_logger(__name__)

_WD_ENTITY = "http://www.wikidata.org/entity/"
_QID_RE    = re.compile(r"^Q\d+$")

_DESC_SPARQL = """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX schema: <http://schema.org/>

SELECT ?desc WHERE {{
  wd:{qid} schema:description ?desc .
  FILTER(LANG(?desc) = "en")
}} LIMIT 1
"""

_HIERARCHY_SPARQL = """
PREFIX wd:   <http://www.wikidata.org/entity/>
PREFIX wdt:  <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?ancestor ?lbl WHERE {{
  {{wd:{qid} wdt:P279 ?ancestor .}}
  UNION
  {{wd:{qid} wdt:P279 ?mid . ?mid wdt:P279 ?ancestor .}}
  OPTIONAL {{ ?ancestor rdfs:label ?lbl . FILTER(LANG(?lbl) = "en") }}
}} LIMIT 20
"""


async def fetch_l3(qid: str, cache: "WikidataCache") -> WikidataL3 | None:
    """
    Fetch L3 lazy enrichment for `qid`.
    Returns cached result if fresh (7d TTL).
    Does NOT write to graph — caller uses data as runtime context only.
    """
    if not _QID_RE.match(qid):
        log.warning("wikidata_invalid_qid", qid=qid, fetcher="L3")
        return None

    cached = cache.get(qid, "L3")
    if cached:
        return WikidataL3(**cached)

    desc = await _fetch_description(qid)
    hierarchy = await _fetch_hierarchy(qid)

    l3 = WikidataL3(qid=qid, extended_description=desc, deep_hierarchy=hierarchy)
    cache.set(qid, "L3", l3.model_dump())
    log.debug("wikidata_l3_fetched", qid=qid, hierarchy_size=len(hierarchy))
    return l3


async def _fetch_description(qid: str) -> str:
    bindings = await wd_client.sparql_query(_DESC_SPARQL.format(qid=qid))
    if not bindings:
        return ""
    return bindings[0].get("desc", {}).get("value", "")


async def _fetch_hierarchy(qid: str) -> list[str]:
    bindings = await wd_client.sparql_query(_HIERARCHY_SPARQL.format(qid=qid))
    labels: list[str] = []
    seen: set[str] = set()
    for row in bindings:
        lbl = row.get("lbl", {}).get("value", "")
        if lbl and lbl not in seen:
            seen.add(lbl)
            labels.append(lbl)
    return labels
