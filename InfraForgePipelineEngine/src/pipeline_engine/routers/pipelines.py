"""Pipeline execution API routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pipeline_engine.engine.executor import PipelineExecutor, ResumeState
from pipeline_engine.models.pipeline import PipelineRequest
from pipeline_engine.streaming.emitter import EventEmitter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipelines"])


class ResumeBody(BaseModel):
    """Body for POST /pipelines/{run_id}/steps/{step_id}/complete."""
    outputs: dict[str, Any] = Field(
        ...,
        description="Key-value outputs provided by the human/external actor",
    )


def _get_run_store(request: Request):
    return request.app.state.run_store


def _get_registry(request: Request):
    return request.app.state.registry


async def _execute_and_stream(
    executor: PipelineExecutor,
    pipeline_request: PipelineRequest,
    run_store,
    *,
    resume: ResumeState | None = None,
):
    """Shared logic: run the executor, push events to an emitter, return StreamingResponse."""
    emitter = EventEmitter()

    async def _produce():
        run_id: str | None = None
        try:
            async for event in executor.execute(pipeline_request, resume=resume):
                if isinstance(event, dict):
                    if "__pipeline_result__" in event:
                        result = event["__pipeline_result__"]
                        run_id = result.get("run_id")
                        if run_id:
                            run_store.update_status(run_id, "completed")
                        continue
                    if "__pipeline_paused__" in event:
                        paused = event["__pipeline_paused__"]
                        r_id = paused["context"]["run_id"]
                        run_store.save_pause(
                            r_id,
                            context_dict=paused["context"],
                            position=tuple(paused["position"]),
                            waiting_step_id=paused["step_id"],
                            waiting_config=paused["waiting_config"],
                        )
                        # Also save the original request for resume
                        state = run_store.get(r_id)
                        if state:
                            state.request_dict = pipeline_request.model_dump(mode="json")
                        continue
                await emitter.emit(event)

                # Capture run_id from pipeline_start or pipeline_resumed
                if hasattr(event, "type"):
                    if event.type == "pipeline_start":
                        run_id = event.run_id
                        run_store.create(run_id, pipeline_request.model_dump(mode="json"))
                        run_store.update_status(run_id, "running")
                    elif event.type == "pipeline_resumed":
                        run_id = event.run_id
                        run_store.update_status(run_id, "running")

                # Store events for replay
                if run_id and hasattr(event, "model_dump"):
                    run_store.append_event(run_id, event.model_dump(mode="json"))

        except asyncio.CancelledError:
            if run_id:
                run_store.update_status(run_id, "cancelled")
        except Exception as e:
            logger.exception("Pipeline execution failed")
            if run_id:
                run_store.update_status(run_id, "failed", error=str(e))
            await emitter.emit({
                "type": "pipeline_done",
                "run_id": run_id or "unknown",
                "status": "failed",
                "error": str(e),
                "duration_ms": 0,
            })
        finally:
            await emitter.finish()

    task = asyncio.create_task(_produce())

    async def _stream():
        try:
            async for line in emitter.stream():
                yield line
        except asyncio.CancelledError:
            emitter.close()
            task.cancel()
            raise

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/pipelines/run")
async def run_pipeline(body: PipelineRequest, request: Request):
    """Execute a pipeline and stream NDJSON progress events."""
    registry = _get_registry(request)
    run_store = _get_run_store(request)
    executor = PipelineExecutor(registry)

    errors = executor.validate(body)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    return await _execute_and_stream(executor, body, run_store)


@router.post("/pipelines/{run_id}/steps/{step_id}/complete")
async def complete_step(run_id: str, step_id: str, body: ResumeBody, request: Request):
    """Resume a paused pipeline by providing outputs for a waiting step.

    This is the callback endpoint for human-in-the-loop gates.  When a
    pipeline pauses at a gate step, the client (or a human via UI) calls
    this endpoint with the required outputs to continue execution.
    """
    run_store = _get_run_store(request)
    registry = _get_registry(request)

    state = run_store.get(run_id)
    if not state:
        raise HTTPException(404, detail=f"Run {run_id!r} not found")
    if state.status != "paused":
        raise HTTPException(409, detail=f"Run is not paused (status={state.status!r})")
    if state.waiting_step_id != step_id:
        raise HTTPException(
            409,
            detail=f"Run is waiting on step {state.waiting_step_id!r}, not {step_id!r}",
        )

    # Reconstruct the pipeline request and resume state
    pipeline_request = PipelineRequest(**state.request_dict)
    resume = ResumeState(
        context_dict=state.context_dict,
        position=tuple(state.position),
        step_outputs=body.outputs,
    )

    executor = PipelineExecutor(registry)
    return await _execute_and_stream(executor, pipeline_request, run_store, resume=resume)


@router.get("/pipelines/{run_id}")
async def get_run_status(run_id: str, request: Request):
    """Get the status and details of a pipeline run."""
    run_store = _get_run_store(request)
    state = run_store.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return state.to_dict()


@router.get("/pipelines/{run_id}/events")
async def get_run_events(run_id: str, request: Request):
    """Get all events emitted by a pipeline run."""
    run_store = _get_run_store(request)
    state = run_store.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return {"run_id": run_id, "event_count": len(state.events), "events": state.events}


@router.get("/pipelines")
async def list_runs(request: Request, status: str | None = None):
    """List all pipeline runs, optionally filtered by status."""
    run_store = _get_run_store(request)
    return {"runs": run_store.list_runs(status=status)}


@router.post("/pipelines/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """Cancel a running pipeline (best-effort)."""
    run_store = _get_run_store(request)
    state = run_store.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    if state.status not in ("running", "paused"):
        raise HTTPException(status_code=409, detail=f"Run cannot be cancelled (status={state.status!r})")
    run_store.update_status(run_id, "cancelled")
    return {"run_id": run_id, "status": "cancelled"}
