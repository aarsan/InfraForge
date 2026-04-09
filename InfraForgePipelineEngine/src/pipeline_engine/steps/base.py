"""Step handler abstract base class — the plugin contract.

Every step type (built-in or third-party) implements this interface.
Handlers are discovered at startup via ``importlib.metadata.entry_points``
under the ``pipeline_engine.steps`` group.
"""

from __future__ import annotations

import abc
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import PipelineEvent


class StepError(Exception):
    """Raised by a step handler when execution fails.

    Attributes:
        healable: If True and healing is configured, the engine will
                  invoke the healer step instead of immediately failing.
        detail:   Freeform error detail passed to the healer.
    """

    def __init__(self, message: str, *, healable: bool = True, detail: str = "") -> None:
        super().__init__(message)
        self.healable = healable
        self.detail = detail


class StepPaused(Exception):
    """Raised by a step handler when it needs external input to continue.

    When raised, the pipeline executor pauses the pipeline, persists its
    state, and emits a ``step_waiting`` event.  The pipeline resumes when
    a client submits the required data via the resume API.

    Attributes:
        required_inputs: List of input field descriptors the external
                         actor must provide before the step can continue.
        assignee:        Who should act (team, role, or individual).
        instructions:    Human-readable description of what's needed.
        gate_type:       Category of gate (approval, review, input, manual).
        form_schema:     Optional JSON-schema describing the expected input form.
    """

    def __init__(
        self,
        message: str,
        *,
        gate_type: str = "manual",
        assignee: str = "",
        instructions: str = "",
        required_inputs: list[dict] | None = None,
        form_schema: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.gate_type = gate_type
        self.assignee = assignee
        self.instructions = instructions
        self.required_inputs = required_inputs or []
        self.form_schema = form_schema or {}

    def to_dict(self) -> dict:
        return {
            "gate_type": self.gate_type,
            "assignee": self.assignee,
            "instructions": self.instructions,
            "required_inputs": self.required_inputs,
            "form_schema": self.form_schema,
        }


class StepHandler(abc.ABC):
    """Abstract base class that all step type plugins must implement."""

    @abc.abstractmethod
    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        """Execute the step.

        Yields:
            ``PipelineEvent`` instances (progress, log, etc.) during execution.
            The **last** yielded dict with key ``"__result__"`` is treated as
            the step's output mapping.  Example::

                yield {"__result__": {"template": "<arm json>"}}

        Raises:
            StepError: on expected/handleable failures.
        """
        yield  # pragma: no cover — abstract
        return  # type: ignore[return-value]

    def config_schema(self) -> dict[str, Any]:
        """Return a JSON Schema dict describing the ``config`` this step accepts.

        Override to provide schema validation.  Default: accept anything.
        """
        return {"type": "object", "additionalProperties": True}

    def description(self) -> str:
        """Human-readable description shown in the step catalog."""
        return self.__class__.__doc__ or self.__class__.__name__
