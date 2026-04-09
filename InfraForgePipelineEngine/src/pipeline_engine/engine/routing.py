"""Step / stage routing logic."""

from __future__ import annotations

from typing import Literal


RouteAction = Literal["next", "done", "abort"] | tuple[Literal["stage", "step"], str]


def parse_route(route_str: str) -> RouteAction:
    """Parse a routing string into a structured action.

    Supported formats:
        "next"        → "next"
        "done"        → "done"
        "abort"       → "abort"
        "stage:foo"   → ("stage", "foo")
        "step:bar"    → ("step", "bar")
    """
    route_str = route_str.strip().lower()
    if route_str in ("next", "done", "abort"):
        return route_str  # type: ignore[return-value]
    if ":" in route_str:
        kind, target = route_str.split(":", 1)
        if kind in ("stage", "step"):
            return (kind, target.strip())  # type: ignore[return-value]
    raise ValueError(f"Invalid route: {route_str!r}. Expected 'next', 'done', 'abort', 'stage:{{id}}', or 'step:{{id}}'")
