"""
Tests for MRL (Matryoshka Representation Learning) truncation in Qwen3EmbeddingLocal.
4B model outputs 4096-dim; must be truncated to 1024 before upsert.
No model download required — uses mocked SentenceTransformer output.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
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


emb_mod = _load("qwen3-embedding")
Qwen3EmbeddingLocal = emb_mod.Qwen3EmbeddingLocal


def _make_embedder(native_dim: int, truncate_dim: int = 1024):
    embedder = Qwen3EmbeddingLocal(
        model_id="test-model",
        device="cpu",
        truncate_dim=truncate_dim,
    )
    embedder._prompts = {"notes": "Instruct: test\nQuery: "}
    mock_model = MagicMock()
    # Return vectors with native_dim dimensions
    mock_model.encode.return_value = np.random.rand(1, native_dim).astype(np.float32)
    embedder._model = mock_model
    return embedder, mock_model


class TestMRLTruncation:
    @pytest.mark.asyncio
    async def test_native_1024_not_truncated(self):
        """0.6B model: native 1024-dim output → 1024-dim Vector1024."""
        embedder, _ = _make_embedder(native_dim=1024, truncate_dim=1024)
        result = await embedder.embed_query("test", channel="notes")
        assert len(result) == 1024

    @pytest.mark.asyncio
    async def test_native_4096_truncated_to_1024(self):
        """4B model: native 4096-dim output → truncated to 1024."""
        embedder, _ = _make_embedder(native_dim=4096, truncate_dim=1024)
        result = await embedder.embed_query("test", channel="notes")
        assert len(result) == 1024

    @pytest.mark.asyncio
    async def test_truncation_preserves_first_dims(self):
        """Truncation must keep the first truncate_dim values, not last or random."""
        embedder, mock_model = _make_embedder(native_dim=4096, truncate_dim=1024)
        original = np.random.rand(1, 4096).astype(np.float32)
        mock_model.encode.return_value = original
        result = await embedder.embed_query("test", channel="notes")
        expected = original[0][:1024].tolist()
        assert result == expected

    @pytest.mark.asyncio
    async def test_embed_passages_all_truncated(self):
        embedder, mock_model = _make_embedder(native_dim=4096, truncate_dim=1024)
        mock_model.encode.return_value = np.random.rand(3, 4096).astype(np.float32)
        results = await embedder.embed_passages(["a", "b", "c"])
        assert len(results) == 3
        for vec in results:
            assert len(vec) == 1024

    @pytest.mark.asyncio
    async def test_custom_truncate_dim_respected(self):
        """truncate_dim=512 should produce 512-dim output."""
        embedder, mock_model = _make_embedder(native_dim=1024, truncate_dim=512)
        result = await embedder.embed_query("test", channel="books")
        assert len(result) == 512
