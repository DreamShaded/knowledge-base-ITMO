"""
Unit tests for config loader: slug validation, overlap detection,
ENV interpolation, profile merge precedence.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from app.config import AppConfig, SourcesConfig, VaultConfig


# ── Slug validation ───────────────────────────────────────────────────────────

def test_valid_vault_id_slug():
    v = VaultConfig(id="my-vault", path=Path("/tmp/v"))
    assert v.id == "my-vault"


@pytest.mark.parametrize("bad_id", ["MyVault", "1vault", "vault id", "vault_id", "VAULT"])
def test_invalid_vault_id_slug_raises(bad_id):
    with pytest.raises(ValueError, match="kebab-slug"):
        VaultConfig(id=bad_id, path=Path("/tmp/v"))


# ── Overlap detection ─────────────────────────────────────────────────────────

def test_no_overlap_passes(tmp_path):
    a = tmp_path / "vault-a"
    b = tmp_path / "vault-b"
    a.mkdir(); b.mkdir()
    cfg = SourcesConfig(
        vaults=[VaultConfig(id="a", path=a), VaultConfig(id="b", path=b)]
    )
    assert len(cfg.vaults) == 2


def test_identical_vault_paths_raises(tmp_path):
    p = tmp_path / "vault"
    p.mkdir()
    with pytest.raises(ValueError, match="Overlapping"):
        SourcesConfig(
            vaults=[VaultConfig(id="a", path=p), VaultConfig(id="b", path=p)]
        )


def test_nested_vault_path_raises(tmp_path):
    parent = tmp_path / "vault"
    child  = tmp_path / "vault" / "nested"
    parent.mkdir(); child.mkdir()
    with pytest.raises(ValueError, match="Overlapping"):
        SourcesConfig(
            vaults=[VaultConfig(id="a", path=parent), VaultConfig(id="b", path=child)]
        )


def test_vault_and_book_path_overlap_raises(tmp_path):
    p = tmp_path / "shared"
    p.mkdir()
    with pytest.raises(ValueError, match="Overlapping"):
        SourcesConfig(
            vaults=[VaultConfig(id="a", path=p)],
            book_paths=[p],
        )


def test_vault_and_book_path_no_overlap_passes(tmp_path):
    v = tmp_path / "vault"
    b = tmp_path / "books"
    v.mkdir(); b.mkdir()
    cfg = SourcesConfig(
        vaults=[VaultConfig(id="a", path=v)],
        book_paths=[b],
    )
    assert cfg.book_paths[0] == b


# ── ENV interpolation ─────────────────────────────────────────────────────────

def test_expand_env_in_yaml():
    from app.config import _expand_env
    os.environ["_TEST_KB_HOME"] = "/test/home"
    result = _expand_env({"path": "${_TEST_KB_HOME}/vault"})
    assert result["path"] == "/test/home/vault"


def test_expand_env_missing_var_keeps_placeholder():
    from app.config import _expand_env
    result = _expand_env("${_NONEXISTENT_VAR_KB}/path")
    assert "${_NONEXISTENT_VAR_KB}" in result


def test_expand_env_nested():
    from app.config import _expand_env
    os.environ["_TEST_KB_DIR"] = "/data"
    result = _expand_env({"outer": {"inner": "${_TEST_KB_DIR}/sub"}})
    assert result["outer"]["inner"] == "/data/sub"


# ── Profile merge precedence ──────────────────────────────────────────────────

def test_profile_yaml_overrides_base(tmp_path, monkeypatch):
    """Profile YAML values must win over base config.yaml values."""
    from app.config import YamlFileSource, REPO_ROOT

    base_yaml = tmp_path / "config.yaml"
    base_yaml.write_text(yaml.dump({"embedder": {"model_id": "base-model", "batch_size": 8}}))

    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text(yaml.dump({"embedder": {"batch_size": 64}}))

    base_source = YamlFileSource(AppConfig, base_yaml)
    profile_source = YamlFileSource(AppConfig, profile_yaml)

    base_data = base_source()
    profile_data = profile_source()

    # Profile batch_size should be 64, base model_id preserved
    assert base_data["embedder"]["model_id"] == "base-model"
    assert profile_data["embedder"]["batch_size"] == 64


def test_missing_yaml_returns_empty_dict(tmp_path):
    from app.config import YamlFileSource
    source = YamlFileSource(AppConfig, tmp_path / "nonexistent.yaml")
    assert source() == {}


def test_env_var_overrides_yaml(monkeypatch):
    """KB_ env vars must beat YAML values."""
    monkeypatch.setenv("KB_OLLAMA__CHAT_MODEL", "override-model")
    cfg = AppConfig()
    assert cfg.ollama.chat_model == "override-model"
