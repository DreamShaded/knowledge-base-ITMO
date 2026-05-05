"""Markdown heading extractor for Tier-0 graph extraction.

Uses mistune 3.x AST renderer to extract headings without rendering HTML.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Heading:
    level: int   # 1-6
    text: str    # plain text content
    idx: int     # 0-based position within document


def extract_headings(content: str) -> list[Heading]:
    """Parse markdown and return ordered list of headings."""
    import mistune
    md = mistune.create_markdown(renderer=None)
    tokens = md(content) or []
    results: list[Heading] = []
    idx = 0
    for token in _walk_tokens(tokens):
        if token.get("type") == "heading":
            level = token["attrs"]["level"]
            text = _collect_text(token.get("children", []))
            results.append(Heading(level=level, text=text, idx=idx))
            idx += 1
    return results


def _walk_tokens(tokens: list) -> list:
    """Flatten token tree into a plain list."""
    flat = []
    for tok in tokens:
        flat.append(tok)
        if isinstance(tok, dict) and tok.get("children"):
            flat.extend(_walk_tokens(tok["children"]))
    return flat


def _collect_text(children: list) -> str:
    """Recursively extract raw text from a token's children."""
    parts: list[str] = []
    for child in children:
        if isinstance(child, dict):
            raw = child.get("raw", "")
            if raw:
                parts.append(raw)
            elif child.get("children"):
                parts.append(_collect_text(child["children"]))
    return "".join(parts).strip()
