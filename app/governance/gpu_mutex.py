"""
GpuMutex: asyncio priority mutex for serialising GPU-bound work (ADR-0013 §3).
Higher priority value = runs first. Callers: Marker, Embedder, Reranker, KGE, Ollama RAG.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from typing import AsyncIterator


class GpuMutex:
    """
    Single-holder async mutex with priority queue.
    Grants access to the highest-priority waiter when the current holder releases.
    Ties broken by insertion order (FIFO within same priority).
    """

    def __init__(self) -> None:
        self._locked = False
        # Heap items: (-priority, seq, future)  — min-heap; negated priority so highest runs first
        self._heap: list[tuple[int, int, asyncio.Future]] = []
        self._counter = itertools.count()

    @asynccontextmanager
    async def acquire(self, priority: int = 0) -> AsyncIterator[None]:
        """Acquire the GPU lock. Higher priority = admitted sooner."""
        await self._acquire(priority)
        try:
            yield
        finally:
            self._release()

    async def _acquire(self, priority: int) -> None:
        if not self._locked:
            self._locked = True
            return

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        entry = (-priority, next(self._counter), fut)
        heapq.heappush(self._heap, entry)
        try:
            await fut
        except asyncio.CancelledError:
            # Remove this waiter; rebuild heap (O(n) but rare)
            try:
                self._heap.remove(entry)
                heapq.heapify(self._heap)
            except ValueError:
                pass
            raise

    def _release(self) -> None:
        if not self._heap:
            self._locked = False
            return
        _, _, fut = heapq.heappop(self._heap)
        fut.set_result(None)

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def queue_depth(self) -> int:
        return len(self._heap)
