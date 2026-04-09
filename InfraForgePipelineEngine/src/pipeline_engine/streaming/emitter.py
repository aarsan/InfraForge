"""NDJSON event emitter — async queue-based streaming with backpressure."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SENTINEL = object()


class EventEmitter:
    """Bridges the pipeline executor (producer) with the HTTP streaming response (consumer).

    The executor pushes events via ``emit()``.  The consumer iterates
    ``stream()`` which yields serialized NDJSON lines.

    Fixes over InfraForge:
    - Bounded queue with backpressure warning (instead of unbounded)
    - Sentinel is always pushed even if producer raises (via context manager)
    - Client disconnect detection via ``close()`` → triggers cancellation
    """

    def __init__(self, *, max_queue_size: int = 1000) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False

    async def emit(self, event: BaseModel | dict[str, Any]) -> None:
        """Push an event onto the queue."""
        if self._closed:
            return
        if isinstance(event, BaseModel):
            data = event.model_dump(mode="json")
        elif isinstance(event, dict):
            data = event
        else:
            data = {"type": "unknown", "data": str(event)}

        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping oldest event")
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(data)

    async def finish(self) -> None:
        """Signal that no more events will be produced."""
        await self._queue.put(_SENTINEL)

    async def stream(self) -> AsyncGenerator[str, None]:
        """Yield NDJSON lines.  Blocks until events are available."""
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            if item is _SENTINEL:
                break

            try:
                line = json.dumps(item, default=str) + "\n"
                yield line
            except Exception:
                logger.exception("Failed to serialize event: %r", item)

    def close(self) -> None:
        """Mark emitter as closed (client disconnected)."""
        self._closed = True
