"""Extract a verbatim quote window from source content relative to query tokens."""
from __future__ import annotations

import re

# Stop-words to skip when tokenising the query (Russian + English common words)
_STOP_WORDS: frozenset[str] = frozenset([
    "и", "в", "не", "на", "что", "с", "он", "как", "это", "из", "о", "по",
    "все", "то", "её", "мне", "ли", "же", "а", "для", "да", "от", "за",
    "the", "a", "an", "is", "in", "of", "to", "for", "and", "or", "it",
])


def _tokenize_query(query: str) -> list[str]:
    """Return significant lowercase tokens from query text."""
    tokens = re.findall(r"[a-zA-Zа-яёА-ЯЁ0-9]+", query.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 2]


def extract_verbatim(content: str, query: str, max_chars: int = 140) -> str:
    """Extract a verbatim quote from *content* relevant to *query*.

    Algorithm:
    1. Tokenize query to significant words.
    2. Find position of first query-token match in content.
    3. Take a window of ±(max_chars/2) chars around match, trim to whitespace.
    4. If content ≤ max_chars or no match found, return first max_chars chars.
    """
    if len(content) <= max_chars:
        return content.strip()

    tokens = _tokenize_query(query)
    content_lower = content.lower()

    match_pos: int | None = None
    for token in tokens:
        idx = content_lower.find(token)
        if idx != -1:
            match_pos = idx
            break

    if match_pos is None:
        # No match: return first max_chars chars trimmed to last whitespace
        excerpt = content[:max_chars]
        last_space = excerpt.rfind(" ")
        return excerpt[:last_space].strip() if last_space > 0 else excerpt.strip()

    half = max_chars // 2
    start = max(0, match_pos - half)
    end = min(len(content), match_pos + half)

    # Expand to word boundaries
    if start > 0:
        space_before = content.rfind(" ", 0, start)
        start = (space_before + 1) if space_before >= 0 else start

    if end < len(content):
        space_after = content.find(" ", end)
        end = space_after if space_after >= 0 else end

    excerpt = content[start:end].strip()

    # Prefix ellipsis if we started mid-text
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(content):
        excerpt = excerpt + "…"

    return excerpt
