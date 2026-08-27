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

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from docmax import __version__
from docmax.core.branding import APP_NAME, HOMEPAGE
from docmax.server.config import API_VERSION, ServerSettings
from docmax.server.errors import install_error_handlers
from docmax.server.execution import RegistryRunner
from docmax.server.jobs import InMemoryJobStore
from docmax.server.routes import capabilities, jobs, outputs, tools, uploads
from docmax.server.storage import InMemoryStorage

if TYPE_CHECKING:
    from docmax.server.execution import ToolRunner
    from docmax.server.jobs import JobStore
    from docmax.server.storage import Storage

API_PREFIX = "/v1"


def create_app(
    settings: ServerSettings | None = None,
    *,
    storage: Storage | None = None,
    job_store: JobStore | None = None,
    runner: ToolRunner | None = None,
) -> FastAPI:
    """Build one server.

    The backends are parameters because the reference ones are in-memory and
    single-process. A real deployment passes object storage and a shared job
    store; nothing above this line changes when it does.
    """
    resolved = settings or ServerSettings.from_env()

    app = FastAPI(
        title=f"{APP_NAME} Cloud Engine",
        version=__version__,
        summary="Run document operations without installing their dependencies.",
        description=(
            "The reference implementation of the Cloud Engine contract. "
            f"Every operation it offers also runs locally. See {HOMEPAGE}"
        ),
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    app.state.settings = resolved
    app.state.storage = storage or InMemoryStorage(max_bytes=resolved.max_upload_bytes)
    app.state.jobs = job_store or InMemoryJobStore()
    app.state.runner = runner or RegistryRunner()

    install_error_handlers(app)

    for router in (capabilities.router, tools.router, uploads.router, jobs.router, outputs.router):
        app.include_router(router, prefix=API_PREFIX)

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
