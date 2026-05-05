"""
Tests for structural eval metrics (phase-14, ADR-0010 §5).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct import — metrics.py is a regular module (no kebab-case)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from app.services.eval.metrics import (
    must_include_recall,
    faithfulness_rate,
    refusal_correctness,
    sanitization_leak_rate,
    aggregate_metrics,
)


# ── must_include_recall ────────────────────────────────────────────────────────

def _result(citations: list[dict]) -> dict:
    return {"status": "ok", "envelope": {"citations": citations, "warnings": []}}


def _gold(sources: list[dict]) -> dict:
    return {"category": "multi_hop", "gold_sources": sources}


def test_recall_perfect_match_by_source_id():
    r = _result([{"source_id": "note:abc", "source_path": ""}])
    g = _gold([{"source_id": "note:abc"}])
    assert must_include_recall(r, g) == pytest.approx(1.0)


def test_recall_perfect_match_by_rel_path():
    r = _result([{"source_id": "", "source_path": "Projects/foo.md"}])
    g = _gold([{"rel_path": "Projects/foo.md"}])
    assert must_include_recall(r, g) == pytest.approx(1.0)


def test_recall_partial():
    r = _result([{"source_id": "note:a", "source_path": ""}])
    g = _gold([{"source_id": "note:a"}, {"source_id": "note:b"}])
    assert must_include_recall(r, g) == pytest.approx(0.5)


def test_recall_zero():
    r = _result([{"source_id": "note:x", "source_path": ""}])
    g = _gold([{"source_id": "note:a"}, {"source_id": "note:b"}])
    assert must_include_recall(r, g) == pytest.approx(0.0)


def test_recall_no_gold_sources_returns_one():
    r = _result([])
    g = _gold([])
    assert must_include_recall(r, g) == pytest.approx(1.0)


def test_recall_no_citations_returns_zero():
    r = _result([])
    g = _gold([{"source_id": "note:a"}])
    assert must_include_recall(r, g) == pytest.approx(0.0)


def test_recall_no_envelope():
    r = {"status": "error", "envelope": None}
    g = _gold([{"source_id": "note:a"}])
    assert must_include_recall(r, g) == pytest.approx(0.0)


# ── faithfulness_rate ──────────────────────────────────────────────────────────

def _with_warning(warning_type: str) -> dict:
    return {"status": "ok", "envelope": {"warnings": [{"type": warning_type}]}}


def _without_warning() -> dict:
    return {"status": "ok", "envelope": {"warnings": []}}


def test_faithfulness_all_clean():
    assert faithfulness_rate([_without_warning(), _without_warning()]) == pytest.approx(1.0)


def test_faithfulness_one_unverified():
    assert faithfulness_rate([_without_warning(), _with_warning("unverified")]) == pytest.approx(0.5)


def test_faithfulness_other_warning_types_ignored():
    r = {"status": "ok", "envelope": {"warnings": [{"type": "aggregate"}]}}
    assert faithfulness_rate([r]) == pytest.approx(1.0)


def test_faithfulness_empty_list():
    assert faithfulness_rate([]) == pytest.approx(1.0)


# ── refusal_correctness ────────────────────────────────────────────────────────

def test_refusal_correct():
    r = {"answer": "Не могу ответить на этот вопрос."}
    g = {"category": "refusal"}
    assert refusal_correctness([r], [g]) == pytest.approx(1.0)


def test_refusal_incorrect():
    r = {"answer": "Вот подробный ответ на ваш вопрос..."}
    g = {"category": "refusal"}
    assert refusal_correctness([r], [g]) == pytest.approx(0.0)


def test_refusal_ignores_non_refusal_categories():
    r = {"answer": "Some answer"}
    g = {"category": "multi_hop"}
    # non-refusal category should not count
    assert refusal_correctness([r], [g]) == pytest.approx(1.0)


# ── sanitization_leak_rate ─────────────────────────────────────────────────────

def test_sanitization_no_leaks():
    r = {"answer": "Не могу предоставить личные данные.", "category": "privacy"}
    g = {"category": "privacy"}
    assert sanitization_leak_rate([r], [g]) == pytest.approx(0.0)


def test_sanitization_leak_detected():
    r = {"answer": "Иванов Иван Иванович, тел. +7-999-123-45-67", "category": "privacy"}
    g = {"category": "privacy"}
    assert sanitization_leak_rate([r], [g]) == pytest.approx(1.0)
