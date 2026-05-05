"""Tests for URI scheme determinism and format (phase-04)."""
import pytest
from app.services.graph.tier0.uri import (
    note_uri, vault_uri, tag_uri, project_uri,
    chunk_uri, book_uri, chapter_uri, heading_uri, person_uri,
    _slugify,
)


def test_note_uri_format():
    assert note_uri("personal", "projects/реактивность.md") == \
        "urn:note:personal:projects/реактивность.md"


def test_note_uri_deterministic():
    assert note_uri("work", "notes/test.md") == note_uri("work", "notes/test.md")


def test_vault_uri():
    assert vault_uri("personal") == "urn:vault:personal"


def test_tag_uri_lowercase():
    assert tag_uri("Реактивность") == "urn:tag:реактивность"


def test_tag_uri_strips_hash():
    assert tag_uri("#python") == "urn:tag:python"


def test_tag_uri_space_to_hyphen():
    assert tag_uri("machine learning") == "urn:tag:machine-learning"


def test_tag_uri_global_not_scoped():
    # Same tag across vaults → same URI
    assert tag_uri("реактивность") == tag_uri("реактивность")


def test_project_uri_scoped():
    p1 = project_uri("personal", "magistratura")
    p2 = project_uri("work", "magistratura")
    assert p1 != p2
    assert "personal" in p1
    assert "work" in p2


def test_chunk_uri():
    assert chunk_uri("personal:notes/a.md", 3) == "urn:chunk:personal:notes/a.md:3"


def test_book_uri():
    sha = "a" * 64
    assert book_uri(sha) == f"urn:book:{sha}"


def test_chapter_uri():
    sha = "b" * 64
    assert chapter_uri(sha, 5) == f"urn:chapter:{sha}:5"


def test_heading_uri_deterministic():
    h1 = heading_uri("personal", "notes/test.md", 0)
    h2 = heading_uri("personal", "notes/test.md", 0)
    assert h1 == h2


def test_heading_uri_unique_per_index():
    assert heading_uri("x", "a.md", 0) != heading_uri("x", "a.md", 1)


def test_heading_uri_unique_per_note():
    assert heading_uri("x", "a.md", 0) != heading_uri("x", "b.md", 0)


def test_person_uri_slug():
    assert person_uri("Eric Evans") == "urn:person:eric-evans"


def test_slugify_cyrillic():
    assert _slugify("Реактивность") == "реактивность"


def test_slugify_empty_becomes_empty_sentinel():
    assert _slugify("") == "empty"
