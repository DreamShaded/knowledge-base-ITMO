"""
Marker primary parser: surya layout + per-block OCR (ADR-0001).
Requires: marker-pdf>=1.6  (pip install marker-pdf)
Runs blocking model inference in a thread executor to avoid blocking asyncio loop.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.di.ports import ParsedBook, ParsedChapter
from app.services.pdf_parser.base import PdfParserBase
from app.services.pdf_parser.heuristics import chapter_slug, qa_score

# Marker models are heavy; loaded once and cached at module level.
_models = None


def unload_models() -> None:
    """Evict Marker models from GPU and free CUDA cache."""
    global _models
    if _models is not None:
        del _models
        _models = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        from app.logging import get_logger
        get_logger(__name__).info("marker_models_unloaded")


class MarkerParser(PdfParserBase):

    @classmethod
    def is_available(cls) -> bool:
        try:
            import marker.convert  # noqa: F401
            return True
        except ImportError:
            try:
                import marker.converters.pdf  # noqa: F401
                return True
            except ImportError:
                return False

    async def parse(self, path: str) -> ParsedBook:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._parse_sync, path)

    def _parse_sync(self, path: str) -> ParsedBook:
        try:
            full_text, images_dict, meta = _run_marker(path)
        except (RuntimeError, ImportError) as exc:
            # Marker unavailable or GPU OOM during inference — fall back to PyMuPDF.
            # CPU Marker is prohibitively slow (~hours) so we skip it entirely.
            from app.logging import get_logger
            get_logger(__name__).warning(
                "marker_fallback_pymupdf", error=str(exc)[:120], path=path
            )
            from app.services.pdf_parser.pymupdf_parser import parse_sync as _pymupdf_sync
            return _pymupdf_sync(path)

        title = meta.get("title") or Path(path).stem
        authors = _split_authors(meta.get("author", ""))
        page_count = meta.get("pages", 0)

        chapters = _split_chapters(full_text)

        return ParsedBook(
            sha256="",  # filled in by worker
            title=title,
            authors=authors,
            page_count=page_count,
            chapters=chapters,
        )


# ── marker invocation ─────────────────────────────────────────────────────────

def _run_marker(path: str) -> tuple[str, dict, dict]:
    """
    Try new API (marker-pdf>=1.3) then fall back to legacy API.
    Returns (markdown_text, images_dict, metadata_dict).
    """
    try:
        return _run_marker_new_api(path)
    except AttributeError:
        pass
    except ImportError as exc:
        # OOM-during-inference ImportError — propagate directly to _parse_sync for PyMuPDF fallback.
        # Only suppress "module not found"-style ImportErrors to try legacy API.
        if "OOM" in str(exc) or "out of memory" in str(exc).lower():
            raise
        pass
    try:
        return _run_marker_legacy_api(path)
    except ImportError as exc:
        raise RuntimeError(
            "marker-pdf not installed — run: uv add --optional pdf marker-pdf"
        ) from exc


def _is_oom(exc: Exception) -> bool:
    return "OutOfMemoryError" in type(exc).__name__ or "out of memory" in str(exc).lower()


def _run_marker_new_api(path: str) -> tuple[str, dict, dict]:
    from marker.converters.pdf import PdfConverter  # type: ignore[import]
    from marker.models import create_model_dict  # type: ignore[import]
    from app.logging import get_logger
    _log = get_logger(__name__)

    global _models

    def _load_models(device: str | None) -> object:
        return create_model_dict(device=device)

    def _run(models: object) -> tuple[str, dict, dict]:
        converter = PdfConverter(artifact_dict=models)
        rendered = converter(path)
        text = getattr(rendered, "markdown", "") or str(rendered)
        images = getattr(rendered, "images", {}) or {}
        metadata = getattr(rendered, "metadata", {}) or {}
        return text, images, metadata

    # Load models if not cached; fall back to CPU on OOM; raise ImportError if weights missing
    if _models is None:
        try:
            _models = _load_models(device=None)
        except FileNotFoundError as exc:
            # Marker model weights not downloaded — caller falls back to PyMuPDF
            raise ImportError(f"Marker model weights not found: {exc}") from exc
        except Exception as exc:
            if _is_oom(exc):
                _log.warning("marker_models_gpu_oom_fallback_cpu", error=str(exc)[:120])
                _models = _load_models(device="cpu")
            else:
                raise

    # Run inference; on OOM signal ImportError so _parse_sync falls back to PyMuPDF.
    # CPU Marker inference is prohibitively slow (~hours); prefer fast PyMuPDF fallback.
    try:
        return _run(_models)
    except Exception as exc:
        if not _is_oom(exc):
            raise
        _log.warning("marker_inference_gpu_oom_pymupdf_fallback", error=str(exc)[:120])
        raise ImportError(f"Marker GPU OOM during inference — falling back to PyMuPDF: {exc}") from exc


def _run_marker_legacy_api(path: str) -> tuple[str, dict, dict]:
    from marker.convert import convert_single_pdf  # type: ignore[import]
    from marker.models import load_all_models  # type: ignore[import]

    global _models
    if _models is None:
        _models = load_all_models()

    full_text, images, out_meta = convert_single_pdf(path, _models, batch_multiplier=1)
    return full_text, images or {}, out_meta or {}


# ── chapter splitting ─────────────────────────────────────────────────────────

_H1_RE = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)


def _split_chapters(full_text: str) -> list[ParsedChapter]:
    """Split Marker Markdown output at top-level headings."""
    positions = [(m.start(), m.group(1)) for m in _H1_RE.finditer(full_text)]

    if not positions:
        score = qa_score(full_text)
        return [ParsedChapter(
            slug="01-full-text",
            content_md=full_text,
            images=[],
            qa_score_value=score,
        )]

    chapters: list[ParsedChapter] = []
    for idx, (start, heading) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(full_text)
        content = full_text[start:end].strip()
        chapters.append(ParsedChapter(
            slug=chapter_slug(idx + 1, heading),
            content_md=content,
            images=[],  # images embedded in Markdown as base64 refs — phase-03 extracts
            qa_score_value=qa_score(content),
        ))

    return chapters


def _split_authors(raw: str) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in re.split(r"[;,]", raw) if a.strip()]
