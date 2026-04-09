"""Pipeline execution context — mutable shared state flowing through steps."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any


class PipelineContext:
    """Mutable bag of state that flows through pipeline execution.

    Steps read from ``inputs`` (mapped from context keys) and write
    results that get stored back into context via ``outputs`` mapping.
    """

    def __init__(
        self,
        pipeline_name: str,
        initial_values: dict[str, Any] | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.run_id: str = uuid.uuid4().hex
        self.pipeline_name: str = pipeline_name
        self.dry_run: bool = dry_run
        self.started_at: datetime = datetime.now(timezone.utc)

        # Core state dict — steps read/write through this
        self._data: dict[str, Any] = dict(initial_values or {})

        # Execution tracking
        self.current_stage: str | None = None
        self.current_step: str | None = None
        self.total_stages: int = 0
        self.total_steps: int = 0
        self.stages_completed: int = 0
        self.steps_completed: int = 0

        # Cancellation
        self._cancelled: bool = False

    # ── Data access ──────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def resolve_ref(self, ref: str) -> Any:
        """Resolve a context reference like 'ctx.template' → self._data['template']."""
        if ref.startswith("ctx."):
            return self._data.get(ref[4:])
        return ref

    def resolve_inputs(self, inputs: dict[str, str]) -> dict[str, Any]:
        """Resolve a step's input mapping to actual values from context."""
        return {name: self.resolve_ref(ref) for name, ref in inputs.items()}

    def apply_outputs(self, outputs_mapping: dict[str, str], results: dict[str, Any]) -> None:
        """Write step results back into context using the outputs mapping."""
        for result_key, ctx_ref in outputs_mapping.items():
            if result_key in results:
                if ctx_ref.startswith("ctx."):
                    self._data[ctx_ref[4:]] = results[result_key]
                else:
                    self._data[ctx_ref] = results[result_key]

    def snapshot(self) -> dict[str, Any]:
        """Deep copy of the data dict — used for healing attempt isolation."""
        return copy.deepcopy(self._data)

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore context data from a snapshot."""
        self._data = copy.deepcopy(snapshot)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # ── Cancellation ─────────────────────────────────────────────

    def request_cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ── Serialization ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat(),
            "current_stage": self.current_stage,
            "current_step": self.current_step,
            "stages_completed": self.stages_completed,
            "steps_completed": self.steps_completed,
            "cancelled": self._cancelled,
            "data": self._data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PipelineContext":
        """Reconstruct a PipelineContext from a serialized dict (for resume)."""
        ctx = cls.__new__(cls)
        ctx.run_id = d["run_id"]
        ctx.pipeline_name = d["pipeline_name"]
        ctx.dry_run = d.get("dry_run", False)
        ctx.started_at = datetime.fromisoformat(d["started_at"])
        ctx.current_stage = d.get("current_stage")
        ctx.current_step = d.get("current_step")
        ctx.total_stages = d.get("total_stages", 0)
        ctx.total_steps = d.get("total_steps", 0)
        ctx.stages_completed = d.get("stages_completed", 0)
        ctx.steps_completed = d.get("steps_completed", 0)
        ctx._cancelled = d.get("cancelled", False)
        ctx._data = dict(d.get("data", {}))
        return ctx
