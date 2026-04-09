"""NDJSON event models emitted during pipeline execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Pipeline-level events ────────────────────────────────────────

class PipelineStartEvent(BaseModel):
    type: Literal["pipeline_start"] = "pipeline_start"
    run_id: str
    name: str
    total_stages: int
    ts: str = Field(default_factory=_now)


class PipelineDoneEvent(BaseModel):
    type: Literal["pipeline_done"] = "pipeline_done"
    run_id: str
    status: Literal["success", "failed", "cancelled"]
    duration_ms: int
    error: str | None = None
    ts: str = Field(default_factory=_now)


# ── Stage-level events ───────────────────────────────────────────

class StageStartEvent(BaseModel):
    type: Literal["stage_start"] = "stage_start"
    stage_id: str
    stage_name: str
    step_count: int
    ts: str = Field(default_factory=_now)


class StageDoneEvent(BaseModel):
    type: Literal["stage_done"] = "stage_done"
    stage_id: str
    status: Literal["success", "failed", "skipped", "cancelled"]
    duration_ms: int
    ts: str = Field(default_factory=_now)


# ── Step-level events ────────────────────────────────────────────

class StepStartEvent(BaseModel):
    type: Literal["step_start"] = "step_start"
    step_id: str
    step_name: str
    step_type: str
    ts: str = Field(default_factory=_now)


class StepProgressEvent(BaseModel):
    type: Literal["step_progress"] = "step_progress"
    step_id: str
    progress: float = Field(ge=0.0, le=1.0)
    detail: str = ""
    ts: str = Field(default_factory=_now)


class StepDoneEvent(BaseModel):
    type: Literal["step_done"] = "step_done"
    step_id: str
    status: Literal["success", "failed", "skipped", "cancelled"]
    duration_ms: int
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    ts: str = Field(default_factory=_now)


# ── Healing events ───────────────────────────────────────────────

class HealingStartEvent(BaseModel):
    type: Literal["healing_start"] = "healing_start"
    step_id: str
    attempt: int
    max_attempts: int
    ts: str = Field(default_factory=_now)


class HealingDoneEvent(BaseModel):
    type: Literal["healing_done"] = "healing_done"
    step_id: str
    attempt: int
    strategy: str = ""
    success: bool = True
    ts: str = Field(default_factory=_now)


# ── Step skipped event ───────────────────────────────────────────

class StepSkippedEvent(BaseModel):
    type: Literal["step_skipped"] = "step_skipped"
    step_id: str
    reason: str = ""
    ts: str = Field(default_factory=_now)


# ── Step waiting event (human-in-the-loop / gate) ───────────────

class StepWaitingEvent(BaseModel):
    """Emitted when a step needs external input before it can continue."""
    type: Literal["step_waiting"] = "step_waiting"
    step_id: str
    step_name: str = ""
    gate_type: str = "manual"
    assignee: str = ""
    instructions: str = ""
    required_inputs: list[dict[str, Any]] = Field(default_factory=list)
    form_schema: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=_now)


# ── Pipeline paused / resumed events ────────────────────────────

class PipelinePausedEvent(BaseModel):
    """Emitted when the pipeline pauses waiting for external input."""
    type: Literal["pipeline_paused"] = "pipeline_paused"
    run_id: str
    waiting_step_id: str
    gate_type: str = "manual"
    assignee: str = ""
    instructions: str = ""
    duration_ms: int = 0
    ts: str = Field(default_factory=_now)


class PipelineResumedEvent(BaseModel):
    """Emitted when a paused pipeline is resumed with external input."""
    type: Literal["pipeline_resumed"] = "pipeline_resumed"
    run_id: str
    resumed_step_id: str
    ts: str = Field(default_factory=_now)


# ── Log event (catch-all for arbitrary info) ─────────────────────

class LogEvent(BaseModel):
    type: Literal["log"] = "log"
    level: Literal["debug", "info", "warn", "error"] = "info"
    message: str
    step_id: str | None = None
    ts: str = Field(default_factory=_now)


# ── Union type for all events ────────────────────────────────────

PipelineEvent = (
    PipelineStartEvent
    | PipelineDoneEvent
    | StageStartEvent
    | StageDoneEvent
    | StepStartEvent
    | StepProgressEvent
    | StepDoneEvent
    | HealingStartEvent
    | HealingDoneEvent
    | StepSkippedEvent
    | StepWaitingEvent
    | PipelinePausedEvent
    | PipelineResumedEvent
    | LogEvent
)
