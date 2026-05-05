"""
L1 fetcher: always-fetched base properties for any Wikidata entity (ADR-0008 §4 task 3).
Properties: P31 (instance of), P279 (subclass of), schema:description, rdfs:label, skos:altLabel.
Cache: 30 days in SQLite. Returns WikidataL1 with parsed labels/descriptions/qids.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.services.wikidata import client as wd_client
from app.services.wikidata.schemas import WikidataL1
from app.logging import get_logger

if TYPE_CHECKING:
    from app.services.wikidata.cache import WikidataCache

log = get_logger(__name__)

_WD_ENTITY = "http://www.wikidata.org/entity/"
_WD_PROP   = "http://www.wikidata.org/prop/direct/"
_QID_RE    = re.compile(r"^Q\d+$")

_L1_SPARQL = """
PREFIX wd:   <http://www.wikidata.org/entity/>
PREFIX wdt:  <http://www.wikidata.org/prop/direct/>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?p ?o WHERE {{
  wd:{qid} ?p ?o .
  FILTER(?p IN (wdt:P31, wdt:P279, schema:description, rdfs:label, skos:altLabel))
  FILTER(!isLiteral(?o) || LANG(?o) IN ("ru", "en"))
}}
"""


async def fetch_l1(qid: str, cache: "WikidataCache") -> WikidataL1 | None:
    """Fetch L1 properties for `qid`. Returns cached value if fresh."""
    if not _QID_RE.match(qid):
        log.warning("wikidata_invalid_qid", qid=qid, fetcher="L1")
        return None

    cached = cache.get(qid, "L1")
    if cached:
        return WikidataL1(**cached)

    bindings = await wd_client.sparql_query(_L1_SPARQL.format(qid=qid))
    if bindings is None:  # CB open — do not cache empty result
        return None

    l1 = _parse_l1(qid, bindings)
    cache.set(qid, "L1", l1.model_dump())
    log.debug("wikidata_l1_fetched", qid=qid, instance_of=l1.instance_of)
    return l1


def _parse_l1(qid: str, bindings: list[dict[str, Any]]) -> WikidataL1:
    labels: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    aliases: dict[str, list[str]] = {}
    instance_of: list[str] = []
    subclass_of: list[str] = []

    for row in bindings:
        p = row.get("p", {}).get("value", "")
        o_node = row.get("o", {})
        o_val = o_node.get("value", "")
        lang = o_node.get("xml:lang", "")

        if f"{_WD_PROP}P31" in p:
            q = _qid(o_val)
            if q and q not in instance_of:
                instance_of.append(q)
        elif f"{_WD_PROP}P279" in p:
            q = _qid(o_val)
            if q and q not in subclass_of:
                subclass_of.append(q)
        elif "description" in p:
            if lang and o_val:
                descriptions[lang] = o_val
        elif "altLabel" in p:
            if lang and o_val:
                aliases.setdefault(lang, []).append(o_val)
        elif "label" in p.lower():
            if lang and o_val:
                labels[lang] = o_val

    return WikidataL1(
        qid=qid,
        labels=labels,
        descriptions=descriptions,
        aliases=aliases,
        instance_of=instance_of,
        subclass_of=subclass_of,
    )


def _qid(uri: str) -> str:
    """Extract Q-id from Wikidata entity URI, e.g. .../entity/Q5 → Q5."""
    if _WD_ENTITY in uri:
        part = uri.split(_WD_ENTITY, 1)[-1]
        if part.startswith("Q") and part[1:].isdigit():
            return part
    return ""
