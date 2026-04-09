"""Shell step handler — runs a subprocess command."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any, AsyncGenerator

from pipeline_engine.models.context import PipelineContext
from pipeline_engine.models.events import LogEvent, PipelineEvent, StepProgressEvent
from pipeline_engine.steps.base import StepError, StepHandler


class ShellStepHandler(StepHandler):
    """Execute a shell command as a subprocess.

    Config:
        command:  The command string to execute
        cwd:      Working directory (optional)
        env:      Extra environment variables (optional, merged with current env)
        shell:    If true, run via shell interpreter (default: false)
        expected_exit_code: Expected exit code (default: 0)
    """

    async def execute(
        self,
        ctx: PipelineContext,
        config: dict[str, Any],
        inputs: dict[str, Any],
    ) -> AsyncGenerator[PipelineEvent | dict[str, Any], None]:
        command = config.get("command")
        if not command:
            raise StepError("Shell step requires 'command' in config", healable=False)

        step_id = ctx.current_step or "shell"
        cwd = config.get("cwd")
        env = config.get("env")
        use_shell = config.get("shell", False)
        expected_exit = config.get("expected_exit_code", 0)

        # Template substitution: replace {ctx.key} in command string
        for key, value in (ctx.data or {}).items():
            command = command.replace(f"{{ctx.{key}}}", str(value))
        for key, value in inputs.items():
            command = command.replace(f"{{inputs.{key}}}", str(value))

        yield LogEvent(
            level="info",
            message=f"Running: {command}",
            step_id=step_id,
        )

        if ctx.dry_run:
            yield StepProgressEvent(step_id=step_id, progress=1.0, detail="dry_run: skipped")
            yield {"__result__": {"dry_run": True, "command": command}}
            return

        try:
            if use_shell:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            else:
                args = shlex.split(command)
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )

            stdout_bytes, stderr_bytes = await proc.communicate()
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            exit_code = proc.returncode or 0
        except FileNotFoundError as e:
            raise StepError(
                f"Command not found: {command!r}",
                healable=False,
                detail=str(e),
            ) from e
        except Exception as e:
            raise StepError(
                f"Shell execution failed: {e}",
                healable=True,
                detail=str(e),
            ) from e

        if exit_code != expected_exit:
            raise StepError(
                f"Command exited with code {exit_code} (expected {expected_exit}): {stderr[:500]}",
                healable=True,
                detail=stderr[:2000],
            )

        yield StepProgressEvent(step_id=step_id, progress=1.0, detail=f"exit_code={exit_code}")
        yield {"__result__": {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}}

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
                "cwd": {"type": "string", "description": "Working directory"},
                "env": {"type": "object", "description": "Extra environment variables"},
                "shell": {"type": "boolean", "description": "Use shell interpreter", "default": False},
                "expected_exit_code": {"type": "integer", "description": "Expected exit code", "default": 0},
            },
        }

    def description(self) -> str:
        return "Execute a shell command as a subprocess with stdout/stderr capture."
