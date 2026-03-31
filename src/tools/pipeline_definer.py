"""
Pipeline definition tools for Copilot SDK agents.

Allows agents to create, retrieve, and list standardised pipeline
definitions using the ``infraforge.pipeline.v1`` schema.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, ValidationError
from copilot import define_tool

from src.pipeline_schema import PipelineDefinition, get_builtin_definitions
from src.database import (
    save_pipeline_definition,
    get_pipeline_definition,
    list_pipeline_definitions,
)

logger = logging.getLogger("infraforge.tools.pipeline_definer")


# ══════════════════════════════════════════════════════════════
# TOOL 1 — define_pipeline
# ══════════════════════════════════════════════════════════════

class DefinePipelineParams(BaseModel):
    definition_json: str = Field(
        description=(
            "A full PipelineDefinition JSON string conforming to "
            "infraforge.pipeline.v1.  Must include id, name, stages "
            "(each with id, name, steps), and each step must have id, "
            "name, and action fields."
        )
    )


@define_tool(description=(
    "Create or update a standardised pipeline definition. "
    "Use this tool when the user asks to define a new pipeline, "
    "workflow, or automation sequence. The definition uses the "
    "infraforge.pipeline.v1 schema with stages and steps. "
    "Provide the full pipeline definition as a JSON string."
))
async def define_pipeline(params: DefinePipelineParams) -> str:
    """Validate and persist a pipeline definition."""
    try:
        raw = json.loads(params.definition_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid JSON: {exc}"

    try:
        defn = PipelineDefinition.model_validate(raw)
    except ValidationError as exc:
        return f"❌ Schema validation failed:\n{exc}"

    # Prevent overwriting builtins
    existing = await get_pipeline_definition(defn.id)
    if existing and existing.get("is_builtin"):
        return (
            f"❌ Cannot overwrite built-in pipeline '{defn.id}'. "
            "Choose a different ID for your custom pipeline."
        )

    await save_pipeline_definition(
        definition_id=defn.id,
        name=defn.name,
        version=defn.version,
        definition_json=defn.model_dump_json(),
        created_by="copilot-agent",
        is_builtin=False,
    )

    preview = defn.to_preview()
    stage_summary = " → ".join(
        f"{s['icon']} {s['name']} ({s['step_count']} steps)"
        for s in preview["stages"]
    )

    return (
        f"✅ Pipeline **{defn.name}** (v{defn.version}) saved.\n\n"
        f"**ID:** `{defn.id}`\n"
        f"**Stages:** {stage_summary}\n"
        f"**Total steps:** {preview['total_steps']}\n\n"
        f"View it at `/api/pipelines/definitions/{defn.id}/preview`."
    )


# ══════════════════════════════════════════════════════════════
# TOOL 2 — get_pipeline_definition
# ══════════════════════════════════════════════════════════════

class GetPipelineParams(BaseModel):
    pipeline_id: str = Field(
        description=(
            "The ID of the pipeline definition to retrieve. "
            "Examples: 'service_onboarding', 'template_validation', "
            "'api_version_update', 'deployment'."
        )
    )


@define_tool(description=(
    "Retrieve a stored pipeline definition by ID. "
    "Returns the full stage/step structure in infraforge.pipeline.v1 format. "
    "Use this to inspect an existing pipeline's structure before modifying "
    "it or explaining it to the user."
))
async def get_pipeline_definition_tool(params: GetPipelineParams) -> str:
    """Fetch and format a pipeline definition for display."""
    row = await get_pipeline_definition(params.pipeline_id)
    if not row:
        available = await list_pipeline_definitions()
        ids = ", ".join(f"`{d['id']}`" for d in available)
        return (
            f"❌ Pipeline '{params.pipeline_id}' not found.\n\n"
            f"Available pipelines: {ids or 'none'}"
        )

    try:
        defn = PipelineDefinition.model_validate(row["definition"])
    except (ValidationError, KeyError):
        return f"⚠️ Pipeline '{params.pipeline_id}' exists but has invalid schema."

    preview = defn.to_preview()
    lines = [
        f"## {defn.icon} {defn.name} (v{defn.version})",
        "",
        defn.description,
        "",
    ]

    for stage in preview["stages"]:
        lines.append(f"### {stage['icon']} {stage['name']}")
        for step in stage["steps"]:
            heal = " 🔧 healable" if step["healable"] else ""
            lines.append(f"  {step['icon']} **{step['name']}** — `{step['action']}`{heal}")
        lines.append("")

    lines.append(f"**Total steps:** {preview['total_steps']}")
    if defn.metadata.tags:
        lines.append(f"**Tags:** {', '.join(defn.metadata.tags)}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# TOOL 3 — list_pipeline_definitions
# ══════════════════════════════════════════════════════════════

class ListPipelinesParams(BaseModel):
    include_disabled: bool = Field(
        default=False,
        description="Include disabled pipeline definitions",
    )


@define_tool(description=(
    "List all available pipeline definitions in the system. "
    "Shows pipeline names, IDs, versions, and whether they are "
    "built-in or user-created. Use this to discover what pipelines "
    "exist before creating or modifying one."
))
async def list_pipeline_definitions_tool(params: ListPipelinesParams) -> str:
    """List all pipeline definitions."""
    definitions = await list_pipeline_definitions(
        enabled_only=not params.include_disabled,
    )

    if not definitions:
        return "No pipeline definitions found."

    lines = ["## Available Pipeline Definitions\n"]
    for d in definitions:
        builtin = " *(built-in)*" if d.get("is_builtin") else ""
        enabled = "" if d.get("enabled", True) else " — **disabled**"
        lines.append(
            f"- **{d['name']}** — `{d['id']}` v{d['version']}{builtin}{enabled}"
        )

    return "\n".join(lines)
