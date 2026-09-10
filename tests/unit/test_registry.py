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


def test_output_required_is_set_on_exactly_the_tools_that_cannot_derive_one() -> None:
    """ADR 0033: no destination may ever be implied for `convert` or `metadata`.

    `convert`'s real extension depends on `--to`, and `default_suffix` is
    Pandoc's one format it can never write (ADR 0011). `metadata` only ever
    writes a new document when asked, and promises never to edit one in
    place, so implying a destination for it is exactly as wrong.

    Pinned as an explicit set — a nineteenth tool that needs `True` here and
    quietly keeps the `False` default would otherwise pass silently, which is
    the failure mode this test exists to catch.
    """
    expected = {"convert", "metadata"}
    actual = {spec.name for spec in iter_tools() if spec.output_required}
    assert actual == expected


def test_output_required_defaults_to_false() -> None:
    """The common case: most tools' `default_suffix` is a trustworthy guess."""
    from docmax.core.registry import ToolSpec

    assert ToolSpec.__dataclass_fields__["output_required"].default is False


def test_binary_catalogue_matches_the_registry() -> None:
    """`Binary.used_by` and `ToolSpec.requires_binaries` record one fact twice.

    `used_by` may name a tool that is not registered yet — `_binaries.py` says
    so explicitly, since `doctor` has reported on roadmap binaries since M0.
    But a tool that *is* registered must not disagree with what its own
    binary catalogue entry says it needs, in either direction: `setup` reads
    `requires_binaries` generically, and a tool claiming a binary the
    catalogue does not know about, or missing one the catalogue says it
    needs, would make `setup` either install nothing or crash on an unknown
    name.
    """
    from docmax.tools import _binaries

    registry = build_registry()
    known_binaries = {binary.name for binary in _binaries.EXTERNAL_BINARIES}

    for binary in _binaries.EXTERNAL_BINARIES:
        for tool_name in binary.used_by:
            spec = registry.get(tool_name)
            if spec is None:
                continue  # not registered yet — a roadmap entry, not a bug
            assert binary.name in spec.requires_binaries, (
                f"_binaries.EXTERNAL_BINARIES says {tool_name!r} needs "
                f"{binary.name!r}, but its ToolSpec.requires_binaries does not"
            )

    for spec in registry.values():
        unknown = set(spec.requires_binaries) - known_binaries
        assert not unknown, f"{spec.name}.requires_binaries names unknown binaries: {unknown}"


def test_pip_extra_catalogue_matches_the_registry() -> None:
    """Every `ToolSpec.pip_extra` names a real entry in `_install.PIP_EXTRAS`.

    The same discipline as `test_binary_catalogue_matches_the_registry`, for
    the same reason: `setup` and the TUI's install buttons both read
    `_install.describe_extra` generically, and a tool naming an extra the
    catalogue doesn't know would make either one crash on an unknown name
    instead of installing it.
    """
    from docmax.tools import _install

    known_extras = {extra.name for extra in _install.PIP_EXTRAS}

    for spec in build_registry().values():
        if spec.pip_extra is not None:
            assert spec.pip_extra in known_extras, (
                f"{spec.name}.pip_extra={spec.pip_extra!r} has no entry in _install.PIP_EXTRAS"
            )
