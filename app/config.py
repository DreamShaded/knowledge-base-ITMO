"""
App configuration via pydantic-settings v2.
Load chain: Python defaults → infra/config.yaml → infra/profiles/<KB_PROFILE>.yaml → .env → env vars (KB_*)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

REPO_ROOT = Path(__file__).parent.parent
SLUG_RE = re.compile(r"^[a-z][a-z0-9\-]*$")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} patterns in YAML values."""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class YamlFileSource(PydanticBaseSettingsSource):
    """Settings source that loads a YAML file with ${ENV} variable expansion."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self._path = path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with self._path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return _expand_env(data)


# ── Sub-models ────────────────────────────────────────────────────────────────

class VaultConfig(BaseModel):
    id: str
    label: str = ""
    path: Path
    obsidian_uri_name: str = ""
    sensitive_entities_file: Optional[Path] = None

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"vault id must be kebab-slug (got: {v!r})")
        return v


class SourcesConfig(BaseModel):
    vaults: list[VaultConfig] = []
    book_paths: list[Path] = []

    @model_validator(mode="after")
    def no_overlapping_paths(self) -> "SourcesConfig":
        def _str(p: Path) -> str:
            try:
                return str(p.resolve())
            except Exception:
                return str(p.absolute())

        all_paths = [_str(v.path) for v in self.vaults] + [_str(p) for p in self.book_paths]
        for i, a in enumerate(all_paths):
            for j, b in enumerate(all_paths):
                if i >= j:
                    continue
                if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
                    raise ValueError(f"Overlapping source paths: {a!r} and {b!r}")
        return self


class OllamaConfig(BaseModel):
    host: str = "http://localhost:11434"
    chat_model: str = "qwen3:8b"
    keep_alive: str = "-1"
    max_concurrent: int = 1
    timeout: int = 120


class QdrantConfig(BaseModel):
    path: str = "data/qdrant"
    mode: str = "embedded"
    url: str = "http://localhost:6333"
    collection_version: int = 1
    dense_size: int = 1024


class OxigraphConfig(BaseModel):
    path: str = "data/oxigraph"


class SQLiteConfig(BaseModel):
    path: str = "data/sqlite/app.db"


class EmbedderConfig(BaseModel):
    model_id: str = "Qwen/Qwen3-Embedding-0.6B"
    device: str = "cuda"
    truncate_dim: int = 1024
    batch_size: int = 16
    instruct_query: str = "Retrieve semantically similar text"
    instruct_passage: str = ""


class RerankerConfig(BaseModel):
    model_id: str = "Qwen/Qwen3-Reranker-0.6B"
    device: str = "cpu"
    batch_size: int = 8


class MarkerConfig(BaseModel):
    device: str = "cuda"
    gpu_footprint_cap_mb: int = 2000
    workers: int = 1


class KGEConfig(BaseModel):
    batch_size: int = 512
    neg_per_pos: int = 64
    embedding_dim: int = 256


class PlaywrightConfig(BaseModel):
    headless_concurrency: int = 1


class GraphWalkConfig(BaseModel):
    hops_budget: int = 1
    predicate_weights: dict[str, float] = {
        "https://my-pkm.local/ontology#linksTo": 1.0,
        "https://my-pkm.local/ontology#about": 0.9,
        "https://my-pkm.local/ontology#tagged": 0.7,
        "https://my-pkm.local/ontology#project": 0.6,
        "https://my-pkm.local/ontology#author": 0.4,
    }
    hub_blacklist: list[str] = []  # entity URIs with degree > 50 to skip


class WebConfig(BaseModel):
    searxng_host: str = "http://localhost:8888"
    search_top_k: int = 5
    cache_ttl_seconds: int = 86400        # 24h default
    cache_fresh_ttl_seconds: int = 3600   # 1h for fresh-mode
    page_cache_ttl_days: int = 7
    daily_quota: int = 50
    sanitization_mode: str = "strict"     # "strict" | "permissive"
    playwright_gpu_priority: int = 3      # low priority vs RAG/indexer


class OpenIEConfig(BaseModel):
    model: str = "qwen3:8b"
    batch_size: int = 8          # chunks per batch
    auto_promote_enabled: bool = True
    review_threshold_min: float = 0.60
    gate4_cross_source_boost: float = 0.10
    predicate_whitelist_file: str = "infra/graph/predicate_whitelist.yaml"
    job_priority: int = 1        # low priority (nightly batch)
    timeout: float = 60.0        # seconds per chunk LLM call


class GpuPolicyConfig(BaseModel):
    nightly_threshold_hour: int = 22
    rag_priority: int = 10
    indexing_priority: int = 5
    kge_priority: int = 1
    openie_priority: int = 1


class WatchdogConfig(BaseModel):
    debounce_seconds: float = 60.0


class LogConfig(BaseModel):
    level: str = "INFO"
    log_llm_full: bool = False


# ── AppConfig ─────────────────────────────────────────────────────────────────

class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_nested_delimiter="__",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: str = "laptop-4070-8gb"
    sources: SourcesConfig = SourcesConfig()
    ollama: OllamaConfig = OllamaConfig()
    qdrant: QdrantConfig = QdrantConfig()
    oxigraph: OxigraphConfig = OxigraphConfig()
    sqlite: SQLiteConfig = SQLiteConfig()
    embedder: EmbedderConfig = EmbedderConfig()
    reranker: RerankerConfig = RerankerConfig()
    marker: MarkerConfig = MarkerConfig()
    kge: KGEConfig = KGEConfig()
    playwright: PlaywrightConfig = PlaywrightConfig()
    web: WebConfig = WebConfig()
    graph_walk: GraphWalkConfig = GraphWalkConfig()
    gpu_policy: GpuPolicyConfig = GpuPolicyConfig()
    openie: OpenIEConfig = OpenIEConfig()
    watchdog: WatchdogConfig = WatchdogConfig()
    log: LogConfig = LogConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Read profile name early (only from env, not from files) to locate profile YAML
        profile = os.environ.get("KB_PROFILE", "laptop-4070-8gb")
        profile_path = REPO_ROOT / "infra" / "profiles" / f"{profile}.yaml"
        base_path = REPO_ROOT / "infra" / "config.yaml"
        return (
            init_settings,                                      # highest: explicit kwargs
            env_settings,                                       # env vars KB_*
            dotenv_settings,                                    # .env file
            YamlFileSource(settings_cls, profile_path),        # profile overrides
            YamlFileSource(settings_cls, base_path),           # base config
        )
