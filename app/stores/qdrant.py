"""
Qdrant store: init, health, collection lifecycle, schema-mismatch detection (ADR-0011).
Phase-03: adds vault_id/chapter_id payload indexes, schema record writes, mismatch guard.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.logging import get_logger

log = get_logger(__name__)


@dataclass
class QdrantStore:
    path: str
    mode: str = "embedded"
    url: str = "http://localhost:6333"
    collection_version: int = 1
    dense_size: int = 1024

    def __post_init__(self) -> None:
        self._client = None

    @property
    def collection_name(self) -> str:
        return f"chunks_v{self.collection_version}"

    def init(self) -> None:
        """Open or create the Qdrant store and ensure the chunks collection exists."""
        from pathlib import Path
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance, VectorParams, SparseVectorParams, SparseIndexParams,
            Modifier, PayloadSchemaType,
        )

        if self.mode == "server":
            self._client = QdrantClient(url=self.url)
            log.info("connected_server", url=self.url)
        else:
            Path(self.path).mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=self.path)
            log.info("opened_embedded", path=self.path)

        existing = {c.name for c in self._client.get_collections().collections}
        if self.collection_name not in existing:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config={"dense": VectorParams(
                    size=self.dense_size,
                    distance=Distance.COSINE,
                    on_disk=True,
                )},
                sparse_vectors_config={"sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=True),
                    modifier=Modifier.IDF,
                )},
            )
            for field_name, schema in [
                ("vault_id",     PayloadSchemaType.KEYWORD),
                ("source_type",  PayloadSchemaType.KEYWORD),
                ("trust_tier",   PayloadSchemaType.KEYWORD),
                ("chapter_id",   PayloadSchemaType.KEYWORD),
                ("source_id",    PayloadSchemaType.KEYWORD),
                ("project",      PayloadSchemaType.KEYWORD),
                ("removed",      PayloadSchemaType.BOOL),
            ]:
                self._client.create_payload_index(self.collection_name, field_name, schema)
            log.info("collection_created", name=self.collection_name)
        else:
            log.info("collection_exists", name=self.collection_name)

    def write_schema_record(
        self,
        sqlite_path: str,
        embed_model_id: str,
        mrl_dim: int,
        instruct_template_hash: str,
        app_version: str = "0.1.0",
    ) -> None:
        """Write collection metadata to qdrant_schema table (called after collection create)."""
        import sqlite3
        from datetime import datetime, timezone

        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO qdrant_schema
                    (collection_name, embed_model_id, mrl_dim, sparse_modifier,
                     instruct_template_hash, created_at, created_by_app_version)
                VALUES (?, ?, ?, 'IDF', ?, ?, ?)
            """, (
                self.collection_name,
                embed_model_id,
                mrl_dim,
                instruct_template_hash,
                datetime.now(tz=timezone.utc).isoformat(),
                app_version,
            ))
            conn.commit()
        finally:
            conn.close()

    def check_schema_mismatch(
        self,
        sqlite_path: str,
        embed_model_id: str,
        mrl_dim: int,
        instruct_template_hash: str,
    ) -> bool:
        """
        Compare stored schema record against current config.
        Returns True if mismatch detected (logs warning). App continues on old collection.
        """
        import sqlite3

        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM qdrant_schema WHERE collection_name=?",
                (self.collection_name,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return False

        mismatches = []
        if row["embed_model_id"] != embed_model_id:
            mismatches.append(f"embed_model_id: {row['embed_model_id']} → {embed_model_id}")
        if row["mrl_dim"] != mrl_dim:
            mismatches.append(f"mrl_dim: {row['mrl_dim']} → {mrl_dim}")
        if row["instruct_template_hash"] != instruct_template_hash:
            mismatches.append("instruct_template_hash changed")

        if mismatches:
            log.warning(
                "schema_mismatch",
                collection=self.collection_name,
                mismatches=mismatches,
                hint="Run `python -m app reindex --new-version` to migrate",
            )
            return True
        return False

    def health(self) -> dict:
        if self._client is None:
            return {"status": "not_initialized"}
        try:
            info = self._client.get_collection(self.collection_name)
            return {
                "status": "ok",
                "collection": self.collection_name,
                "points_count": info.points_count,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("QdrantStore not initialized — call init() first")
        return self._client
