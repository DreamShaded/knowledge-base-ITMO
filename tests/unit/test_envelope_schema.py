"""Unit tests for AnswerEnvelope schema (ADR-0006 §1)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).parent.parent.parent / "app/services/generator/answer-envelope.py"
_spec = importlib.util.spec_from_file_location("app.services.generator._envelope_test", _path)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["app.services.generator._envelope_test"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

AnswerEnvelope = _mod.AnswerEnvelope
Citation = _mod.Citation
Section = _mod.Section
ConfidenceSignal = _mod.ConfidenceSignal


class TestSchemaConstruction:
    def test_minimal_envelope(self):
        env = AnswerEnvelope(tldr="Test answer")
        assert env.tldr == "Test answer"
        assert env.sections == []
        assert env.citations == []
        assert env.refusal is False

    def test_full_envelope(self):
        env = AnswerEnvelope(
            tldr="Summary",
            sections=[
                Section(zone="Z1", heading="Answer", content="Fact [^1]"),
                Section(zone="Z2", heading="Context", content="Background"),
            ],
            citations=[
                Citation(id=1, source_id="note-1", source_type="note", vault_id="personal",
                         title="My Note", snippet="Fact excerpt")
            ],
            confidence_signal=ConfidenceSignal(level="high", reason="3 sources", retrieval_hit_count=3),
        )
        assert len(env.sections) == 2
        assert env.sections[0].zone == "Z1"
        assert env.citations[0].id == 1
        assert env.confidence_signal.level == "high"

    def test_refusal_envelope(self):
        env = AnswerEnvelope(
            tldr="Not found",
            refusal=True,
            refusal_message="Не нашёл в источниках.",
        )
        assert env.refusal is True
        assert "Не нашёл" in env.refusal_message

    def test_default_confidence_signal(self):
        env = AnswerEnvelope(tldr="x")
        assert env.confidence_signal.level == "medium"
        assert env.confidence_signal.retrieval_hit_count == 0


class TestSectionZones:
    def test_z1_z2_z3_allowed(self):
        for zone in ("Z1", "Z2", "Z3"):
            s = Section(zone=zone, content="text")
            assert s.zone == zone

    def test_invalid_zone_raises(self):
        with pytest.raises(Exception):
            Section(zone="Z4", content="text")  # type: ignore[arg-type]


class TestConfidenceSignal:
    def test_valid_levels(self):
        for level in ("high", "medium", "low"):
            cs = ConfidenceSignal(level=level)
            assert cs.level == level

    def test_invalid_level_raises(self):
        with pytest.raises(Exception):
            ConfidenceSignal(level="very_high")  # type: ignore[arg-type]


class TestToMarkdown:
    def test_refusal_renders_message(self):
        env = AnswerEnvelope(
            tldr="Not found",
            refusal=True,
            refusal_message="Не нашёл в источниках.",
        )
        md = env.to_markdown()
        assert "Не нашёл" in md

    def test_normal_renders_tldr(self):
        env = AnswerEnvelope(
            tldr="Python is a language",
            sections=[Section(zone="Z1", heading="Answer", content="Python is great [^1]")],
            citations=[Citation(id=1, source_id="s1", source_type="note",
                                title="Python Note", snippet="snippet")],
        )
        md = env.to_markdown()
        assert "Python is a language" in md
        assert "Python is great" in md
        assert "[^1]:" in md

    def test_related_topics_included(self):
        env = AnswerEnvelope(
            tldr="x",
            related=["topic A", "topic B"],
        )
        md = env.to_markdown()
        assert "topic A" in md
        assert "topic B" in md

    def test_z3_section_rendered(self):
        env = AnswerEnvelope(
            tldr="Partial answer",
            sections=[
                Section(zone="Z1", content="Known fact [^1]"),
                Section(zone="Z3", heading="Из общих знаний", content="General knowledge"),
            ],
        )
        md = env.to_markdown()
        assert "Из общих знаний" in md
        assert "General knowledge" in md

    def test_no_sections_still_valid(self):
        env = AnswerEnvelope(tldr="Just a summary")
        md = env.to_markdown()
        assert "Just a summary" in md

    def test_prompt_version_stored(self):
        env = AnswerEnvelope(tldr="x", prompt_version="three_zone_generator_v1")
        assert env.prompt_version == "three_zone_generator_v1"


class TestCitationSchema:
    def test_required_fields(self):
        c = Citation(id=1, source_id="note-abc", source_type="note")
        assert c.vault_id == ""
        assert c.title == ""
        assert c.snippet == ""

    def test_book_chapter_type(self):
        c = Citation(id=2, source_id="book-1:ch-3", source_type="book_chapter",
                     title="Chapter 3", snippet="extract")
        assert c.source_type == "book_chapter"
