"""
Hardware profile detection and validation.
Called by install.sh (via subprocess) and by `python -m app diag profile`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import REPO_ROOT, AppConfig

VALID_PROFILES = {
    "laptop-4070-8gb",
    "desktop-mid-12-16gb",
    "desktop-large-24gb-plus",
}

_VRAM_RULES = [
    (24000, "desktop-large-24gb-plus"),
    (12000, "desktop-mid-12-16gb"),
    (7000,  "laptop-4070-8gb"),
]

_FAIL_MSG = (
    "FATAL: MVP requires NVIDIA GPU ≥ 7 GB VRAM.\n"
    "CPU-only deploy is future work — see ADR-0013 §10.\n"
)


def detect_profile() -> str:
    """
    Detect the hardware profile name.
    Priority: KB_PROFILE env var > nvidia-smi VRAM measurement.
    Exits with code 1 if GPU is absent or below 7 GB VRAM.
    """
    import os
    if profile := os.environ.get("KB_PROFILE"):
        _validate_profile_name(profile)
        return profile
    return _detect_from_nvidia_smi()


def _detect_from_nvidia_smi() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        print(_FAIL_MSG + "nvidia-smi not found.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(_FAIL_MSG + "nvidia-smi timed out.", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(_FAIL_MSG + f"nvidia-smi exited {result.returncode}.", file=sys.stderr)
        sys.exit(1)

    try:
        vram_mb = int(result.stdout.strip().splitlines()[0].strip())
    except (ValueError, IndexError):
        print(_FAIL_MSG + f"Unexpected nvidia-smi output: {result.stdout!r}", file=sys.stderr)
        sys.exit(1)

    for threshold, profile in _VRAM_RULES:
        if vram_mb >= threshold:
            return profile

    print(
        _FAIL_MSG + f"Detected VRAM: {vram_mb} MB (< 7 GB minimum).",
        file=sys.stderr,
    )
    sys.exit(1)


def _validate_profile_name(name: str) -> None:
    if name not in VALID_PROFILES:
        print(
            f"FATAL: Unknown profile {name!r}. Valid: {', '.join(sorted(VALID_PROFILES))}",
            file=sys.stderr,
        )
        sys.exit(1)


def load_config(profile: str | None = None) -> AppConfig:
    """Build AppConfig with profile detection applied."""
    import os
    if profile:
        _validate_profile_name(profile)
        os.environ["KB_PROFILE"] = profile
    return AppConfig()


def profile_path(name: str) -> Path:
    return REPO_ROOT / "infra" / "profiles" / f"{name}.yaml"
