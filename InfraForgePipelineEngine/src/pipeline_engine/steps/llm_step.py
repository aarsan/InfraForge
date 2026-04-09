"""LLM step handler — first-class large language model invocation.

Calls an OpenAI-compatible chat completions API with prompt templating,
response extraction, and structured output support.  Works with Azure
OpenAI, OpenAI, and any OpenAI-compatible endpoint.

YAML example::

    - id: generate_template
      type: llm
      name: Generate ARM Template
      config:
        endpoint: "https://my-openai.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-02-01"
        auth:
          type: bearer
          token: "{ctx.llm_token}"
        messages:
          - role: system
            content: "You are an Azure infrastructure architect."
          - role: user
            content: |
              Generate an ARM template for {ctx.service_id} in region {ctx.region}.
        temperature: 0.2
        max_tokens: 4000
        response_format: json_object
        extract: "choices.0.message.content"
      outputs:
        response: ctx.template
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import LogEvent, PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepError, StepHandler


def _template_string(s: str, ctx_data: dict[str, Any], inputs: dict[str, Any]) -> str:
    """Replace {ctx.key} and {inputs.key} placeholders in a string."""
    if not isinstance(s, str):
        return s
    for key, value in ctx_data.items():
        s = s.replace(f"{{ctx.{key}}}", str(value))
    for key, value in inputs.items():
        s = s.replace(f"{{inputs.{key}}}", str(value))
    return s


def _template_deep(obj: Any, ctx_data: dict[str, Any], inputs: dict[str, Any]) -> Any:
    """Recursively apply template substitution to nested dicts/lists/strings."""
    if isinstance(obj, str):
        return _template_string(obj, ctx_data, inputs)
    elif isinstance(obj, dict):
        return {k: _template_deep(v, ctx_data, inputs) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_template_deep(item, ctx_data, inputs) for item in obj]
    return obj


def _extract_path(data: Any, path: str) -> Any:
    """Extract a value from nested data using dot notation.

    Supports dict keys and integer list indices::

        "choices.0.message.content"  →  data["choices"][0]["message"]["content"]
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


class LlmStepHandler(StepHandler):
    """Call an OpenAI-compatible chat completions API.

    Config:
        endpoint:        Full URL to the chat completions endpoint
        auth:            Auth config: {type: bearer|api_key|header, token/key, header_name}
        messages:        List of {role, content} message dicts (supports {ctx.*} templating)
        model:           Model name (optional, for endpoints that need it in the body)
        temperature:     Sampling temperature (default: 0.7)
        max_tokens:      Max response tokens (default: 4096)
        response_format: "text" | "json_object" (default: "text")
        extract:         Dot-notation path to extract from response (default: "choices.0.message.content")
        timeout:         HTTP request timeout in seconds (default: 120)
    """

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        import httpx

        endpoint = config.get("endpoint")
        if not endpoint:
            raise StepError("LLM step requires 'endpoint' in config", healable=False)

        step_id = ctx.current_step or "llm"
        ctx_data = ctx.data or {}

        # Template the endpoint URL
        endpoint = _template_string(endpoint, ctx_data, inputs)

        # Build auth headers
        headers: dict[str, str] = {"Content-Type": "application/json"}
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("type", "none")
        if auth_type == "bearer":
            token = _template_string(auth_config.get("token", ""), ctx_data, inputs)
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key":
            key = _template_string(auth_config.get("key", ""), ctx_data, inputs)
            header_name = auth_config.get("header_name", "api-key")
            headers[header_name] = key
        elif auth_type == "header":
            header_name = auth_config.get("header_name", "Authorization")
            header_value = _template_string(auth_config.get("header_value", ""), ctx_data, inputs)
            headers[header_name] = header_value

        # Build messages with templating
        messages = config.get("messages", [])
        messages = _template_deep(messages, ctx_data, inputs)

        if not messages:
            raise StepError("LLM step requires at least one message", healable=False)

        # Build request body
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": config.get("temperature", 0.7),
            "max_tokens": config.get("max_tokens", 4096),
        }

        model = config.get("model")
        if model:
            body["model"] = model

        response_format = config.get("response_format", "text")
        if response_format == "json_object":
            body["response_format"] = {"type": "json_object"}

        # Extra body params (for advanced configs)
        extra_body = config.get("extra_body", {})
        body.update(_template_deep(extra_body, ctx_data, inputs))

        req_timeout = config.get("timeout", 120)
        extract_path = config.get("extract", "choices.0.message.content")

        yield LogEvent(
            level="info",
            message=f"LLM call: {model or 'default'} → {endpoint[:80]}...",
            step_id=step_id,
        )

        if ctx.dry_run:
            yield StepProgressEvent(step_id=step_id, progress=1.0, detail="dry_run: skipped LLM call")
            yield {"__result__": {
                "dry_run": True,
                "endpoint": endpoint,
                "model": model,
                "message_count": len(messages),
            }}
            return

        # Make the API call
        try:
            async with httpx.AsyncClient(timeout=req_timeout) as client:
                resp = await client.post(endpoint, headers=headers, json=body)
        except httpx.TimeoutException as e:
            raise StepError(
                f"LLM request timed out after {req_timeout}s",
                healable=True,
                detail=str(e),
            ) from e
        except Exception as e:
            raise StepError(f"LLM request failed: {e}", healable=True, detail=str(e)) from e

        if resp.status_code not in (200, 201):
            raise StepError(
                f"LLM API returned {resp.status_code}: {resp.text[:500]}",
                healable=True,
                detail=resp.text[:2000],
            )

        # Parse response
        try:
            resp_data = resp.json()
        except json.JSONDecodeError as e:
            raise StepError(
                f"LLM response is not valid JSON: {resp.text[:200]}",
                healable=True,
                detail=str(e),
            ) from e

        # Extract the target value
        extracted = _extract_path(resp_data, extract_path)
        if extracted is None:
            yield LogEvent(
                level="warn",
                message=f"Extract path {extract_path!r} returned None from response",
                step_id=step_id,
            )

        # Try to parse JSON from extracted content
        if isinstance(extracted, str) and response_format == "json_object":
            try:
                extracted = json.loads(extracted)
            except json.JSONDecodeError:
                pass  # Leave as string

        # Compute token usage if available
        usage = resp_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        yield StepProgressEvent(
            step_id=step_id,
            progress=1.0,
            detail=f"tokens: {prompt_tokens}+{completion_tokens}={prompt_tokens + completion_tokens}",
        )

        yield {"__result__": {
            "response": extracted,
            "model": resp_data.get("model", model),
            "usage": usage,
            "raw_response": resp_data,
        }}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["endpoint", "messages"],
            "properties": {
                "endpoint": {"type": "string", "description": "Chat completions API URL"},
                "auth": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["bearer", "api_key", "header", "none"]},
                        "token": {"type": "string"},
                        "key": {"type": "string"},
                        "header_name": {"type": "string"},
                        "header_value": {"type": "string"},
                    },
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                },
                "model": {"type": "string", "description": "Model name (if needed in request body)"},
                "temperature": {"type": "number", "default": 0.7},
                "max_tokens": {"type": "integer", "default": 4096},
                "response_format": {"type": "string", "enum": ["text", "json_object"], "default": "text"},
                "extract": {"type": "string", "default": "choices.0.message.content"},
                "timeout": {"type": "integer", "default": 120},
            },
        }

    def description(self) -> str:
        return (
            "LLM step — calls an OpenAI-compatible chat completions API with prompt templating, "
            "auth support, and response extraction. Works with Azure OpenAI, OpenAI, and compatible endpoints."
        )
