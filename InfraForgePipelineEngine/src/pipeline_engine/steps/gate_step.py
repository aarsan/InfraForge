"""Gate step handler — pauses the pipeline for human input.

Used for approvals, reviews, manual sign-offs, and any step that
requires external (human or system) input before the pipeline can
continue.  When the gate's required inputs are not yet in context,
it raises ``StepPaused`` which causes the executor to persist state
and wait for a resume call.

YAML example::

    - id: security_review
      type: gate
      name: Security Review
      config:
        gate_type: approval
        assignee: security-team
        instructions: "Review the ARM template for security compliance"
        required_inputs:
          - name: verdict
            type: enum
            options: [approved, rejected, needs_changes]
            required: true
          - name: comments
            type: text
            required: false
        sla_seconds: 86400
        escalation_assignee: ciso
      inputs:
        template: ctx.template
      outputs:
        verdict: ctx.security_verdict
        comments: ctx.security_comments
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepHandler, StepPaused


class GateStepHandler(StepHandler):
    """Human-in-the-loop gate — pauses pipeline until external input arrives."""

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        gate_type = config.get("gate_type", "manual")
        assignee = config.get("assignee", "")
        instructions = config.get("instructions", "")
        required_inputs = config.get("required_inputs", [])
        sla_seconds = config.get("sla_seconds")

        # Check if all required inputs have already been provided (i.e., we're
        # being re-executed after a resume injected the values into context).
        all_satisfied = True
        for field in required_inputs:
            field_name = field.get("name", "")
            is_required = field.get("required", True)
            if is_required and field_name not in inputs:
                all_satisfied = False
                break
            if is_required and inputs.get(field_name) is None:
                all_satisfied = False
                break

        # If no required_inputs defined, treat as a simple pause-and-wait
        if not required_inputs:
            all_satisfied = False

        if not all_satisfied:
            # Build form schema for the UI
            form_schema = _build_form_schema(required_inputs)

            raise StepPaused(
                message=f"Waiting for {gate_type}: {instructions}",
                gate_type=gate_type,
                assignee=assignee,
                instructions=instructions,
                required_inputs=required_inputs,
                form_schema=form_schema,
            )

        # All inputs satisfied — pass through
        step_id = ctx.current_step or "gate"
        yield StepProgressEvent(
            step_id=step_id,
            progress=1.0,
            detail=f"{gate_type} completed by {assignee or 'external'}",
        )

        # Return all provided required fields as outputs
        result = {}
        for field in required_inputs:
            name = field.get("name", "")
            if name in inputs:
                result[name] = inputs[name]
        # Also pass through any extra inputs not in required_inputs
        for k, v in inputs.items():
            if k not in result:
                result[k] = v

        yield {"__result__": result}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "gate_type": {
                    "type": "string",
                    "enum": ["approval", "review", "input", "manual", "sign_off"],
                    "default": "manual",
                    "description": "Category of gate",
                },
                "assignee": {
                    "type": "string",
                    "description": "Who needs to act (team, role, email, etc.)",
                },
                "instructions": {
                    "type": "string",
                    "description": "Human-readable instructions for the assignee",
                },
                "required_inputs": {
                    "type": "array",
                    "description": "Fields the assignee must provide",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["text", "enum", "boolean", "number", "json"]},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "required": {"type": "boolean", "default": True},
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "sla_seconds": {
                    "type": "integer",
                    "description": "SLA in seconds — used for escalation tracking",
                },
                "escalation_assignee": {
                    "type": "string",
                    "description": "Who to escalate to if SLA is breached",
                },
            },
        }

    def description(self) -> str:
        return (
            "Human-in-the-loop gate step. Pauses the pipeline until an external "
            "actor (human or system) provides the required inputs via the resume API. "
            "Supports approval, review, manual input, and sign-off gate types."
        )


def _build_form_schema(required_inputs: list[dict]) -> dict:
    """Build a JSON Schema from the required_inputs field descriptors."""
    if not required_inputs:
        return {"type": "object", "properties": {}, "required": []}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for field in required_inputs:
        name = field.get("name", "")
        ftype = field.get("type", "text")
        desc = field.get("description", "")
        options = field.get("options", [])

        schema: dict[str, Any] = {}
        if ftype == "enum" and options:
            schema = {"type": "string", "enum": options}
        elif ftype == "boolean":
            schema = {"type": "boolean"}
        elif ftype == "number":
            schema = {"type": "number"}
        elif ftype == "json":
            schema = {"type": "object"}
        else:
            schema = {"type": "string"}

        if desc:
            schema["description"] = desc
        properties[name] = schema

        if field.get("required", True):
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}
