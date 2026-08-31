"""``/v1/mcp`` — the Cloud Engine, spoken over MCP instead of REST.

[ADR 0035](../../../../docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
is the decision this module implements: remote MCP is a transport bridge over
this server, not a fourth interface. Every call here resolves a tool through
the same ``RegistryRunner`` and reads and writes through the same ``Storage``
and ``JobStore`` that ``routes/tools.py`` already uses — there is no second
execution path, no second idea of what a ``file_id`` is, and no second job
model. The only things this module owns are the protocol translation and the
transport-specific pieces ADR 0035 could not reuse from ``docmax.mcp``:
the schema wording (paths there, ``file_id``s here), the auth model (a flat
bearer token standing in for identity, not a local ``ConsentStore``), and the
policy boundary (per-key storage ownership, not filesystem roots).

## Why this needs the SDK's *session manager*, not just its ``Server``

``docmax/mcp/server.py`` builds one ``EngineRouter`` for one trusted local
caller and speaks to it over stdio — there is exactly one connection, ever.
This route serves many callers over one running process, so "the same
identity that opened this MCP session must be the one still using it" is a
real question here that never arises over stdio. The SDK's
``StreamableHTTPSessionManager`` already answers it: it records which
verified identity created each session and refuses a later request on that
session from a different one, with the same "not found" shape a made-up
session id gets — see ``mcpauth.py``. Building that by hand would be a second,
untested implementation of exactly the check the dependency already carries.

## Why stateful, not stateless

``StreamableHTTPSessionManager(stateless=True)`` skips session tracking
entirely — a fresh transport per request, no ``_session_owners`` map, and
therefore no session/auth binding at all. This route is deliberately
**stateful** (the manager's default) so that guarantee is live.

## Why JSON responses, not SSE

DocMax tools are synchronous, request-response operations — nothing here ever
pushes a server-initiated message mid-call, the same reason ``routes/tools.py``
answers a request without opening a stream. ``json_response=True`` returns a
plain JSON body per call instead of holding an SSE connection open for a
response that was never going to stream. If a future tool genuinely needs
server push, this is the flag to revisit — not a reason to hold one open today.

## What this route does not add

No OAuth, no dynamic client registration, no per-key tool authorization
beyond the existing "cloud engine or not" filter every REST caller already
gets. ADR 0035 names all three as deliberately out of scope until there is a
real per-user identity to hang them on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from docmax.core.branding import APP_NAME, CLI_NAME
from docmax.core.errors import DocMaxError, InvalidParameterError
from docmax.core.models import Engine, JobStatus
from docmax.core.registry import iter_tools
from docmax.mcpschema import INPUTS, OUTPUT, described, input_schema
from docmax.server.mcpauth import ApiKeyVerifier

if TYPE_CHECKING:
    from mcp.server import ServerRequestContext

    from docmax.core.registry import ToolSpec
    from docmax.server.config import ServerSettings
    from docmax.server.execution import ToolRunner
    from docmax.server.jobs import Job, JobStore
    from docmax.server.storage import Storage

MOUNT_PATH = "/mcp"

#: The server's own identity on the wire — `docmax-cloud`, distinct from the
#: local stdio server's `SERVER_NAME` in `docmax/mcp/server.py` (plain
#: `CLI_NAME`), so a client that somehow talks to both can tell them apart.
SERVER_NAME = f"{CLI_NAME}-cloud"

#: Loopback names TLS is not required for, matching the exemption
#: `docs/cloud-api.md` already documents for the client's own plaintext
#: refusal — plaintext works for local development, and is refused everywhere
#: else, on both ends of the connection.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_INPUTS_DESCRIPTION_ONE = (
    "The file_id of the document to read, as a one-element array. "
    "Obtain it from POST /v1/uploads (or the small-file path of POST /v1/tools)."
)
_OUTPUT_DESCRIPTION = (
    "Not used by this endpoint. Every call writes to a new, server-generated "
    "file automatically; the result names its file_id and download URL. Any "
    "value given here is ignored"
)


class DocMaxCloudMCP:
    """Turns MCP requests into `RegistryRunner` calls, over the Cloud Engine."""

    __slots__ = ("_jobs", "_runner", "_storage")

    def __init__(self, *, storage: Storage, jobs: JobStore, runner: ToolRunner) -> None:
        self._storage = storage
        self._jobs = jobs
        self._runner = runner

    # -- discovery ------------------------------------------------------

    def tools(self) -> list[types.Tool]:
        """Every tool this deployment can run in the cloud — a strict subset
        of what a local `docmax mcp` client sees, the same asymmetry
        `/v1/capabilities` already exposes for REST.
        """
        return [
            types.Tool(
                name=spec.name,
                title=spec.name,
                description=described(spec),
                input_schema=input_schema(
                    spec,
                    inputs_description=_INPUTS_DESCRIPTION_ONE,
                    output_description=_OUTPUT_DESCRIPTION,
                ),
            )
            for spec in iter_tools()
            if spec.supports(Engine.CLOUD)
        ]

    async def on_list_tools(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self.tools())

    # -- invocation -------------------------------------------------------

    async def on_call_tool(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        spec = self._spec_for(params.name)
        owner = _owner()
        arguments = dict(params.arguments or {})

        try:
            plan = self._plan(spec, arguments)
            job = self._run(spec, plan, owner=owner, base_url=_base_url(ctx))
        except DocMaxError as exc:
            return _failure(exc)

        return _result(job)

    def _spec_for(self, name: str) -> ToolSpec:
        """Resolve the tool, or fail on the protocol's own rung.

        `tools/list` already excludes anything without a cloud engine, so a
        call naming one anyway is a client that ignored discovery — a
        protocol error, not a document one. Mirrors
        `docmax/mcp/server.py::DocMaxServer._spec_for`.
        """
        try:
            return self._runner.resolve(name)
        except DocMaxError as exc:
            raise MCPError(types.METHOD_NOT_FOUND, f"Unknown tool: {name}") from exc

    def _plan(self, spec: ToolSpec, arguments: dict[str, Any]) -> _Plan:
        declared = {param.name for param in spec.params}
        unknown = sorted(set(arguments) - declared - {INPUTS, OUTPUT})
        if unknown:
            raise InvalidParameterError(
                f"Unknown argument{'s' if len(unknown) > 1 else ''}: "
                + ", ".join(repr(name) for name in unknown),
                remedy=f"{spec.name!r} accepts: " + ", ".join(sorted(declared | {INPUTS, OUTPUT})),
                context={"tool": spec.name, "keys": unknown},
            )

        raw_inputs = arguments.get(INPUTS)
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise InvalidParameterError(
                f"{INPUTS!r} must be a non-empty array containing one file_id.",
                remedy="Upload the document first with POST /v1/uploads, then pass its file_id.",
                context={"tool": spec.name},
            )
        if len(raw_inputs) != 1:
            # The job model behind this route carries one `file_id` per job —
            # a limitation of `docmax.server`'s execution model today, not of
            # this tool's own `accepts_multiple_inputs`. Named rather than
            # silently truncated to the first entry.
            raise InvalidParameterError(
                f"{spec.name!r} runs one document per call over this endpoint.",
                remedy="Call it once per document.",
                context={"tool": spec.name},
            )
        file_id = raw_inputs[0]
        if not isinstance(file_id, str) or not file_id:
            raise InvalidParameterError(
                f"{INPUTS!r} must contain a file_id string.",
                context={"tool": spec.name},
            )

        params = {name: value for name, value in arguments.items() if name in declared}
        return _Plan(file_id=file_id, params=params)

    def _run(self, spec: ToolSpec, plan: _Plan, *, owner: str, base_url: str) -> Job:
        """The one place this module calls the runner.

        `storage.get`/`.filename` check `owner` against the id's own
        reservation in the same lookup that finds it — a caller naming a
        `file_id` it did not upload gets the identical "nothing has been
        uploaded" error a bad id would raise. See `storage.py` and ADR 0035.
        """
        payload = self._storage.get(plan.file_id, owner=owner)
        filename = self._storage.filename(plan.file_id, owner=owner)
        job = self._jobs.create(spec.name, file_id=plan.file_id, params=plan.params, owner=owner)
        return self._runner.start(
            job,
            payload,
            filename=filename,
            base_url=base_url,
            storage=self._storage,
            owner=owner,
        )


class _Plan:
    """A checked call: the one input file_id and the parameters the tool declared."""

    __slots__ = ("file_id", "params")

    def __init__(self, *, file_id: str, params: dict[str, Any]) -> None:
        self.file_id = file_id
        self.params = params


def _owner() -> str:
    """The identity `RequireAuthMiddleware` already verified for this call.

    Always present by construction: nothing reaches a handler without passing
    that middleware first. Raised as a protocol-level error rather than
    silently falling back to no owner, which would turn a bug in the transport
    wiring into a missing ownership check instead of a loud failure.
    """
    token = get_access_token()
    if token is None:  # pragma: no cover - guarded by RequireAuthMiddleware
        raise MCPError(types.INTERNAL_ERROR, "No authenticated caller for this call.")
    return token.client_id


def _base_url(ctx: ServerRequestContext[Any, Any]) -> str:
    """The request's own base URL, so a server reachable by two names hands
    each caller a URL on the name they used — the same reasoning
    `routes/tools.py` gives for taking this from the request rather than from
    configuration.
    """
    request = ctx.request
    if request is None:  # pragma: no cover - always set by the HTTP transport
        raise MCPError(types.INTERNAL_ERROR, "This call has no associated HTTP request.")
    return str(request.base_url)


def _result(job: Job) -> types.CallToolResult:
    """The job's own wire payload — `job.to_payload()`, unmodified.

    Reusing it rather than inventing an MCP-shaped result is the point: a
    tool call over this route and a call to `POST /v1/tools/{name}` describe
    the same job in the same words.
    """
    payload = job.to_payload()
    if job.status is JobStatus.FAILED:
        error = job.error
        line = error.message if error else "The job failed."
        if error and error.remedy:
            line += f"\n{error.remedy}"
        return types.CallToolResult(
            content=[types.TextContent(text=line)],
            structured_content=payload,
            is_error=True,
        )
    output = payload.get("output")
    line = f"{job.tool}: {output['url']}" if output else f"{job.tool}: nothing to write"
    return types.CallToolResult(content=[types.TextContent(text=line)], structured_content=payload)


def _failure(exc: DocMaxError) -> types.CallToolResult:
    """The same error envelope the REST route puts in a job's `error` field."""
    envelope = exc.to_dict()
    line = exc.message + (f"\n{exc.remedy}" if exc.remedy else "")
    return types.CallToolResult(
        content=[types.TextContent(text=line)],
        structured_content={"ok": False, "error": envelope},
        is_error=True,
    )


