#!/usr/bin/env python3
"""Pipeline Engine Test Client

Reads YAML pipeline definitions, converts to JSON, POSTs to the engine,
and streams NDJSON events until completion. Reports pass/fail per pipeline.

Usage:
    # Run all pipelines in scripts/pipelines/
    python scripts/run_pipelines.py

    # Run a specific pipeline
    python scripts/run_pipelines.py scripts/pipelines/01_hello_world.yaml

    # Run against a custom engine URL
    python scripts/run_pipelines.py --url http://localhost:9000

    # Verbose mode (show all events)
    python scripts/run_pipelines.py -v

    # Just convert YAML to JSON (no execution)
    python scripts/run_pipelines.py --dry-convert scripts/pipelines/01_hello_world.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

# ── ANSI colors ──────────────────────────────────────────────────

try:
    import colorama
    colorama.init()
except ImportError:
    pass

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

STATUS_COLORS = {
    "success": GREEN,
    "failed": RED,
    "cancelled": YELLOW,
    "skipped": DIM,
    "running": BLUE,
}


def colorize(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


# ── YAML → JSON conversion ──────────────────────────────────────

def load_yaml_pipeline(path: Path) -> dict[str, Any]:
    """Load a YAML pipeline file and return as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: Expected a YAML mapping at top level, got {type(data).__name__}")
    if "name" not in data or "stages" not in data:
        raise ValueError(f"{path}: Missing required keys 'name' and/or 'stages'")
    return data


def yaml_to_json(data: dict[str, Any]) -> str:
    """Convert pipeline dict to JSON string."""
    return json.dumps(data, indent=2, default=str)


# ── Event rendering ──────────────────────────────────────────────

EVENT_ICONS = {
    "pipeline_start": "🚀",
    "pipeline_done": "🏁",
    "pipeline_paused": "⏸️",
    "pipeline_resumed": "▶️",
    "stage_start": "📦",
    "stage_done": "📦",
    "step_start": "  ▶",
    "step_progress": "  ⋯",
    "step_done": "  ✓",
    "step_skipped": "  ⊘",
    "step_waiting": "  🔔",
    "healing_start": "  🔧",
    "healing_done": "  🔧",
    "log": "  📝",
}


def format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60_000:
        return f"{ms / 1000:.1f}s"
    else:
        return f"{ms / 60_000:.1f}m"


