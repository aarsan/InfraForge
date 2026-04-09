"""Result models returned by the run-status endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StepResult(BaseModel):
    step_id: str
    step_type: str
    status: Literal["success", "failed", "skipped", "cancelled"]
    duration_ms: int = 0
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    heal_attempts: int = 0


class StageResult(BaseModel):
    stage_id: str
    status: Literal["success", "failed", "skipped", "cancelled"]
    duration_ms: int = 0
    steps: list[StepResult] = Field(default_factory=list)


class PipelineResult(BaseModel):
    run_id: str
    name: str
    status: Literal["running", "success", "failed", "cancelled"]
    duration_ms: int = 0
    stages: list[StageResult] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
