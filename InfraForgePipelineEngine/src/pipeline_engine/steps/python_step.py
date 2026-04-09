"""Python step handler — calls a function from an installed Python package."""

from __future__ import annotations

import importlib
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import LogEvent, PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepError, StepHandler


class PythonStepHandler(StepHandler):
    """Execute a Python callable from an installed package.

    Config:
        module:   Dotted module path (e.g. ``my_package.steps``)
        function: Name of an async or sync callable in that module
        kwargs:   Extra keyword arguments merged with inputs
    """

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        module_path = config.get("module")
        function_name = config.get("function")

        if not module_path or not function_name:
            raise StepError(
                "Python step requires 'module' and 'function' in config",
                healable=False,
            )

        step_id = ctx.current_step or "python"
        yield LogEvent(
            level="info",
            message=f"Calling {module_path}.{function_name}()",
            step_id=step_id,
        )

        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            raise StepError(
                f"Cannot import module {module_path!r}: {e}",
                healable=False,
                detail=str(e),
            ) from e

        fn = getattr(mod, function_name, None)
        if fn is None:
            raise StepError(
                f"Module {module_path!r} has no attribute {function_name!r}",
                healable=False,
            )

        if not callable(fn):
            raise StepError(
                f"{module_path}.{function_name} is not callable",
                healable=False,
            )

        kwargs = {**inputs, **config.get("kwargs", {})}
        if ctx.dry_run:
            yield StepProgressEvent(step_id=step_id, progress=1.0, detail="dry_run: skipped execution")
            yield {"__result__": {"dry_run": True, "would_call": f"{module_path}.{function_name}"}}
            return

        import asyncio

        if asyncio.iscoroutinefunction(fn):
            result = await fn(ctx=ctx, **kwargs)
        else:
            result = fn(ctx=ctx, **kwargs)

        if not isinstance(result, dict):
            result = {"return_value": result}

        yield StepProgressEvent(step_id=step_id, progress=1.0, detail="done")
        yield {"__result__": result}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["module", "function"],
            "properties": {
                "module": {"type": "string", "description": "Dotted Python module path"},
                "function": {"type": "string", "description": "Callable name in the module"},
                "kwargs": {"type": "object", "description": "Extra keyword arguments"},
            },
        }

    def description(self) -> str:
        return "Execute a Python callable from an installed package."
