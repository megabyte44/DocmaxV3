"""The cloud half of a tool, written once.

Every cloud strategy does the same five things in the same order:

    upload -> wait -> fetch -> validate -> write atomically

Only two things differ between tools: which tool name to send, and which
validators to run over what comes back. So the flow lives here and a tool's
``cloud.py`` supplies those two, exactly as ``_pagespec``, ``_position``,
``_permissions`` and ``_formats`` own the vocabularies their tools share.

A directory-producing tool (``ToolSpec.produces_directory``, ADR 0031 —
``to-images`` is the first cloud tool shaped this way) supplies a third: it says
so, and "fetch, validate, write" becomes "fetch, unzip, validate, write" —
``tools/_archive.py`` owns the zip shape, this module still owns when it runs.

## What this module is not allowed to decide

**Whether to use cloud at all.** That is ``EngineRouter``'s, and it is settled
before this code is reached: ``offline`` makes cloud unreachable regardless of
flags, and no route to cloud passes without a recorded per-tool consent. Nothing
here re-checks either, because a second implementation of a rule that must never
differ is how the two come to disagree — and the copy that runs in tests would
not be the copy that matters.

**How to talk to the endpoint.** That is ``cloud_client``'s. This module knows
`upload, wait, fetch`; it does not know HTTP, retries, idempotency or polling.

## The result is validated the same way a local one is

``fetch_output`` returns bytes, and they go to ``core/atomic.py`` with the
tool's *own* validators — the same functions the local engine passes. A cloud
`compress` that came back with a page missing is caught by the check that
catches it locally, and the destination is untouched either way. That symmetry
is the point of the dual-engine design: one result type, one set of guarantees,
two places the work can happen.

## Configuration

The endpoint and key come from the resolved ``core.config.Config`` and not from
the environment alone — [ADR 0013](../../../docs/adr/0013-cloud-config-comes-from-the-resolved-config.md),
written because ``[cloud] endpoint`` was documented, parsed, and then ignored.
Resolved once per strategy instance and memoised, because ``is_available`` is
asked on every routing decision and must not read a file each time.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from docmax.cloud_client import CloudClient
    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import ProgressSink, Validator

    #: Built at call time from the inputs and parameters, because what to check
    #: usually depends on them -- ``compress`` needs the source's page count,
    #: which it only knows once it has opened the source.
    ValidatorFactory = Callable[
        [Sequence[DocumentRef], Mapping[str, Any]],
        Sequence[Validator],
    ]


class CloudEngine:
    """A tool's cloud strategy: the shared flow, plus this tool's two details.

    Composition rather than a base class. ``EngineStrategy`` is a structural
    protocol precisely so an implementation never has to inherit anything, and a
    tool's ``cloud.py`` builds one of these rather than subclassing it.
    """

    def __init__(
        self,
        tool: str,
        *,
        validators: ValidatorFactory | None = None,
        client: CloudClient | None = None,
        produces_directory: bool = False,
    ) -> None:
        self._tool = tool
        self._validators = validators
        #: Injectable so tests drive the whole strategy through a mock
        #: transport. Left alone, it is built on first use from the user's
        #: resolved configuration.
        self._client = client
        #: Mirrors ``ToolSpec.produces_directory`` (ADR 0031) -- the tool's own
        #: ``cloud.py`` sets this because it already knows the shape of its
        #: output, the same way it already supplies ``validators``. When set,
        #: the job's output bytes are a zip archive (``tools/_archive.py``)
        #: rather than the file itself, because the wire contract carries
        #: exactly one file per job and a directory of many has no single file
        #: to be.
        self._produces_directory = produces_directory

    # -- availability ------------------------------------------------------

    def is_available(self) -> bool:
        """Configured with an API key.

        Deliberately not a reachability check. Availability is asked on every
        routing decision, including the ones that end up choosing local, and a
        network round trip there would make every command slower to answer a
        question the request itself answers.
        """
        return self.client.config.is_configured

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        from docmax.core.branding import CLI_NAME

        return (
            "No API key is configured for the cloud endpoint. "
            f"Run `{CLI_NAME} cloud login` to set one."
        )

    @property
    def client(self) -> CloudClient:
        """The client for this run, built once from the resolved configuration."""
        if self._client is None:
            from docmax.cloud_client import CloudClient, CloudConfig
            from docmax.core.config import load

            self._client = CloudClient(CloudConfig.from_core(load()))
        return self._client

    # -- the flow ----------------------------------------------------------

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Send ``docs[0]``, wait for it, and write what comes back.

        A failure here is reported as a cloud failure. It is **not** retried
        locally: the router chose one engine before any work started, and
        silently re-running would double the work on a document the user has
        already paid to upload while making ``engine_used`` a guess rather than
        a fact. See ADR 0012.
        """
        from docmax.core.atomic import atomic_dir, atomic_write

        if not docs:
            raise InvalidParameterError(
                f"{self._tool} needs a document.",
                remedy="Pass the file to send.",
            )

        payload = _wire_params(params)
        document = docs[0]

        # Built before the upload, so a document that cannot produce validators
        # -- an unreadable PDF -- fails here rather than after a round trip.
        checks = self._validators(docs, payload) if self._validators else ()

        started = time.monotonic()
        cancellation.raise_if_cancelled(operation=self._tool)

        # One indeterminate step. The endpoint reports nothing about its own
        # progress until the job is done, and inventing a percentage would be a
        # lie the user cannot check.
        progress.start(f"Running {self._tool} on {self.client.config.endpoint}", total=None)

        job = self.client.run(self._tool, document, payload, cancellation=cancellation)
        cancellation.raise_if_cancelled(operation=self._tool)

        output = self.client.fetch_output(job)
        progress.advance()

        if self._produces_directory:
            from docmax.tools._archive import unzip_into

            with atomic_dir(target, validators=checks) as staged:
                unzip_into(output, staged)
            outputs = tuple(sorted(target.destination.iterdir()))
        else:
            with atomic_write(target, validators=checks) as handle:
                handle.write(output)
            outputs = (target.destination,)

        return ToolResult(
            outputs=outputs,
            engine_used=Engine.CLOUD,
            duration_ms=job.duration_ms or int((time.monotonic() - started) * 1000),
            engine_version=job.engine_version,
            details={
                "tool": self._tool,
                "endpoint": self.client.config.endpoint,
                "job_id": job.job_id,
                "output_bytes": len(output),
                # The parameters as sent -- the endpoint, never the key.
                # `details` travels into logs and into `--json`, and ADR 0014
                # makes "no credential in output" a rule with tests behind it.
                **payload,
            },
        )


def _wire_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """The parameters to send, with the absent ones left out.

    A ``None`` from an unset CLI option means "no preference", and the server
    has its own defaults from the same ``ToolSpec``. Sending ``null`` would
    override those defaults with nothing, which is a different request from the
    one the user made.
    """
    return {key: value for key, value in params.items() if value is not None}


__all__ = ["CloudEngine"]
