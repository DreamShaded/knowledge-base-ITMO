"""
PDF parser selection heuristics (ADR-0001).
Fast path: books with text-layer and no form objects → PyMuPDF.
Default path: Marker (surya layout + OCR).
"""
from __future__ import annotations

from pathlib import Path

# Thresholds for the fast text-only path
_MIN_CHARS_PER_PAGE = 500
_QA_SCORE_THRESHOLD = 0.3


def should_use_pymupdf(path: str) -> bool:
    """
    Return True when PDF has a dense text layer and no AcroForm objects.
    Heuristic: avg chars/page > 500 AND no /AcroForm in trailer.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return False

    doc = fitz.open(path)
    try:
        if _has_acroform(doc):
            return False
        if doc.page_count == 0:
            return False
        total_chars = sum(len(page.get_text()) for page in doc)
        return (total_chars / doc.page_count) >= _MIN_CHARS_PER_PAGE
    finally:
        doc.close()


def qa_score(text: str) -> float:
    """
    Ratio of alphabetic chars to total chars.
    Low score indicates OCR noise or mostly non-text content.
    """
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isalpha())
    return alpha / len(text)


def needs_review(score: float) -> bool:
    return score < _QA_SCORE_THRESHOLD


def chapter_slug(index: int, title: str) -> str:
    """Derive a filesystem-safe chapter slug from its index and title."""
    import re
    safe = re.sub(r"[^\w\s-]", "", title.lower())
    safe = re.sub(r"[\s_]+", "-", safe).strip("-")
    safe = safe[:60] or "chapter"
    return f"{index:02d}-{safe}"


# ── internal ──────────────────────────────────────────────────────────────────

def _has_acroform(doc) -> bool:  # type: ignore[no-untyped-def]
    try:
        trailer = doc.pdf_trailer()
        root = trailer.get("Root", {})
        return "AcroForm" in root
    except Exception:
        return False
