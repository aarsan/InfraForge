"""Pipeline executor — the core orchestration loop.

Iterates stages → steps, resolves handlers from the registry,
manages context, handles routing, healing, timeouts, cancellation,
and pause/resume for human-in-the-loop gates.
Yields NDJSON ``PipelineEvent`` objects as an async generator.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from pipeline_engine.config import settings
from pipeline_engine.engine.healing import run_healing
from pipeline_engine.engine.routing import parse_route
from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import (
    LogEvent,
    PipelineDoneEvent,
    PipelineEvent,
    PipelinePausedEvent,
    PipelineResumedEvent,
    PipelineStartEvent,
    StageDoneEvent,
    StageStartEvent,
    StepDoneEvent,
    StepProgressEvent,
    StepSkippedEvent,
    StepStartEvent,
    StepWaitingEvent,
)
from pipeline_engine.models.pipeline import PipelineRequest, StageDefinition, StepDefinition
from pipeline_engine.steps.base import StepError, StepPaused
from pipeline_engine.steps.registry import StepRegistry

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _evaluate_condition(condition: str | None, ctx: PipelineContext) -> bool:
    """Evaluate a simple condition expression against context.

    Supports basic comparisons: ``ctx.key == value``, ``ctx.key != value``,
    ``ctx.key`` (truthy check).  Not a full expression language — keeps it
    safe from injection.
    """
    if not condition:
        return True
    condition = condition.strip()

    # Truthy check: "ctx.foo"
    if condition.startswith("ctx.") and " " not in condition:
        val = ctx.get(condition[4:])
        return bool(val)

    # Equality: "ctx.foo == bar"
    for op, negate in [("!=", True), ("==", False)]:
        if op in condition:
            left, right = condition.split(op, 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            if left.startswith("ctx."):
                val = ctx.get(left[4:])
                result = str(val) == right
                return (not result) if negate else result

    # Default: pass
    logger.warning("Unrecognized condition %r, defaulting to true", condition)
    return True


async def _run_step_handler(
    handler,
    ctx: PipelineContext,
    config: dict[str, Any],
    inputs: dict[str, Any],
    step_id: str,
    timeout: int,
) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
    """Execute a step handler with timeout and cancellation support."""

    async def _execute():
        results: list[PipelineEvent | dict[str, Any]] = []
        async for event in handler.execute(ctx, config, inputs):
            results.append(event)
        return results

    try:
        events = await asyncio.wait_for(_execute(), timeout=timeout)
        for event in events:
            yield event
    except asyncio.TimeoutError:
        raise StepError(
            f"Step {step_id!r} timed out after {timeout}s",
            healable=False,
            detail=f"timeout={timeout}s",
        )
    except (StepError, StepPaused):
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise StepError(str(e), healable=True, detail=str(e)) from e


@dataclass
class ResumeState:
    """State needed to resume a paused pipeline."""
    context_dict: dict[str, Any]
    position: tuple[int, int]  # (stage_idx, step_idx) of the paused step
    step_outputs: dict[str, Any]  # Human-provided outputs for the paused step


class PipelineExecutor:
    """Executes a pipeline request, yielding NDJSON events."""

    def __init__(self, registry: StepRegistry) -> None:
        self._registry = registry

    def validate(self, request: PipelineRequest) -> list[str]:
        """Pre-flight validation: ensure all step types are registered.

        Returns a list of error messages (empty = valid).
        """
        errors: list[str] = []
        seen_ids: set[str] = set()

        for stage in request.stages:
            if stage.id in seen_ids:
                errors.append(f"Duplicate stage id: {stage.id!r}")
            seen_ids.add(stage.id)

            for step in stage.steps:
                full_id = f"{stage.id}.{step.id}"
                if full_id in seen_ids:
                    errors.append(f"Duplicate step id: {step.id!r} in stage {stage.id!r}")
                seen_ids.add(full_id)

                if self._registry.get(step.type) is None:
                    errors.append(
                        f"Step {step.id!r} in stage {stage.id!r} uses unknown type {step.type!r}. "
                        f"Available: {', '.join(self._registry.list_types())}"
                    )

                if step.healing.enabled and step.healing.healer_type:
                    if self._registry.get(step.healing.healer_type) is None:
                        errors.append(
                            f"Step {step.id!r} healer uses unknown type {step.healing.healer_type!r}"
                        )

        return errors

    async def execute(
        self,
        request: PipelineRequest,
        *,
        resume: ResumeState | None = None,
    ) -> AsyncGenerator[PipelineEvent | dict, None]:
        """Execute the pipeline, yielding events as they occur.

        Args:
            request: The pipeline definition.
            resume:  If provided, resume a previously paused pipeline from
                     the given position with human-supplied outputs.
        """
        pipeline_start = time.monotonic()

        if resume:
            # ── Resuming a paused pipeline ───────────────────────
            ctx = PipelineContext.from_dict(resume.context_dict)
            ctx.total_stages = len(request.stages)
            ctx.total_steps = sum(len(s.steps) for s in request.stages)

            # Apply the human-provided outputs to the paused step
            paused_stage_idx, paused_step_idx = resume.position
            paused_step = request.stages[paused_stage_idx].steps[paused_step_idx]
            ctx.apply_outputs(paused_step.outputs, resume.step_outputs)

            yield PipelineResumedEvent(
                run_id=ctx.run_id,
                resumed_step_id=paused_step.id,
            )
            yield StepDoneEvent(
                step_id=paused_step.id,
                status="success",
                duration_ms=0,
                outputs={k: str(v)[:200] for k, v in resume.step_outputs.items()},
            )
            ctx.steps_completed += 1

            # Determine where to continue: next step after the paused one
            start_stage_idx = paused_stage_idx
            start_step_idx = paused_step_idx + 1
        else:
            # ── Fresh pipeline execution ─────────────────────────
            ctx = PipelineContext(
                pipeline_name=request.name,
                initial_values=request.context,
                dry_run=request.options.dry_run,
            )
            ctx.total_stages = len(request.stages)
            ctx.total_steps = sum(len(s.steps) for s in request.stages)

            yield PipelineStartEvent(
                run_id=ctx.run_id,
                name=request.name,
                total_stages=ctx.total_stages,
            )
            start_stage_idx = 0
            start_step_idx = 0

        final_status = "success"
        final_error: str | None = None

        # Build lookup tables for jump routing
        stage_index: dict[str, int] = {s.id: i for i, s in enumerate(request.stages)}

        stage_idx = start_stage_idx
        while stage_idx < len(request.stages):
            if ctx.cancelled:
                final_status = "cancelled"
                break

            stage = request.stages[stage_idx]

            # Check pipeline-level timeout
            if _elapsed_ms(pipeline_start) > request.options.timeout * 1000:
                final_status = "failed"
                final_error = f"Pipeline timeout ({request.options.timeout}s) exceeded"
                yield LogEvent(level="error", message=final_error)
                break

            # Evaluate stage condition
            if not _evaluate_condition(stage.condition, ctx):
                yield StageDoneEvent(stage_id=stage.id, status="skipped", duration_ms=0)
                ctx.stages_completed += 1
                stage_idx += 1
                continue

            stage_start_time = time.monotonic()
            ctx.current_stage = stage.id

            # When resuming, skip the stage_start for the stage we're
            # continuing inside (it was already emitted before the pause).
            if not (resume and stage_idx == start_stage_idx):
                yield StageStartEvent(
                    stage_id=stage.id,
                    stage_name=stage.name or stage.id,
                    step_count=len(stage.steps),
                )

            stage_failed = False
            stage_paused = False
            step_idx = start_step_idx if stage_idx == start_stage_idx else 0
            # Reset start_step_idx after first use so subsequent stages start at 0
            step_index: dict[str, int] = {s.id: i for i, s in enumerate(stage.steps)}

            while step_idx < len(stage.steps):
                if ctx.cancelled:
                    stage_failed = True
                    break

                step = stage.steps[step_idx]

                # Evaluate step condition
                if not _evaluate_condition(step.condition, ctx):
                    yield StepSkippedEvent(step_id=step.id, reason="condition not met")
                    ctx.steps_completed += 1
                    step_idx += 1
                    continue

                step_result = await self._execute_step(ctx, step, request, pipeline_start)
                step_events = step_result["events"]
                for ev in step_events:
                    yield ev

                # ── Handle paused step (gate) ────────────────────
                if step_result["status"] == "paused":
                    paused_info = step_result["paused_info"]
                    yield PipelinePausedEvent(
                        run_id=ctx.run_id,
                        waiting_step_id=step.id,
                        gate_type=paused_info.get("gate_type", "manual"),
                        assignee=paused_info.get("assignee", ""),
                        instructions=paused_info.get("instructions", ""),
                        duration_ms=_elapsed_ms(pipeline_start),
                    )
                    # Yield state for the router to persist
                    yield {"__pipeline_paused__": {
                        "context": ctx.to_dict(),
                        "position": [stage_idx, step_idx],
                        "step_id": step.id,
                        "waiting_config": paused_info,
                    }}
                    return  # Stop execution — will resume later

                if step_result["status"] == "success":
                    route = parse_route(step.on_success)
                elif step_result["status"] == "cancelled":
                    stage_failed = True
                    break
                else:
                    route = parse_route(step.on_failure)

                # Apply routing
                if route == "next":
                    step_idx += 1
                elif route == "done":
                    step_idx = len(stage.steps)  # Exit step loop
                elif route == "abort":
                    stage_failed = True
                    final_error = step_result.get("error", f"Step {step.id!r} failed")
                    break
                elif isinstance(route, tuple):
                    kind, target = route
                    if kind == "step" and target in step_index:
                        step_idx = step_index[target]
                    elif kind == "stage":
                        # Break out of step loop to handle stage jump
                        stage_failed = False
                        yield StageDoneEvent(
                            stage_id=stage.id,
                            status="success",
                            duration_ms=_elapsed_ms(stage_start_time),
                        )
                        ctx.stages_completed += 1
                        if target in stage_index:
                            stage_idx = stage_index[target]
                        else:
                            final_status = "failed"
                            final_error = f"Stage jump target {target!r} not found"
                        # Use goto-like jump — continue outer while
                        break
                    else:
                        stage_failed = True
                        final_error = f"Route target {target!r} not found"
                        break
                else:
                    step_idx += 1

                ctx.steps_completed += 1

            else:
                # Step loop completed normally (no break)
                yield StageDoneEvent(
                    stage_id=stage.id,
                    status="success",
                    duration_ms=_elapsed_ms(stage_start_time),
                )
                ctx.stages_completed += 1
                stage_idx += 1
                continue

            # Step loop broke — check if stage jump or failure
            if stage_failed:
                stage_route_action = parse_route(stage.on_failure)
                yield StageDoneEvent(
                    stage_id=stage.id,
                    status="cancelled" if ctx.cancelled else "failed",
                    duration_ms=_elapsed_ms(stage_start_time),
                )
                ctx.stages_completed += 1

                if stage_route_action == "abort" or ctx.cancelled:
                    final_status = "cancelled" if ctx.cancelled else "failed"
                    break
                elif stage_route_action == "next":
                    stage_idx += 1
                elif stage_route_action == "done":
                    break
                elif isinstance(stage_route_action, tuple) and stage_route_action[0] == "stage":
                    target = stage_route_action[1]
                    if target in stage_index:
                        stage_idx = stage_index[target]
                    else:
                        final_status = "failed"
                        final_error = f"Stage route target {target!r} not found"
                        break
                else:
                    final_status = "failed"
                    break
            else:
                # Stage jump already handled above
                continue

        yield PipelineDoneEvent(
            run_id=ctx.run_id,
            status=final_status,  # type: ignore[arg-type]
            duration_ms=_elapsed_ms(pipeline_start),
            error=final_error,
        )

        # Yield final context for the run store
        yield {"__pipeline_result__": ctx.to_dict()}

    async def _execute_step(
        self,
        ctx: PipelineContext,
        step: StepDefinition,
        request: PipelineRequest,
        pipeline_start: float,
    ) -> dict[str, Any]:
        """Execute a single step, handling healing if configured.

        Returns dict with keys: status, events, outputs, error.
        """
        events: list[PipelineEvent | dict] = []
        step_start = time.monotonic()
        ctx.current_step = step.id
        timeout = step.timeout or settings.default_step_timeout

        handler = self._registry.resolve(step.type)
        inputs = ctx.resolve_inputs(step.inputs)

        events.append(StepStartEvent(
            step_id=step.id,
            step_name=step.name or step.id,
            step_type=step.type,
        ))

        async def _do_execute():
            """Execute the step handler, collecting events and results."""
            result_outputs: dict[str, Any] = {}
            exec_events: list[PipelineEvent | dict] = []
            async for event in _run_step_handler(handler, ctx, step.config, inputs, step.id, timeout):
                if isinstance(event, dict) and "__result__" in event:
                    result_outputs = event["__result__"]
                else:
                    exec_events.append(event)
            return exec_events, result_outputs

        try:
            exec_events, outputs = await _do_execute()
            events.extend(exec_events)

            # Apply outputs to context
            ctx.apply_outputs(step.outputs, outputs)

            events.append(StepDoneEvent(
                step_id=step.id,
                status="success",
                duration_ms=_elapsed_ms(step_start),
                outputs={k: str(v)[:200] for k, v in outputs.items()},  # Truncate for events
            ))
            return {"status": "success", "events": events, "outputs": outputs, "error": None}

        except StepError as e:
            if e.healable and step.healing.enabled:
                # ── Enter healing loop ───────────────────────────
                snapshot = ctx.snapshot()

                async def retry_fn():
                    """Re-execute the step (used by healing engine)."""
                    _events, _outputs = await _do_execute()
                    for ev in _events:
                        yield ev
                    yield {"__result__": _outputs}

                healing_result: dict[str, Any] = {
                    "success": False, "outputs": {}, "error": str(e),
                }

                async for h_event in run_healing(
                    ctx=ctx,
                    step_id=step.id,
                    healing=step.healing,
                    execute_step_fn=retry_fn,
                    step_timeout=timeout,
                    registry=self._registry,
                ):
                    if isinstance(h_event, dict) and "__healing_result__" in h_event:
                        healing_result = h_event["__healing_result__"]
                    else:
                        events.append(h_event)

                if healing_result["success"]:
                    ctx.apply_outputs(step.outputs, healing_result["outputs"])
                    events.append(StepDoneEvent(
                        step_id=step.id,
                        status="success",
                        duration_ms=_elapsed_ms(step_start),
                        outputs={k: str(v)[:200] for k, v in healing_result["outputs"].items()},
                    ))
                    return {"status": "success", "events": events, "outputs": healing_result["outputs"], "error": None}
                else:
                    # Restore context to pre-healing state
                    ctx.restore(snapshot)
                    events.append(StepDoneEvent(
                        step_id=step.id,
                        status="failed",
                        duration_ms=_elapsed_ms(step_start),
                        error=healing_result.get("error", str(e)),
                    ))
                    return {
                        "status": "failed",
                        "events": events,
                        "outputs": {},
                        "error": healing_result.get("error", str(e)),
                    }
            else:
                events.append(StepDoneEvent(
                    step_id=step.id,
                    status="failed",
                    duration_ms=_elapsed_ms(step_start),
                    error=str(e),
                ))
                return {"status": "failed", "events": events, "outputs": {}, "error": str(e)}

        except StepPaused as e:
            # ── Step needs external input — pause the pipeline ───
            events.append(StepWaitingEvent(
                step_id=step.id,
                step_name=step.name or step.id,
                gate_type=e.gate_type,
                assignee=e.assignee,
                instructions=e.instructions,
                required_inputs=e.required_inputs,
                form_schema=e.form_schema,
            ))
            return {
                "status": "paused",
                "events": events,
                "outputs": {},
                "error": None,
                "paused_info": e.to_dict(),
            }

        except asyncio.CancelledError:
            events.append(StepDoneEvent(
                step_id=step.id,
                status="cancelled",
                duration_ms=_elapsed_ms(step_start),
            ))
            return {"status": "cancelled", "events": events, "outputs": {}, "error": "cancelled"}

        except Exception as e:
            logger.exception("Unexpected error in step %r", step.id)
            events.append(StepDoneEvent(
                step_id=step.id,
                status="failed",
                duration_ms=_elapsed_ms(step_start),
                error=str(e),
            ))
            return {"status": "failed", "events": events, "outputs": {}, "error": str(e)}
