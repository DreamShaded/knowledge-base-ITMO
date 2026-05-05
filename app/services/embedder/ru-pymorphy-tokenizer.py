"""
Russian morphological tokenizer using pymorphy3 for BM25 lemmatization (ADR-0003).
Cyrillic words → pymorphy3 normal_form (lemma); Latin/digits pass through unchanged.
Dispatch by unicode code-point range; lemmatizer instance created once and reused.
"""
from __future__ import annotations

import re
import unicodedata

_CYRILLIC_RE = re.compile(r"^[а-яёА-ЯЁ]+$")
_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)

_morph = None


def _get_morph():
    global _morph
    if _morph is None:
        import pymorphy3
        _morph = pymorphy3.MorphAnalyzer()
    return _morph


def is_cyrillic_word(token: str) -> bool:
    return bool(_CYRILLIC_RE.match(token))


def lemmatize_token(token: str) -> str:
    """Return lemma for cyrillic words; other tokens returned as-is."""
    if is_cyrillic_word(token):
        morph = _get_morph()
        parses = morph.parse(token)
        if parses:
            return parses[0].normal_form
    return token.lower()


def lemmatize_text(text: str) -> str:
    """
    Lemmatize all tokens in text.
    Cyrillic words → normal_form; English terms, digits, abbreviations → lowercase.
    """
    def _replace(m: re.Match) -> str:
        return lemmatize_token(m.group(0))

    return _WORD_RE.sub(_replace, text)
