"""The local engine for ``permissions``.

Read-only: it never writes anything, and ``outputs`` is always empty. The answer
travels in ``ToolResult.details``, which is where structured data belongs -- an
interface then renders it as a table, JSON, or an MCP response without this tool
knowing which. ``get-info`` has the same shape.

## Why reading and setting are two tools

``protect`` sets permissions; this reports them. Splitting them looks like an
asymmetry next to ``metadata``, which does both, so it is worth saying why.

Metadata's two halves are the same field set with nothing else involved. These
are not. A PDF's permission bits live *inside its encryption dictionary*, so
writing them means encrypting the document -- choosing an algorithm, taking a
user password and an owner password, and producing a file nobody can open
without one. That is the whole of ``protect``. A ``permissions --allow print``
that quietly did all of it would be a second implementation of encryption behind
a name that does not mention it, and the project's rule is one implementation of
one thing.

So: ``protect --allow print`` is how permissions are set, and this answers what a
document currently allows -- a question nothing else answers, since ``get-info``
reports only *whether* a file is encrypted.

## An unencrypted document allows everything

Not as a default, and not as a guess: a file with no encryption has no
permission field at all, so there is nothing restricting it. ``_permissions.py``
owns that reading, and the same function produces it here and for a locked file,
so the two cannot drift.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _permissions
from docmax.tools._pdf import open_encrypted_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class PermissionsLocal:
    """Report what a document allows, without changing it."""

    def is_available(self) -> bool:
        return importlib.util.find_spec(DEPENDENCY) is not None

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return f"{DEPENDENCY} is not installed."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Report what ``docs[0]`` allows. Writes nothing; ``target`` is unused.

        ``target`` is accepted and ignored, the same seam ``get-info`` sits in:
        ``ToolSpec`` cannot say "this tool produces no output", so the CLI builds
        a target directly rather than resolving one. See
        ``tools/get_info/local.py`` for the note and the fix that is owed.
        """
        import time

        if not docs:
            raise InvalidParameterError(
                "Permissions needs a document.",
                remedy="Pass the PDF to inspect.",
            )

        password = _password(params)

        started = time.monotonic()
        document = docs[0]
        cancellation.raise_if_cancelled(operation="permissions")

        reader, opened_with = open_encrypted_pdf(document, password)

        # `user_access_permissions` is None for an unencrypted document, which
        # `describe` reads as "nothing is restricted" rather than as "no
        # permissions". Passing it straight through keeps that one reading in
        # one place.
        allowed = _permissions.describe(reader.user_access_permissions)

        return ToolResult(
            outputs=(),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "path": str(document.path),
                "name": document.path.name,
                "encrypted": opened_with is not None,
                # Which password matched, never the password. An owner password
                # bypasses every bit below, so a reader of this result needs to
                # know which one was used to interpret it.
                "opened_with": opened_with,
                "permissions": allowed,
                "descriptions": dict(_permissions.DESCRIPTIONS),
                # Stated in the result rather than only in the renderer, so a
                # caller reading --json is told as plainly as one reading a
                # terminal. These bits are a request, not a boundary.
                "advisory": True,
            },
        )


def _password(params: dict[str, Any]) -> str | None:
    """The password, if one was given. Not needed for an unencrypted document."""
    value = params.get("password")
    if value is None or isinstance(value, str):
        return value
    raise InvalidParameterError(
        "password must be text.",
        remedy="Pass one with --password.",
        context={"parameter": "password"},
    )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return PermissionsLocal()


__all__ = ["PermissionsLocal", "build"]
