"""Tool discovery: metadata now, implementations later.

Two requirements pull against each other here, and ADR 0002 explains why both
matter: adding tool #51 must not touch a central file, and *listing* tools must
not import their implementations. v2 failed both — a hardcoded dispatch chain
that failed silently when an entry drifted, and eager imports that pulled Pillow
into ``--help``.

So a tool contributes a :class:`ToolSpec`: name, summary, parameters, which
engines it supports. That is everything the CLI, the TUI palette, and the API
server need in order to *offer* it. The spec holds the dotted path to the tool
package, not its modules; ``tools/<name>/local.py`` is imported only when the
router has resolved that engine for that call.

Discovery has two sources, and both end up in the same registry:

* ``tools/*/tool.py`` in this package, found by walking the directory.
* ``importlib.metadata`` entry points in the plugin group, so a third party can
  publish a tool on PyPI and have it appear without a fork.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from docmax.core.branding import PLUGIN_GROUP
from docmax.core.errors import EngineNotSupportedError, InternalError, InvalidParameterError
from docmax.core.models import Engine

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from docmax.core.protocols import EngineStrategy

#: Derived rather than written out, because brand literals live in one module
#: only (``core/branding.py``) and ``tests/hygiene/test_branding.py`` enforces it.
_ROOT_PACKAGE = __name__.partition(".")[0]
TOOLS_PACKAGE = f"{_ROOT_PACKAGE}.tools"

#: The module each tool package must expose, and the attribute a strategy module
#: must provide. Convention over configuration: there is no manifest to keep in
#: sync with the filesystem.
SPEC_MODULE = "tool"
STRATEGY_FACTORY = "build"


@dataclass(frozen=True, slots=True)
class Param:
    """One parameter of one tool, described once and rendered many ways.

    The CLI turns these into options, the TUI into form fields, the API server
    into request validation, and the M10 MCP server into a JSON schema. A tool
    that described its parameters only in its ``typer`` signature could not do
    any of that.
    """

    name: str
    description: str
    #: ``str`` | ``int`` | ``float`` | ``bool`` | ``path`` — deliberately a small
    #: closed set, since every consumer has to be able to render it.
    type_: str = "str"
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    #: Labels a comma-separated composite value's parts, e.g. ``("x", "y",
    #: "width", "height")`` for ``crop``'s ``box``. Purely presentational: the
    #: value is still one ``str`` on the wire, parsed exactly as it always was,
    #: so a CLI flag, an MCP schema and API validation are unaffected — only
    #: the TUI reads it, rendering one labelled field per label and rejoining
    #: them with commas rather than asking someone to type a tuple blind. Empty
    #: (the default) renders as the single text field every other param gets.
    #: See ADR 0032.
    component_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Everything about a tool except the code that performs it."""

    name: str
    summary: str
    category: str
    #: Dotted path of the tool *package*, e.g. ``...tools.merge``. Its ``local``
    #: and ``cloud`` submodules are imported on demand and never before.
    module: str
    supported_engines: frozenset[Engine]
    params: tuple[Param, ...] = ()
    #: ``merge`` and ``from-images`` take several inputs; most tools take one.
    accepts_multiple_inputs: bool = False
    #: Used by ``OutputTarget.resolve`` when the user does not pass ``-o``.
    default_suffix: str = ".pdf"
    #: ``split`` and ``to-images`` write many files into ``-o`` as a directory
    #: — see ADR 0003's ``atomic_dir`` and ADR 0031. Every other tool writes
    #: one file, and ``OutputTarget.resolve`` reads this to decide whether an
    #: extensionless destination is a typo to correct (a file) or exactly what
    #: was meant (a directory almost never has a dot in its name).
    produces_directory: bool = False
    #: Free-form, for ``--help`` grouping and search.
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def supports(self, engine: Engine) -> bool:
        return engine in self.supported_engines

    def load_strategy(self, engine: Engine) -> EngineStrategy:
        """Import the strategy module for ``engine`` and build its strategy.

        This is the *only* place a tool implementation is imported, which is
        what keeps the promise that discovering fifty tools costs fifty cheap
        metadata objects and not fifty OpenCV imports.
        """
        if not self.supports(engine):
            raise EngineNotSupportedError(
                f"The {self.name!r} tool has no {engine.value} engine.",
                remedy=f"Available: {', '.join(sorted(e.value for e in self.supported_engines))}.",
                context={"tool": self.name, "engine": engine.value},
            )

        module_name = f"{self.module}.{engine.value}"
        try:
            module = importlib.import_module(module_name)
            factory = getattr(module, STRATEGY_FACTORY)
        except (ImportError, AttributeError) as exc:
            # Not a missing *dependency* — those are reported by the strategy's
            # own is_available(). This means the tool package is malformed.
            raise InternalError(
                f"The {self.name!r} tool declares a {engine.value} engine but "
                f"{module_name}.{STRATEGY_FACTORY}() could not be loaded: {exc}",
                context={"tool": self.name, "module": module_name},
            ) from exc

        strategy: EngineStrategy = factory()
        return strategy