class RequireHTTPSMiddleware:
    """Refuses a plaintext connection that is not to loopback.

    Bearer tokens flow over this route to clients this deployment's operator
    may not control end to end — unlike `docmax`'s own `CloudClient`, which
    already refuses a plaintext non-local endpoint itself
    (`docs/cloud-api.md`), a third-party MCP client has no reason to have made
    the same choice. This applies the same rule at the server, for the one
    route built for exactly that kind of caller.

    Placed outermost, before authentication: a credential is not evaluated
    over a channel that should not have received it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scheme = scope.get("scheme", "http")
        client = scope.get("client")
        host = client[0] if client else None
        if scheme == "https" or host in _LOOPBACK_HOSTS:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {
                "error": "https_required",
                "error_description": "This endpoint requires TLS, except from loopback.",
            },
            status_code=400,
        )
        await response(scope, receive, send)


@dataclass(slots=True)
class McpAsgiApp:
    """The mountable app, and the lifespan it needs entered around requests.

    `app` is a plain ASGI callable, not a `Starlette` instance with its own
    routing: `app.py` registers it as a Starlette `Route` at the exact path
    `/v1/mcp`, not with `FastAPI.mount()` — `Mount` matches its path as a
    directory prefix and 307-redirects a bare hit on it to add a trailing
    slash, which would make the documented endpoint `/v1/mcp/`. A `Route`
    whose endpoint is a plain callable (not a function or method) dispatches
    to it directly as ASGI, with no prefix matching and no such redirect.

    `StreamableHTTPSessionManager.run()` must be entered once, for the whole
    process, before any request reaches it — and FastAPI does not enter a
    mounted sub-app's own lifespan automatically, so `app.py` enters
    `session_manager` here as part of the *outer* app's own lifespan rather
    than relying on ASGI to do it for a `Mount`.
    """

    app: ASGIApp
    session_manager: StreamableHTTPSessionManager


def build_mcp_asgi_app(
    *, storage: Storage, jobs: JobStore, runner: ToolRunner, settings: ServerSettings
) -> McpAsgiApp:
    """Wire the Cloud Engine into an MCP-over-HTTP app, mountable at `/v1/mcp`."""
    facade = DocMaxCloudMCP(storage=storage, jobs=jobs, runner=runner)
    lowlevel = Server(
        SERVER_NAME,
        version=_version(),
        title=APP_NAME,
        instructions=(
            f"{APP_NAME} Cloud Engine tools, over MCP. Every call runs on this deployment's machine."
        ),
        on_list_tools=facade.on_list_tools,
        on_call_tool=facade.on_call_tool,
    )

    session_manager = StreamableHTTPSessionManager(app=lowlevel, json_response=True)
    verifier = ApiKeyVerifier(accepted=settings.api_keys)

    # Built as a plain chain of ASGI callables, innermost first, rather than a
    # `Starlette(routes=[...])` wrapper: there is exactly one endpoint here,
    # and layering middleware by hand keeps the order explicit and avoids a
    # second router with its own path-matching and trailing-slash rules for a
    # mount that already owns its whole subtree. Order matters: each layer
    # wraps the one before it, so the *last* one applied is the *first* one a
    # request reaches.
    chain: ASGIApp = StreamableHTTPASGIApp(session_manager)
    chain = RequireAuthMiddleware(chain, required_scopes=[])
    chain = AuthContextMiddleware(chain)
    chain = AuthenticationMiddleware(chain, backend=BearerAuthBackend(verifier))
    # Outermost: a credential is refused transport before it is evaluated.
    chain = RequireHTTPSMiddleware(chain)

    return McpAsgiApp(app=chain, session_manager=session_manager)


def _version() -> str:
    from docmax import __version__

    return __version__


__all__ = [
    "MOUNT_PATH",
    "SERVER_NAME",
    "DocMaxCloudMCP",
    "McpAsgiApp",
    "RequireHTTPSMiddleware",
    "build_mcp_asgi_app",
]
