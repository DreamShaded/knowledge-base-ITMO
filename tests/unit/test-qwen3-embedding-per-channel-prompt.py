"""
Tests that Qwen3EmbeddingLocal dispatches correct per-channel prompts.
Model is mocked — tests verify prompt loading + dispatch logic without downloading weights.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).parent.parent.parent / "app" / "services" / "embedder"
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "infra" / "prompts"


def _load(stem):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


emb_mod = _load("qwen3-embedding")
Qwen3EmbeddingLocal = emb_mod.Qwen3EmbeddingLocal
_load_prompts = emb_mod._load_prompts
compute_template_hash = emb_mod.compute_template_hash


class TestPromptLoading:
    def test_all_four_channels_loaded(self):
        prompts = _load_prompts()
        for ch in ("notes", "books", "web_saved", "web"):
            assert ch in prompts, f"Missing prompt for channel: {ch}"

    def test_prompts_non_empty(self):
        prompts = _load_prompts()
        for ch, text in prompts.items():
            assert text.strip(), f"Empty prompt for channel: {ch}"

    def test_query_placeholder_stripped(self):
        prompts = _load_prompts()
        for ch, text in prompts.items():
            assert "{query}" not in text, (
                f"Channel {ch!r} prompt still contains {{query}} placeholder"
            )

    def test_notes_prompt_contains_instruct_prefix(self):
        prompts = _load_prompts()
        assert prompts["notes"].startswith("Instruct:")

    def test_template_hash_is_deterministic(self):
        h1 = compute_template_hash(_load_prompts())
        h2 = compute_template_hash(_load_prompts())
        assert h1 == h2

    def test_template_hash_changes_with_different_prompts(self):
        p1 = {"notes": "A", "books": "B", "web_saved": "C", "web": "D"}
        p2 = {"notes": "X", "books": "B", "web_saved": "C", "web": "D"}
        assert compute_template_hash(p1) != compute_template_hash(p2)


class TestChannelDispatch:
    def _make_embedder_with_mock(self):
        embedder = Qwen3EmbeddingLocal(model_id="test-model", device="cpu", truncate_dim=1024)
        # Inject pre-loaded prompts and a mock ST model
        embedder._prompts = _load_prompts()
        mock_model = MagicMock()
        import numpy as np
        mock_model.encode.return_value = np.zeros((1, 1024))
        embedder._model = mock_model
        return embedder, mock_model

    @pytest.mark.asyncio
    async def test_notes_channel_uses_prompt_name(self):
        embedder, mock_model = self._make_embedder_with_mock()
        await embedder.embed_query("test query", channel="notes")
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs.kwargs.get("prompt_name") == "notes"

    @pytest.mark.asyncio
    async def test_books_channel_uses_prompt_name(self):
        embedder, mock_model = self._make_embedder_with_mock()
        await embedder.embed_query("test query", channel="books")
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs.kwargs.get("prompt_name") == "books"

    @pytest.mark.asyncio
    async def test_unknown_channel_uses_none_prompt(self):
        embedder, mock_model = self._make_embedder_with_mock()
        await embedder.embed_query("test query", channel="unknown_channel")
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs.kwargs.get("prompt_name") is None

    @pytest.mark.asyncio
    async def test_embed_passages_uses_no_prompt(self):
        embedder, mock_model = self._make_embedder_with_mock()
        import numpy as np
        mock_model.encode.return_value = np.zeros((2, 1024))
        await embedder.embed_passages(["passage one", "passage two"])
        call_kwargs = mock_model.encode.call_args
        # passages must NOT have prompt_name (passes empty prompt)
        assert "prompt_name" not in call_kwargs.kwargs or call_kwargs.kwargs.get("prompt_name") is None

    @pytest.mark.asyncio
    async def test_embed_query_returns_vector1024_length(self):
        embedder, _ = self._make_embedder_with_mock()
        result = await embedder.embed_query("query text", channel="notes")
        assert len(result) == 1024
