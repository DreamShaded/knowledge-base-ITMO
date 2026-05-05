"""Unit tests for MarkdownRenderer — 5 canonical envelope snapshots (ADR-0006)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Load answer-envelope via importlib (kebab-case filename) ──────────────────

_ROOT = Path(__file__).parent.parent.parent / "app"
_ENV_PATH = _ROOT / "services" / "generator" / "answer-envelope.py"
_ENV_KEY = "app.services.generator._envelope_render_test"

if _ENV_KEY not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_ENV_KEY, _ENV_PATH)
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    sys.modules[_ENV_KEY] = _mod
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]

AnswerEnvelope = sys.modules[_ENV_KEY].AnswerEnvelope
Citation = sys.modules[_ENV_KEY].Citation
Section = sys.modules[_ENV_KEY].Section
ConfidenceSignal = sys.modules[_ENV_KEY].ConfidenceSignal

from app.services.render.markdown import MarkdownRenderer

renderer = MarkdownRenderer()

_VAULT_MAP = {"personal": "My Vault"}
_BASE = "http://localhost:8000"


# ── Helpers ───────────────────────────────────────────────────────────────────

def render(envelope, query: str = "test query") -> str:
    return renderer.render(envelope, vault_map=_VAULT_MAP, base_url=_BASE, query=query)


# ── Scenario 1: lookup — 2 note citations, high confidence ───────────────────

class TestLookupScenario:
    def _envelope(self) -> AnswerEnvelope:
        return AnswerEnvelope(
            tldr="Python — высокоуровневый язык программирования [^1]",
            sections=[
                Section(zone="Z1", heading="Ответ", content="Python создан в 1991 году [^1]."),
                Section(zone="Z2", heading="Контекст", content="Используется в ML и веб [^2]."),
            ],
            citations=[
                Citation(
                    id=1, source_id="notes/python.md", source_type="note",
                    vault_id="personal", title="Python Note",
                    snippet="Python создан Гвидо ван Россумом в 1991 году.",
                    score=0.82, modified_at="2024-01-15",
                ),
                Citation(
                    id=2, source_id="notes/ml.md", source_type="note",
                    vault_id="personal", title="ML Note",
                    snippet="Python широко используется в машинном обучении.",
                    score=0.75, modified_at="2024-02-01",
                ),
            ],
            confidence_signal=ConfidenceSignal(level="high", reason="2 заметки, score=0.82"),
        )

    def test_tldr_present(self):
        md = render(self._envelope(), query="что такое python")
        assert "## TL;DR" in md
        assert "Python" in md

    def test_sources_section_present(self):
        md = render(self._envelope(), query="python")
        assert "## Источники" in md

    def test_two_footnote_entries(self):
        md = render(self._envelope(), query="python")
        assert "[^1]:" in md
        assert "[^2]:" in md

    def test_obsidian_link_in_footnote(self):
        md = render(self._envelope(), query="python")
        assert "obsidian://open" in md

    def test_preview_link_in_footnote(self):
        md = render(self._envelope(), query="python")
        assert "/preview/note/personal/" in md

    def test_score_in_footnote(self):
        md = render(self._envelope(), query="python")
        assert "0.82" in md

    def test_modified_at_in_footnote(self):
        md = render(self._envelope(), query="python")
        assert "2024-01-15" in md

    def test_confidence_section(self):
        md = render(self._envelope(), query="python")
        assert "## Уверенность" in md
        assert "high" in md


# ── Scenario 2: multi_hop — 3 sources (notes+book), has related ──────────────

class TestMultiHopScenario:
    def _envelope(self) -> AnswerEnvelope:
        return AnswerEnvelope(
            tldr="Event sourcing связан с CQRS [^1][^2][^3]",
            sections=[
                Section(zone="Z1", content="ES хранит события [^1][^2]."),
                Section(zone="Z2", content="CQRS разделяет чтение и запись [^3]."),
            ],
            related=[
                "[CQRS](https://example.com/cqrs) — паттерн разделения команд и запросов",
                "[DDD](https://example.com/ddd) — предметно-ориентированное проектирование",
            ],
            citations=[
                Citation(id=1, source_id="notes/es.md", source_type="note",
                         vault_id="personal", title="Event Sourcing", snippet="ES stores events."),
                Citation(id=2, source_id="notes/patterns.md", source_type="note",
                         vault_id="personal", title="Patterns", snippet="Design patterns."),
                Citation(id=3, source_id="abc123/ch-1", source_type="book_chapter",
                         title="CQRS Book Ch.1", snippet="CQRS separates reads from writes."),
            ],
            confidence_signal=ConfidenceSignal(level="high", reason="3 источника"),
        )

    def test_related_materials_section(self):
        md = render(self._envelope(), query="event sourcing CQRS")
        assert "## Связанные материалы" in md
        assert "CQRS" in md

    def test_book_preview_link(self):
        md = render(self._envelope(), query="event sourcing")
        assert "/preview/book/abc123/ch-1" in md

    def test_three_footnotes(self):
        md = render(self._envelope(), query="event sourcing")
        assert "[^1]:" in md
        assert "[^2]:" in md
        assert "[^3]:" in md


# ── Scenario 3: fresh + web_saved citation — no obsidian:// link ─────────────

class TestWebSavedScenario:
    def _envelope(self) -> AnswerEnvelope:
        return AnswerEnvelope(
            tldr="Последние новости о GPT-5 [^1]",
            sections=[Section(zone="Z1", content="OpenAI анонсировала GPT-5 [^1].")],
            citations=[
                Citation(
                    id=1, source_id="deadbeef1234", source_type="web_saved",
                    title="GPT-5 Announcement", snippet="OpenAI announced GPT-5 today.",
                    score=0.65,
                ),
            ],
            confidence_signal=ConfidenceSignal(level="medium", reason="веб-источник"),
        )

    def test_no_obsidian_link_for_web(self):
        md = render(self._envelope(), query="gpt-5 news")
        assert "obsidian://" not in md

    def test_preview_web_link_present(self):
        md = render(self._envelope(), query="gpt-5")
        assert "/preview/web/deadbeef1234" in md

    def test_footnote_present(self):
        md = render(self._envelope(), query="gpt-5")
        assert "[^1]:" in md


# ── Scenario 4: refusal envelope ─────────────────────────────────────────────

class TestRefusalScenario:
    def _envelope(self) -> AnswerEnvelope:
        return AnswerEnvelope(
            tldr="Не найдено",
            refusal=True,
            refusal_message="",
        )

    def test_refusal_header(self):
        md = render(self._envelope(), query="секретный запрос")
        assert "## Не нашёл в источниках" in md

    def test_refusal_contains_query(self):
        md = render(self._envelope(), query="секретный запрос")
        assert "секретный запрос" in md

    def test_refusal_no_sources_section(self):
        md = render(self._envelope(), query="секретный запрос")
        assert "## Источники" not in md

    def test_refusal_suggests_explain(self):
        md = render(self._envelope(), query="query")
        assert "/explain" in md


# ── Scenario 5: Z3-dominant, low confidence ──────────────────────────────────

class TestZ3DominantScenario:
    def _envelope(self) -> AnswerEnvelope:
        return AnswerEnvelope(
            tldr="Квантовая запутанность — феномен квантовой механики",
            sections=[
                Section(zone="Z1", content="Несколько заметок [^1]."),
                Section(zone="Z3", heading="Из общих знаний",
                        content="Квантовая запутанность описывает корреляцию между частицами."),
            ],
            citations=[
                Citation(id=1, source_id="notes/qm.md", source_type="note",
                         vault_id="personal", title="QM Note",
                         snippet="Краткая заметка о квантах.", score=0.45),
            ],
            confidence_signal=ConfidenceSignal(level="low", reason="Доминирует Z3"),
        )

    def test_general_knowledge_section(self):
        md = render(self._envelope(), query="квантовая запутанность")
        assert "## Из общих знаний (не из источников)" in md

    def test_z3_content_present(self):
        md = render(self._envelope(), query="квантовая запутанность")
        assert "корреляцию между частицами" in md

    def test_low_confidence(self):
        md = render(self._envelope(), query="квантовая запутанность")
        assert "## Уверенность" in md
        assert "low" in md

    def test_answer_section_also_present(self):
        md = render(self._envelope(), query="квантовая запутанность")
        assert "## Ответ" in md
