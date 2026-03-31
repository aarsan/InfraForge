"""
Pipeline Definitions REST API.

Exposes CRUD endpoints for the standardised pipeline definition schema
(``infraforge.pipeline.v1``).  Built-in definitions are seeded at startup;
agents and admin UIs can create additional definitions.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from src.database import (
    get_pipeline_definition,
    list_pipeline_definitions,
    save_pipeline_definition,
    delete_pipeline_definition,
)
from src.pipeline_schema import PipelineDefinition

logger = logging.getLogger("infraforge.routers.pipelines")

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


# ── Request / response models ────────────────────────────────

class CreatePipelineRequest(BaseModel):
    definition: dict = Field(
        description="Full PipelineDefinition JSON conforming to infraforge.pipeline.v1",
    )
    created_by: str | None = Field(
        default=None,
        description="Who created this definition (agent name, user email, etc.)",
    )


class UpdatePipelineRequest(BaseModel):
    definition: dict = Field(
        description="Updated PipelineDefinition JSON",
    )


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/definitions")
async def api_list_definitions(enabled_only: bool = True):
    """List all pipeline definitions (lightweight — no body)."""
    definitions = await list_pipeline_definitions(enabled_only=enabled_only)
    return {"definitions": definitions, "total": len(definitions)}


@router.get("/definitions/{definition_id}")
async def api_get_definition(definition_id: str):
    """Get a single pipeline definition with full JSON body."""
    row = await get_pipeline_definition(definition_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline definition '{definition_id}' not found")
    return row


@router.get("/definitions/{definition_id}/preview")
async def api_preview_definition(definition_id: str):
    """Get a frontend-friendly preview of a pipeline definition.

    Returns the stage/step tree without the full JSON, ready for
    ``_renderPipelineBlueprint()`` on the frontend.
    """
    row = await get_pipeline_definition(definition_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline definition '{definition_id}' not found")
    try:
        defn = PipelineDefinition.model_validate(row["definition"])
    except (ValidationError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid definition JSON: {exc}")
    return defn.to_preview()


@router.post("/definitions", status_code=201)
async def api_create_definition(req: CreatePipelineRequest):
    """Create a new pipeline definition.

    Validates the JSON against the Pydantic schema before persisting.
    """
    try:
        defn = PipelineDefinition.model_validate(req.definition)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid pipeline definition: {exc}")

    # Check for collision with builtin
    existing = await get_pipeline_definition(defn.id)
    if existing and existing.get("is_builtin"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot overwrite built-in pipeline '{defn.id}'",
        )

    await save_pipeline_definition(
        definition_id=defn.id,
        name=defn.name,
        version=defn.version,
        definition_json=defn.model_dump_json(),
        created_by=req.created_by,
        is_builtin=False,
    )

    return {
        "id": defn.id,
        "name": defn.name,
        "version": defn.version,
        "message": f"Pipeline definition '{defn.id}' saved",
    }


@router.put("/definitions/{definition_id}")
async def api_update_definition(definition_id: str, req: UpdatePipelineRequest):
    """Update an existing pipeline definition.

    Cannot update built-in definitions.
    """
    existing = await get_pipeline_definition(definition_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Pipeline definition '{definition_id}' not found")
    if existing.get("is_builtin"):
        raise HTTPException(status_code=403, detail="Cannot modify built-in pipeline definitions")

    try:
        defn = PipelineDefinition.model_validate(req.definition)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid pipeline definition: {exc}")

    if defn.id != definition_id:
        raise HTTPException(status_code=400, detail="Definition ID in body does not match URL")

    await save_pipeline_definition(
        definition_id=defn.id,
        name=defn.name,
        version=defn.version,
        definition_json=defn.model_dump_json(),
    )

    return {"id": defn.id, "message": "Updated"}


@router.delete("/definitions/{definition_id}")
async def api_delete_definition(definition_id: str):
    """Delete a user-created pipeline definition.

    Built-in definitions cannot be deleted.
    """
    removed = await delete_pipeline_definition(definition_id)
    if not removed:
        existing = await get_pipeline_definition(definition_id)
        if existing and existing.get("is_builtin"):
            raise HTTPException(status_code=403, detail="Cannot delete built-in pipeline definitions")
        raise HTTPException(status_code=404, detail=f"Pipeline definition '{definition_id}' not found")
    return {"deleted": True}
