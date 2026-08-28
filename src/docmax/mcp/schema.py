"""``ToolSpec`` rendered as JSON Schema, for a client that has never seen DocMax.

``Param``'s docstring has said since M0 that this is M10's share of the registry:

    The CLI turns these into options, the TUI into form fields, the API server
    into request validation, and the M10 MCP server into a JSON schema.

and that ``Param.type_`` is *"deliberately a small closed set, since every
consumer has to be able to render it"*. That closed set is what makes this
module four lines of mapping rather than a type system.

**Nothing here imports the MCP SDK**, and nothing here names a tool. Both are
deliberate. The first means the interesting half of this interface — which
properties exist, what is required, whether ``choices`` became an ``enum`` — is
testable with no protocol session, no event loop and no optional dependency,
which is the argument ``tui/forms.py`` made at M7. The second is ADR 0021 and
CLAUDE.md rule 1: a tool is declared once, and every surface is generated.

What this schema **cannot** say is recorded in
``docs/adr/0028-the-mcp-tool-surface-is-the-registry.md``: ``ToolSpec`` carries
nothing describing which formats a tool reads, so ``inputs`` is constrained to
"a path" and not to "a PDF". Adding ``input_suffixes`` would be a Core change and
a fourth open seam, and M10 does not make it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docmax.core.registry import Param, ToolSpec

#: ``Param.type_`` is closed — ``str``, ``int``, ``float``, ``bool``, ``path`` —
#: so this mapping is total and a new spelling is a loud ``KeyError`` rather than
#: a silently untyped property.
_JSON_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    # A path is a string on the wire. It is *not* given a format keyword: the
    # only meaningful constraint on it is the root boundary, which is policy and
    # cannot be expressed in a schema. See ADR 0029.
    "path": "string",
}

#: The two properties every tool has, which do not come from ``params`` because
#: they are not parameters — they are the document and its destination.
INPUTS = "inputs"
OUTPUT = "output"


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    """The JSON Schema for one tool's arguments.

    ``additionalProperties`` is false so a client that invents a parameter is
    told at the boundary rather than having it silently dropped into
    ``**params`` — where it would reach a strategy that never asked for it.
    """
    properties: dict[str, Any] = {INPUTS: _inputs_property(spec), OUTPUT: _output_property(spec)}
    required = [INPUTS]

    for param in spec.params:
        properties[param.name] = _param_property(param)
        if param.required:
            required.append(param.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _inputs_property(spec: ToolSpec) -> dict[str, Any]:
    """Always an array, bounded by ``accepts_multiple_inputs``.

    An array even for the nineteen-out-of-twenty tools that take one document,
    because a single shape is easier for a client to generate against than two —
    and ``maxItems`` still says plainly that only one is accepted.
    """
    schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": (
            "Paths to the documents to read. Must be inside one of the server's --root directories."
        ),
    }
    if not spec.accepts_multiple_inputs:
        schema["maxItems"] = 1
        schema["description"] = (
            "Path to the document to read, as a one-element array. Must be inside "
            "one of the server's --root directories."
        )
    return schema


def _output_property(spec: ToolSpec) -> dict[str, Any]:
    """Optional, and described by the tool's own default extension.

    Optional rather than required so that the tools which produce no output —
    ``get-info``, ``permissions``, a bare ``metadata`` — are callable without an
    invented destination. ``ToolSpec`` cannot say which those are, so the server
    finds out by running into a temporary directory; see ADR 0028.
    """
    return {
        "type": "string",
        "description": (
            f"Where to write the result (typically {spec.default_suffix}). Must be inside "
            "one of the server's --root directories. Omit for a tool that only reports. "
            "An existing file is never overwritten."
        ),
    }


def _param_property(param: Param) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": _JSON_TYPES[param.type_],
        "description": param.description,
    }
    if param.choices:
        schema["enum"] = list(param.choices)
    if param.default is not None:
        schema["default"] = param.default
    return schema


def described(spec: ToolSpec) -> str:
    """The tool's one-line description, with its category for grouping."""
    return f"{spec.summary} (category: {spec.category})"


__all__ = ["INPUTS", "OUTPUT", "described", "input_schema"]
