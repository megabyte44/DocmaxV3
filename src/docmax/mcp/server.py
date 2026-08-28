"""The MCP server: one funnel from a protocol call to ``EngineRouter.run``.

This is the only module in ``docmax.mcp`` that imports the SDK, and the only one
that reaches the router. Both are deliberate. ``schema.py`` and ``policy.py`` are
SDK-free so the interesting halves — what a tool's schema says, and which paths
are refused — are testable with no protocol session and no optional dependency.
And **one** router call site is what makes ADR 0029's boundary a property rather
than a habit: there is nowhere else a path could reach a tool from.

The shape of a call:

    tools/call
      -> policy.check on every path the client sent   (ADR 0029)
      -> DocumentRef / OutputTarget                   (the same types the CLI builds)
      -> EngineRouter.run in a worker thread          (ADR 0030)
      -> tool -> validators -> core/atomic.py         (unchanged since M1)

Nothing here knows what any tool does, and nothing here names one. `list_tools`
is `iter_tools()` rendered through `schema.py`; adding tool #20 makes it appear
over MCP with no edit to this file. See ADR 0028.

Errors arrive in two rungs, and keeping them apart is the point. A *DocMax*
failure — a corrupt document, a destination that exists, a path outside the
roots — is an anticipated result: it comes back as `CallToolResult(is_error=True)`
carrying the same `DocMaxError.to_dict()` envelope the CLI puts on stdout, codes
and remedies intact. A *protocol* failure — an unknown tool, arguments that are
not an object — is an `MCPError` on the JSON-RPC rung, which is the one place a
second representation is correct. Neither path can emit a traceback: anything
unexpected is already wrapped as `InternalError` by the router, and the catch-all
here reports its message without the stack.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from docmax.core.branding import APP_NAME, CLI_NAME
from docmax.core.cancellation import CancellationToken
from docmax.core.errors import DocMaxError, InvalidParameterError
from docmax.core.models import DocumentRef, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import iter_tools
from docmax.mcp import schema
from docmax.mcp.policy import Policy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp.server import ServerRequestContext

    from docmax.core.models import ToolResult
    from docmax.core.registry import ToolSpec
    from docmax.core.router import EngineRouter

#: The server's own identity on the wire.
SERVER_NAME = CLI_NAME


def build_router(policy: Policy) -> EngineRouter:
    """A router wired to the user's configuration, with the policy applied.

    Mirrors ``cli/execution.build_router`` rather than importing it —
    ``interfaces-are-independent`` forbids reaching into the CLI, and four lines
    are the right thing to copy where an import would be wrong. What differs is
    one line: :meth:`Policy.configure` forces ``offline`` unless the operator
    passed ``--allow-cloud``.

    The consent store is real and is *read* like any other caller's. It is never
    written here: consent is a human act, and an agent that could grant it on the
    user's behalf would make ADR 0008's record a formality.
    """
    from docmax.core.config import consent_file, load
    from docmax.core.consent import ConsentStore
    from docmax.core.router import EngineRouter

    config = policy.configure(load())
    return EngineRouter(
        config=config,
        consent=ConsentStore(consent_file(), endpoint=config.cloud_endpoint),
    )


class DocMaxServer:
    """Turns MCP requests into router calls, and results back into MCP."""

    __slots__ = ("_policy", "_router")

    def __init__(self, policy: Policy, router: EngineRouter | None = None) -> None:
        self._policy = policy
        self._router = router if router is not None else build_router(policy)

    # -- discovery ----------------------------------------------------------

    def tools(self) -> list[types.Tool]:
        """Every registered tool, described from its own ``ToolSpec``."""
        return [
            types.Tool(
                name=spec.name,
                title=spec.name,
                description=schema.described(spec),
                input_schema=schema.input_schema(spec),
            )
            for spec in iter_tools()
        ]

    async def on_list_tools(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self.tools())

    # -- invocation ---------------------------------------------------------

    async def on_call_tool(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        spec = self._spec_for(params.name)
        arguments = dict(params.arguments or {})

        try:
            plan = self._plan(spec, arguments)
        except DocMaxError as exc:
            # Refused before anything ran: an unknown parameter, a path outside
            # the roots, a destination that already exists.
            return _failure(exc)

        try:
            result = await self._execute(spec, plan)
        except DocMaxError as exc:
            return _failure(exc)

        return _success(spec, result)

    def _spec_for(self, name: str) -> ToolSpec:
        """Resolve the tool, or fail on the protocol's own rung.

        An unknown tool name is a client asking for something `tools/list` never
        offered, which is a protocol error rather than a document one — so it is
        an `MCPError`, not a `CallToolResult`.
        """
        try:
            return self._router.lookup(name)
        except DocMaxError as exc:
            raise MCPError(types.METHOD_NOT_FOUND, f"Unknown tool: {name}") from exc

    def _plan(self, spec: ToolSpec, arguments: dict[str, Any]) -> _Plan:
        """Check every argument and every path, before anything is created."""
        declared = {param.name for param in spec.params}
        unknown = sorted(set(arguments) - declared - {schema.INPUTS, schema.OUTPUT})
        if unknown:
            raise InvalidParameterError(
                f"Unknown argument{'s' if len(unknown) > 1 else ''}: "
                + ", ".join(repr(name) for name in unknown),
                remedy=f"{spec.name!r} accepts: "
                + ", ".join(sorted(declared | {schema.INPUTS, schema.OUTPUT})),
                context={"tool": spec.name, "keys": unknown},
            )

        raw_inputs = arguments.get(schema.INPUTS)
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise InvalidParameterError(
                f"{schema.INPUTS!r} must be a non-empty array of paths.",
                remedy=f'Pass {schema.INPUTS!r} as an array, e.g. ["report.pdf"].',
                context={"tool": spec.name},
            )
        if not spec.accepts_multiple_inputs and len(raw_inputs) > 1:
            raise InvalidParameterError(
                f"{spec.name!r} takes one document, not {len(raw_inputs)}.",
                remedy="Call it once per document.",
                context={"tool": spec.name},
            )

        # The security boundary. Every path the client sent, resolved and checked
        # against the roots before it can reach a tool. See ADR 0029.
        inputs = self._policy.check_all(
            [Path(str(item)) for item in raw_inputs], field=schema.INPUTS
        )

        raw_output = arguments.get(schema.OUTPUT)
        output = (
            self._policy.check(Path(str(raw_output)), field=schema.OUTPUT)
            if raw_output is not None
            else None
        )

        params = {name: value for name, value in arguments.items() if name in declared}
        return _Plan(inputs=inputs, output=output, params=params)

    async def _execute(self, spec: ToolSpec, plan: _Plan) -> ToolResult:
        """Run the tool off the event loop, and map cancellation onto the token.

        ADR 0030: DocMax tools are synchronous and blocking, so running one here
        would stall the loop that carries the very `notifications/cancelled` that
        would stop it. The blocking call goes to a worker thread;
        `abandon_on_cancel=True` lets the protocol be answered at once, and the
        token is what actually stops the tool — which is also what guarantees the
        atomic writers discard the staged file, so a cancelled call leaves no
        partial output.
        """
        token = CancellationToken()

        def work() -> ToolResult:
            return self._run(spec, plan, token)

        try:
            return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            token.cancel()
            # Re-raised, never swallowed: the dispatcher drops the response for a
            # cancelled request, and a handler returning normally would be lying
            # to its own runtime.
            raise

    def _run(self, spec: ToolSpec, plan: _Plan, token: CancellationToken) -> ToolResult:
        """The one place this package calls the router.

        When no destination was given, the run is staged into a temporary
        directory. A tool that only reports writes nothing there and is
        unaffected; a tool that *does* produce a file is refused, having written
        it only inside the temporary directory that is about to be removed. That
        is how `get-info` and `permissions` stay callable without this package
        holding a list of which tools produce no output — the `ToolSpec` seam
        ADR 0028 declines to close.
        """
        docs = [DocumentRef.from_path(path) for path in plan.inputs]

        if plan.output is not None:
            target = self._router.target_for(spec.name, docs, requested=str(plan.output))
            return self._router.run(
                spec.name,
                docs,
                target,
                progress=NULL_PROGRESS,
                cancellation=token,
                **plan.params,
            )

        with tempfile.TemporaryDirectory(prefix=f"{CLI_NAME}-mcp-") as scratch:
            staged = Path(scratch) / f"{docs[0].path.stem}{spec.default_suffix}"
            target = OutputTarget.resolve(inputs=docs, requested=staged)
            result = self._router.run(
                spec.name,
                docs,
                target,
                progress=NULL_PROGRESS,
                cancellation=token,
                **plan.params,
            )
            if result.outputs:
                raise InvalidParameterError(
                    f"{spec.name!r} produces a document, so it needs a destination.",
                    remedy=f"Pass {schema.OUTPUT!r} with a path inside an allowed root.",
                    context={"tool": spec.name},
                )
            return result


class _Plan:
    """A checked call: resolved paths and the parameters the tool declared."""

    __slots__ = ("inputs", "output", "params")

    def __init__(self, *, inputs: Sequence[Path], output: Path | None, params: dict[str, Any]):
        self.inputs = list(inputs)
        self.output = output
        self.params = params


def _plain(value: Any) -> Any:
    """Reduce a tool's ``details`` to things JSON can carry.

    The CLI has the same four lines in ``cli/json_output.py``. That is not
    duplicated logic but duplicated *rendering*, which is what
    ``architecture/layers.md`` says an interface owns — and importing the CLI's
    copy would break ``interfaces-are-independent`` for a serialisation helper.
    The envelope's *shape* is shared; turning a ``Path`` into a string is each
    interface's own business.
    """
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _success(spec: ToolSpec, result: ToolResult) -> types.CallToolResult:
    """The M6 success envelope, as structured content plus one readable line."""
    payload = {
        "tool": spec.name,
        "engine": result.engine_used.value,
        "outputs": [str(path) for path in result.outputs],
        "duration_ms": result.duration_ms,
        "engine_version": result.engine_version,
        "details": _plain(result.details),
    }
    wrote = ", ".join(payload["outputs"]) if result.outputs else "nothing to write"
    return types.CallToolResult(
        content=[types.TextContent(text=f"{spec.name}: {wrote}")],
        structured_content={"ok": True, "result": payload},
    )


def _failure(exc: DocMaxError) -> types.CallToolResult:
    """The same error envelope the CLI puts on stdout — codes, remedies, no stack."""
    envelope = exc.to_dict()
    line = exc.message + (f"\n{exc.remedy}" if exc.remedy else "")
    return types.CallToolResult(
        content=[types.TextContent(text=line)],
        structured_content={"ok": False, "error": envelope},
        is_error=True,
    )


def build_server(policy: Policy, router: EngineRouter | None = None) -> Server[Any]:
    """Wire a :class:`DocMaxServer` into the SDK's low-level server.

    The 2.x low-level API takes handlers as constructor callbacks rather than
    decorators, which is what lets the tool surface be generated from the
    registry instead of declared as decorated functions — one per tool, which is
    the shape ADR 0021 and CLAUDE.md rule 1 forbid.
    """
    from docmax import __version__

    facade = DocMaxServer(policy, router)
    return Server(
        SERVER_NAME,
        version=__version__,
        title=APP_NAME,
        instructions=(
            f"{APP_NAME} document tools, running locally. {policy.describe()} "
            "Existing files are never overwritten."
        ),
        on_list_tools=facade.on_list_tools,
        on_call_tool=facade.on_call_tool,
    )


async def serve_stdio(policy: Policy) -> None:
    """Run the server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    server = build_server(policy)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


__all__ = ["SERVER_NAME", "DocMaxServer", "build_router", "build_server", "serve_stdio"]
