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

**This module lives below every interface, not inside one.** It was built at
M10 inside ``docmax.mcp`` for the local stdio server, and moved here at M11
([ADR 0035](../../../docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md))
so that ``docmax.server``'s remote MCP route can render the same schema without
``docmax.server`` importing ``docmax.mcp`` — which ``interfaces-are-independent``
forbids. It sits beside ``docmax.pickers`` and ``docmax.runners``: wanted by more
than one interface, never prints, never exits, imports ``docmax.core`` only.
``docmax.mcp.schema`` re-exports this module's contents unchanged, so nothing
importing it before M11 needs to change.

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
    # only meaningful constraint on it is a policy boundary, which differs by
    # transport (local roots for stdio, per-key upload ownership for remote —
    # see ADR 0029 and ADR 0035) and cannot be expressed in a schema shared by
    # both.
    "path": "string",
}

#: The two properties every tool has, which do not come from ``params`` because
#: they are not parameters — they are the document and its destination.
INPUTS = "inputs"
OUTPUT = "output"


#: The original, stdio-local wording — a path inside a ``--root`` directory.
#: Kept as the default so every existing caller (``docmax.mcp``, and every test
#: that calls ``input_schema(spec)`` with no keywords) sees unchanged output.
#: A transport for which this is not true — the remote route, where ``inputs``
#: and ``output`` are ``file_id``s, not paths — passes its own text instead.
#: See ADR 0035: what a client is told these strings mean is transport policy,
#: not something this shared module can decide for both of them.
_DEFAULT_INPUTS_DESCRIPTION_ONE = (
    "Path to the document to read, as a one-element array. Must be inside "
    "one of the server's --root directories."
)
_DEFAULT_INPUTS_DESCRIPTION_MANY = (
    "Paths to the documents to read. Must be inside one of the server's --root directories."
)
_DEFAULT_OUTPUT_DESCRIPTION = (
    "Where to write the result{suffix}. Must be inside one of the server's --root "
    "directories. Omit for a tool that only reports. An existing file is never overwritten."
)


def input_schema(
    spec: ToolSpec,
    *,
    inputs_description: str | None = None,
    output_description: str | None = None,
) -> dict[str, Any]:
    """The JSON Schema for one tool's arguments.

    ``additionalProperties`` is false so a client that invents a parameter is
    told at the boundary rather than having it silently dropped into
    ``**params`` — where it would reach a strategy that never asked for it.

    ``inputs_description`` and ``output_description`` default to the original
    stdio-local wording ("...inside one of the server's --root directories"),
    because what an ``inputs`` or ``output`` value *is* differs by transport: a
    local filesystem path inside a ``--root`` for stdio, a ``file_id`` from an
    upload for the remote route (ADR 0035). Passing them explicitly is how a
    transport says what the strings mean *for it*, without this shared module
    hardcoding either transport's policy.
    """
    properties: dict[str, Any] = {
        INPUTS: _inputs_property(spec, description=inputs_description),
        OUTPUT: _output_property(spec, description=output_description),
    }
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


def _inputs_property(spec: ToolSpec, *, description: str | None) -> dict[str, Any]:
    """Always an array, bounded by ``accepts_multiple_inputs``.

    An array even for the nineteen-out-of-twenty tools that take one document,
    because a single shape is easier for a client to generate against than two —
    and ``maxItems`` still says plainly that only one is accepted.
    """
    single = not spec.accepts_multiple_inputs
    if description is None:
        description = (
            _DEFAULT_INPUTS_DESCRIPTION_ONE if single else _DEFAULT_INPUTS_DESCRIPTION_MANY
        )
    schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": description,
    }
    if single:
        schema["maxItems"] = 1
    return schema


def _output_property(spec: ToolSpec, *, description: str | None) -> dict[str, Any]:
    """Optional, and described by the tool's own default extension.

    Optional rather than required so that the tools which produce no output —
    ``get-info``, ``permissions``, a bare ``metadata`` — are callable without an
    invented destination. ``ToolSpec`` cannot say which those are, so the caller
    finds out by running into a temporary destination; see ADR 0028.
    """
    if description is None:
        description = _DEFAULT_OUTPUT_DESCRIPTION.format(
            suffix=f" (typically {spec.default_suffix})"
        )
    return {"type": "string", "description": description}


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
