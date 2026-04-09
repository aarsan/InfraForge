"""Shared test fixtures."""

from __future__ import annotations

import pytest

from pipeline_engine.app import create_app
from pipeline_engine.steps.base import StepHandler
from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import StepProgressEvent


class FailNTimesHandler(StepHandler):
    """Test helper: fails N times with a healable error, then succeeds."""

    def __init__(self, fail_count: int = 2) -> None:
        self._fail_count = fail_count
        self._calls = 0

    async def execute(self, ctx, config, inputs):
        self._calls += 1
        if self._calls <= self._fail_count:
            from pipeline_engine.steps.base import StepError
            raise StepError(f"Simulated failure #{self._calls}", healable=True)
        yield StepProgressEvent(step_id=ctx.current_step or "test", progress=1.0, detail="success")
        yield {"__result__": {"attempt": self._calls}}

    def config_schema(self):
        return {"type": "object"}


class AlwaysFailHandler(StepHandler):
    """Test helper: always fails."""

    async def execute(self, ctx, config, inputs):
        from pipeline_engine.steps.base import StepError
        raise StepError("Always fails", healable=config.get("healable", True))
        yield  # unreachable but satisfies async generator

    def config_schema(self):
        return {"type": "object"}


class OutputHandler(StepHandler):
    """Test helper: returns config['outputs'] as results."""

    async def execute(self, ctx, config, inputs):
        yield StepProgressEvent(step_id=ctx.current_step or "out", progress=1.0, detail="done")
        yield {"__result__": config.get("outputs", {})}

    def config_schema(self):
        return {"type": "object"}


class SimpleHealerHandler(StepHandler):
    """Test helper: pretends to heal by adding 'healed' key to context."""

    async def execute(self, ctx, config, inputs):
        ctx.set("healed", True)
        yield {"__result__": {"strategy": "test-heal"}}

    def config_schema(self):
        return {"type": "object"}


@pytest.fixture
def app():
    application = create_app()
    # Ensure built-in types are registered even without pip install -e
    from pipeline_engine.steps.noop_step import NoopStepHandler
    from pipeline_engine.steps.gate_step import GateStepHandler
    reg = application.state.registry
    if not reg.get("noop"):
        reg.register("noop", NoopStepHandler())
    if not reg.get("output"):
        reg.register("output", OutputHandler())
    if not reg.get("gate"):
        reg.register("gate", GateStepHandler())
    return application


@pytest.fixture
def registry(app):
    return app.state.registry


def make_pipeline_request(stages, name="test-pipeline", context=None, timeout=60):
    """Helper to build a PipelineRequest dict."""
    return {
        "name": name,
        "context": context or {},
        "options": {"timeout": timeout, "dry_run": False},
        "stages": stages,
    }


def make_stage(stage_id, steps, **kwargs):
    """Helper to build a stage dict."""
    return {"id": stage_id, "name": kwargs.get("name", stage_id), "steps": steps, **kwargs}


def make_step(step_id, step_type="noop", config=None, **kwargs):
    """Helper to build a step dict."""
    return {
        "id": step_id,
        "type": step_type,
        "config": config or {},
        **kwargs,
    }
