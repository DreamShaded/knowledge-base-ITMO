"""
PDF parser façade. Selects Marker (primary) or PyMuPDF (fast text-only path).
"""
from __future__ import annotations

from app.services.pdf_parser.base import PdfParserBase
from app.services.pdf_parser.heuristics import should_use_pymupdf


def get_parser(path: str, force_marker: bool = False) -> PdfParserBase:
    """
    Return the appropriate parser for this PDF.
    force_marker=True bypasses heuristic and always uses Marker.
    Falls back to whichever parser is installed if the preferred one is not.
    """
    from app.services.pdf_parser.marker_parser import MarkerParser
    from app.services.pdf_parser.pymupdf_parser import PyMuPDFParser

    prefer_pymupdf = not force_marker and should_use_pymupdf(path)

    if prefer_pymupdf and PyMuPDFParser.is_available():
        return PyMuPDFParser()
    if MarkerParser.is_available():
        return MarkerParser()
    if PyMuPDFParser.is_available():
        return PyMuPDFParser()

    raise RuntimeError(
        "No PDF parser available. Install at least one: "
        "uv sync --extra pdf"
    )
