"""
Unit tests for hardware profile autodetect (VRAM thresholds → profile name).
All GPU calls mocked via monkeypatch.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _mock_nvidia(vram_mb: int):
    """Return a mock CompletedProcess with nvidia-smi-like output."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = f"{vram_mb}\n"
    return m


# ── VRAM threshold rules ──────────────────────────────────────────────────────

@pytest.mark.parametrize("vram,expected_profile", [
    (24576, "desktop-large-24gb-plus"),
    (24000, "desktop-large-24gb-plus"),
    (16384, "desktop-mid-12-16gb"),
    (12000, "desktop-mid-12-16gb"),
    (8188,  "laptop-4070-8gb"),
    (7000,  "laptop-4070-8gb"),
])
def test_vram_threshold_selects_correct_profile(vram, expected_profile, monkeypatch):
    monkeypatch.delenv("KB_PROFILE", raising=False)
    with patch("subprocess.run", return_value=_mock_nvidia(vram)):
        from app import config_profile
        # Reload to avoid cached state
        import importlib; importlib.reload(config_profile)
        profile = config_profile._detect_from_nvidia_smi()
    assert profile == expected_profile


@pytest.mark.parametrize("vram", [6999, 4096, 0])
def test_vram_below_minimum_exits(vram, monkeypatch):
    monkeypatch.delenv("KB_PROFILE", raising=False)
    with patch("subprocess.run", return_value=_mock_nvidia(vram)):
        with pytest.raises(SystemExit) as exc_info:
            from app import config_profile
            import importlib; importlib.reload(config_profile)
            config_profile._detect_from_nvidia_smi()
    assert exc_info.value.code == 1


# ── nvidia-smi absent ─────────────────────────────────────────────────────────

def test_no_nvidia_smi_exits(monkeypatch):
    monkeypatch.delenv("KB_PROFILE", raising=False)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SystemExit) as exc_info:
            from app import config_profile
            import importlib; importlib.reload(config_profile)
            config_profile._detect_from_nvidia_smi()
    assert exc_info.value.code == 1


def test_nvidia_smi_timeout_exits(monkeypatch):
    monkeypatch.delenv("KB_PROFILE", raising=False)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 10)):
        with pytest.raises(SystemExit) as exc_info:
            from app import config_profile
            import importlib; importlib.reload(config_profile)
            config_profile._detect_from_nvidia_smi()
    assert exc_info.value.code == 1


# ── KB_PROFILE env override ───────────────────────────────────────────────────

def test_kb_profile_env_skips_nvidia(monkeypatch):
    monkeypatch.setenv("KB_PROFILE", "desktop-large-24gb-plus")
    with patch("subprocess.run", side_effect=AssertionError("should not call nvidia-smi")):
        from app import config_profile
        import importlib; importlib.reload(config_profile)
        result = config_profile.detect_profile()
    assert result == "desktop-large-24gb-plus"


def test_invalid_kb_profile_env_exits(monkeypatch):
    monkeypatch.setenv("KB_PROFILE", "unknown-profile")
    with pytest.raises(SystemExit) as exc_info:
        from app import config_profile
        import importlib; importlib.reload(config_profile)
        config_profile.detect_profile()
    assert exc_info.value.code == 1


# ── Profile file presence ─────────────────────────────────────────────────────

def test_all_profile_yaml_files_exist():
    from app.config import REPO_ROOT
    from app.config_profile import VALID_PROFILES
    for name in VALID_PROFILES:
        path = REPO_ROOT / "infra" / "profiles" / f"{name}.yaml"
        assert path.exists(), f"Missing profile file: {path}"
