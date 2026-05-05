"""URI/IRI scheme for PKM RDF graph (ADR-0008, ADR-0012).

All URIs are deterministic: same inputs always produce the same URI.
"""
from __future__ import annotations

import re
from urllib.parse import quote as _pct

PKM = "https://my-pkm.local/ontology#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
DCT = "http://purl.org/dc/terms/"

TIER0_GRAPH = "urn:tier0"

# Keep word chars (incl. Cyrillic Ѐ-ӿ), digits, hyphens; collapse rest to hyphen
_SLUG_STRIP_RE = re.compile(r"[^\wЀ-ӿ\-]")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def _slugify(text: str) -> str:
    """Lowercase slug: keeps Cyrillic, alnum, hyphens; spaces → hyphens."""
    text = text.lower().strip().lstrip("#").replace(" ", "-")
    text = _SLUG_STRIP_RE.sub("-", text)
    text = _MULTI_HYPHEN_RE.sub("-", text)
    return text.strip("-") or "empty"


def note_uri(vault_id: str, rel_path: str) -> str:
    """urn:note:<vault_id>:<rel_path>  (ADR-0012 §3) — rel_path is percent-encoded for IRI safety."""
    return f"urn:note:{vault_id}:{_pct(rel_path, safe='/:.-_')}"


def vault_uri(vault_id: str) -> str:
    """urn:vault:<vault_id>  (ADR-0012 §5)"""
    return f"urn:vault:{vault_id}"


def tag_uri(tag: str) -> str:
    """urn:tag:<slug>  (global, not per-vault)"""
    return f"urn:tag:{_slugify(tag)}"


def project_uri(vault_id: str, segment: str) -> str:
    """urn:project:<vault_id>:<segment>  (scoped per ADR-0012)"""
    return f"urn:project:{vault_id}:{_pct(segment, safe='.-_')}"


def chunk_uri(source_id: str, chunk_index: int) -> str:
    """urn:chunk:<source_id>:<chunk_index>"""
    return f"urn:chunk:{source_id}:{chunk_index}"


def book_uri(sha256: str) -> str:
    """urn:book:<sha256>  (content-addressed)"""
    return f"urn:book:{sha256}"


def chapter_uri(sha256: str, chapter_idx: int) -> str:
    """urn:chapter:<sha256>:<chapter_idx>"""
    return f"urn:chapter:{sha256}:{chapter_idx}"


def heading_uri(vault_id: str, rel_path: str, idx: int) -> str:
    """urn:heading:<vault_id>:<path-segments>:<idx>  (derived from note URI)"""
    path_key = _pct(rel_path, safe="/:.-_").replace("/", ":").replace("\\", ":").rstrip(":")
    return f"urn:heading:{vault_id}:{path_key}:{idx}"


def person_uri(name: str) -> str:
    """urn:person:<slug>  (stub; disambiguated against Wikidata in phase-11)"""
    return f"urn:person:{_slugify(name)}"
