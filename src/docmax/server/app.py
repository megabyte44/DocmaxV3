"""The application factory.

A factory rather than a module-level ``app``, because a test needs to build one
with its own settings, its own storage, and a stub runner — and because an
application object created as an import side effect is the kind of thing that
makes a test suite depend on import order.

Everything a request needs hangs off ``app.state``: settings, storage, the job
store, and the runner. Swapping any of them for a test double is an assignment,
not a patch.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from starlette.routing import Route

from docmax import __version__
from docmax.core.branding import APP_NAME, HOMEPAGE
from docmax.server.config import API_VERSION, ServerSettings
from docmax.server.errors import install_error_handlers
from docmax.server.execution import RegistryRunner
from docmax.server.identity import SqliteIdentityStore
from docmax.server.jobs import InMemoryJobStore
from docmax.server.routes import capabilities, jobs, outputs, tools, uploads
from docmax.server.routes.mcp import MOUNT_PATH as MCP_MOUNT_PATH
from docmax.server.routes.mcp import build_mcp_asgi_app
from docmax.server.storage import InMemoryStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from docmax.server.execution import ToolRunner
    from docmax.server.identity import IdentityStore
    from docmax.server.jobs import JobStore
    from docmax.server.storage import Storage

API_PREFIX = "/v1"


def create_app(
    settings: ServerSettings | None = None,
    *,
    storage: Storage | None = None,
    job_store: JobStore | None = None,
    runner: ToolRunner | None = None,
    identity: IdentityStore | None = None,
) -> FastAPI:
    """Build one server.

    The backends are parameters because the reference ones are in-memory and
    single-process. A real deployment passes object storage and a shared job
    store; nothing above this line changes when it does.

    ``identity`` follows the same pattern as the others, with one difference:
    its *absence* is a normal, supported deployment shape
    ([ADR 0037](../../../docs/adr/0037-server-token-identity.md)), not
    something every caller must supply a stub for. Passing nothing here and
    setting nothing in ``settings.identity_db_path`` leaves the server on
    ``api_keys`` alone, exactly as it behaved before this parameter existed.
    """
    resolved = settings or ServerSettings.from_env()
    resolved_storage = storage or InMemoryStorage(max_bytes=resolved.max_upload_bytes)
    resolved_jobs = job_store or InMemoryJobStore()
    resolved_runner = runner or RegistryRunner()
    resolved_identity = identity or (
        SqliteIdentityStore(resolved.identity_db_path) if resolved.identity_db_path else None
    )

    mcp = build_mcp_asgi_app(
        storage=resolved_storage,
        jobs=resolved_jobs,
        runner=resolved_runner,
        settings=resolved,
        identity=resolved_identity,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # `StreamableHTTPSessionManager.run()` must be entered once for the
        # process's lifetime before any request reaches it. FastAPI does not
        # enter a `Mount`-ed sub-app's own `lifespan` automatically -- this is
        # the one place that has to do it by hand. See `routes/mcp.py::McpAsgiApp`.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title=f"{APP_NAME} Cloud Engine",
        version=__version__,
        summary="Run document operations without installing their dependencies.",
        description=(
            "The reference implementation of the Cloud Engine contract. "
            f"Every operation it offers also runs locally. See {HOMEPAGE}"
        ),
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    app.state.settings = resolved
    app.state.storage = resolved_storage
    app.state.jobs = resolved_jobs
    app.state.runner = resolved_runner
    app.state.identity = resolved_identity

    install_error_handlers(app)

    for router in (capabilities.router, tools.router, uploads.router, jobs.router, outputs.router):
        app.include_router(router, prefix=API_PREFIX)

    # A `Route`, not `app.mount()`: `Mount` matches its path as a directory
    # prefix and 307-redirects a bare hit on it to add a trailing slash, which
    # would make the documented endpoint `/v1/mcp/`, not `/v1/mcp`, and would
    # ask an MCP client to follow a redirect on every call. `chain` is a plain
    # ASGI callable (an instance, not a function or method), which `Route`
    # recognises and dispatches to directly, with no prefix matching at all.
    app.router.routes.append(
        Route(f"{API_PREFIX}{MCP_MOUNT_PATH}", mcp.app, methods=["GET", "POST", "DELETE"])
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        """Liveness only. Says nothing about the tools, on purpose.

        A health check that reports on dependencies gets used as a monitoring
        endpoint and then answers slowly under exactly the conditions where it
        needs to answer fast.
        """
        return {"ok": True, "api_version": API_VERSION}

    return app


__all__ = ["API_PREFIX", "create_app"]
