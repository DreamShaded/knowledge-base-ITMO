"""
Tests for chunker promotion logic (ADR-0002).
Note ≥3 sections → whole-note parent created; book chapter ≥2 → whole-chapter parent.
Uses fallback tokenizer to avoid requiring model downloads.
"""
import importlib.util
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "chunker"


def _load(stem):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


promotion = _load("promotion")
base = _load("base")

tok_mod = _load("qwen3-tokenizer")
Qwen3Tokenizer = tok_mod.Qwen3Tokenizer

mpc_mod = _load("markdown-parent-child")
MarkdownParentChildChunker = mpc_mod.MarkdownParentChildChunker


def make_chunker():
    return MarkdownParentChildChunker(tokenizer=Qwen3Tokenizer(use_fallback=True))


NOTE_3_SECTIONS = """\
## Introduction
This is the intro section with some content about the topic.

## Background
Here we discuss the background. More text to make it realistic.

## Analysis
Deep dive into the analysis of our subject matter.
"""

NOTE_2_SECTIONS = """\
## Introduction
Short intro.

## Conclusion
Brief conclusion.
"""

BOOK_2_SECTIONS = """\
## Chapter Overview
Overview content here.

## Main Concepts
Detailed concepts here.
"""

BOOK_1_SECTION = """\
## Single Section
Only one section in this chapter.
"""


class TestPromotionThresholds:
    def test_note_threshold_constant(self):
        assert promotion.NOTE_SECTION_THRESHOLD == 3

    def test_book_threshold_constant(self):
        assert promotion.BOOK_SECTION_THRESHOLD == 2

    def test_should_promote_note_true(self):
        assert promotion.should_promote_note(3) is True
        assert promotion.should_promote_note(5) is True

    def test_should_promote_note_false(self):
        assert promotion.should_promote_note(2) is False
        assert promotion.should_promote_note(0) is False

    def test_should_promote_book_true(self):
        assert promotion.should_promote_book_chapter(2) is True
        assert promotion.should_promote_book_chapter(4) is True

    def test_should_promote_book_false(self):
        assert promotion.should_promote_book_chapter(1) is False


class TestNotePromotion:
    def test_note_with_3_sections_gets_whole_note_parent(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            NOTE_3_SECTIONS,
            source_id="note-abc",
            source_type="note",
            vault_id="personal",
        )
        # At least one parent should contain the full document text
        full_parents = [p for p in doc.parents if p.parent_id == ""]
        assert len(full_parents) >= 1, "Expected whole-note parent (parent_id='')"
        # The whole-note parent content should include all sections
        combined = full_parents[0].content
        assert "Introduction" in combined
        assert "Analysis" in combined

    def test_note_with_2_sections_no_whole_note_parent(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            NOTE_2_SECTIONS,
            source_id="note-xyz",
            source_type="note",
            vault_id="personal",
        )
        # When not promoted, no parent covers the entire multi-section document
        full_doc_parents = [
            p for p in doc.parents
            if "Introduction" in p.content and "Conclusion" in p.content
        ]
        assert len(full_doc_parents) == 0

    def test_note_always_produces_children(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            NOTE_3_SECTIONS,
            source_id="note-abc",
            source_type="note",
            vault_id="personal",
        )
        assert len(doc.children) >= 3  # at least one child per section

    def test_child_parent_id_matches_section_parent(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            NOTE_2_SECTIONS,
            source_id="note-link",
            source_type="note",
        )
        section_ids = {p.chunk_id for p in doc.parents}
        for child in doc.children:
            assert child.parent_id in section_ids


class TestBookChapterPromotion:
    def test_book_chapter_with_2_sections_gets_whole_chapter_parent(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            BOOK_2_SECTIONS,
            source_id="book-sha:ch01",
            source_type="book_chapter",
            chapter_id="ch01",
        )
        full_parents = [p for p in doc.parents if p.parent_id == ""]
        assert len(full_parents) >= 1

    def test_book_chapter_with_1_section_no_whole_chapter_parent(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            BOOK_1_SECTION,
            source_id="book-sha:ch02",
            source_type="book_chapter",
            chapter_id="ch02",
        )
        # With 1 section (<2 threshold) there should be exactly 1 section-level parent,
        # not an extra whole-chapter parent wrapping it.
        assert len(doc.parents) == 1


class TestPayloadFields:
    def test_vault_id_propagated(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            NOTE_3_SECTIONS,
            source_id="n1",
            source_type="note",
            vault_id="work",
        )
        for chunk in [*doc.parents, *doc.children]:
            assert chunk.vault_id == "work"

    def test_content_hash_non_empty(self):
        chunker = make_chunker()
        doc = chunker.chunk(NOTE_2_SECTIONS, source_id="n2", source_type="note")
        for chunk in [*doc.parents, *doc.children]:
            assert len(chunk.content_hash) == 16  # 16-char hex

    def test_chunk_ids_are_unique(self):
        chunker = make_chunker()
        doc = chunker.chunk(NOTE_3_SECTIONS, source_id="n3", source_type="note")
        all_ids = [c.chunk_id for c in [*doc.parents, *doc.children]]
        assert len(all_ids) == len(set(all_ids))
