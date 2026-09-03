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
    from collections.abc import Callable, Iterator, Mapping, Sequence

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
    #: What a form calls this parameter, when its name is not what a person
    #: would call it. Purely presentational, exactly like
    #: :attr:`component_labels`: the wire name is still ``name``, so the CLI
    #: flag, the MCP schema and API validation are unaffected.
    #:
    #: Exists because a flag name and a form label are not the same kind of
    #: word. ``convert-image --to png`` reads correctly on a command line,
    #: where the verb is already in the command; the same parameter rendered
    #: above a dropdown says only "to", which names nothing. Empty (the
    #: default) derives the label from ``name``, which is right for the
    #: parameters whose names are already nouns — ``quality``, ``width``,
    #: ``password``.
    label: str = ""
    #: Marks this parameter as one of several mutually exclusive ways to say
    #: the same thing — ``resize``'s ``scale`` versus its ``width``/``height``.
    #: The value is the question a form asks once for the whole set, e.g.
    #: ``"Resize method"``; every parameter sharing it is an alternative
    #: answer, distinguished by :attr:`group_option`. Empty (the default)
    #: means this parameter stands alone, which is every parameter but these.
    #:
    #: Presentational only, exactly like :attr:`label`: the CLI still accepts
    #: every one of these flags plainly and simultaneously (their own
    #: validation is what makes them exclusive, not this). Only the TUI reads
    #: it, rendering one selector for the group and showing just the fields
    #: for whichever answer is currently chosen — a user configuring
    #: percentage resizing is not also shown the width and height fields that
    #: answer.
    group: str = ""
    #: Which answer within :attr:`group` this parameter belongs to, e.g.
    #: ``"Percentage"``. Meaningless when ``group`` is empty.
    group_option: str = ""


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
    #: ``get-info``, ``permissions`` and a bare ``metadata`` write nothing —
    #: the answer travels in ``ToolResult.details`` instead. Defaults to
    #: ``True`` so every other tool needs no edit. ``EngineRouter.target_for``
    #: reads this to skip ``OutputTarget.resolve`` entirely for such a tool,
    #: and every interface reads it to skip asking for ``-o`` in the first
    #: place — see ADR 0036. Before this flag existed, each interface worked
    #: around the gap on its own: the CLI built a throwaway ``OutputTarget``
    #: by hand in ``execute_read_only`` rather than calling ``target_for``,
    #: and the TUI had no such workaround at all, so it asked for an output
    #: path a report-only tool would never use.
    produces_output: bool = True
    #: Used by ``OutputTarget.resolve`` when the user does not pass ``-o``.
    default_suffix: str = ".pdf"
    #: The output extension when a *parameter* decides it, rather than the
    #: constant above. Given the run's parameters and the suffix the
    #: destination already carries (``""`` when there is none yet),
    #: returns the suffix the result must carry (``".png"``) or ``None``
    #: to leave what is there alone.
    #:
    #: The current suffix is passed in because only the tool can judge
    #: whether it is *already* right: ``.jpeg`` and ``.jpg`` are one
    #: format, and rewriting either into the other would be a change the
    #: user did not ask for. ``core`` cannot know that -- the table that
    #: does live in ``tools``.
    #:
    #: This closes the "output extension depends on a parameter" seam
    #: ``convert/tool.py`` has documented since M5. ``convert-image --to png``
    #: names the format outright, and before this the destination's extension
    #: was a second, contradictory answer to the same question — leaving the
    #: user to reconcile two things they had already said once.
    #:
    #: A callable rather than a table because the mapping from a parameter
    #: value to a suffix is the *tool's* knowledge: ``convert-image`` reads it
    #: from ``tools/_formats``, which ``core`` must never import (see
    #: ``.importlinter``). The router calls this without knowing what it
    #: consults, which is what keeps the layering intact.
    #:
    #: Consumed generically by ``EngineRouter.target_for``, so every surface —
    #: CLI, TUI, MCP — inherits the behaviour without a line of per-tool code.
    suffix_for_params: Callable[[Mapping[str, Any], str], str | None] | None = None
    #: One line describing the chosen inputs, shown by a form before it asks
    #: for parameters that depend on them. Given the input paths, returns the
    #: line or ``None`` to say nothing.
    #:
    #: Exists because some parameters cannot be answered without a fact about
    #: the document: `resize` asks for a width in pixels, and a user who does
    #: not know the image is 1920 x 1080 has to leave the application to find
    #: out. The fact lives with the tool that needs it -- reading image
    #: dimensions means Pillow, which the TUI must not import.
    #:
    #: Advisory only, exactly like `forms.describe_missing_paths`: a
    #: description that fails must never stop a run, so implementations return
    #: ``None`` rather than raising.
    describe_inputs: Callable[[Sequence[Path]], str | None] | None = None
    #: ``split`` and ``to-images`` write many files into ``-o`` as a directory
    #: — see ADR 0003's ``atomic_dir`` and ADR 0031. Every other tool writes
    #: one file, and ``OutputTarget.resolve`` reads this to decide whether an
    #: extensionless destination is a typo to correct (a file) or exactly what
    #: was meant (a directory almost never has a dot in its name).
    produces_directory: bool = False
    #: Free-form, for ``--help`` grouping and search.
    aliases: tuple[str, ...] = field(default_factory=tuple)
    #: No destination may ever be *implied* for this tool -- not derived from
    #: the first input, and not guessed by appending ``default_suffix`` to an
    #: extensionless ``-o``. Most tools are safe to guess for; a handful are
    #: not, and guessing anyway would be actively wrong rather than merely
    #: unhelpful. ``convert``'s real output extension is ``to``, a parameter,
    #: not a constant -- Pandoc can never write PDF (ADR 0011), so
    #: ``default_suffix`` (``.pdf``) is never a valid guess for it.
    #: ``metadata`` only ever writes a *new* document when asked to
    #: (``--set``/``--clear``); *whether* ``-o`` is required that time still
    #: has to be decided in ``cli/commands.py``, since it depends on other
    #: parameters ``ToolSpec`` cannot see -- but "no destination may ever be
    #: implied" is true of it unconditionally, and is exactly what this field
    #: records. See ADR 0033. Consumed by ``tui/app.py:RunScreen``, generically,
    #: to mark the generated output field required and drop the placeholder's
    #: extension hint where it would mislead.
    output_required: bool = False

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
