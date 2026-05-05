"""
PyMuPDF fast-path parser for text-layer PDFs without formulas (ADR-0001).
Requires: pymupdf>=1.24  (import fitz)
"""
from __future__ import annotations

import re
from pathlib import Path

from app.di.ports import ParsedBook, ParsedChapter
from app.services.pdf_parser.base import PdfParserBase
from app.services.pdf_parser.heuristics import chapter_slug, qa_score

_HEADING_RE = re.compile(
    r"^(#{1,3}\s+.+|Chapter\s+\d+|CHAPTER\s+\d+|Part\s+[IVX\d]+)$",
    re.IGNORECASE,
)
_MAX_PAGES_PER_CHAPTER = 30  # fallback split when no headings detected


class PyMuPDFParser(PdfParserBase):

    @classmethod
    def is_available(cls) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            return False

    async def parse(self, path: str) -> ParsedBook:
        return parse_sync(path)


def parse_sync(path: str) -> ParsedBook:
    """Synchronous entry point — usable from threads (e.g. Marker fallback)."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("pymupdf not installed — run: uv add pymupdf") from exc

    doc = fitz.open(path)
    try:
        meta = doc.metadata or {}
        title = meta.get("title") or Path(path).stem
        authors = _split_authors(meta.get("author", ""))

        pages_md: list[str] = [page.get_text("text") for page in doc]
        images_by_page = _extract_images(doc)
        page_count = doc.page_count
    finally:
        doc.close()

    chapters = _split_into_chapters(pages_md, images_by_page)

    return ParsedBook(
        sha256="",  # filled in by worker before storage
        title=title,
        authors=authors,
        isbn=meta.get("subject", ""),
        page_count=page_count,
        chapters=chapters,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _split_authors(raw: str) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in re.split(r"[;,]", raw) if a.strip()]


def _extract_images(doc) -> dict[int, list[bytes]]:  # type: ignore[no-untyped-def]
    """Return {page_index: [raw_image_bytes, ...]}."""
    result: dict[int, list[bytes]] = {}
    for page_idx, page in enumerate(doc):
        imgs = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base = doc.extract_image(xref)
                imgs.append(base["image"])
            except Exception:
                pass
        if imgs:
            result[page_idx] = imgs
    return result


def _split_into_chapters(
    pages_md: list[str],
    images_by_page: dict[int, list[bytes]],
) -> list[ParsedChapter]:
    """
    Detect chapter boundaries from headings; fall back to fixed page-count split.
    """
    boundaries: list[tuple[int, str]] = []
    for i, text in enumerate(pages_md):
        for line in text.splitlines():
            if _HEADING_RE.match(line.strip()):
                boundaries.append((i, line.strip()))
                break

    # Fallback: no headings detected → one chapter per N pages
    if not boundaries:
        boundaries = [
            (i, f"Chapter {i // _MAX_PAGES_PER_CHAPTER + 1}")
            for i in range(0, len(pages_md), _MAX_PAGES_PER_CHAPTER)
        ]

    chapters: list[ParsedChapter] = []
    for chap_idx, (start, title) in enumerate(boundaries):
        end = boundaries[chap_idx + 1][0] if chap_idx + 1 < len(boundaries) else len(pages_md)
        content = "\n\n".join(pages_md[start:end])
        img_paths = _image_placeholder_paths(start, end, images_by_page)
        chapters.append(ParsedChapter(
            slug=chapter_slug(chap_idx + 1, title),
            content_md=content,
            images=img_paths,
            qa_score_value=qa_score(content),
        ))

    return chapters or [ParsedChapter(
        slug="01-full-text",
        content_md="\n\n".join(pages_md),
        images=[],
        qa_score_value=qa_score("".join(pages_md)),
    )]


def _image_placeholder_paths(
    start: int, end: int, images_by_page: dict[int, list[bytes]]
) -> list[str]:
    """Return relative image filenames (actual bytes written by BookStorage)."""
    paths = []
    for page_idx in range(start, end):
        for img_idx in range(len(images_by_page.get(page_idx, []))):
            paths.append(f"images/p{page_idx:04d}-{img_idx:02d}.png")
    return paths
