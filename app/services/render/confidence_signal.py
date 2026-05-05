"""Compute confidence level from retrieval parents (ADR-0006 §2)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.retrieval.schemas import Parent


def compute_confidence(
    parents: "list[Parent]",
    has_z3: bool = False,
) -> tuple[str, str]:
    """Return (level, reason_str) from retrieval parents.

    Heuristic rules (in priority order):
    - high:   ≥3 distinct sources AND top score > 0.70
    - medium: 1–2 sources OR top score in [0.50, 0.70]
    - low:    weak retrieval OR Z3 dominant OR web-only
    """
    if not parents:
        return "low", "Источники не найдены"

    top_score = max(p.score for p in parents)

    # Count distinct source_ids
    distinct = {p.parent_id for p in parents}
    n_sources = len(distinct)

    # Detect web-only
    source_types = {p.source_meta.source_type for p in parents}
    web_only = source_types <= {"web", "web_saved"}

    note_count = sum(1 for p in parents if p.source_meta.source_type == "note")
    book_count = sum(1 for p in parents if p.source_meta.source_type == "book_chapter")

    if web_only:
        level = "low"
        reason = f"Только веб-источники ({n_sources}), score={top_score:.2f}"
    elif has_z3 and n_sources < 2:
        level = "low"
        reason = f"Доминирует общее знание (Z3), источников мало ({n_sources})"
    elif n_sources >= 3 and top_score > 0.70:
        level = "high"
        parts = []
        if note_count:
            parts.append(f"{note_count} заметок")
        if book_count:
            parts.append(f"{book_count} глав книг")
        web_used = any(t in source_types for t in ("web", "web_saved"))
        parts_str = ", ".join(parts) if parts else f"{n_sources} источников"
        web_str = ", веб использован" if web_used else ""
        reason = f"{parts_str}{web_str}"
    elif n_sources >= 1 and top_score >= 0.50:
        level = "medium"
        parts = []
        if note_count:
            parts.append(f"{note_count} заметок")
        if book_count:
            parts.append(f"{book_count} глав книг")
        parts_str = ", ".join(parts) if parts else f"{n_sources} источников"
        reason = f"{parts_str}, score={top_score:.2f}"
    else:
        level = "low"
        reason = f"Слабое совпадение (score={top_score:.2f}, источников: {n_sources})"

    return level, reason
