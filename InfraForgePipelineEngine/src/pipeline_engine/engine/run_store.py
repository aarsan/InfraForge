"""In-memory run store — persists pipeline state for pause/resume.

Stores the full pipeline context snapshot, position, and request so that
a paused pipeline can be resumed later when external input arrives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class RunState:
    """Serializable state of a single pipeline run."""

    __slots__ = (
        "run_id", "status", "request_dict", "context_dict",
        "position", "waiting_step_id", "waiting_config",
        "events", "created_at", "updated_at", "error",
    )

    def __init__(
        self,
        run_id: str,
        request_dict: dict[str, Any],
        context_dict: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.status: str = "running"
        self.request_dict = request_dict
        self.context_dict: dict[str, Any] = context_dict or {}
        self.position: tuple[int, int] = (0, 0)  # (stage_idx, step_idx)
        self.waiting_step_id: str | None = None
        self.waiting_config: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.error: str | None = None
        now = datetime.now(timezone.utc).isoformat()
        self.created_at: str = now
        self.updated_at: str = now

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "waiting_step_id": self.waiting_step_id,
            "waiting_config": self.waiting_config,
            "position": list(self.position),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "context": self.context_dict,
            "event_count": len(self.events),
        }


class RunStore:
    """Thread-safe in-memory store for pipeline run states."""

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

    def create(self, run_id: str, request_dict: dict[str, Any]) -> RunState:
        state = RunState(run_id, request_dict)
        self._runs[run_id] = state
        logger.info("RunStore: created run %s", run_id)
        return state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def save_pause(
        self,
        run_id: str,
        context_dict: dict[str, Any],
        position: tuple[int, int],
        waiting_step_id: str,
        waiting_config: dict[str, Any],
    ) -> None:
        """Persist state when a pipeline pauses for external input."""
        state = self._runs.get(run_id)
        if not state:
            logger.warning("RunStore: save_pause for unknown run %s", run_id)
            return
        state.status = "paused"
        state.context_dict = context_dict
        state.position = position
        state.waiting_step_id = waiting_step_id
        state.waiting_config = waiting_config
        state.updated_at = datetime.now(timezone.utc).isoformat()
        logger.info("RunStore: paused run %s at step %s", run_id, waiting_step_id)

    def update_status(self, run_id: str, status: str, error: str | None = None) -> None:
        state = self._runs.get(run_id)
        if state:
            state.status = status
            state.error = error
            state.updated_at = datetime.now(timezone.utc).isoformat()

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        state = self._runs.get(run_id)
        if state:
            state.events.append(event)

    def list_runs(self, status: str | None = None) -> list[dict[str, Any]]:
        runs = self._runs.values()
        if status:
            runs = [r for r in runs if r.status == status]
        return [r.to_dict() for r in runs]
