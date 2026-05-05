"""Unit tests for PDF parser heuristics and routing logic."""
import pytest

from app.services.pdf_parser.heuristics import (
    chapter_slug,
    needs_review,
    qa_score,
    should_use_pymupdf,
)
from app.services.pdf_parser import get_parser
from app.services.pdf_parser.marker_parser import MarkerParser
from app.services.pdf_parser.pymupdf_parser import PyMuPDFParser


# ── qa_score ──────────────────────────────────────────────────────────────────

def test_qa_score_high_for_clean_text():
    assert qa_score("Hello world, this is a book.") > 0.7


def test_qa_score_low_for_garbage():
    assert qa_score("!@#$%^&*()1234567890") < 0.3


def test_qa_score_zero_for_empty():
    assert qa_score("") == 0.0


def test_qa_score_mixed():
    score = qa_score("abc 123")
    assert 0.0 < score < 1.0


# ── needs_review ──────────────────────────────────────────────────────────────

def test_needs_review_flags_low_score():
    assert needs_review(0.1) is True
    assert needs_review(0.29) is True


def test_needs_review_passes_good_score():
    assert needs_review(0.5) is False
    assert needs_review(1.0) is False


def test_needs_review_boundary():
    assert needs_review(0.3) is False   # threshold is strictly <0.3


# ── chapter_slug ──────────────────────────────────────────────────────────────

def test_chapter_slug_basic():
    assert chapter_slug(1, "Introduction") == "01-introduction"


def test_chapter_slug_strips_special_chars():
    slug = chapter_slug(3, "Part I: The Beginning!")
    assert slug.startswith("03-")
    assert "!" not in slug
    assert ":" not in slug


def test_chapter_slug_truncates_long_title():
    long_title = "A" * 100
    slug = chapter_slug(1, long_title)
    assert len(slug) <= 63  # "01-" + 60 chars


def test_chapter_slug_fallback_for_empty():
    slug = chapter_slug(5, "!@#$")
    assert slug == "05-chapter"


def test_chapter_slug_zero_pads_index():
    assert chapter_slug(9, "End").startswith("09-")
    assert chapter_slug(10, "End").startswith("10-")


# ── parser availability / routing ────────────────────────────────────────────

def test_marker_parser_availability_reflects_import():
    available = MarkerParser.is_available()
    try:
        import marker.convert  # noqa: F401
        assert available is True
    except ImportError:
        try:
            import marker.converters.pdf  # noqa: F401
            assert available is True
        except ImportError:
            assert available is False


def test_pymupdf_parser_availability_reflects_import():
    available = PyMuPDFParser.is_available()
    try:
        import fitz  # noqa: F401
        assert available is True
    except ImportError:
        assert available is False


def test_get_parser_raises_when_no_parsers_installed(tmp_path, monkeypatch):
    """get_parser raises RuntimeError when neither parser is available."""
    monkeypatch.setattr(MarkerParser, "is_available", lambda: False)
    monkeypatch.setattr(PyMuPDFParser, "is_available", lambda: False)
    # should_use_pymupdf also returns False since fitz unavailable
    monkeypatch.setattr(
        "app.services.pdf_parser.heuristics.should_use_pymupdf", lambda path: False
    )
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(RuntimeError, match="No PDF parser available"):
        get_parser(str(dummy_pdf))


def test_get_parser_prefers_pymupdf_for_text_pdf(monkeypatch):
    """When heuristic says text-PDF and PyMuPDF is available, return PyMuPDFParser."""
    monkeypatch.setattr(
        "app.services.pdf_parser.heuristics.should_use_pymupdf", lambda path: True
    )
    monkeypatch.setattr(PyMuPDFParser, "is_available", lambda: True)
    parser = get_parser("any.pdf", force_marker=False)
    assert isinstance(parser, PyMuPDFParser)


def test_get_parser_force_marker_bypasses_heuristic(monkeypatch):
    """force_marker=True skips heuristic and returns MarkerParser when available."""
    monkeypatch.setattr(
        "app.services.pdf_parser.heuristics.should_use_pymupdf", lambda path: True
    )
    monkeypatch.setattr(MarkerParser, "is_available", lambda: True)
    monkeypatch.setattr(PyMuPDFParser, "is_available", lambda: True)
    parser = get_parser("any.pdf", force_marker=True)
    assert isinstance(parser, MarkerParser)


def test_get_parser_falls_back_to_pymupdf_when_marker_unavailable(monkeypatch):
    """If Marker not installed but PyMuPDF is, fall back to PyMuPDF even for scan-PDFs."""
    monkeypatch.setattr(
        "app.services.pdf_parser.heuristics.should_use_pymupdf", lambda path: False
    )
    monkeypatch.setattr(MarkerParser, "is_available", lambda: False)
    monkeypatch.setattr(PyMuPDFParser, "is_available", lambda: True)
    parser = get_parser("scan.pdf")
    assert isinstance(parser, PyMuPDFParser)
