"""The HTTP client for the Cloud Engine.

Deliberately thin. It knows the wire contract in ``docs/cloud-api.md`` and
nothing else: it does not decide whether cloud should be used (the router does,
after checking consent), it does not know what a tool is beyond its name, and it
does not write files — :meth:`CloudClient.fetch_output` hands back bytes for the
caller to put through ``core/atomic.py``, because there is exactly one module in
this project permitted to touch a destination path.

Three behaviours are contract requirements rather than implementation choices:

* **Idempotency.** The key is derived from the input digest and the parameters,
  so a retry after a dropped connection returns the original result instead of
  re-running the job and billing it twice.
* **Server-controlled polling.** The client sleeps for ``poll_after_ms`` rather
  than choosing an interval, so a busy endpoint can slow its callers down.
* **Retries only when retrying can help.** ``retryable`` decides. Re-sending a
  bad API key or an exhausted quota just wastes the user's time.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx

from docmax import __version__
from docmax.cloud_client.config import CloudConfig
from docmax.cloud_client.errors import raise_for_error
from docmax.cloud_client.models import (
    Capabilities,
    CloudJob,
    JobStatus,
    UploadTicket,
    as_mapping,
)
from docmax.core.branding import CLI_NAME
from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.errors import (
    CloudAuthError,
    CloudEngineUnavailableError,
    CloudProtocolError,
    CloudTimeoutError,
    DocMaxError,
    InternalError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef

IDEMPOTENCY_HEADER = "Idempotency-Key"

#: How long to keep waiting for one job before giving up on it entirely.
#:
#: ``CloudConfig.read_timeout`` must be at least this, because the reference
#: server answers synchronously (ADR 0016) and a long job is a long response.
DEFAULT_JOB_TIMEOUT = 900.0

#: The longest this sleeps without looking at the cancellation token.
#:
#: The server chooses ``poll_after_ms`` and that is honoured in aggregate; this
#: only decides how finely the wait is chopped up. A cooperative token cannot
#: interrupt ``time.sleep``, so without this a Ctrl-C during a job with a
#: two-second poll interval would sit unnoticed for up to two seconds -- and
#: with a server that asked for sixty, for a minute. ``core/cancellation.py``
#: says "nothing here starts a thread", so the answer is to look often rather
#: than to be woken.
_CANCEL_CHECK_INTERVAL = 0.1

_CHUNK = 1 << 20


def user_agent() -> str:
    """Identify the client, its Python, and its platform — nothing else."""
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    return f"{CLI_NAME}/{__version__} (python/{python}; {sys.platform})"


class CloudClient:
    """One configured endpoint, one connection pool.

    Constructing this opens no socket and makes no request, so the router can
    build one to ask a cheap question and throw it away.
    """

    def __init__(
        self,
        config: CloudConfig | None = None,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self.config = config or CloudConfig.from_env()
        #: Injectable so the test suite can drive the whole client through a
        #: mock transport, which is how the contract is verified without a
        #: server existing.
        self._http_client = http
        self._capabilities: Capabilities | None = None

    # -- lifecycle ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._http_client is None:
            if not self.config.is_configured:
                raise CloudAuthError("No API key is configured for the cloud endpoint.")
            self._http_client = httpx.Client(
                base_url=self.config.endpoint,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "User-Agent": user_agent(),
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(
                    self.config.read_timeout,
                    connect=self.config.connect_timeout,
                ),
            )
        return self._http_client

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- discovery ---------------------------------------------------------

    def capabilities(self, *, refresh: bool = False) -> Capabilities:
        """What this endpoint offers. Fetched once, then cached.

        A self-hosted server implementing three of the five cloud tools is a
        supported deployment, so "this endpoint cannot do that" is answered
        before a document is uploaded rather than by a failed job.
        """
        if self._capabilities is None or refresh:
            payload = self._request("GET", "/v1/capabilities")
            self._capabilities = Capabilities.from_payload(payload)
        return self._capabilities

    # -- running a tool ----------------------------------------------------

    def run(
        self,
        tool: str,
        doc: DocumentRef,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_JOB_TIMEOUT,
        cancellation: CancellationToken = NEVER_CANCELLED,
    ) -> CloudJob:
        """Submit ``doc`` and block until the job reaches a terminal state.

        ``cancellation`` defaults to the shared do-nothing token, mirroring
        ``EngineRouter.run``: a caller with nothing to cancel still hands over a
        real object, so no branch here has to check for ``None``.
        """
        cancellation.raise_if_cancelled(operation=tool)
        return self.wait(self.submit(tool, doc, params), timeout=timeout, cancellation=cancellation)

    def submit(
        self,
        tool: str,
        doc: DocumentRef,
        params: Mapping[str, Any] | None = None,
    ) -> CloudJob:
        """Send the work, taking whichever path the payload size calls for."""
        resolved = dict(params or {})
        if doc.size_bytes <= self._sync_limit():
            return self._submit_sync(tool, doc, resolved)
        return self._submit_large(tool, doc, resolved)

    def poll(self, job_id: str) -> CloudJob:
        """Ask after one job. Raises if the server reports it failed."""
        body = self._request("GET", f"/v1/jobs/{job_id}")
        if body.get("status") == JobStatus.FAILED.value:
            raise_for_error(500, body)
        return CloudJob.from_payload(body)

    def wait(
        self,
        job: CloudJob,
        *,
        timeout: float = DEFAULT_JOB_TIMEOUT,
        cancellation: CancellationToken = NEVER_CANCELLED,
    ) -> CloudJob:
        """Poll at the server's requested interval until the job settles.

        Interruptible, which is the whole of ADR 0015. The deadline is the
        earlier of ``timeout`` and whatever the token carries, so a deadline set
        once at the top of an operation bounds a subprocess and a cloud job the
        same way -- ``_binaries.run`` already reads ``remaining_seconds`` for
        exactly this.

        Cancelling stops DocMax waiting; it does not stop the server working.
        The wire contract has no cancel endpoint, so the job runs to completion
        over there and may be billed. That is stated in ``docmax cloud --help``
        rather than being quietly true.
        """
        deadline = time.monotonic() + _effective_timeout(timeout, cancellation)
        current = job
        while not current.is_terminal:
            cancellation.raise_if_cancelled(operation="cloud")
            if time.monotonic() >= deadline:
                raise CloudTimeoutError(
                    f"The job did not finish within {timeout:.0f}s.",
                    remedy="Try again, or run locally with --engine local.",
                    context={"job_id": current.job_id},
                )
            self._sleep(current.poll_after_ms / 1000, cancellation)
            current = self.poll(current.job_id)
        return current

    @staticmethod
    def _sleep(seconds: float, cancellation: CancellationToken) -> None:
        """Wait, in slices, looking at the token between them.

        Cut short by a cancel rather than raising here: the caller's loop checks
        immediately afterwards, so there is one place that decides what a cancel
        means instead of two.
        """
        remaining = seconds
        while remaining > 0:
            if cancellation.is_cancelled:
                return
            slice_ = min(_CANCEL_CHECK_INTERVAL, remaining)
            time.sleep(slice_)
            remaining -= slice_

    def fetch_output(self, job: CloudJob) -> bytes:
        """Download a finished job's document.

        Returns bytes rather than writing them. The caller hands these to
        ``core/atomic.py``, which is the only module allowed to touch a
        destination path — so a cloud result is delivered under exactly the same
        crash-safety guarantee as a local one.
        """
        if job.output is None:
            raise CloudProtocolError(
                "The job reported success but carried no output.",
                context={"job_id": job.job_id},
            )
        return self._storage("GET", job.output.url).content

    # -- the two submission paths ------------------------------------------

    def _submit_sync(self, tool: str, doc: DocumentRef, params: dict[str, Any]) -> CloudJob:
        """Small payloads: one multipart request, answered when the work is done."""
        body = self._request(
            "POST",
            f"/v1/tools/{tool}",
            files={"file": (doc.path.name, doc.path.read_bytes(), "application/octet-stream")},
            data={"params": json.dumps(params, sort_keys=True)},
            headers={IDEMPOTENCY_HEADER: idempotency_key(doc, params)},
        )
        return CloudJob.from_payload(body)

    def _submit_large(self, tool: str, doc: DocumentRef, params: dict[str, Any]) -> CloudJob:
        """Large payloads: presigned upload, then a job to poll."""
        ticket = UploadTicket.from_payload(
            self._request(
                "POST",
                "/v1/uploads",
                json={"filename": doc.path.name, "size_bytes": doc.size_bytes},
            )
        )
        self._upload(ticket, doc)
        body = self._request(
            "POST",
            f"/v1/tools/{tool}",
            json={"file_id": ticket.file_id, "params": params},
            headers={IDEMPOTENCY_HEADER: idempotency_key(doc, params)},
        )
        return CloudJob.from_payload(body)

    def _upload(self, ticket: UploadTicket, doc: DocumentRef) -> None:
        """PUT the bytes straight to storage, never through the API."""
        self._storage("PUT", ticket.upload_url, content=doc.path.read_bytes())

    def _storage(self, method: str, url: str, *, content: bytes | None = None) -> httpx.Response:
        """Transfer bytes to or from the storage host, through a bare client.

        Deliberately not the authenticated one: a presigned URL points at
        storage, not at the API, and our bearer token has no business being
        sent there.
        """
        with httpx.Client(timeout=self._timeout()) as storage:
            try:
                response = storage.request(method, url, content=content)
            except httpx.TimeoutException as exc:
                raise CloudTimeoutError(
                    f"The transfer to storage timed out: {exc}",
                    context={"url": url},
                ) from exc
            except httpx.TransportError as exc:
                raise CloudEngineUnavailableError(
                    f"The transfer to storage failed: {exc}",
                    context={"url": url},
                ) from exc

        if response.status_code >= 400:
            raise_for_error(response.status_code, None)
        return response

    def _sync_limit(self) -> int:
        """The server's own threshold when we know it, ours otherwise.

        Never fetches capabilities just to answer this: a size check must not
        cost a round trip.
        """
        if self._capabilities is not None:
            return self._capabilities.max_sync_bytes
        return self.config.max_sync_bytes

    # -- transport ---------------------------------------------------------

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.config.read_timeout, connect=self.config.connect_timeout)

    def _request(self, method: str, url: str, **kwargs: Any) -> Mapping[str, Any]:
        """One request, retried while retrying can plausibly help."""
        attempts = self.config.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return as_mapping(_decode(self._raw(method, url, **kwargs)))
            except DocMaxError as exc:
                if not exc.retryable or attempt == attempts:
                    raise
                time.sleep(_backoff(attempt))
        raise InternalError("The retry loop finished without a result or an error.")

    def _raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one request and turn transport and status failures into typed errors."""
        try:
            response = self._http().request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise CloudTimeoutError(
                f"The cloud endpoint did not respond within {self.config.read_timeout:.0f}s.",
                context={"url": url},
            ) from exc
        except httpx.TransportError as exc:
            raise CloudEngineUnavailableError(
                f"Could not reach the cloud endpoint: {exc}",
                remedy="Check your connection, or run locally with --engine local.",
                context={"url": url},
            ) from exc

        if response.status_code >= 400:
            raise_for_error(response.status_code, _decode(response, strict=False))
        return response


