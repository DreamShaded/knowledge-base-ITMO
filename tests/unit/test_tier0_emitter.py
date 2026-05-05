"""Tests for Tier-0 triple emitter (phase-04)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "graph" / "tier0"


def _load(stem: str):
    name = stem.replace("-", "_") + "_em_mod"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def emitter():
    return _load("emitter")


SAMPLE_NOTE = """\
---
tags: [реактивность, rxjs]
project: magistratura
aliases: [Reactivity]
---
# Реактивность

See [[RxJS]] and [[UnknownNote]].

## Введение

Some text here.
"""

HASH = "abc123"
TS = "2026-04-29T10:00:00Z"


def _uri_values(quads) -> set[str]:
    """Collect all subject/predicate/object string values from quads."""
    result = set()
    for q in quads:
        result.add(q.subject.value)
        result.add(q.predicate.value)
        val = q.object.value
        result.add(val)
    return result


def test_note_type_triple(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    uris = _uri_values(quads)
    assert "https://my-pkm.local/ontology#Note" in uris


def test_note_in_vault(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    preds = {q.predicate.value for q in quads}
    assert "https://my-pkm.local/ontology#inVault" in preds


def test_tags_emitted(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    objs = {q.object.value for q in quads if q.predicate.value.endswith("tagged")}
    assert "urn:tag:реактивность" in objs
    assert "urn:tag:rxjs" in objs


def test_project_emitted(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    preds = {q.predicate.value for q in quads}
    assert "https://my-pkm.local/ontology#inProject" in preds


def test_headings_emitted(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    heading_quads = [q for q in quads if q.predicate.value.endswith("hasHeading")]
    assert len(heading_quads) >= 2  # "Реактивность" + "Введение"


def test_resolved_wikilink_emitted(emitter):
    notes_index = {"rxjs": "urn:note:personal:rxjs.md"}
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, notes_index)
    link_objs = {q.object.value for q in quads if q.predicate.value.endswith("linksTo")}
    assert "urn:note:personal:rxjs.md" in link_objs


def test_unresolved_wikilink_not_emitted(emitter):
    # Stubs should NOT be emitted as linksTo triples (is_stub=True → skip)
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    link_objs = {q.object.value for q in quads if q.predicate.value.endswith("linksTo")}
    assert not any("wikilink-stub" in o for o in link_objs)


def test_provenance_triples(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    preds = {q.predicate.value for q in quads}
    assert "http://purl.org/dc/terms/modified" in preds
    assert "https://my-pkm.local/ontology#contentHash" in preds
    assert "https://my-pkm.local/ontology#source" in preds


def test_all_in_tier0_graph(emitter):
    quads = emitter.emit_note_triples("personal", "note.md", SAMPLE_NOTE, HASH, TS, {})
    for q in quads:
        assert q.graph_name.value == "urn:tier0", f"Wrong graph: {q.graph_name.value}"


def test_book_triples(emitter):
    quads = emitter.emit_book_triples(
        sha256="x" * 64,
        title="Reactive Design Patterns",
        authors=["Roland Kuhn"],
        chapter_slugs=["intro", "patterns"],
    )
    uris = _uri_values(quads)
    assert "https://my-pkm.local/ontology#Book" in uris
    assert "urn:person:roland-kuhn" in uris


def test_para_project_from_path(emitter):
    content = "---\n---\n# Note\n"
    quads = emitter.emit_note_triples(
        "personal", "Projects/magistratura/note.md", content, HASH, TS, {}
    )
    preds = {q.predicate.value for q in quads}
    assert "https://my-pkm.local/ontology#inProject" in preds
