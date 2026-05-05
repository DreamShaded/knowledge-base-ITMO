"""Rule-based query intent scorer (ADR-0004 §2).

Returns confidence scores per intent category using regex heuristics
and keyword bags. Skips LLM router when max confidence > 0.8.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.router.decision import QueryIntent

INTENTS: tuple[str, ...] = (
    "lookup", "multi_hop", "personal", "educational", "fresh", "comparison", "refusal"
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")
_WIKILINK_RE = re.compile(r"\[\[.+?\]\]")
_GIBBERISH_RE = re.compile(r"[^\w\s\?\.,\-\!\:\;\(\)\[\]]", re.UNICODE)

_LOOKUP_KW = frozenset([
    "что такое", "определение", "когда", "кто такой", "кто написал",
    "дата", "когда был", "год", "в каком", "who is", "what is", "when was",
])
_MULTI_HOP_KW = frozenset([
    "связь", "связано", "как связаны", "относится к", "между", "взаимосвязь",
    "общие темы", "common themes", "в контексте", "hidden links",
])
_PERSONAL_KW = frozenset([
    "я писал", "мои заметки", "у меня есть", "я думал", "мой", "мои",
    "я изучал", "я читал", "я работал", "в моих", "по моим", "в своих",
    "my notes", "i wrote", "i was thinking",
])
_EDUCATIONAL_KW = frozenset([
    "объясни", "как работает", "расскажи о", "объяснить", "научи",
    "разберись", "помоги понять", "explain", "how does", "tutorial",
])
_FRESH_KW = frozenset([
    "последние новости", "актуально", "сейчас", "текущий", "новый",
    "latest", "recent", "current", "today", "news",
])
_COMPARISON_KW = frozenset([
    "отличие", "разница", "сравни", "vs", "versus", "чем отличается",
    "сравнение", "compare", "difference between", "pros and cons",
])
_REFUSAL_KW = frozenset([
    "ignore previous", "ignore all", "system prompt", "jailbreak",
    "забудь всё", "forget all instructions", "act as", "pretend you are",
])

import datetime as _dt
_CURRENT_YEAR = _dt.date.today().year


def score(query: str) -> dict[str, float]:
    """Return confidence scores {intent: 0.0–1.0} for all categories."""
    q = query.lower().strip()
    scores: dict[str, float] = {intent: 0.0 for intent in INTENTS}

    # Refusal signals
    if any(kw in q for kw in _REFUSAL_KW):
        scores["refusal"] = 0.9
    word_count = len(q.split())
    non_word = len(_GIBBERISH_RE.findall(q))
    if word_count <= 1 and non_word > 0:
        scores["refusal"] = max(scores["refusal"], 0.6)

    # Lookup: question words + dates/years
    if _YEAR_RE.search(q) or _DATE_RE.search(q):
        scores["lookup"] = max(scores["lookup"], 0.5)
    if any(kw in q for kw in _LOOKUP_KW):
        scores["lookup"] = max(scores["lookup"], 0.75)

    # Multi-hop: wikilinks or relational keywords
    if _WIKILINK_RE.search(query):  # case-sensitive for [[WikiLinks]]
        scores["multi_hop"] = max(scores["multi_hop"], 0.85)
    if any(kw in q for kw in _MULTI_HOP_KW):
        scores["multi_hop"] = max(scores["multi_hop"], 0.65)

    # Personal: first-person possessives/verbs
    if any(kw in q for kw in _PERSONAL_KW):
        scores["personal"] = max(scores["personal"], 0.85)

    # Educational: explanation requests
    if any(kw in q for kw in _EDUCATIONAL_KW):
        scores["educational"] = max(scores["educational"], 0.75)

    # Fresh: recency signals
    years_found = [int(m) for m in _YEAR_RE.findall(q)]
    if any(y >= _CURRENT_YEAR - 1 for y in years_found):
        scores["fresh"] = max(scores["fresh"], 0.6)
    if any(kw in q for kw in _FRESH_KW):
        scores["fresh"] = max(scores["fresh"], 0.65)

    # Comparison: comparative keywords
    if any(kw in q for kw in _COMPARISON_KW):
        scores["comparison"] = max(scores["comparison"], 0.75)

    # Default fallback: assign weak lookup if nothing matched
    if max(scores.values()) == 0.0:
        scores["lookup"] = 0.4

    return scores


def best_intent(query: str) -> tuple[str, float]:
    """Return (intent, confidence) for the highest-scoring category."""
    s = score(query)
    intent = max(s, key=lambda k: s[k])
    return intent, s[intent]
