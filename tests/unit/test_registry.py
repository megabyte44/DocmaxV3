"""Discovery is metadata, and metadata is a contract.

Every consumer of the registry — the CLI, the TUI palette, the API server's
capabilities endpoint — reads the same ``ToolSpec``. These tests pin the parts
of it that those consumers depend on, and the laziness that makes discovery
free.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

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

    Checked in a **subprocess**, for the same reason
    ``tests/hygiene/test_no_heavy_imports.py`` does: pytest imports every test
    module during collection, so as soon as any test file imports ``MergeLocal``
    to exercise it, an in-process assertion here reports that module as loaded
    and fails — regardless of what the registry actually did. The property is
    real and worth pinning; only the measurement had to move.
    """
    probe = textwrap.dedent(
        """
        import sys
        from docmax.core.registry import build_registry

        build_registry()

        # Discovery legitimately imports each tool package and its `tool.py` —
        # that is where the metadata lives. What must stay out is the code that
        # does the work, and the validators that pull in the same heavy deps.
        implementation = {"local", "cloud", "validators"}
        leaked = sorted(
            name
            for name in sys.modules
            if name.startswith("docmax.tools.") and name.rsplit(".", 1)[-1] in implementation
        )
        if leaked:
            print(",".join(leaked))
            sys.exit(1)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (
        "Building the registry imported tool implementations: "
        f"{result.stdout.strip()}\n"
        "Discovery must read metadata only — see docs/adr/0002-registry-mechanism.md.\n"
        f"{result.stderr}"
    )


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
        assert spec.name
        assert spec.summary
        assert spec.category
        assert spec.supported_engines, f"{spec.name} supports no engine at all"
        assert spec.module.endswith(spec.name.replace("-", "_"))
        for param in spec.params:
            assert param.description, f"{spec.name}.{param.name} has no description"
