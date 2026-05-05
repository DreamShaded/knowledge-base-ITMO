"""Markdown frontmatter parser for Tier-0 graph extraction.

Extracts tags, aliases, project, area, created from YAML/TOML frontmatter
using python-frontmatter library.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FrontmatterResult:
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    project: str = ""
    area: str = ""
    created: str = ""
    raw: dict = field(default_factory=dict)


def parse_frontmatter(content: str) -> FrontmatterResult:
    """Extract structured metadata from YAML/TOML frontmatter block."""
    try:
        import frontmatter as fm
        post = fm.loads(content)
        meta = dict(post.metadata)
    except Exception:
        meta = {}

    tags = _extract_list(meta, "tags")
    # Also collect inline #tags from text body (below frontmatter)
    tags = list(dict.fromkeys(tags + _extract_inline_tags(content)))

    return FrontmatterResult(
        tags=tags,
        aliases=_extract_list(meta, "aliases"),
        project=_str_field(meta, "project"),
        area=_str_field(meta, "area"),
        created=_str_field(meta, "created"),
        raw=meta,
    )


def _extract_list(meta: dict, key: str) -> list[str]:
    val = meta.get(key, [])
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    if isinstance(val, list):
        return [str(v).strip() for v in val if v]
    return []


def _str_field(meta: dict, key: str) -> str:
    val = meta.get(key, "")
    return str(val).strip() if val else ""


def _extract_inline_tags(content: str) -> list[str]:
    """Collect #hashtag occurrences from the note body (not frontmatter)."""
    import re
    # Skip frontmatter block (between --- delimiters)
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
    # Match #tag (Cyrillic + ASCII word chars, at least 2 chars)
    found = re.findall(r"(?<!\w)#([\wЀ-ӿ]{2,}(?:[/\-][\wЀ-ӿ]+)*)", body)
    return [t.lower() for t in found]
