"""The ``--json`` envelope, and the switch that turns it on.

One JSON object on stdout per command, and nothing else on stdout at all.
[ADR 0017](../../../docs/adr/0017-json-output-contract.md) settles the shape;
this module is where it is built.

## Why a module-level flag

``--json`` is a global option on the root callback, so it is spelled the same
way for every command and a new command inherits it rather than remembering it.
That means the value has to reach renderers that are called from eighteen
command bodies, none of which take it as a parameter.

Threading it through every call site would be the tidier arrangement and would
touch every command signature. A module-level switch is the smaller change and
is honest about what it is: process-wide state, set once from argv before any
command runs, read by the renderers. It is not thread-safe and does not need to
be — a CLI process runs one command.

## The stable part, and the part that is not

``ok``, ``result``, ``error`` and ``error.code`` are the contract. ``details``
is passed through as the tool produced it and is **not** — freezing every tool's
internal counters as public API would make a page count a breaking change.
Documented in ``docs/implementation/json.md`` so a script author knows which
keys they may depend on.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docmax.core.errors import DocMaxError
    from docmax.core.models import ToolResult

_enabled = False


def set_enabled(value: bool) -> None:
    """Turn JSON output on or off for this process. Called once, from the root callback."""
    global _enabled
    _enabled = value


def enabled() -> bool:
    return _enabled


def note(flag: bool) -> None:
    """Record a command-level ``--json``, without unsetting a root-level one.

    ``--json`` is accepted both before and after the command name, because both
    are things people type. The two must not fight: a command whose own flag
    defaults to ``False`` would otherwise switch off a ``--json`` given at the
    root. So this only ever turns the switch on.
    """
    if flag:
        set_enabled(True)


def success(result: ToolResult, *, tool: str) -> str:
    """The success envelope, as one line of JSON.

    ``outputs`` is always present and always a list — including the empty list
    for a read-only tool such as ``get-info``, whose answer lives in
    ``details``. A missing key would make a caller distinguish "wrote nothing"
    from "this version does not report that", and those are different questions.
    """
    return _dump(
        {
            "ok": True,
            "result": {
                "tool": tool,
                "engine": result.engine_used.value,
                "outputs": [str(path) for path in result.outputs],
                "duration_ms": result.duration_ms,
                "engine_version": result.engine_version,
                "details": _plain(result.details),
            },
        }
    )


def failure(error: DocMaxError) -> str:
    """The error envelope — the same shape ``cloud-api.md`` puts on the wire.

    Deliberately identical, so a script parsing a DocMax failure sees one shape
    whether the work ran locally or on an endpoint. ``to_dict`` is what the
    cloud client already produces from a wire error.
    """
    return _dump({"ok": False, "error": error.to_dict()})


def report(payload: dict[str, Any]) -> str:
    """The envelope for a command that answers a question rather than writing a file.

    ``doctor`` and ``formats`` produce structured answers a script would want,
    and they have no ``ToolResult`` to wrap. Same ``ok``/``result`` shape, so a
    caller parses one thing.
    """
    return _dump({"ok": True, "result": payload})


def _dump(payload: dict[str, Any]) -> str:
    # `ensure_ascii=False` so a filename with an accent in it round-trips as
    # itself rather than as escapes; the stream is UTF-8 either way.
    return json.dumps(payload, ensure_ascii=False, default=_fallback)


def _plain(value: Any) -> Any:
    """Reduce a tool's ``details`` to things ``json`` can serialise.

    Tools put counts, names and occasionally a ``Path`` in here. Anything
    exotic becomes its string form rather than raising — a result that cannot
    be printed because one detail was unusual would be a poor trade.
    """
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _fallback(value: object) -> str:
    from pathlib import Path

    if isinstance(value, Path):
        return str(value)
    return str(value)


__all__ = ["enabled", "failure", "note", "report", "set_enabled", "success"]
