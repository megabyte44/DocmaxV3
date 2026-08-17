"""Discovery is metadata, and metadata is a contract.

Every consumer of the registry — the CLI, the TUI palette, the API server's
capabilities endpoint — reads the same ``ToolSpec``. These tests pin the parts
of it that those consumers depend on, and the laziness that makes discovery
free.
"""

from __future__ import annotations

import sys

import pytest

from docmax.core.errors import EngineNotSupportedError, InvalidParameterError
from docmax.core.models import Engine
from docmax.core.registry import build_registry, get_tool, iter_tools


def test_builtin_tools_are_discovered() -> None:
    """No central file lists these. They are found by walking the package."""
    registry = build_registry()

    assert "merge" in registry
    assert "ocr" in registry


def test_discovery_does_not_import_implementations() -> None:
    """Listing tools must not cost what running them costs.

    The strategy modules stay out of ``sys.modules`` until something asks for
    an engine — which is the whole reason ``ToolSpec`` carries a dotted path
    instead of a callable.
    """
    build_registry()

    assert "docmax.tools.merge.local" not in sys.modules
    assert "docmax.tools.ocr.local" not in sys.modules


def test_specs_declare_their_engines() -> None:
    """A pypdf-only tool has no cloud engine, deliberately."""
    assert get_tool("merge").supported_engines == {Engine.LOCAL}
    assert get_tool("ocr").supports(Engine.CLOUD)


def test_cloud_filter_is_what_capabilities_answers_with() -> None:
    """The server derives its tool list from here rather than keeping its own."""
    cloud_tools = {spec.name for spec in iter_tools(engine=Engine.CLOUD)}

    assert "ocr" in cloud_tools
    assert "merge" not in cloud_tools


def test_unknown_tool_is_a_typed_error() -> None:
    with pytest.raises(InvalidParameterError) as caught:
        get_tool("definitely-not-a-tool")

    assert caught.value.remedy, "the error must say how to see the real list"


def test_asking_for_an_unsupported_engine_is_refused() -> None:
    """And refused *before* anything is imported or uploaded."""
    with pytest.raises(EngineNotSupportedError):
        get_tool("merge").load_strategy(Engine.CLOUD)


def test_every_spec_is_renderable() -> None:
    """Whatever a UI needs to offer a tool, every spec must actually carry."""
    for spec in iter_tools():
        assert spec.name and spec.summary and spec.category
        assert spec.supported_engines, f"{spec.name} supports no engine at all"
        assert spec.module.endswith(spec.name.replace("-", "_"))
        for param in spec.params:
            assert param.description, f"{spec.name}.{param.name} has no description"
