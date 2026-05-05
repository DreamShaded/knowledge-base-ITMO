"""
OllamaLifecycle FSM (ADR-0013 §3).
Manages model load/unload to free VRAM for Marker on 8 GB VRAM laptops.

States: UNLOADED → LOADING → LOADED → UNLOADING → UNLOADED
"""
from __future__ import annotations

import asyncio
from enum import Enum, auto

import httpx

from app.logging import get_logger

log = get_logger(__name__)


class OllamaState(Enum):
    UNLOADED = auto()
    LOADING = auto()
    LOADED = auto()
    UNLOADING = auto()


_TRANSITIONS: dict[OllamaState, set[OllamaState]] = {
    OllamaState.UNLOADED:   {OllamaState.LOADING},
    OllamaState.LOADING:    {OllamaState.LOADED, OllamaState.UNLOADED},
    OllamaState.LOADED:     {OllamaState.UNLOADING},
    OllamaState.UNLOADING:  {OllamaState.UNLOADED},
}


class OllamaLifecycle:
    """
    Tracks and controls the loaded state of the primary chat model in Ollama.
    On 8 GB VRAM: unload before Marker GPU pass, reload after.
    On larger GPUs: no-op (models co-reside).
    """

    def __init__(self, host: str, model: str) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._state = OllamaState.UNLOADED
        self._lock = asyncio.Lock()

    @property
    def state(self) -> OllamaState:
        return self._state

    def _transition(self, to: OllamaState) -> None:
        allowed = _TRANSITIONS.get(self._state, set())
        if to not in allowed:
            raise RuntimeError(
                f"OllamaLifecycle: invalid transition {self._state.name} → {to.name}"
            )
        log.debug("ollama_lifecycle", from_state=self._state.name, to_state=to.name)
        self._state = to

    async def ensure_loaded(self) -> None:
        """Ensure model is loaded; no-op if already LOADED."""
        async with self._lock:
            if self._state == OllamaState.LOADED:
                return
            if self._state != OllamaState.UNLOADED:
                raise RuntimeError(f"Cannot load from state {self._state.name}")
            self._transition(OllamaState.LOADING)
            try:
                await self._api_warmup()
                self._transition(OllamaState.LOADED)
                log.info("model_loaded", model=self._model)
            except Exception:
                self._transition(OllamaState.UNLOADED)
                raise

    async def unload(self) -> None:
        """Unload model to free VRAM."""
        async with self._lock:
            if self._state == OllamaState.UNLOADED:
                return
            if self._state != OllamaState.LOADED:
                raise RuntimeError(f"Cannot unload from state {self._state.name}")
            self._transition(OllamaState.UNLOADING)
            try:
                await self._api_unload()
                self._transition(OllamaState.UNLOADED)
                log.info("model_unloaded", model=self._model)
            except Exception:
                self._transition(OllamaState.LOADED)
                raise

    async def _api_warmup(self) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            # Sending empty generate with keep_alive loads the model
            await client.post(
                f"{self._host}/api/generate",
                json={"model": self._model, "prompt": "", "keep_alive": -1},
            )

    async def _api_unload(self) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            # keep_alive=0 immediately evicts from VRAM
            await client.post(
                f"{self._host}/api/generate",
                json={"model": self._model, "prompt": "", "keep_alive": 0},
            )