def idempotency_key(doc: DocumentRef, params: Mapping[str, Any]) -> str:
    """Digest the input and the parameters, so a retry cannot run the job twice.

    Same document plus same parameters means same key means the server returns
    the original result. Anything that would change the output — a different
    page range, a different language — changes the key.
    """
    digest = hashlib.sha256()
    with doc.path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    digest.update(json.dumps(params, sort_keys=True, separators=(",", ":")).encode())
    return f"sha256:{digest.hexdigest()}"


def _decode(response: httpx.Response, *, strict: bool = True) -> object:
    """Decode a JSON body, or say so in the contract's own terms."""
    try:
        return response.json()
    except ValueError as exc:
        if not strict:
            return None
        raise CloudProtocolError(
            "The cloud endpoint returned a body that is not JSON.",
            remedy="Check that the configured endpoint speaks this API version.",
            context={"content_type": response.headers.get("content-type", "")},
        ) from exc


def _effective_timeout(timeout: float, cancellation: CancellationToken) -> float:
    """The earlier of the caller's budget and the token's deadline.

    A token with no deadline returns ``None`` from ``remaining_seconds``, in
    which case the caller's own timeout is the only bound -- and there is always
    one, because there is no way to call this without it.
    """
    remaining = cancellation.remaining_seconds()
    if remaining is None:
        return timeout
    return min(timeout, remaining)


def _backoff(attempt: int) -> float:
    """Exponential, with jitter so concurrent clients do not resynchronise."""
    base = min(2.0 ** (attempt - 1), 30.0)
    # secrets, not random: not for secrecy, but because ruff's bandit rules
    # (correctly) flag the stdlib PRNG and this is the cheaper answer.
    return base + secrets.randbelow(1000) / 1000


__all__ = ["DEFAULT_JOB_TIMEOUT", "CloudClient", "idempotency_key", "user_agent"]
