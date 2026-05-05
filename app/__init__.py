"""
Knowledge Base app package.
Composition root: build_app() wires config → stores → governance → providers → FastAPI.
"""
from app.config_profile import load_config
from app.di.composition import build_container
from app.main import create_app

__all__ = ["build_app", "load_config", "build_container", "create_app"]


def build_app():
    """Single entry point: detect profile, wire dependencies, return FastAPI app."""
    cfg = load_config()
    container = build_container(cfg)
    return create_app(container)
