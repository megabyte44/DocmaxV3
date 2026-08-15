"""The bridge from an HTTP request back to the registry.

This is the part that makes the server a thin adapter rather than a second
implementation of the product. It resolves a tool name against the same registry
the CLI uses and runs the same ``EngineStrategy`` — the *local* one. That is the
whole trick of the Cloud Engine: the server is a machine that already has
Ghostscript, Tesseract, and a LaTeX distribution installed, running exactly the
code a user would run if they had installed them too.

So the cloud engine is not a separate feature with separate behaviour, and a
cloud result cannot quietly diverge from a local one. There is one
implementation of ``compress``; the only question is whose machine it runs on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from docmax.core.errors import EngineNotSupportedError
from docmax.core.models import Engine
from docmax.core.registry import get_tool

if TYPE_CHECKING:
    from docmax.core.registry import ToolSpec
    from docmax.server.jobs import Job


class ToolRunner(Protocol):
    """Whatever actually performs the work behind an endpoint."""

    def resolve(self, tool_name: str) -> ToolSpec:
        """Find the tool, or explain that this endpoint does not offer it."""
        ...

    def start(self, job: Job, payload: bytes, *, filename: str) -> Job:
        """Begin the work. May finish synchronously or leave the job running."""
        ...


@dataclass(slots=True)
class RegistryRunner:
    """Runs tools through the registry, in this process."""

    def resolve(self, tool_name: str) -> ToolSpec:
        spec = get_tool(tool_name)
        if not spec.supports(Engine.CLOUD):
            # Not an error in the tool — a deliberate boundary. Tools whose
            # local engine is pure Python have no cloud engine anywhere, because
            # uploading a document to perform a millisecond-long operation is
            # strictly worse than doing it where the document already is.
            raise EngineNotSupportedError(
                f"This endpoint does not offer {tool_name!r}.",
                remedy="Run it locally instead — no installation is required for this one.",
                context={"tool": tool_name},
            )
        return spec

    def start(self, job: Job, payload: bytes, *, filename: str) -> Job:
        """Run ``job``. Lands in M6, alongside the cloud strategies themselves.

        The shape it takes: stage ``payload`` under a temp path, build a
        ``DocumentRef`` and an ``OutputTarget`` over it, call
        ``spec.load_strategy(Engine.LOCAL).run(...)``, publish the output, and
        discard the input — on success and on failure alike, because the
        contract says documents are deleted on completion, not on success.
        """
        # Spelled out because this one reaches a user: it is answered as a 500
        # envelope, and "NotImplementedError:" with nothing after it tells them
        # nothing at all.
        raise NotImplementedError(
            "This endpoint accepts and validates the request, but does not run the tool yet."
        )


__all__ = ["RegistryRunner", "ToolRunner"]
