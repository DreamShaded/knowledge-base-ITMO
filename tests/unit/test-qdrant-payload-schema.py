"""
Tests for Qdrant payload schema correctness and upsert helpers.
Runs without a real Qdrant instance — mocks the client.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_APP = Path(__file__).parent.parent.parent / "app"


def _load_module(path: Path, name: str):
    import sys
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# base.py has no hyphen — import normally so isinstance in upsert.py resolves same class
from app.services.chunker.base import Chunk, ParentChunk  # noqa: E402

upsert_mod = _load_module(_APP / "services" / "indexer" / "upsert.py", "upsert")
_build_payload = upsert_mod._build_payload
stable_point_id = upsert_mod.stable_point_id


def make_chunk(**kwargs):
    defaults = dict(
        chunk_id="a" * 16,
        parent_id="b" * 16,
        source_type="note",
        source_id="note-001",
        vault_id="personal",
        chapter_id="",
        project="",
        tags=["python"],
        trust_tier="tier1",
        content="sample text",
        content_hash="c" * 16,
        chunk_index=0,
        modified_at="2026-04-29T00:00:00Z",
        removed=False,
    )
    defaults.update(kwargs)
    return Chunk(**defaults)


def make_parent(**kwargs):
    defaults = dict(
        chunk_id="d" * 16,
        parent_id="",
        source_type="note",
        source_id="note-001",
        vault_id="personal",
        chapter_id="",
        project="",
        tags=[],
        trust_tier="tier1",
        content="parent text",
        content_hash="e" * 16,
        chunk_index=0,
        modified_at="2026-04-29T00:00:00Z",
        removed=False,
    )
    defaults.update(kwargs)
    return ParentChunk(**defaults)


REQUIRED_PAYLOAD_FIELDS = {
    "chunk_id", "parent_id", "source_type", "source_id",
    "vault_id", "chapter_id", "project", "tags",
    "trust_tier", "content", "content_hash", "modified_at", "removed",
}


class TestPayloadSchema:
    def test_child_payload_has_all_required_fields(self):
        chunk = make_chunk()
        payload = _build_payload(chunk)
        missing = REQUIRED_PAYLOAD_FIELDS - set(payload.keys())
        assert not missing, f"Missing payload fields: {missing}"

    def test_parent_payload_has_all_required_fields(self):
        parent = make_parent()
        payload = _build_payload(parent)
        missing = REQUIRED_PAYLOAD_FIELDS - set(payload.keys())
        assert not missing, f"Missing payload fields: {missing}"

    def test_vault_id_in_payload(self):
        chunk = make_chunk(vault_id="work")
        payload = _build_payload(chunk)
        assert payload["vault_id"] == "work"

    def test_removed_defaults_false(self):
        chunk = make_chunk()
        payload = _build_payload(chunk)
        assert payload["removed"] is False

    def test_parent_flag_set_in_payload(self):
        parent = make_parent()
        payload = _build_payload(parent)
        assert payload["is_parent"] is True

    def test_child_is_parent_false(self):
        chunk = make_chunk()
        payload = _build_payload(chunk)
        assert payload["is_parent"] is False

    def test_tags_is_list(self):
        chunk = make_chunk(tags=["ai", "rag"])
        payload = _build_payload(chunk)
        assert isinstance(payload["tags"], list)
        assert payload["tags"] == ["ai", "rag"]


class TestStablePointId:
    def test_deterministic(self):
        assert stable_point_id("abc" * 6) == stable_point_id("abc" * 6)

    def test_different_ids_differ(self):
        assert stable_point_id("a" * 16) != stable_point_id("b" * 16)

    def test_returns_int(self):
        assert isinstance(stable_point_id("a" * 16), int)

    def test_fits_uint64(self):
        pid = stable_point_id("f" * 16)
        assert 0 <= pid < 2**64


class TestUpsertIdempotency:
    def _make_store(self, existing_hashes: dict):
        """Create a mock QdrantStore whose client returns specific existing hashes."""
        store = MagicMock()
        store.collection_name = "chunks_v1"

        def mock_retrieve(col, ids, with_payload):
            results = []
            for pid, h in existing_hashes.items():
                r = MagicMock()
                r.id = pid
                r.payload = {"content_hash": h}
                results.append(r)
            return results

        store.client.retrieve.side_effect = mock_retrieve
        store.client.upsert = MagicMock()
        return store

    def test_unchanged_chunk_skipped(self):
        chunk = make_chunk(chunk_id="a" * 16, content_hash="c" * 16)
        pid = stable_point_id(chunk.chunk_id)
        store = self._make_store({pid: "c" * 16})  # same hash

        from app.di.ports import SparseVec
        written = upsert_mod.upsert_chunks(
            store, [chunk], [[0.0] * 1024], [SparseVec(indices=[1], values=[1.0])]
        )
        assert written == 0
        store.client.upsert.assert_not_called()

    def test_changed_chunk_upserted(self):
        chunk = make_chunk(chunk_id="a" * 16, content_hash="new_hash_here__")
        pid = stable_point_id(chunk.chunk_id)
        store = self._make_store({pid: "old_hash_here__"})  # different hash

        from app.di.ports import SparseVec
        written = upsert_mod.upsert_chunks(
            store, [chunk], [[0.0] * 1024], [SparseVec(indices=[1], values=[1.0])]
        )
        assert written == 1

    def test_new_chunk_always_upserted(self):
        chunk = make_chunk(chunk_id="b" * 16, content_hash="fresh__hash____")
        store = self._make_store({})  # nothing in store

        from app.di.ports import SparseVec
        written = upsert_mod.upsert_chunks(
            store, [chunk], [[0.0] * 1024], [SparseVec(indices=[1], values=[1.0])]
        )
        assert written == 1
