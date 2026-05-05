"""Tier-0 triple emitter: builds pyoxigraph Quads from extracted note/book data.

Provenance strategy: per-source metadata triples (source, contentHash, modified)
attached to the root document URI in <urn:tier0>. Simpler than reification;
sufficient for acceptance check (PRD §11 п.2) and phase-04 scope.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.services.graph.tier0.uri import (
    PKM, RDF, RDFS, XSD, DCT, TIER0_GRAPH,
    note_uri, vault_uri, tag_uri, project_uri,
    book_uri, chapter_uri, heading_uri, person_uri,
)

_HERE = Path(__file__).parent


def _load_module(stem: str):
    name = stem.replace("-", "_")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fp():
    return _load_module("frontmatter-parser")


def _we():
    return _load_module("wikilink-extractor")


def _he():
    return _load_module("heading-extractor")


def _nn(iri: str):
    import pyoxigraph as px
    return px.NamedNode(iri)


def _lit(value: str, datatype: str | None = None):
    import pyoxigraph as px
    if datatype:
        return px.Literal(value, datatype=px.NamedNode(datatype))
    return px.Literal(value)


def _quad(s: str, p: str, o, graph: str = TIER0_GRAPH):
    import pyoxigraph as px
    obj = _nn(o) if isinstance(o, str) else o
    return px.Quad(_nn(s), _nn(p), obj, _nn(graph))


def emit_note_triples(
    vault_id: str,
    rel_path: str,
    content: str,
    content_hash: str,
    modified_at: str,
    notes_index: dict[str, str],
) -> list:
    """Return list of pyoxigraph Quad for one note → <urn:tier0>."""
    fm_mod = _fp()
    we_mod = _we()
    he_mod = _he()

    fm = fm_mod.parse_frontmatter(content)
    raw_links = we_mod.extract_wikilinks(content)
    links = we_mod.resolve_wikilinks(raw_links, vault_id, notes_index)
    headings = he_mod.extract_headings(content)

    n_uri = note_uri(vault_id, rel_path)
    v_uri = vault_uri(vault_id)
    quads = []

    quads.append(_quad(n_uri, RDF + "type", PKM + "Note"))
    quads.append(_quad(n_uri, PKM + "inVault", v_uri))
    quads.append(_quad(v_uri, RDF + "type", PKM + "Vault"))

    # PARA project: frontmatter takes precedence, else derive from path
    project_seg = fm.project or _para_segment(rel_path)
    if project_seg:
        p_uri = project_uri(vault_id, project_seg)
        quads.append(_quad(n_uri, PKM + "inProject", p_uri))
        quads.append(_quad(p_uri, RDF + "type", PKM + "Project"))

    for tag in fm.tags:
        t_uri = tag_uri(tag)
        quads.append(_quad(n_uri, PKM + "tagged", t_uri))
        quads.append(_quad(t_uri, RDF + "type", PKM + "Tag"))

    for link in links:
        if link.resolved_uri and not link.is_stub:
            quads.append(_quad(n_uri, PKM + "linksTo", link.resolved_uri))

    for h in headings:
        h_uri = heading_uri(vault_id, rel_path, h.idx)
        quads.append(_quad(n_uri, PKM + "hasHeading", h_uri))
        quads.append(_quad(h_uri, RDF + "type", PKM + "Section"))
        quads.append(_quad(h_uri, PKM + "level", _lit(str(h.level), XSD + "integer")))
        quads.append(_quad(h_uri, RDFS + "label", _lit(h.text)))

    # Provenance metadata
    quads.append(_quad(n_uri, DCT + "modified", _lit(modified_at, XSD + "dateTime")))
    quads.append(_quad(n_uri, PKM + "contentHash", _lit(content_hash)))
    quads.append(_quad(n_uri, PKM + "source", _lit("tier0")))

    return quads


def emit_book_triples(
    sha256: str,
    title: str,
    authors: list[str],
    chapter_slugs: list[str],
    modified_at: str = "",
) -> list:
    """Return list of pyoxigraph Quad for one book → <urn:tier0>."""
    b_uri = book_uri(sha256)
    quads = [_quad(b_uri, RDF + "type", PKM + "Book")]

    if title:
        quads.append(_quad(b_uri, RDFS + "label", _lit(title)))

    for author in authors:
        pe_uri = person_uri(author)
        quads.append(_quad(b_uri, PKM + "authoredBy", pe_uri))
        quads.append(_quad(pe_uri, RDFS + "label", _lit(author)))

    for idx, slug in enumerate(chapter_slugs):
        ch_uri = chapter_uri(sha256, idx)
        quads.append(_quad(b_uri, PKM + "hasChapter", ch_uri))
        quads.append(_quad(ch_uri, RDF + "type", PKM + "Chapter"))
        if slug:
            quads.append(_quad(ch_uri, RDFS + "label", _lit(slug)))

    quads.append(_quad(b_uri, PKM + "source", _lit("tier0")))
    if modified_at:
        quads.append(_quad(b_uri, DCT + "modified", _lit(modified_at, XSD + "dateTime")))

    return quads


def _para_segment(rel_path: str) -> str:
    """Extract PARA top-level segment from path like Projects/X/note.md → X."""
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0].lower() in ("projects", "areas", "resources", "archives"):
        return parts[1]
    return ""
