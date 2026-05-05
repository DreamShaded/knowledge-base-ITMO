"""Tests for wiki-link extraction and vault-scoped resolution (phase-04)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "graph" / "tier0"


def _load(stem: str):
    name = stem.replace("-", "_") + "_test_mod"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def we():
    return _load("wikilink-extractor")


def test_extract_simple(we):
    links = we.extract_wikilinks("See [[Реактивность]] and [[Python basics]].")
    assert links == ["Реактивность", "Python basics"]


def test_extract_with_alias(we):
    links = we.extract_wikilinks("[[Реактивность|реактивность]]")
    assert links == ["Реактивность"]


def test_extract_with_heading_ref(we):
    links = we.extract_wikilinks("See [[Note#Section|label]]")
    assert links == ["Note"]


def test_extract_empty(we):
    assert we.extract_wikilinks("No links here.") == []


def test_resolve_exact_match(we):
    index = {"реактивность": "urn:note:personal:реактивность.md"}
    results = we.resolve_wikilinks(["Реактивность"], "personal", index)
    assert len(results) == 1
    assert results[0].resolved_uri == "urn:note:personal:реактивность.md"
    assert not results[0].is_stub


def test_resolve_case_insensitive(we):
    index = {"python basics": "urn:note:personal:python basics.md"}
    results = we.resolve_wikilinks(["Python Basics"], "personal", index)
    assert results[0].resolved_uri == "urn:note:personal:python basics.md"


def test_resolve_alias(we):
    # Index contains alias mapping
    index = {
        "rxjs": "urn:note:personal:rxjs-overview.md",
        "rxjs overview": "urn:note:personal:rxjs-overview.md",
    }
    results = we.resolve_wikilinks(["RxJS"], "personal", index)
    assert results[0].resolved_uri == "urn:note:personal:rxjs-overview.md"


def test_resolve_unresolved_is_stub(we):
    results = we.resolve_wikilinks(["NonexistentNote"], "personal", {})
    assert results[0].is_stub
    assert "wikilink-stub" in results[0].resolved_uri


def test_resolve_with_md_extension(we):
    index = {"note.md": "urn:note:personal:note.md"}
    results = we.resolve_wikilinks(["note.md"], "personal", index)
    assert not results[0].is_stub


def test_resolve_cross_vault_not_linked(we):
    # In production, build_notes_index is always called with the target vault_id,
    # so cross-vault resolution never happens. Simulate: pass empty index for 'work'.
    results = we.resolve_wikilinks(["Реактивность"], "work", {})
    assert results[0].is_stub is True, "Cross-vault links must produce stubs"
