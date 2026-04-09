"""Step registry — discovers and manages step handler plugins.

Plugins register via the ``pipeline_engine.steps`` entry-point group
in their ``pyproject.toml``::

    [project.entry-points."pipeline_engine.steps"]
    my_step = "my_package.steps:MyStepHandler"

At startup, ``StepRegistry.discover()`` loads all entry points and
instantiates the handlers.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Any

from pipeline_engine.steps.base import StepHandler

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pipeline_engine.steps"


class StepRegistry:
    """Registry mapping step type names to handler instances."""

    def __init__(self) -> None:
        self._handlers: dict[str, StepHandler] = {}

    def register(self, type_name: str, handler: StepHandler) -> None:
        if type_name in self._handlers:
            logger.warning("Overwriting step type %r with %r", type_name, handler)
        self._handlers[type_name] = handler
        logger.info("Registered step type: %s -> %s", type_name, handler.__class__.__name__)

    def discover(self) -> None:
        """Load step handlers from installed entry points."""
        eps = importlib.metadata.entry_points()
        group = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") else eps.get(ENTRY_POINT_GROUP, [])
        for ep in group:
            try:
                handler_cls = ep.load()
                if not (isinstance(handler_cls, type) and issubclass(handler_cls, StepHandler)):
                    logger.warning("Entry point %r is not a StepHandler subclass, skipping", ep.name)
                    continue
                self.register(ep.name, handler_cls())
            except Exception:
                logger.exception("Failed to load step entry point %r", ep.name)

    def get(self, type_name: str) -> StepHandler | None:
        return self._handlers.get(type_name)

    def resolve(self, type_name: str) -> StepHandler:
        """Get a handler or raise ``KeyError`` with a helpful message."""
        handler = self._handlers.get(type_name)
        if handler is None:
            available = ", ".join(sorted(self._handlers)) or "(none)"
            raise KeyError(
                f"Unknown step type {type_name!r}. Available: {available}"
            )
        return handler

    def list_types(self) -> list[str]:
        return sorted(self._handlers)

    def catalog(self) -> list[dict[str, Any]]:
        """Return catalog entries for all registered step types."""
        return [
            {
                "type": name,
                "description": handler.description(),
                "config_schema": handler.config_schema(),
            }
            for name, handler in sorted(self._handlers.items())
        ]
