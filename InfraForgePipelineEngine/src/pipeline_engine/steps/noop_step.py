"""No-op step handler — passes through, useful for testing and placeholders."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepHandler


class NoopStepHandler(StepHandler):
    """No-op step that optionally echoes inputs to outputs."""

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        message = config.get("message", "noop")
        yield StepProgressEvent(
            step_id=ctx.current_step or "noop",
            progress=1.0,
            detail=message,
        )
        # Echo all inputs as outputs, plus any config-defined outputs
        result = {**inputs, **config.get("outputs", {})}
        yield {"__result__": result}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to include in progress event"},
                "outputs": {"type": "object", "description": "Static key-value pairs to return as outputs"},
            },
        }

    def description(self) -> str:
        return "No-op step that passes through inputs to outputs. Useful for testing and placeholders."