_REGISTRY: dict[str, ToolSpec] = {}
_PLUGIN_ERRORS: dict[str, str] = {}
_LOADED = False


def register(spec: ToolSpec) -> ToolSpec:
    """Add ``spec`` to the registry and hand it straight back.

    Called at the top level of each ``tool.py``, so the module both declares its
    spec and exports it::

        SPEC = register(ToolSpec(name="merge", ...))

    Returning the argument is what makes that one statement instead of two, and
    keeps the spec importable by name for tests.
    """
    if spec.name in _REGISTRY and _REGISTRY[spec.name].module != spec.module:
        raise InternalError(
            f"Two packages both register a tool named {spec.name!r}: "
            f"{_REGISTRY[spec.name].module} and {spec.module}.",
            context={"tool": spec.name},
        )
    _REGISTRY[spec.name] = spec
    return spec


def _builtin_spec_modules() -> Iterator[str]:
    """Dotted paths of every ``tools/*/tool.py``, found without importing them.

    ``find_spec`` locates the ``tools`` package without executing any tool code,
    so this walk stays as cheap as a directory listing.
    """
    package = importlib.util.find_spec(TOOLS_PACKAGE)
    if package is None or package.submodule_search_locations is None:
        return

    for location in package.submodule_search_locations:
        directory = Path(location)
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if (child / f"{SPEC_MODULE}.py").is_file():
                yield f"{TOOLS_PACKAGE}.{child.name}.{SPEC_MODULE}"


def _load_builtin_specs() -> None:
    for module_name in _builtin_spec_modules():
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            # A broken tool in *this* package is our bug, and a loud one.
            raise InternalError(
                f"Tool module {module_name} failed to load: {exc}",
                context={"module": module_name},
            ) from exc


def _load_plugin_specs() -> None:
    """Load third-party tools, recording failures instead of raising.

    A published plugin can break against a new release at any time. That must
    degrade to "one tool is missing, and here is why" — not to an application
    that will not start. Read the failures with :func:`plugin_errors`.
    """
    for entry_point in entry_points(group=PLUGIN_GROUP):
        try:
            entry_point.load()
        except Exception as exc:
            _PLUGIN_ERRORS[entry_point.name] = str(exc)


def build_registry(*, refresh: bool = False) -> Mapping[str, ToolSpec]:
    """Return every known tool, keyed by name. Idempotent and cheap after the first call."""
    global _LOADED
    if refresh:
        _REGISTRY.clear()
        _PLUGIN_ERRORS.clear()
        _LOADED = False
    if not _LOADED:
        _load_builtin_specs()
        _load_plugin_specs()
        _LOADED = True
    return MappingProxyType(_REGISTRY)


def get_tool(name: str) -> ToolSpec:
    """Look up one tool, or explain what to run to see the list."""
    registry = build_registry()
    try:
        return registry[name]
    except KeyError as exc:
        raise InvalidParameterError(
            f"Unknown tool: {name!r}",
            remedy="Run `tools` to see everything available.",
            context={"tool": name},
        ) from exc


def iter_tools(*, engine: Engine | None = None) -> Iterator[ToolSpec]:
    """Every tool, in name order, optionally filtered to one engine.

    The ``engine=Engine.CLOUD`` filter is what ``GET /v1/capabilities`` answers
    with, so the server never has to keep its own list of cloud-backed tools.
    """
    for spec in sorted(build_registry().values(), key=lambda item: item.name):
        if engine is None or spec.supports(engine):
            yield spec


def plugin_errors() -> Mapping[str, str]:
    """Third-party tools that failed to load, mapped to why."""
    return MappingProxyType(_PLUGIN_ERRORS)


__all__ = [
    "Param",
    "ToolSpec",
    "build_registry",
    "get_tool",
    "iter_tools",
    "plugin_errors",
    "register",
]
