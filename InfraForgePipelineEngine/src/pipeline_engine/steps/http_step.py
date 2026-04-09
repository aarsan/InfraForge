"""HTTP step handler — makes an HTTP request."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import LogEvent, PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepError, StepHandler


class HttpStepHandler(StepHandler):
    """Make an HTTP request.

    Config:
        method:          HTTP method (GET, POST, PUT, DELETE, PATCH)
        url:             Target URL (supports {ctx.key} and {inputs.key} templating)
        headers:         Request headers dict
        body:            Request body (string or dict — dict is JSON-serialized)
        expected_status: Expected HTTP status code or list of codes (default: [200, 201, 202, 204])
        timeout:         Request timeout in seconds (default: 30)
    """

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        import httpx

        url = config.get("url")
        method = config.get("method", "GET").upper()

        if not url:
            raise StepError("HTTP step requires 'url' in config", healable=False)

        step_id = ctx.current_step or "http"

        # Template substitution
        for key, value in (ctx.data or {}).items():
            url = url.replace(f"{{ctx.{key}}}", str(value))
        for key, value in inputs.items():
            url = url.replace(f"{{inputs.{key}}}", str(value))

        headers = config.get("headers", {})
        body = config.get("body")
        if isinstance(body, dict):
            # Template string values in body
            for k, v in body.items():
                if isinstance(v, str):
                    for ck, cv in (ctx.data or {}).items():
                        v = v.replace(f"{{ctx.{ck}}}", str(cv))
                    for ik, iv in inputs.items():
                        v = v.replace(f"{{inputs.{ik}}}", str(iv))
                    body[k] = v

        expected = config.get("expected_status", [200, 201, 202, 204])
        if isinstance(expected, int):
            expected = [expected]
        req_timeout = config.get("timeout", 30)

        yield LogEvent(level="info", message=f"{method} {url}", step_id=step_id)

        if ctx.dry_run:
            yield StepProgressEvent(step_id=step_id, progress=1.0, detail="dry_run: skipped")
            yield {"__result__": {"dry_run": True, "method": method, "url": url}}
            return

        try:
            async with httpx.AsyncClient(timeout=req_timeout) as client:
                kwargs: dict[str, Any] = {"method": method, "url": url, "headers": headers}
                if body is not None:
                    if isinstance(body, dict):
                        kwargs["json"] = body
                    else:
                        kwargs["content"] = str(body)

                resp = await client.request(**kwargs)
        except httpx.TimeoutException as e:
            raise StepError(
                f"HTTP request timed out after {req_timeout}s: {url}",
                healable=True,
                detail=str(e),
            ) from e
        except Exception as e:
            raise StepError(
                f"HTTP request failed: {e}",
                healable=True,
                detail=str(e),
            ) from e

        if resp.status_code not in expected:
            raise StepError(
                f"HTTP {method} {url} returned {resp.status_code} (expected {expected}): {resp.text[:500]}",
                healable=True,
                detail=resp.text[:2000],
            )

        # Parse response
        try:
            response_body = resp.json()
        except (json.JSONDecodeError, Exception):
            response_body = resp.text

        yield StepProgressEvent(step_id=step_id, progress=1.0, detail=f"status={resp.status_code}")
        yield {"__result__": {
            "status_code": resp.status_code,
            "body": response_body,
            "headers": dict(resp.headers),
        }}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["url"],
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "default": "GET"},
                "url": {"type": "string", "description": "Target URL with optional {ctx.key} templating"},
                "headers": {"type": "object", "description": "Request headers"},
                "body": {"description": "Request body (string or object)"},
                "expected_status": {
                    "description": "Expected status code(s)",
                    "oneOf": [{"type": "integer"}, {"type": "array", "items": {"type": "integer"}}],
                },
                "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 30},
            },
        }

    def description(self) -> str:
        return "Make an HTTP request with configurable method, headers, body, and response validation."