def render_event(event: dict[str, Any], verbose: bool = False) -> str | None:
    """Render an NDJSON event as a human-readable line. Returns None to skip."""
    etype = event.get("type", "unknown")
    icon = EVENT_ICONS.get(etype, "  ?")
    status = event.get("status", "")
    color = STATUS_COLORS.get(status, "")

    if etype == "pipeline_start":
        name = event.get("name", "?")
        stages = event.get("total_stages", "?")
        run_id = event.get("run_id", "?")[:8]
        return f"{icon} {colorize(f'Pipeline: {name}', BOLD)}  ({stages} stages, run={run_id})"

    elif etype == "pipeline_done":
        status_text = colorize(status.upper(), color)
        duration = format_duration(event.get("duration_ms", 0))
        error = event.get("error")
        line = f"{icon} Pipeline {status_text}  [{duration}]"
        if error:
            line += f"  {colorize(error, RED)}"
        return line

    elif etype == "stage_start":
        name = event.get("stage_name", event.get("stage_id", "?"))
        count = event.get("step_count", "?")
        return f"\n{icon} {colorize(name, CYAN)}  ({count} steps)"

    elif etype == "stage_done":
        sid = event.get("stage_id", "?")
        status_text = colorize(status, color)
        duration = format_duration(event.get("duration_ms", 0))
        return f"{icon} Stage {sid}: {status_text}  [{duration}]"

    elif etype == "step_start":
        name = event.get("step_name", event.get("step_id", "?"))
        stype = event.get("step_type", "?")
        return f"{icon} {name} ({colorize(stype, DIM)})"

    elif etype == "step_progress":
        if verbose:
            detail = event.get("detail", "")
            progress = event.get("progress", 0)
            return f"{icon} {progress:.0%} {detail}"
        return None

    elif etype == "step_done":
        sid = event.get("step_id", "?")
        status_text = colorize(status, color)
        duration = format_duration(event.get("duration_ms", 0))
        error = event.get("error")
        line = f"  ✓ {sid}: {status_text}  [{duration}]"
        if error:
            line += f"  — {colorize(error, RED)}"
        return line

    elif etype == "step_skipped":
        sid = event.get("step_id", "?")
        reason = event.get("reason", "")
        return f"{icon} {sid}: {colorize('skipped', DIM)}  ({reason})"

    elif etype == "healing_start":
        sid = event.get("step_id", "?")
        attempt = event.get("attempt", "?")
        max_att = event.get("max_attempts", "?")
        return f"{icon} Healing {sid} (attempt {attempt}/{max_att})"

    elif etype == "healing_done":
        strategy = event.get("strategy", "")
        ok = event.get("success", False)
        color_h = GREEN if ok else YELLOW
        return f"{icon} Healed: {colorize(strategy or 'done', color_h)}"

    elif etype == "log":
        level = event.get("level", "info")
        msg = event.get("message", "")
        level_color = {"error": RED, "warn": YELLOW, "info": DIM, "debug": DIM}.get(level, "")
        if verbose or level in ("error", "warn"):
            return f"{icon} [{colorize(level, level_color)}] {msg}"
        return None

    elif etype == "step_waiting":
        sid = event.get("step_id", "?")
        gate_type = event.get("gate_type", "manual")
        assignee = event.get("assignee", "unassigned")
        instructions = event.get("instructions", "")
        required = event.get("required_inputs", [])
        fields = ", ".join(f["name"] for f in required) if required else "none"
        line = f"{icon} {colorize(f'WAITING: {gate_type}', YELLOW)} — assignee: {colorize(assignee, CYAN)}"
        line += f"\n       Required inputs: {fields}"
        if instructions:
            # Show first line of instructions
            first_line = instructions.strip().split("\n")[0][:80]
            line += f"\n       {colorize(first_line, DIM)}"
        return line

    elif etype == "pipeline_paused":
        step_id = event.get("waiting_step_id", "?")
        gate_type = event.get("gate_type", "manual")
        assignee = event.get("assignee", "")
        return (
            f"\n{icon} {colorize('PIPELINE PAUSED', YELLOW + BOLD)} — waiting on "
            f"{colorize(step_id, CYAN)} ({gate_type})"
            f"\n   Resume via: POST /api/pipelines/{{run_id}}/steps/{step_id}/complete"
        )

    elif etype == "pipeline_resumed":
        step_id = event.get("resumed_step_id", "?")
        return f"\n{icon} {colorize('PIPELINE RESUMED', GREEN + BOLD)} — step {colorize(step_id, CYAN)} completed"

    else:
        if verbose:
            return f"  ? {etype}: {json.dumps(event, default=str)[:120]}"
        return None


# ── Pipeline execution ───────────────────────────────────────────

class PipelineResult:
    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.status: str = "unknown"
        self.duration_ms: int = 0
        self.run_id: str = ""
        self.error: str | None = None
        self.events: list[dict] = []
        self.stages_passed: int = 0
        self.stages_failed: int = 0
        self.stages_skipped: int = 0
        self.steps_passed: int = 0
        self.steps_failed: int = 0
        self.steps_skipped: int = 0
        self.paused_at_step: str | None = None
        self.waiting_config: dict | None = None


def _stream_events(
    client: httpx.Client,
    response: httpx.Response,
    result: PipelineResult,
    verbose: bool,
) -> None:
    """Read NDJSON lines from a streaming response, updating result."""
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if verbose:
                    print(colorize(f"  ? Bad JSON: {line[:100]}", YELLOW))
                continue

            result.events.append(event)
            _track_event(result, event)

            rendered = render_event(event, verbose=verbose)
            if rendered is not None:
                print(rendered)


