"""
Promotion threshold logic (ADR-0002).
Determines when to upgrade from section-level to document-level parent granularity.
"""
from __future__ import annotations

# note (obsidian): ≥ 3 sections → whole-note parent
# book chapter: ≥ 2 sections → whole-chapter parent
NOTE_SECTION_THRESHOLD = 3
BOOK_SECTION_THRESHOLD = 2


def should_promote_note(section_count: int) -> bool:
    return section_count >= NOTE_SECTION_THRESHOLD


def should_promote_book_chapter(section_count: int) -> bool:
    return section_count >= BOOK_SECTION_THRESHOLD
