"""Step catalog API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["catalog"])


@router.get("/catalog/steps")
async def list_step_types(request: Request):
    """List all registered step types with their config schemas."""
    registry = request.app.state.registry
    return {"steps": registry.catalog()}