def _generate_gate_response(waiting_config: dict) -> dict[str, Any]:
    """Auto-generate approval responses for gate steps in test mode."""
    outputs: dict[str, Any] = {}
    for field in waiting_config.get("required_inputs", []):
        name = field.get("name", "")
        ftype = field.get("type", "text")
        options = field.get("options", [])

        if ftype == "boolean":
            outputs[name] = True
        elif ftype == "enum" and options:
            # Pick the first "positive" option, or just the first one
            positive = [o for o in options if o.lower() in ("approved", "yes", "true", "accept")]
            outputs[name] = positive[0] if positive else options[0]
        elif ftype == "number":
            outputs[name] = 0
        elif ftype == "json":
            outputs[name] = {}
        else:
            outputs[name] = f"Auto-approved by test client"
    return outputs


def run_pipeline(
    base_url: str,
    pipeline_data: dict[str, Any],
    path: Path,
    verbose: bool = False,
    timeout: int = 120,
    auto_approve: bool = True,
    interactive: bool = False,
) -> PipelineResult:
    """POST a pipeline to the engine and stream results until completion.

    If the pipeline pauses at a gate step:
      - ``auto_approve=True``: automatically generates approval responses
      - ``interactive=True``: prompts the user for input
      - Otherwise: returns with status=paused
    """
    result = PipelineResult(pipeline_data.get("name", path.stem), path)

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10)) as client:
            # ── Initial run ──────────────────────────────────────
            with client.stream(
                "POST",
                f"{base_url}/api/pipelines/run",
                json=pipeline_data,
                headers={"Accept": "application/x-ndjson"},
            ) as response:
                if response.status_code != 200:
                    response.read()
                    result.status = "failed"
                    result.error = f"HTTP {response.status_code}: {response.text[:500]}"
                    print(colorize(f"  ✗ HTTP {response.status_code}: {response.text[:200]}", RED))
                    return result

                _stream_events(client, response, result, verbose)

            # ── Handle pause/resume loop ─────────────────────────
            max_resumes = 20  # Safety limit
            resumes = 0
            while result.status == "paused" and resumes < max_resumes:
                resumes += 1
                step_id = result.paused_at_step
                run_id = result.run_id

                if not step_id or not run_id:
                    result.error = "Pipeline paused but no step_id or run_id"
                    break

                if interactive:
                    gate_outputs = _prompt_gate_inputs(result.waiting_config or {})
                elif auto_approve:
                    gate_outputs = _generate_gate_response(result.waiting_config or {})
                    print(colorize(
                        f"\n  ⚡ Auto-approving gate {step_id}: {json.dumps(gate_outputs, default=str)}",
                        MAGENTA,
                    ))
                else:
                    print(colorize(
                        f"\n  ⏸️  Pipeline paused at {step_id}. Use --interactive or --auto-approve to continue.",
                        YELLOW,
                    ))
                    break

                # Resume the pipeline
                result.status = "running"
                result.paused_at_step = None

                with client.stream(
                    "POST",
                    f"{base_url}/api/pipelines/{run_id}/steps/{step_id}/complete",
                    json={"outputs": gate_outputs},
                    headers={"Accept": "application/x-ndjson"},
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        result.status = "failed"
                        result.error = f"Resume HTTP {response.status_code}: {response.text[:500]}"
                        print(colorize(f"  ✗ Resume failed: {response.text[:200]}", RED))
                        break

                    _stream_events(client, response, result, verbose)

    except httpx.ConnectError:
        result.status = "failed"
        result.error = f"Cannot connect to {base_url}"
        print(colorize(f"  ✗ Cannot connect to {base_url}", RED))
    except httpx.ReadTimeout:
        result.status = "failed"
        result.error = "Read timeout waiting for pipeline completion"
        print(colorize("  ✗ Read timeout", RED))
    except Exception as e:
        result.status = "failed"
        result.error = str(e)
        print(colorize(f"  ✗ {e}", RED))

    return result


def _prompt_gate_inputs(waiting_config: dict) -> dict[str, Any]:
    """Interactively prompt the user for gate inputs."""
    outputs: dict[str, Any] = {}
    print(colorize("\n  === Input Required ===", BOLD))
    for field in waiting_config.get("required_inputs", []):
        name = field.get("name", "")
        ftype = field.get("type", "text")
        options = field.get("options", [])
        desc = field.get("description", "")

        prompt_text = f"  {name}"
        if desc:
            prompt_text += f" ({desc})"
        if ftype == "enum" and options:
            prompt_text += f" [{'/'.join(options)}]"
        elif ftype == "boolean":
            prompt_text += " [true/false]"
        prompt_text += ": "

        value = input(prompt_text).strip()
        if ftype == "boolean":
            outputs[name] = value.lower() in ("true", "yes", "1", "y")
        elif ftype == "number":
            try:
                outputs[name] = float(value)
            except ValueError:
                outputs[name] = 0
        elif ftype == "json":
            try:
                outputs[name] = json.loads(value)
            except json.JSONDecodeError:
                outputs[name] = value
        else:
            outputs[name] = value
    return outputs


def _track_event(result: PipelineResult, event: dict) -> None:
    """Update result counters from an event."""
    etype = event.get("type")
    status = event.get("status")

    if etype == "pipeline_start":
        result.run_id = event.get("run_id", "")
    elif etype == "pipeline_resumed":
        result.run_id = event.get("run_id", result.run_id)
    elif etype == "pipeline_done":
        result.status = status or "unknown"
        result.duration_ms = event.get("duration_ms", 0)
        result.error = event.get("error")
    elif etype == "pipeline_paused":
        result.status = "paused"
        result.paused_at_step = event.get("waiting_step_id")
    elif etype == "step_waiting":
        result.waiting_config = {
            "step_id": event.get("step_id"),
            "gate_type": event.get("gate_type"),
            "required_inputs": event.get("required_inputs", []),
        }
    elif etype == "stage_done":
        if status == "success":
            result.stages_passed += 1
        elif status == "failed":
            result.stages_failed += 1
        elif status == "skipped":
            result.stages_skipped += 1
    elif etype == "step_done":
        if status == "success":
            result.steps_passed += 1
        elif status == "failed":
            result.steps_failed += 1
    elif etype == "step_skipped":
        result.steps_skipped += 1


# ── Summary ──────────────────────────────────────────────────────

def print_summary(results: list[PipelineResult]) -> bool:
    """Print a summary table. Returns True if all passed."""
    passed = sum(1 for r in results if r.status == "success")
    failed = sum(1 for r in results if r.status != "success")

    print("\n" + "═" * 70)
    print(colorize("  PIPELINE TEST SUMMARY", BOLD))
    print("═" * 70)

    name_width = max(len(r.name) for r in results) + 2 if results else 20

    for r in results:
        color = STATUS_COLORS.get(r.status, "")
        status_text = colorize(f"{r.status.upper():>10}", color)
        duration = format_duration(r.duration_ms)

        steps_info = f"steps: {r.steps_passed}✓ {r.steps_failed}✗ {r.steps_skipped}⊘"
        stages_info = f"stages: {r.stages_passed}✓ {r.stages_failed}✗ {r.stages_skipped}⊘"

        print(f"  {r.name:<{name_width}} {status_text}  [{duration:>6}]  {stages_info}  {steps_info}")
        if r.error:
            print(f"  {'':>{name_width}} {colorize(f'↳ {r.error}', RED)}")

    print("─" * 70)
    total_color = GREEN if failed == 0 else RED
    print(f"  {colorize(f'{passed} passed', GREEN)}, {colorize(f'{failed} failed', RED if failed else DIM)}"
          f"  out of {len(results)} pipelines")
    print("═" * 70 + "\n")

    return failed == 0


# ── Health check ─────────────────────────────────────────────────

def check_health(base_url: str) -> bool:
    """Check if the engine is running."""
    try:
        resp = httpx.get(f"{base_url}/api/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            version = data.get("version", "?")
            steps = data.get("registered_steps", [])
            print(colorize(f"  Engine: v{version}  |  Steps: {', '.join(steps)}", DIM))
            return True
    except httpx.ConnectError:
        pass
    return False


def check_catalog(base_url: str) -> None:
    """Print the step catalog."""
    try:
        resp = httpx.get(f"{base_url}/api/catalog/steps", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for step in data.get("steps", []):
                print(f"    {colorize(step['type'], CYAN):20} {step.get('description', '')[:60]}")
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────

def discover_pipelines(directory: Path) -> list[Path]:
    """Find all .yaml/.yml files in a directory, sorted by name."""
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline Engine Test Client — run YAML pipelines against the engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pipelines",
        nargs="*",
        help="YAML pipeline files to run (default: all in scripts/pipelines/)",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8100",
        help="Pipeline engine base URL (default: http://127.0.0.1:8100)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show all events including progress and debug logs",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-pipeline HTTP timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--dry-convert",
        action="store_true",
        help="Only convert YAML to JSON and print — don't execute",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Print the step catalog and exit",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=True,
        help="Auto-approve gate steps with generated responses (default: true)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for gate step inputs interactively (overrides --auto-approve)",
    )
    parser.add_argument(
        "--no-auto-approve",
        action="store_true",
        help="Don't auto-approve gates — stop at first gate",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    # ── Catalog mode ─────────────────────────────────────────
    if args.catalog:
        print(colorize("\n  Step Catalog", BOLD))
        print("─" * 50)
        check_catalog(base_url)
        print()
        return

    # ── Discover pipeline files ──────────────────────────────
    if args.pipelines:
        pipeline_paths = [Path(p) for p in args.pipelines]
    else:
        scripts_dir = Path(__file__).parent / "pipelines"
        if not scripts_dir.exists():
            print(colorize(f"Pipeline directory not found: {scripts_dir}", RED))
            sys.exit(1)
        pipeline_paths = discover_pipelines(scripts_dir)

    if not pipeline_paths:
        print(colorize("No pipeline files found.", YELLOW))
        sys.exit(1)

    # ── Load all YAMLs ───────────────────────────────────────
    pipelines: list[tuple[Path, dict]] = []
    for path in pipeline_paths:
        if not path.exists():
            print(colorize(f"File not found: {path}", RED))
            sys.exit(1)
        try:
            data = load_yaml_pipeline(path)
            pipelines.append((path, data))
        except Exception as e:
            print(colorize(f"Failed to parse {path}: {e}", RED))
            sys.exit(1)

    # ── Dry convert mode ─────────────────────────────────────
    if args.dry_convert:
        for path, data in pipelines:
            print(colorize(f"\n── {path.name} ──", BOLD))
            print(yaml_to_json(data))
        return

    # ── Pre-flight: health check ─────────────────────────────
    print(colorize("\n  Pipeline Engine Test Client", BOLD))
    print("─" * 50)
    print(f"  Target: {base_url}")
    print(f"  Pipelines: {len(pipelines)}")

    if not check_health(base_url):
        print(colorize(f"\n  ✗ Engine not reachable at {base_url}", RED))
        print(f"  Start it with: uvicorn pipeline_engine.app:create_app --factory --port 8100")
        sys.exit(1)

    print()

    # ── Execute each pipeline ────────────────────────────────
    results: list[PipelineResult] = []
    for i, (path, data) in enumerate(pipelines, 1):
        header = f"[{i}/{len(pipelines)}] {path.name}"
        print(colorize(f"\n{'━' * 60}", DIM))
        print(colorize(f"  {header}", BOLD))
        print(colorize(f"{'━' * 60}", DIM))

        result = run_pipeline(
            base_url=base_url,
            pipeline_data=data,
            path=path,
            verbose=args.verbose,
            timeout=args.timeout,
            auto_approve=not args.no_auto_approve,
            interactive=args.interactive,
        )
        results.append(result)

    # ── Summary ──────────────────────────────────────────────
    all_passed = print_summary(results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
