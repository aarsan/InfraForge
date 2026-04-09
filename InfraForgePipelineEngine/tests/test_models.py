"""Test Pydantic models — valid and invalid payloads."""

import pytest
from pydantic import ValidationError

from pipeline_engine.models.pipeline import (
    HealingConfig,
    PipelineOptions,
    PipelineRequest,
    StageDefinition,
    StepDefinition,
)
from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import (
    PipelineStartEvent,
    StepDoneEvent,
    HealingStartEvent,
)
from pipeline_engine.models.results import PipelineResult


class TestStepDefinition:
    def test_minimal_valid(self):
        step = StepDefinition(id="s1", type="noop")
        assert step.id == "s1"
        assert step.on_success == "next"
        assert step.on_failure == "abort"
        assert step.healing.enabled is False

    def test_full_config(self):
        step = StepDefinition(
            id="gen",
            type="python",
            name="Generate",
            config={"module": "foo", "function": "bar"},
            inputs={"x": "ctx.x"},
            outputs={"y": "ctx.y"},
            timeout=120,
            on_success="done",
            on_failure="next",
            healing=HealingConfig(enabled=True, max_attempts=3, healer_type="noop"),
        )
        assert step.healing.enabled is True
        assert step.healing.max_attempts == 3

    def test_invalid_id_pattern(self):
        with pytest.raises(ValidationError):
            StepDefinition(id="bad id!", type="noop")

    def test_healing_max_attempts_bounds(self):
        with pytest.raises(ValidationError):
            HealingConfig(max_attempts=0)
        with pytest.raises(ValidationError):
            HealingConfig(max_attempts=100)


class TestPipelineRequest:
    def test_minimal(self):
        req = PipelineRequest(
            name="test",
            stages=[
                StageDefinition(id="s1", steps=[StepDefinition(id="x", type="noop")])
            ],
        )
        assert req.options.timeout == 3600
        assert len(req.stages) == 1

    def test_empty_stages_rejected(self):
        with pytest.raises(ValidationError):
            PipelineRequest(name="test", stages=[])


class TestPipelineContext:
    def test_get_set(self):
        ctx = PipelineContext("test", {"key": "val"})
        assert ctx.get("key") == "val"
        ctx.set("new", 42)
        assert ctx.get("new") == 42

    def test_resolve_ref(self):
        ctx = PipelineContext("test", {"template": "{}json"})
        assert ctx.resolve_ref("ctx.template") == "{}json"
        assert ctx.resolve_ref("literal") == "literal"

    def test_snapshot_restore(self):
        ctx = PipelineContext("test", {"a": 1})
        snap = ctx.snapshot()
        ctx.set("a", 999)
        assert ctx.get("a") == 999
        ctx.restore(snap)
        assert ctx.get("a") == 1

    def test_cancellation(self):
        ctx = PipelineContext("test")
        assert ctx.cancelled is False
        ctx.request_cancel()
        assert ctx.cancelled is True


class TestEvents:
    def test_pipeline_start_serialization(self):
        event = PipelineStartEvent(run_id="abc", name="test", total_stages=3)
        data = event.model_dump(mode="json")
        assert data["type"] == "pipeline_start"
        assert data["run_id"] == "abc"
        assert "ts" in data

    def test_step_done_with_error(self):
        event = StepDoneEvent(step_id="s1", status="failed", duration_ms=100, error="boom")
        assert event.error == "boom"

    def test_healing_start(self):
        event = HealingStartEvent(step_id="s1", attempt=2, max_attempts=5)
        data = event.model_dump(mode="json")
        assert data["attempt"] == 2


class TestResults:
    def test_pipeline_result(self):
        result = PipelineResult(
            run_id="abc",
            name="test",
            status="success",
            duration_ms=1234,
        )
        assert result.status == "success"
