"""
Privacy-first sanitization gate (ADR-0005, PRD §7.3).
Strict mode: detect and remove PII patterns; block if any found.
Permissive mode: warn but pass sanitized query through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── PII pattern regexes ───────────────────────────────────────────────────────

# Obsidian wiki-links: [[...]]
_WIKI_LINK_RE = re.compile(r"\[\[[^\]]+\]\]")

# Hashtags: #word (standalone)
_TAG_RE = re.compile(r"(?<!\w)#[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9_-]+")

# Russian first-person pronouns (nominative + possessives)
_RU_PRONOUN_RE = re.compile(
    r"\b(я|мне|меня|мой|моя|моё|мои|моим|моей|моих|моему|моею|нашего|наш|наша|наше|наши)\b",
    re.IGNORECASE,
)

# English first-person pronouns
_EN_PRONOUN_RE = re.compile(
    r"\b(I|me|my|mine|myself|our|ours|we|us)\b",
    re.IGNORECASE,
)


@dataclass
class SanitizationResult:
    is_safe: bool
    sanitized_query: str
    blocked_patterns: list[str] = field(default_factory=list)
    warning: str | None = None


def sanitize_query(
    query: str,
    sensitive_entities: list[str] | None = None,
    mode: str = "strict",
) -> SanitizationResult:
    """
    Sanitize a query before sending to external search.

    Strict mode: if any PII pattern found → is_safe=False (block).
    Permissive mode: return sanitized text with warning, is_safe=True.
    """
    cleaned = query
    blocked: list[str] = []

    # 1. Wiki-links
    wiki_matches = _WIKI_LINK_RE.findall(cleaned)
    if wiki_matches:
        blocked.extend(wiki_matches)
        cleaned = _WIKI_LINK_RE.sub("", cleaned)

    # 2. Hashtags
    tag_matches = _TAG_RE.findall(cleaned)
    if tag_matches:
        blocked.extend(tag_matches)
        cleaned = _TAG_RE.sub("", cleaned)

    # 3. Sensitive entities (per-vault whitelist)
    for entity in (sensitive_entities or []):
        if not entity.strip():
            continue
        entity_re = re.compile(re.escape(entity.strip()), re.IGNORECASE)
        if entity_re.search(cleaned):
            blocked.append(entity.strip())
            cleaned = entity_re.sub("[ANON]", cleaned)

    # 4. First-person pronouns (Russian)
    ru_matches = _RU_PRONOUN_RE.findall(cleaned)
    if ru_matches:
        blocked.extend(ru_matches)
        cleaned = _RU_PRONOUN_RE.sub("[ANON]", cleaned)

    # 5. First-person pronouns (English)
    en_matches = _EN_PRONOUN_RE.findall(cleaned)
    if en_matches:
        blocked.extend(en_matches)
        cleaned = _EN_PRONOUN_RE.sub("[ANON]", cleaned)

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    if blocked:
        warning = f"Query contained {len(blocked)} private pattern(s): {blocked[:3]!r}"
        if mode == "strict":
            return SanitizationResult(
                is_safe=False,
                sanitized_query=cleaned,
                blocked_patterns=blocked,
                warning=warning,
            )
        # Permissive: pass with warning
        return SanitizationResult(
            is_safe=True,
            sanitized_query=cleaned,
            blocked_patterns=blocked,
            warning=warning,
        )

    return SanitizationResult(is_safe=True, sanitized_query=cleaned)
