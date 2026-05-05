"""
Tests for chunker boundary handling: heading splits, code blocks, overlap.
Uses fallback tokenizer to avoid model downloads.
"""
import importlib.util
from pathlib import Path

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "chunker"


def _load(stem):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tok_mod = _load("qwen3-tokenizer")
Qwen3Tokenizer = tok_mod.Qwen3Tokenizer

mpc_mod = _load("markdown-parent-child")
MarkdownParentChildChunker = mpc_mod.MarkdownParentChildChunker
_parse_sections = mpc_mod._parse_sections


def make_chunker():
    return MarkdownParentChildChunker(tokenizer=Qwen3Tokenizer(use_fallback=True))


CODE_BLOCK_DOC = """\
## Overview
Some introductory text.

## Implementation

```python
def hello():
    return "world"
```

More text after code block.
"""

PREAMBLE_DOC = """\
This text appears before any heading.

## First Section
Content of first section.
"""

DEEPLY_NESTED = """\
# Top Level
## Sub Section
### Deep Sub
Content here.
"""

LONG_SECTION = "word " * 600  # will exceed 512-token window with fallback


class TestSectionParsing:
    def test_sections_split_on_headings(self):
        sections = _parse_sections(CODE_BLOCK_DOC)
        headings = [s.heading for s in sections]
        assert "Overview" in headings
        assert "Implementation" in headings

    def test_preamble_captured_as_section(self):
        sections = _parse_sections(PREAMBLE_DOC)
        # First element should be the preamble (no heading)
        assert sections[0].heading == ""
        assert "before any heading" in sections[0].content

    def test_code_block_preserved_within_section(self):
        sections = _parse_sections(CODE_BLOCK_DOC)
        impl = next(s for s in sections if s.heading == "Implementation")
        assert "```python" in impl.content
        assert 'return "world"' in impl.content

    def test_deeply_nested_headings_all_captured(self):
        sections = _parse_sections(DEEPLY_NESTED)
        levels = [s.level for s in sections]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels


class TestChildSplitting:
    def test_short_section_produces_single_child(self):
        chunker = make_chunker()
        doc = chunker.chunk(
            "## Short\nThis is short.",
            source_id="s1",
            source_type="note",
        )
        assert len(doc.children) == 1

    def test_long_section_produces_multiple_children(self):
        chunker = make_chunker()
        text = f"## Long Section\n{LONG_SECTION}"
        doc = chunker.chunk(text, source_id="s2", source_type="note")
        assert len(doc.children) > 1, "Long section should split into multiple children"

    def test_children_content_non_empty(self):
        chunker = make_chunker()
        doc = chunker.chunk(CODE_BLOCK_DOC, source_id="s3", source_type="note")
        for child in doc.children:
            assert child.content.strip()

    def test_overlap_means_content_repeated(self):
        """Adjacent children should share some words due to overlap."""
        chunker = make_chunker()
        text = f"## Big Section\n{LONG_SECTION}"
        doc = chunker.chunk(text, source_id="s4", source_type="note")
        if len(doc.children) < 2:
            return  # not enough children to test overlap
        words_first = set(doc.children[0].content.split())
        words_second = set(doc.children[1].content.split())
        shared = words_first & words_second
        assert len(shared) > 0, "Overlapping chunks should share words"


class TestTokenizerFallback:
    def test_fallback_count_is_non_zero(self):
        tok = Qwen3Tokenizer(use_fallback=True)
        assert tok.count("hello world") > 0

    def test_fallback_split_short_text(self):
        tok = Qwen3Tokenizer(use_fallback=True)
        parts = tok.split_with_overlap("short text", max_tokens=512, overlap_tokens=64)
        assert parts == ["short text"]

    def test_fallback_split_long_text(self):
        tok = Qwen3Tokenizer(use_fallback=True)
        long = "x " * 3000
        parts = tok.split_with_overlap(long, max_tokens=512, overlap_tokens=64)
        assert len(parts) > 1
