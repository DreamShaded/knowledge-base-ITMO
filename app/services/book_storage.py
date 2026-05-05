"""
Content-hash addressed book storage (ADR-0001, ADR-0012).
Layout: data/books/<sha256>/{metadata.json, chapters/<slug>/{content.md, images/}}
Quality slabs appended to data/books/_quality_slabs.jsonl.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.di.ports import ParsedBook, ParsedChapter
from app.logging import get_logger
from app.services.pdf_parser.heuristics import needs_review

log = get_logger(__name__)

_QA_SLABS_FILE = "_quality_slabs.jsonl"


class BookStorage:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "books"

    # ── queries ───────────────────────────────────────────────────────────────

    def is_parsed(self, sha256: str) -> bool:
        return (self._root / sha256 / "metadata.json").exists()

    def load_metadata(self, sha256: str) -> dict:
        path = self._root / sha256 / "metadata.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write_metadata(self, sha256: str, meta: dict) -> None:
        path = self._root / sha256 / "metadata.json"
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_source_path(self, sha256: str, path: str) -> None:
        """Add path to source_paths list without re-parsing."""
        meta = self.load_metadata(sha256)
        paths: list[str] = meta.setdefault("source_paths", [])
        if path not in paths:
            paths.append(path)
            self.write_metadata(sha256, meta)

    def find_sha256_by_path(self, path: str) -> str | None:
        """Scan book dirs to find sha256 whose source_paths contains path."""
        for meta_file in self._root.glob("*/metadata.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if path in meta.get("source_paths", []):
                    return meta_file.parent.name
            except Exception:
                continue
        return None

    # ── write ─────────────────────────────────────────────────────────────────

    async def save(
        self,
        book: ParsedBook,
        source_path: str,
        parser_used: str,
    ) -> None:
        sha256 = book.sha256
        book_dir = self._root / sha256
        book_dir.mkdir(parents=True, exist_ok=True)

        for chapter in book.chapters:
            self._write_chapter(book_dir, chapter)

        meta = {
            "title": book.title,
            "authors": book.authors,
            "isbn": book.isbn,
            "pages": book.page_count,
            "source_paths": [source_path],
            "parser_used": parser_used,
            "parsed_at": datetime.now(tz=timezone.utc).isoformat(),
            "removed": False,
        }
        self.write_metadata(sha256, meta)
        self._record_quality_slabs(sha256, book)
        log.info("book_saved", sha256=sha256[:12], chapters=len(book.chapters))

    # ── internal ──────────────────────────────────────────────────────────────

    def _write_chapter(self, book_dir: Path, chapter: ParsedChapter) -> None:
        chap_dir = book_dir / "chapters" / chapter.slug
        chap_dir.mkdir(parents=True, exist_ok=True)
        (chap_dir / "content.md").write_text(chapter.content_md, encoding="utf-8")
        (chap_dir / "images").mkdir(exist_ok=True)

    def _record_quality_slabs(self, sha256: str, book: ParsedBook) -> None:
        slabs_path = self._root / _QA_SLABS_FILE
        entries: list[str] = []
        for chap in book.chapters:
            score = chap.qa_score_value
            entry = {
                "sha256": sha256,
                "chapter": chap.slug,
                "qa_score": round(score, 4),
                "needs_review": needs_review(score),
                "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            entries.append(json.dumps(entry, ensure_ascii=False))
            if needs_review(score):
                log.warning(
                    "chapter_needs_review",
                    sha256=sha256[:12],
                    chapter=chap.slug,
                    qa_score=score,
                )
        if entries:
            with slabs_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(entries) + "\n")
