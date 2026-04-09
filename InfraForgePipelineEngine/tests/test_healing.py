"""Test healing engine — retry, backoff, max attempts, healer failure."""

import pytest

from pipeline_engine.engine.executor import PipelineExecutor
from pipeline_engine.models.events import (
    HealingDoneEvent,
    HealingStartEvent,
    PipelineDoneEvent,
    StepDoneEvent,
)
from pipeline_engine.models.pipeline import PipelineRequest
from pipeline_engine.steps.registry import StepRegistry
from pipeline_engine.steps.noop_step import NoopStepHandler

from tests.conftest import (
    AlwaysFailHandler,
    FailNTimesHandler,
    SimpleHealerHandler,
    make_pipeline_request,
    make_stage,
    make_step,
)


def _build_registry(**extra) -> StepRegistry:
    reg = StepRegistry()
    reg.register("noop", NoopStepHandler())
    reg.register("healer", SimpleHealerHandler())
    for name, handler in extra.items():
        reg.register(name, handler)
    return reg


async def _collect(executor, request_dict):
    req = PipelineRequest(**request_dict)
    events = []
    async for event in executor.execute(req):
        events.append(event)
    return events


def _typed(events, cls):
    return [e for e in events if isinstance(e, cls)]


class TestHealing:
    @pytest.mark.asyncio
    async def test_healing_succeeds_after_retries(self):
        """Step fails 2 times, then succeeds on 3rd attempt."""
        handler = FailNTimesHandler(fail_count=2)
        reg = _build_registry(flaky=handler)

        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("flaky_step", step_type="flaky", healing={
                    "enabled": True,
                    "max_attempts": 5,
                    "backoff": "none",
                    "base_delay": 0,
                    "healer_type": "healer",
                    "healer_config": {},
                }),
            ]),
        ])
        events = await _collect(executor, request)

        done = _typed(events, PipelineDoneEvent)[0]
        assert done.status == "success"

        healing_starts = _typed(events, HealingStartEvent)
        assert len(healing_starts) >= 1

    @pytest.mark.asyncio
    async def test_healing_exhausted(self):
        """Step always fails — healing gives up after max_attempts."""
        reg = _build_registry(always_fail=AlwaysFailHandler())

        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("fail_step", step_type="always_fail", healing={
                    "enabled": True,
                    "max_attempts": 3,
                    "backoff": "none",
                    "base_delay": 0,
                    "healer_type": "healer",
                    "healer_config": {},
                }),
            ]),
        ])
        events = await _collect(executor, request)

        done = _typed(events, PipelineDoneEvent)[0]
        assert done.status == "failed"

        healing_starts = _typed(events, HealingStartEvent)
        assert len(healing_starts) == 3

    @pytest.mark.asyncio
    async def test_healing_disabled_fails_immediately(self):
        """Step with healing disabled fails without retrying."""
        reg = _build_registry(always_fail=AlwaysFailHandler())

        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("fail_step", step_type="always_fail",
                          config={"healable": True},
                          healing={"enabled": False}),
            ]),
        ])
        events = await _collect(executor, request)

        done = _typed(events, PipelineDoneEvent)[0]
        assert done.status == "failed"

        healing_starts = _typed(events, HealingStartEvent)
        assert len(healing_starts) == 0

    @pytest.mark.asyncio
    async def test_non_healable_error_skips_healing(self):
        """Non-healable errors bypass healing even if enabled."""
        reg = _build_registry(always_fail=AlwaysFailHandler())

        executor = PipelineExecutor(reg)
        request = make_pipeline_request([
            make_stage("s1", [
                make_step("fail_step", step_type="always_fail",
                          config={"healable": False},
                          healing={
                              "enabled": True,
                              "max_attempts": 3,
                              "healer_type": "healer",
                              "healer_config": {},
                          }),
            ]),
        ])
        events = await _collect(executor, request)

        done = _typed(events, PipelineDoneEvent)[0]
        assert done.status == "failed"

        healing_starts = _typed(events, HealingStartEvent)
        assert len(healing_starts) == 0
