"""Pipeline request models — the API contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealingConfig(BaseModel):
    """Configuration for step-level healing (retry with a healer step)."""

    enabled: bool = False
    max_attempts: int = Field(default=5, ge=1, le=50)
    backoff: Literal["none", "linear", "exponential"] = "exponential"
    base_delay: float = Field(default=1.0, ge=0, description="Base delay in seconds between attempts")
    healer_type: str | None = Field(default=None, description="Step type used to heal (e.g. 'llm.heal')")
    healer_config: dict[str, Any] = Field(default_factory=dict, description="Config passed to the healer step")


class StepDefinition(BaseModel):
    """A single executable step within a stage."""

    id: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$", max_length=128)
    type: str = Field(..., description="Registered step type (e.g. 'python', 'shell', 'http', 'llm.generate')")
    name: str = Field(default="", max_length=256)
    config: dict[str, Any] = Field(default_factory=dict, description="Step-type-specific configuration")
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description="Map of step input names to context keys (e.g. {'template': 'ctx.template'})",
    )
    outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Map of step output names to context keys to write results into",
    )
    timeout: int | None = Field(default=None, ge=1, description="Per-step timeout in seconds (overrides default)")
    on_success: str = Field(default="next", description="Routing on success: 'next' | 'done' | 'stage:{id}' | 'step:{id}' | 'abort'")
    on_failure: str = Field(default="abort", description="Routing on failure: 'next' | 'done' | 'stage:{id}' | 'step:{id}' | 'abort'")
    condition: str | None = Field(default=None, description="Optional condition expression (e.g. 'ctx.deploy_needed == true')")
    healing: HealingConfig = Field(default_factory=HealingConfig)


class StageDefinition(BaseModel):
    """A logical grouping of steps executed sequentially."""

    id: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$", max_length=128)
    name: str = Field(default="", max_length=256)
    steps: list[StepDefinition] = Field(..., min_length=1)
    condition: str | None = Field(default=None, description="Optional condition to skip entire stage")
    on_success: str = Field(default="next", description="Stage-level routing on success")
    on_failure: str = Field(default="abort", description="Stage-level routing on failure")


class PipelineOptions(BaseModel):
    """Pipeline-level options."""

    timeout: int = Field(default=3600, ge=1, description="Total pipeline timeout in seconds")
    dry_run: bool = Field(default=False, description="If true, steps report what they would do without executing")


class PipelineRequest(BaseModel):
    """Top-level pipeline execution request — the JSON contract clients submit."""

    name: str = Field(..., max_length=256)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Initial context values available to all steps (e.g. service_id, region)",
    )
    options: PipelineOptions = Field(default_factory=PipelineOptions)
    stages: list[StageDefinition] = Field(..., min_length=1)
