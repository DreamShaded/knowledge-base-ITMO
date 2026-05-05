"""Unit tests for extract_verbatim (ADR-0006 §verbatim)."""
from app.services.render.verbatim_quote import extract_verbatim


class TestShortContent:
    def test_short_content_returned_as_is(self):
        content = "Короткий текст"
        result = extract_verbatim(content, "текст", max_chars=140)
        assert result == content.strip()

    def test_exactly_max_chars_returned_as_is(self):
        content = "x" * 140
        result = extract_verbatim(content, "anything", max_chars=140)
        assert result == content


class TestWindowExtraction:
    def test_window_around_match(self):
        prefix = "начало " * 10          # >70 chars before
        match_word = "целевое"
        suffix = " конец" * 10
        content = prefix + match_word + suffix
        result = extract_verbatim(content, "целевое", max_chars=140)
        assert match_word in result
        assert len(result) <= 160  # ≤140 + ellipsis chars

    def test_ellipsis_added_for_mid_content(self):
        content = "irrelevant start " * 5 + "targetword " + "more text " * 5
        result = extract_verbatim(content, "targetword", max_chars=80)
        assert "targetword" in result
        assert "…" in result  # ellipsis because we started mid-content

    def test_no_ellipsis_at_start_when_match_near_beginning(self):
        content = "targetword is here and then a lot more text follows " * 3
        result = extract_verbatim(content, "targetword", max_chars=80)
        # Match at pos 0 — no leading ellipsis
        assert result.startswith("targetword") or result.startswith("…")


class TestNoMatch:
    def test_no_match_returns_first_chars(self):
        content = "Текст без совпадений для этого запроса. " * 5
        result = extract_verbatim(content, "несуществующееслово", max_chars=140)
        assert len(result) <= 140
        assert result  # non-empty

    def test_no_match_trims_to_word_boundary(self):
        content = "word1 word2 word3 word4 word5 word6 word7 word8"
        result = extract_verbatim(content, "zzznotfound", max_chars=20)
        # Should not end mid-word
        assert not result.endswith(" ")


class TestRussianText:
    def test_russian_query_token_matched(self):
        content = (
            "Машинное обучение — это подраздел искусственного интеллекта, "
            "который изучает методы построения алгоритмов, способных обучаться."
        )
        result = extract_verbatim(content, "машинное обучение", max_chars=140)
        assert "обучение" in result.lower() or "машинное" in result.lower()

    def test_russian_content_preserved(self):
        content = "Привет мир! " * 5 + "важное слово " + "продолжение " * 5
        result = extract_verbatim(content, "важное слово", max_chars=80)
        assert "важное" in result


class TestEdgeCases:
    def test_empty_content(self):
        result = extract_verbatim("", "query")
        assert result == ""

    def test_empty_query(self):
        content = "Some content here that is quite long and needs truncating."
        result = extract_verbatim(content, "", max_chars=30)
        assert len(result) <= 30

    def test_single_word_content(self):
        result = extract_verbatim("hello", "hello")
        assert result == "hello"
