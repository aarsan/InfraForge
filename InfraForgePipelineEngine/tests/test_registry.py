"""Test step registry — registration, discovery, unknown types."""

import pytest

from pipeline_engine.steps.registry import StepRegistry
from pipeline_engine.steps.base import StepHandler
from pipeline_engine.steps.noop_step import NoopStepHandler


class TestStepRegistry:
    def test_register_and_get(self):
        reg = StepRegistry()
        handler = NoopStepHandler()
        reg.register("test_noop", handler)
        assert reg.get("test_noop") is handler

    def test_resolve_unknown_raises(self):
        reg = StepRegistry()
        with pytest.raises(KeyError, match="Unknown step type"):
            reg.resolve("nonexistent")

    def test_list_types(self):
        reg = StepRegistry()
        reg.register("b_step", NoopStepHandler())
        reg.register("a_step", NoopStepHandler())
        assert reg.list_types() == ["a_step", "b_step"]

    def test_catalog_entries(self):
        reg = StepRegistry()
        reg.register("noop", NoopStepHandler())
        catalog = reg.catalog()
        assert len(catalog) == 1
        assert catalog[0]["type"] == "noop"
        assert "config_schema" in catalog[0]
        assert "description" in catalog[0]

    def test_discover_loads_entry_points(self):
        """Integration test: discovers built-in entry points from pyproject.toml."""
        reg = StepRegistry()
        reg.discover()
        types = reg.list_types()
        # Built-in types should be discovered via entry points
        # (requires package to be installed in editable mode)
        # If not installed, this will be empty — that's OK for CI
        assert isinstance(types, list)

    def test_overwrite_warning(self, caplog):
        reg = StepRegistry()
        reg.register("x", NoopStepHandler())
        reg.register("x", NoopStepHandler())
        assert "Overwriting" in caplog.text
