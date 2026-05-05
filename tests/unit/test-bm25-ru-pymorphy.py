"""
Tests for Russian morphological lemmatization in BM25 tokenizer.
Acceptance: cyrillic words are lemmatized; English/digits pass through.
Tests run without FastEmbed model download — only the lemmatizer module is loaded.
"""
import importlib.util
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "embedder"


def _load(stem):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pymorphy_mod = _load("ru-pymorphy-tokenizer")


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class TestCyrillicDetection:
    def test_cyrillic_word_detected(self):
        assert pymorphy_mod.is_cyrillic_word("префикс") is True
        assert pymorphy_mod.is_cyrillic_word("Слово") is True

    def test_latin_word_not_cyrillic(self):
        assert pymorphy_mod.is_cyrillic_word("prefix") is False
        assert pymorphy_mod.is_cyrillic_word("RxJS") is False

    def test_digit_string_not_cyrillic(self):
        assert pymorphy_mod.is_cyrillic_word("123") is False

    def test_mixed_not_cyrillic(self):
        assert pymorphy_mod.is_cyrillic_word("слово123") is False


class TestLemmatization:
    def test_noun_plural_to_singular(self):
        """«префиксы» (plural) should normalize to «префикс» (singular)."""
        lemma_singular = pymorphy_mod.lemmatize_token("префикс")
        lemma_plural = pymorphy_mod.lemmatize_token("префиксы")
        assert lemma_singular == lemma_plural, (
            f"Expected same lemma: got {lemma_singular!r} vs {lemma_plural!r}"
        )

    def test_noun_nominative_is_its_own_lemma(self):
        lemma = pymorphy_mod.lemmatize_token("префикс")
        assert lemma == "префикс"

    def test_verb_forms_share_lemma(self):
        """«читает», «читал», «читать» → same lemma «читать»."""
        forms = ["читает", "читал", "читать"]
        lemmas = [pymorphy_mod.lemmatize_token(w) for w in forms]
        assert len(set(lemmas)) == 1, f"Expected 1 unique lemma, got: {lemmas}"

    def test_english_word_passes_through_lowercased(self):
        result = pymorphy_mod.lemmatize_token("RxJS")
        assert result == "rxjs"

    def test_digits_pass_through(self):
        result = pymorphy_mod.lemmatize_token("2024")
        assert result == "2024"

    def test_abbreviation_passes_through_lowercased(self):
        result = pymorphy_mod.lemmatize_token("PCRE")
        assert result == "pcre"


class TestTextLemmatization:
    def test_mixed_text_lemmatizes_russian_only(self):
        text = "реактивность и reactive systems"
        result = pymorphy_mod.lemmatize_text(text)
        # «реактивность» should be lemmatized; «reactive», «systems» pass through
        assert "reactive" in result
        assert "systems" in result

    def test_prefix_forms_in_text(self):
        """All morphological variants of «префикс» in one sentence → same root in output."""
        text = "префикс префиксы"
        result = pymorphy_mod.lemmatize_text(text)
        words = result.split()
        assert len(set(words)) == 1, f"Expected both to normalize to same lemma, got: {words}"

    def test_empty_text(self):
        assert pymorphy_mod.lemmatize_text("") == ""

    def test_digits_in_text_preserved(self):
        result = pymorphy_mod.lemmatize_text("версия 3 2024")
        assert "3" in result
        assert "2024" in result
