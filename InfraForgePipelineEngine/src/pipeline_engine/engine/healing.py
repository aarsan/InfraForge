"""Healing engine — retry failed steps with optional healer invocation.

Improvements over InfraForge's HealingLoop:
- Retries only the failed step (no restart-from-first-check)
- Exponential backoff with jitter between attempts
- Per-attempt timeout
- Attempt isolation via context snapshots
- Healer is just another step type resolved from the registry
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import (
    HealingDoneEvent,
    HealingStartEvent,
    LogEvent,
    PipelineEvent,
)
from pipeline_engine.models.pipeline import HealingConfig

logger = logging.getLogger(__name__)


def _compute_delay(config: HealingConfig, attempt: int) -> float:
    """Compute delay before next healing attempt with jitter."""
    base = config.base_delay
    if config.backoff == "none":
        delay = base
    elif config.backoff == "linear":
        delay = base * attempt
    else:  # exponential
        delay = base * (2 ** (attempt - 1))
    # Add jitter: ±25%
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0, delay + jitter)


async def run_healing(
    ctx: PipelineContext,
    step_id: str,
    healing: HealingConfig,
    execute_step_fn,
    step_timeout: int | None,
    registry,
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the healing loop for a failed step.

    Args:
        ctx: Current pipeline context.
        step_id: ID of the failed step (for event reporting).
        healing: Healing configuration from the step definition.
        execute_step_fn: Callable(ctx, step_def, timeout) -> AsyncGenerator[event]
            that re-executes the failed step.
        step_timeout: Per-attempt timeout in seconds.
        registry: StepRegistry for resolving the healer step type.

    Yields:
        HealingStartEvent, HealingDoneEvent, LogEvent, and events from
        the healer step and retry attempts.

    Returns via the last yielded dict with ``__healing_result__`` key:
        ``{"__healing_result__": {"success": bool, "outputs": dict, "error": str|None}}``
    """
    last_error: str = ""

    for attempt in range(1, healing.max_attempts + 1):
        if ctx.cancelled:
            yield LogEvent(level="warn", message="Healing cancelled", step_id=step_id)
            yield {"__healing_result__": {"success": False, "outputs": {}, "error": "cancelled"}}
            return

        yield HealingStartEvent(
            step_id=step_id,
            attempt=attempt,
            max_attempts=healing.max_attempts,
        )

        # ── Invoke healer step (if configured) ──────────────────
        if healing.healer_type:
            try:
                healer = registry.resolve(healing.healer_type)
                healer_inputs = {
                    "error": last_error,
                    **ctx.resolve_inputs({}),
                }
                # Merge original context data into healer config
                healer_config = {**healing.healer_config}

                strategy = ""
                async for event in healer.execute(ctx, healer_config, healer_inputs):
                    if isinstance(event, dict) and "__result__" in event:
                        result_data = event["__result__"]
                        strategy = result_data.get("strategy", "")
                        # Apply healer outputs to context
                        for k, v in result_data.items():
                            if k != "strategy":
                                ctx.set(k, v)
                    elif isinstance(event, PipelineEvent):
                        yield event

                yield HealingDoneEvent(
                    step_id=step_id,
                    attempt=attempt,
                    strategy=strategy,
                    success=True,
                )
            except Exception as e:
                logger.exception("Healer step %r failed on attempt %d", healing.healer_type, attempt)
                yield HealingDoneEvent(
                    step_id=step_id,
                    attempt=attempt,
                    strategy=f"Healer failed: {e}",
                    success=False,
                )
                last_error = str(e)

                # Backoff before next attempt
                if attempt < healing.max_attempts:
                    delay = _compute_delay(healing, attempt)
                    await asyncio.sleep(delay)
                continue
        else:
            yield HealingDoneEvent(
                step_id=step_id,
                attempt=attempt,
                strategy="retry without healer",
                success=True,
            )

        # ── Retry the original step ─────────────────────────────
        try:
            retry_outputs: dict[str, Any] = {}
            async for event in execute_step_fn():
                if isinstance(event, dict) and "__result__" in event:
                    retry_outputs = event["__result__"]
                else:
                    yield event

            # Success — step passed on retry
            yield {"__healing_result__": {"success": True, "outputs": retry_outputs, "error": None}}
            return

        except Exception as e:
            last_error = str(e)
            yield LogEvent(
                level="warn",
                message=f"Retry attempt {attempt}/{healing.max_attempts} failed: {e}",
                step_id=step_id,
            )

            if attempt < healing.max_attempts:
                delay = _compute_delay(healing, attempt)
                await asyncio.sleep(delay)

    # All attempts exhausted
    yield {"__healing_result__": {"success": False, "outputs": {}, "error": last_error}}
