"""
Privacy sanitization acceptance tests — PRD §11 п.9.
6 gold-set cases in strict mode MUST all be blocked (leak_rate = 0.0).
"""
import pytest

from app.services.web.sanitization import sanitize_query


class TestStrictModeBlocks:
    """All 6 acceptance cases must return is_safe=False in strict mode."""

    def test_russian_wiki_link(self):
        result = sanitize_query("Проверь [[личная заметка]] для деталей", mode="strict")
        assert not result.is_safe
        assert any("[[личная заметка]]" in p for p in result.blocked_patterns)

    def test_english_wiki_link(self):
        result = sanitize_query("See [[My Private Journal]] for context", mode="strict")
        assert not result.is_safe
        assert any("[[My Private Journal]]" in p for p in result.blocked_patterns)

    def test_hashtag_confidential(self):
        result = sanitize_query("Tagged as #confidential in my notes", mode="strict")
        assert not result.is_safe
        assert any("#confidential" in p for p in result.blocked_patterns)

    def test_hashtag_russian(self):
        result = sanitize_query("Помечено как #секретно в архиве", mode="strict")
        assert not result.is_safe
        assert any("#секретно" in p for p in result.blocked_patterns)

    def test_named_entity_russian(self):
        result = sanitize_query(
            "Иван Иванов написал этот документ",
            sensitive_entities=["Иван Иванов"],
            mode="strict",
        )
        assert not result.is_safe
        assert "Иван Иванов" in result.blocked_patterns

    def test_first_person_pronoun_russian(self):
        result = sanitize_query("мой план для проекта X", mode="strict")
        assert not result.is_safe
        assert result.blocked_patterns  # "мой" detected


class TestStrictModeAllowsClean:
    def test_clean_query_passes(self):
        result = sanitize_query("What is Module Federation?", mode="strict")
        assert result.is_safe
        assert not result.blocked_patterns
        assert result.warning is None

    def test_clean_russian_query_passes(self):
        result = sanitize_query("Что такое реактивность в Vue?", mode="strict")
        assert result.is_safe


class TestPermissiveMode:
    def test_wiki_link_warned_but_passes(self):
        result = sanitize_query("Check [[note]] please", mode="permissive")
        assert result.is_safe  # permissive: not blocked
        assert result.warning is not None
        assert "[[note]]" not in result.sanitized_query  # still removed

    def test_hashtag_warned_but_passes(self):
        result = sanitize_query("Topic #secret here", mode="permissive")
        assert result.is_safe
        assert "#secret" not in result.sanitized_query


class TestSanitizedQueryContent:
    def test_wiki_links_removed_from_sanitized(self):
        result = sanitize_query("Ask about [[private]] topic", mode="strict")
        assert "[[private]]" not in result.sanitized_query

    def test_entity_replaced_with_anon(self):
        result = sanitize_query(
            "John Smith wrote this",
            sensitive_entities=["John Smith"],
            mode="permissive",
        )
        assert "John Smith" not in result.sanitized_query
        assert "[ANON]" in result.sanitized_query

    def test_pronoun_replaced_with_anon(self):
        result = sanitize_query("my personal project notes", mode="strict")
        assert result.blocked_patterns
        assert "my" not in result.sanitized_query or "[ANON]" in result.sanitized_query
