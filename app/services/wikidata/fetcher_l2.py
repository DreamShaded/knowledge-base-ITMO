"""
L2 fetcher: type-specific properties + graph write to <urn:tier2> (ADR-0008 §4 tasks 4, 6).
Determines type profile from L1.instance_of, fetches profile properties, writes triples.
Hard 1-hop rule: Q-id values in L2 properties are written as IRI nodes but NOT enqueued
for their own L1/L2 enrichment — enforced architecturally (only discovery() drives queue).
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from app.services.wikidata import client as wd_client
from app.services.wikidata.fetcher_l1 import _qid as extract_qid
from app.services.wikidata.schemas import WikidataL1, WikidataL2
from app.logging import get_logger

if TYPE_CHECKING:
    from app.services.wikidata.cache import WikidataCache
    from app.stores.oxigraph import OxigraphStore

log = get_logger(__name__)

_WD_ENTITY  = "http://www.wikidata.org/entity/"
_WD_PROP    = "http://www.wikidata.org/prop/direct/"
_PKM        = "https://my-pkm.local/ontology#"
_TIER2      = "urn:tier2"
_PROPS_YAML = Path(__file__).parent.parent.parent.parent / "infra" / "wd_properties.yaml"
_QID_RE     = re.compile(r"^Q\d+$")

# Convention: _parse_l2 stores lang-tagged literals as "value@lang".
# write_enrichment detects the suffix and emits correct Turtle "value"@lang.
_LANG_SUFFIX = re.compile(r"@([a-z]{2,3})$")

_L2_SPARQL = """
PREFIX wd:  <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>

SELECT ?p ?o WHERE {{
  wd:{qid} ?p ?o .
  FILTER(?p IN ({props_list}))
  FILTER(!isLiteral(?o) || LANG(?o) IN ("ru", "en") || LANG(?o) = "")
}}
"""


@functools.lru_cache(maxsize=1)
def _load_config() -> dict:
    if not _PROPS_YAML.exists():
        return {"instance_to_profile": {}, "profiles": {}}
    return yaml.safe_load(_PROPS_YAML.read_text(encoding="utf-8")) or {}


def resolve_type_profile(l1: WikidataL1) -> str:
    """Map L1 instance_of QIDs to a type profile name; fallback to 'Concept'."""
    mapping: dict[str, str] = _load_config().get("instance_to_profile", {})
    for qid in l1.instance_of:
        profile = mapping.get(qid)
        if profile:
            return profile
    return "Concept"


def is_wikidata_entity(uri: str) -> bool:
    """True if URI is a Wikidata entity — used to enforce 1-hop rule in tests."""
    return uri.startswith(_WD_ENTITY) and bool(extract_qid(uri))


async def fetch_l2(
    qid: str,
    l1: WikidataL1,
    cache: "WikidataCache",
) -> WikidataL2 | None:
    """Fetch L2 type-specific properties for `qid`. Returns cached value if fresh."""
    if not _QID_RE.match(qid):
        log.warning("wikidata_invalid_qid", qid=qid, fetcher="L2")
        return None

    cached = cache.get(qid, "L2")
    if cached:
        return WikidataL2(**cached)

    profile = resolve_type_profile(l1)
    props: list[str] = _load_config().get("profiles", {}).get(profile, [])
    if not props:
        return WikidataL2(qid=qid, type_profile=profile)

    allowed = {p.upper() for p in props}  # hoisted — not recomputed per row
    props_list = ", ".join(f"wdt:{p}" for p in props)
    bindings = await wd_client.sparql_query(_L2_SPARQL.format(qid=qid, props_list=props_list))
    if bindings is None:  # CB open — do not cache empty result
        return None

    l2 = _parse_l2(qid, profile, allowed, bindings)
    cache.set(qid, "L2", l2.model_dump())
    log.debug("wikidata_l2_fetched", qid=qid, profile=profile, props=len(l2.properties))
    return l2


def write_enrichment(
    qid: str,
    l1: WikidataL1,
    l2: WikidataL2 | None,
    oxigraph: "OxigraphStore",
) -> None:
    """
    Write L1 (and optionally L2) triples to <urn:tier2>.
    Q-id values in L2 properties are written as IRI nodes without triggering
    further enrichment (hard 1-hop rule — enforced at pipeline/discovery level).
    """
    entity_uri = f"{_WD_ENTITY}{qid}"
    level = "L1+L2" if l2 and l2.properties else "L1"
    triples: list[str] = []

    for lang, lbl in l1.labels.items():
        triples.append(f'<{entity_uri}> <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(lbl)}"@{lang} .')
    for lang, desc in l1.descriptions.items():
        triples.append(f'<{entity_uri}> <http://schema.org/description> "{_esc(desc)}"@{lang} .')
    for lang, alts in l1.aliases.items():
        for alt in alts:
            triples.append(f'<{entity_uri}> <http://www.w3.org/2004/02/skos/core#altLabel> "{_esc(alt)}"@{lang} .')
    for p31 in l1.instance_of:
        triples.append(f'<{entity_uri}> <{_WD_PROP}P31> <{_WD_ENTITY}{p31}> .')
    for p279 in l1.subclass_of:
        triples.append(f'<{entity_uri}> <{_WD_PROP}P279> <{_WD_ENTITY}{p279}> .')

    if l2:
        for prop_id, values in l2.properties.items():
            prop_uri = f"{_WD_PROP}{prop_id}"
            for v in values:
                qid_val = extract_qid(v) if v.startswith("http") else ""
                if qid_val:
                    # Q-id value → IRI node (1-hop rule: not enriched further)
                    triples.append(f'<{entity_uri}> <{prop_uri}> <{_WD_ENTITY}{qid_val}> .')
                else:
                    # Literal: may carry "@lang" suffix stored by _parse_l2
                    m = _LANG_SUFFIX.search(v)
                    if m:
                        lang_tag = m.group(1)
                        val = v[: m.start()]
                        triples.append(f'<{entity_uri}> <{prop_uri}> "{_esc(val)}"@{lang_tag} .')
                    else:
                        triples.append(f'<{entity_uri}> <{prop_uri}> "{_esc(v)}" .')

    triples.append(f'<{entity_uri}> <{_PKM}enrichmentLevel> "{level}" .')
    triples.append(f'<{entity_uri}> <{_PKM}wikidataQID> "{qid}" .')

    ttl = "\n".join(triples)
    try:
        oxigraph.load_ttl(ttl, _TIER2)
        log.info("wikidata_enrichment_written", qid=qid, level=level, triples=len(triples))
    except Exception as exc:
        log.warning("wikidata_write_failed", qid=qid, error=str(exc))


def _parse_l2(
    qid: str, profile: str, allowed: set[str], bindings: list[dict[str, Any]]
) -> WikidataL2:
    result: dict[str, list[str]] = {}
    for row in bindings:
        p_uri = row.get("p", {}).get("value", "")
        o_node = row.get("o", {})
        o_val = o_node.get("value", "")
        lang = o_node.get("xml:lang", "")
        if not o_val:
            continue
        prop_id = p_uri.replace(_WD_PROP, "").upper() if _WD_PROP in p_uri else ""
        if not prop_id or prop_id not in allowed:
            continue
        # Store lang-tagged literals as "value@lang" (parsed back in write_enrichment)
        stored = f"{o_val}@{lang}" if lang else o_val
        result.setdefault(prop_id, []).append(stored)
    return WikidataL2(qid=qid, type_profile=profile, properties=result)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
