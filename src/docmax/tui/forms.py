"""Turning a ``ToolSpec`` into a form, and a filled-in form back into parameters.

The whole of the TUI's tool support is this module plus the widgets that render
what it describes. There is **no per-tool code anywhere in the TUI**, and that
is the point: ``core.registry.Param`` was designed for it —

    The CLI turns these into options, the TUI into form fields, the API server
    into request validation, and the M10 MCP server into a JSON schema.

``Param.type_`` is *"deliberately a small closed set, since every consumer has to
be able to render it"*, so :func:`field_for` is a five-branch match and a tool
that invents a sixth type is a registry error rather than a TUI one.

## Why the conversion lives here rather than in a widget

Everything in this module is plain data and plain functions, with no ``textual``
import anywhere. That is what lets the interesting half of the TUI — which
parameters exist, what they default to, whether ``"3"`` is a valid page count —
be tested without a terminal, a screen size, or an event loop. The widgets are
then thin enough that a Pilot test only has to prove they are wired up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docmax.core.registry import Param, ToolSpec

#: What a field can be. One per ``Param.type_``, plus ``choice`` for a parameter
#: that declared ``choices`` — which is a rendering distinction, not a new type:
#: a ``str`` with a closed set of values is a dropdown, not a text box.
KINDS = ("text", "integer", "number", "boolean", "path", "choice")

#: Accepted spellings of yes and no, for a boolean typed as text. Closed, and
#: anything outside it is an error — ``core/config.py`` settled that argument:
#: an unrecognised boolean read as false would silently do the wrong thing.
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


@dataclass(frozen=True, slots=True)
class Field:
    """One row of a generated form."""

    name: str
    label: str
    kind: str
    description: str
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    #: Copied from ``Param.component_labels`` — see its docstring and ADR 0032.
    #: Non-empty means "render one labelled text input per label and join
    #: them with commas", instead of the single field every other kind gets.
    components: tuple[str, ...] = ()

    def default_text(self) -> str:
        """The default rendered for a text input. Empty means "nothing yet"."""
        if self.default is None:
            return ""
        if isinstance(self.default, bool):
            return "true" if self.default else "false"
        return str(self.default)

    def default_component(self, index: int) -> str:
        """The default for one part of a composite field, or ``""``.

        Splits ``default_text()`` on commas only when it has exactly as many
        parts as there are labels — a default that does not fit the shape
        (usually because there is no default at all) leaves every part blank
        rather than guessing which numbers go where.
        """
        parts = self.default_text().split(",")
        if len(parts) != len(self.components):
            return ""
        return parts[index].strip()


def field_for(param: Param) -> Field:
    """One :class:`Field` from one ``Param``."""
    # A `str` with a closed set of values is a dropdown, not a text box. That is
    # a rendering distinction rather than a sixth type, so it is decided here
    # and not in `Param`.
    kind = "choice" if param.choices else _KINDS_BY_TYPE.get(param.type_, "text")
    return Field(
        name=param.name,
        label=param.name.replace("_", " "),
        kind=kind,
        description=param.description,
        default=param.default,
        required=param.required,
        choices=param.choices,
        components=param.component_labels,
    )


def fields_for(spec: ToolSpec) -> list[Field]:
    """Every field a tool's form needs, in the order the spec declares them.

    Declaration order, not alphabetical: a tool author put the important
    parameter first, and re-sorting would throw that away.
    """
    return [field_for(param) for param in spec.params]


def collect(fields: Sequence[Field], values: Mapping[str, str]) -> dict[str, Any]:
    """The ``**params`` for ``EngineRouter.run``, from what the user typed.

    Empty input means *"not supplied"* and the key is omitted entirely rather
    than passed as ``None``. That matters: every tool reads its parameters with
    ``params.get(name, default)``, so an explicit ``None`` would override a
    default the tool intended to apply, and ``rotate --by`` would become 0
    degrees instead of 90.

    A required field left empty, or a value of the wrong shape, raises
    :class:`InvalidParameterError` — the same typed error the CLI would raise
    for the same input, so the TUI's error modal has a remedy to show without
    inventing one.
    """
    params: dict[str, Any] = {}
    for field in fields:
        raw = values.get(field.name, "")
        text = raw.strip() if isinstance(raw, str) else raw

        if not text:
            if field.required:
                raise InvalidParameterError(
                    f"{field.label} is required.",
                    remedy=field.description,
                    context={"parameter": field.name},
                )
            continue

        params[field.name] = _convert(field, text)
    return params


def _convert(field: Field, text: str) -> Any:
    """One typed value, or the error naming the field and what it wanted."""
    if field.kind == "integer":
        return _integer(field, text)
    if field.kind == "number":
        return _number(field, text)
    if field.kind == "boolean":
        return _boolean(field, text)
    if field.kind == "choice":
        return _choice(field, text)
    # `text` and `path` both travel as strings. A path is *not* resolved here:
    # `DocumentRef.from_path` and `OutputTarget.resolve` own that, and doing it
    # twice is how two implementations of the in-place check start to differ.
    return text


def _fail(field: Field, message: str, *, remedy: str) -> InvalidParameterError:
    return InvalidParameterError(
        message,
        remedy=remedy,
        context={"parameter": field.name},
    )


def _integer(field: Field, text: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise _fail(
            field,
            f"{field.label} must be a whole number, and {text!r} is not.",
            remedy=field.description,
        ) from exc


def _number(field: Field, text: str) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise _fail(
            field,
            f"{field.label} must be a number, and {text!r} is not.",
            remedy=field.description,
        ) from exc


def _boolean(field: Field, text: str) -> bool:
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise _fail(
        field,
        f"{field.label} must be true or false, and {text!r} is neither.",
        remedy="Use true or false.",
    )


def _choice(field: Field, text: str) -> str:
    if text in field.choices:
        return text
    allowed = ", ".join(field.choices)
    raise _fail(
        field,
        f"{text!r} is not one of the values {field.label} accepts.",
        remedy=f"Choose one of: {allowed}.",
    )


def describe_missing_paths(raw: str) -> str:
    """A one-line problem with the ``__inputs__`` text, or ``""`` if there is none.

    Generic filesystem checks only — existence and "is this a file" — because
    that is all that can be known before a tool has even been chosen to run.
    ``DocumentRef.from_path`` remains the authority the router calls before any
    work starts; this exists only so a typo is visible while the user is still
    looking at the field, not after they press Run.
    """
    problems = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        path = Path(part).expanduser()
        if not path.exists():
            problems.append(f"{part} does not exist")
        elif path.is_dir():
            problems.append(f"{part} is a directory, not a file")
    return "; ".join(problems)


def first_input_directory(raw: str) -> Path | None:
    """The directory of the first path in the ``__inputs__`` text, or ``None``.

    Used to open the output save dialog somewhere more useful than the home
    directory — the same folder the inputs already live in, which is where a
    merged or converted result most often belongs. Purely a starting point for
    the dialog, never a destination: the user still names the file, and
    ``OutputTarget.resolve`` remains the sole authority on whether whatever
    they choose is safe to write to.
    """
    for part in raw.split(","):
        part = part.strip()
        if part:
            return Path(part).expanduser().parent
    return None


#: ``Param.type_`` to :attr:`Field.kind`. Anything unrecognised renders as text
#: rather than raising: a tool declaring an unknown type is a registry bug, and
#: refusing to draw the whole form over it would hide nineteen working tools
#: behind one broken declaration.
_KINDS_BY_TYPE = {
    "str": "text",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "path": "path",
}


__all__ = [
    "KINDS",
    "Field",
    "collect",
    "describe_missing_paths",
    "field_for",
    "fields_for",
    "first_input_directory",
]
