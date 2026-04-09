"""Test pipeline executor — execution, routing, context passing."""

import json
import pytest

from pipeline_engine.engine.executor import PipelineExecutor
from pipeline_engine.models.events import (
    PipelineDoneEvent,
    PipelineStartEvent,
    StageDoneEvent,
    StageStartEvent,
    StepDoneEvent,
    StepSkippedEvent,
    StepStartEvent,
)
from pipeline_engine.models.pipeline import PipelineRequest
from pipeline_engine.steps.registry import StepRegistry
from pipeline_engine.steps.noop_step import NoopStepHandler

from tests.conftest import (
    AlwaysFailHandler,
    OutputHandler,
    make_pipeline_request,
    make_stage,
    make_step,
)


def _build_registry(**extra_handlers) -> StepRegistry:
    reg = StepRegistry()
    reg.register("noop", NoopStepHandler())
    reg.register("output", OutputHandler())
    reg.register("always_fail", AlwaysFailHandler())
    for name, handler in extra_handlers.items():
        reg.register(name, handler)
    return reg


async def _collect_events(executor, request_dict) -> list:
    req = PipelineRequest(**request_dict)
    events = []
    async for event in executor.execute(req):
        events.append(event)
    return events


def _typed_events(events, event_type):
    return [e for e in events if isinstance(e, event_type)]


class TestBasicExecution:
    @pytest.mark.asyncio
    async def test_single_noop_step(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request(
            [make_stage("s1", [make_step("step1")])]
        )
        events = await _collect_events(executor, request)

        starts = _typed_events(events, PipelineStartEvent)
        dones = _typed_events(events, PipelineDoneEvent)
        assert len(starts) == 1
        assert len(dones) == 1
        assert dones[0].status == "success"

    @pytest.mark.asyncio
    async def test_multi_stage_multi_step(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [make_step("a"), make_step("b")]),
            make_stage("s2", [make_step("c")]),
        ])
        events = await _collect_events(executor, request)

        stage_starts = _typed_events(events, StageStartEvent)
        stage_dones = _typed_events(events, StageDoneEvent)
        step_starts = _typed_events(events, StepStartEvent)
        step_dones = _typed_events(events, StepDoneEvent)

        assert len(stage_starts) == 2
        assert len(stage_dones) == 2
        assert len(step_starts) == 3
        assert len(step_dones) == 3
        assert all(d.status == "success" for d in step_dones)

    @pytest.mark.asyncio
    async def test_context_passing_between_steps(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request(
            [make_stage("s1", [
                make_step("producer", step_type="output",
                          config={"outputs": {"value": 42}},
                          outputs={"value": "ctx.my_value"}),
                make_step("consumer", step_type="noop",
                          inputs={"value": "ctx.my_value"}),
            ])],
            context={"initial": "data"},
        )
        events = await _collect_events(executor, request)
        done = _typed_events(events, PipelineDoneEvent)[0]
        assert done.status == "success"


class TestRouting:
    @pytest.mark.asyncio
    async def test_on_failure_abort(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("fail_step", step_type="always_fail",
                          config={"healable": False},
                          on_failure="abort"),
                make_step("never_reached"),
            ]),
        ])
        events = await _collect_events(executor, request)
        done = _typed_events(events, PipelineDoneEvent)[0]
        assert done.status == "failed"

        step_dones = _typed_events(events, StepDoneEvent)
        assert len(step_dones) == 1
        assert step_dones[0].status == "failed"

    @pytest.mark.asyncio
    async def test_on_failure_next(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("fail_step", step_type="always_fail",
                          config={"healable": False},
                          on_failure="next"),
                make_step("reached"),
            ]),
        ])
        events = await _collect_events(executor, request)
        step_starts = _typed_events(events, StepStartEvent)
        assert len(step_starts) == 2  # Both steps started

    @pytest.mark.asyncio
    async def test_on_success_done(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("early_exit", on_success="done"),
                make_step("never_reached"),
            ]),
        ])
        events = await _collect_events(executor, request)
        step_starts = _typed_events(events, StepStartEvent)
        assert len(step_starts) == 1  # Only first step

    @pytest.mark.asyncio
    async def test_condition_skip(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request(
            [make_stage("s1", [
                make_step("skipped", condition="ctx.nonexistent"),
                make_step("runs"),
            ])],
        )
        events = await _collect_events(executor, request)
        skipped = _typed_events(events, StepSkippedEvent)
        assert len(skipped) == 1
        assert skipped[0].step_id == "skipped"

    @pytest.mark.asyncio
    async def test_stage_condition_skip(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [make_step("a")], condition="ctx.skip_me"),
            make_stage("s2", [make_step("b")]),
        ])
        events = await _collect_events(executor, request)
        stage_dones = _typed_events(events, StageDoneEvent)
        assert len(stage_dones) == 2
        assert stage_dones[0].status == "skipped"
        assert stage_dones[1].status == "success"


class TestValidation:
    def test_unknown_step_type(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        req = PipelineRequest(**make_pipeline_request([
            make_stage("s1", [make_step("x", step_type="nonexistent")])
        ]))
        errors = executor.validate(req)
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_valid_pipeline_no_errors(self):
        reg = _build_registry()
        executor = PipelineExecutor(reg)
        req = PipelineRequest(**make_pipeline_request([
            make_stage("s1", [make_step("x", step_type="noop")])
        ]))
        assert executor.validate(req) == []
