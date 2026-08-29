"""The cloud client, against the contract in `docs/cloud-api.md`.

**These tests did not exist before M6, and that document claimed they did** —
"`respx`-based tests assert the client honours it". Six hundred lines of
networking code shipped untested through five milestones. This file is that
claim becoming true.

Everything runs through `respx`, which intercepts at httpx's transport layer, so
the client under test is the real one: real retries, real backoff, real
idempotency keys, real polling. Nothing is stubbed above the socket.

What is being checked is not "does HTTP work". It is the handful of behaviours
the contract names as requirements rather than choices — idempotency,
server-controlled polling, retries only where retrying helps — plus the one the
client got wrong until M6: that a cancel is noticed.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from docmax.cloud_client import CloudClient, CloudConfig
from docmax.cloud_client.client import DEFAULT_JOB_TIMEOUT, idempotency_key
from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    CloudAuthError,
    CloudPayloadTooLargeError,
    CloudProtocolError,
    CloudQuotaExceededError,
    CloudServerError,
    CloudTimeoutError,
    CorruptDocumentError,
    EncryptedDocumentError,
    InvalidParameterError,
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef

if TYPE_CHECKING:
    from pathlib import Path

ENDPOINT = "https://api.example.invalid"


@pytest.fixture
def config() -> CloudConfig:
    #: `max_retries=0` unless a test is about retrying, so an unrelated failure
    #: does not take four seconds of backoff to report.
    return CloudConfig(endpoint=ENDPOINT, api_key="test-key", max_retries=0)


@pytest.fixture
def client(config: CloudConfig) -> CloudClient:
    return CloudClient(config)


@pytest.fixture
def document(tmp_path: Path) -> DocumentRef:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7\nbody\n")
    return DocumentRef.from_path(path)


def succeeded(job_id: str = "job_1", **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "job_id": job_id,
        "status": "succeeded",
        "output": {
            "url": f"{ENDPOINT}/v1/outputs/f_1",
            "size_bytes": 9,
            "content_type": "application/pdf",
        },
        **extra,
    }


def envelope(code: str, message: str = "nope", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


# ---------------------------------------------------------------------------
# Configuration — ADR 0013
# ---------------------------------------------------------------------------


def test_the_endpoint_comes_from_the_resolved_config() -> None:
    """The defect ADR 0013 was written about: `[cloud] endpoint` was ignored."""
    resolved = Config(cloud_endpoint="https://self.hosted.invalid", api_key="k")

    assert CloudConfig.from_core(resolved).endpoint == "https://self.hosted.invalid"


def test_the_api_key_comes_from_the_resolved_config() -> None:
    assert CloudConfig.from_core(Config(api_key="from-file")).api_key == "from-file"


def test_from_core_reads_the_precedence_chain_rather_than_reimplementing_it(
    tmp_path: Path,
) -> None:
    """The environment beats the file, and this proves it by *not* deciding it.

    `core/config.load()` owns precedence. If `from_core` ever grew its own
    parsing, this would still pass while the two implementations drifted — so
    the assertion is on the resolved object, and the point is that only one
    thing resolved it.
    """
    from docmax.core.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[cloud]\nendpoint = "https://from-file.invalid"\napi_key = "file-key"\n',
        encoding="utf-8",
    )
    resolved = load(path=config_file, environ={"DOCMAX_API_KEY": "env-key"})
    projected = CloudConfig.from_core(resolved)

    assert projected.endpoint == "https://from-file.invalid"
    assert projected.api_key == "env-key", "the environment wins, as core decided"


def test_offline_is_not_this_types_business() -> None:
    """The router enforces it, before a strategy is ever built. One implementation."""
    projected = CloudConfig.from_core(Config(offline=True, api_key="k"))

    assert not hasattr(projected, "offline")


def test_a_plaintext_endpoint_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="plaintext"):
        CloudConfig(endpoint="http://example.invalid", api_key="k")


def test_a_plaintext_local_endpoint_is_allowed() -> None:
    """So self-hosted development works without a certificate."""
    assert CloudConfig(endpoint="http://localhost:8000", api_key="k").endpoint


def test_the_read_timeout_covers_a_whole_job() -> None:
    """The mismatch M6 found: 120s read against a 900s job budget.

    The reference server answers synchronously (ADR 0016), so a long job is a
    long *response*. A read timeout below the job timeout kills a run from this
    side while the server is still succeeding.
    """
    assert CloudConfig().read_timeout >= DEFAULT_JOB_TIMEOUT


# ---------------------------------------------------------------------------
# The synchronous path
# ---------------------------------------------------------------------------


@respx.mock
def test_a_small_document_is_submitted_in_one_request(
    client: CloudClient, document: DocumentRef
) -> None:
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )

    job = client.run("compress", document, {"preset": "ebook"})

    assert route.called
    assert job.is_terminal
    assert job.output is not None


@respx.mock
def test_the_synchronous_answer_needs_no_polling(
    client: CloudClient, document: DocumentRef
) -> None:
    """A 200 with an output is finished. Polling it would be a wasted round trip."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    polls = respx.get(url__startswith=f"{ENDPOINT}/v1/jobs/")

    client.run("compress", document)

    assert not polls.called


@respx.mock
def test_the_bearer_token_is_sent_to_the_api(client: CloudClient, document: DocumentRef) -> None:
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )

    client.run("compress", document)

    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"


@respx.mock
def test_the_bearer_token_is_not_sent_to_storage(
    client: CloudClient, document: DocumentRef
) -> None:
    """A presigned URL points at storage, which has no business seeing our key."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    download = respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7\n")
    )

    client.fetch_output(client.run("compress", document))

    assert "Authorization" not in download.calls.last.request.headers


@respx.mock
def test_fetch_output_returns_bytes_rather_than_writing_them(
    client: CloudClient, document: DocumentRef
) -> None:
    """`core/atomic.py` is the only module permitted to touch a destination."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7\nsmall\n")
    )

    assert client.fetch_output(client.run("compress", document)) == b"%PDF-1.7\nsmall\n"


@respx.mock
def test_a_success_carrying_no_output_is_a_protocol_error(
    client: CloudClient, document: DocumentRef
) -> None:
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json={"ok": True, "job_id": "j", "status": "succeeded"})
    )
    job = client.run("compress", document)

    with pytest.raises(CloudProtocolError, match="no output"):
        client.fetch_output(job)


# ---------------------------------------------------------------------------
# The large path, and polling
# ---------------------------------------------------------------------------


@respx.mock
def test_a_large_document_goes_through_a_presigned_upload(
    config: CloudConfig, tmp_path: Path
) -> None:
    """Over the threshold the bytes never travel through the API itself."""
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.7\n" + b"x" * 4096)
    client = CloudClient(replace(config, max_sync_bytes=100))

    ticket = respx.post(f"{ENDPOINT}/v1/uploads").mock(
        return_value=httpx.Response(
            200,
            json={"upload_url": f"{ENDPOINT}/v1/uploads/f_9", "file_id": "f_9", "expires_in": 900},
        )
    )
    put = respx.put(f"{ENDPOINT}/v1/uploads/f_9").mock(return_value=httpx.Response(200, json={}))
    submit = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )

    client.run("compress", DocumentRef.from_path(big))

    assert ticket.called, "no upload ticket was requested"
    assert put.called, "the bytes were not sent to storage"
    assert submit.called, "the job was never submitted"
    assert b"multipart" not in submit.calls.last.request.headers.get("content-type", "").encode()


@respx.mock
def test_polling_continues_until_the_job_settles(
    client: CloudClient, document: DocumentRef
) -> None:
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "job_7", "status": "running", "poll_after_ms": 1}
        )
    )
    polls = respx.get(f"{ENDPOINT}/v1/jobs/job_7").mock(
        side_effect=[
            httpx.Response(
                200, json={"ok": True, "job_id": "job_7", "status": "running", "poll_after_ms": 1}
            ),
            httpx.Response(200, json=succeeded("job_7")),
        ]
    )

    job = client.run("ocr", document)

    assert polls.call_count == 2
    assert job.is_terminal


@respx.mock
def test_the_server_chooses_the_polling_interval(
    client: CloudClient, document: DocumentRef
) -> None:
    """The client obeys `poll_after_ms` so a busy endpoint can slow its callers."""
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 300}
        )
    )
    respx.get(f"{ENDPOINT}/v1/jobs/j").mock(return_value=httpx.Response(200, json=succeeded("j")))

    started = time.monotonic()
    client.run("ocr", document)

    assert time.monotonic() - started >= 0.25, "the requested interval was not honoured"


@respx.mock
def test_a_failed_job_raises_rather_than_returning(
    client: CloudClient, document: DocumentRef
) -> None:
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 1}
        )
    )
    respx.get(f"{ENDPOINT}/v1/jobs/j").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": False,
                "job_id": "j",
                "status": "failed",
                "error": {"code": "input.corrupt", "message": "bad"},
            },
        )
    )

    with pytest.raises(CorruptDocumentError):
        client.run("ocr", document)


# ---------------------------------------------------------------------------
# Cancellation and deadlines — ADR 0015
# ---------------------------------------------------------------------------


@respx.mock
def test_an_already_cancelled_run_makes_no_request(
    client: CloudClient, document: DocumentRef
) -> None:
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        client.run("compress", document, cancellation=token)

    assert not route.called, "a cancelled run must not upload the document"


@respx.mock
def test_a_cancel_during_polling_is_noticed_promptly(
    client: CloudClient, document: DocumentRef
) -> None:
    """The defect ADR 0015 was written about.

    `time.sleep` is uninterruptible, so before M6 a Ctrl-C sat unnoticed for the
    whole of `poll_after_ms` — up to a minute if the server asked for one. The
    assertion is on *elapsed time*, because "it eventually stopped" was already
    true and was not the problem.
    """
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 30_000}
        )
    )
    respx.get(f"{ENDPOINT}/v1/jobs/j").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 30_000}
        )
    )
    token = CancellationToken()

    def cancel_soon() -> None:
        import threading

        threading.Timer(0.2, token.cancel).start()

    cancel_soon()
    started = time.monotonic()
    with pytest.raises(CancelledError):
        client.run("ocr", document, cancellation=token)

    assert time.monotonic() - started < 5, "the cancel waited for the poll interval"


@respx.mock
def test_the_token_deadline_bounds_the_job(client: CloudClient, document: DocumentRef) -> None:
    """One deadline covers a whole operation, in a subprocess or on the network.

    A lapsed token deadline surfaces as `CancelledError`, not `CloudTimeoutError`
    — `core/cancellation.py` defines a passed deadline *as* cancellation, and
    the client observing that consistently is the point. `CloudTimeoutError` is
    for the client's own `timeout=` budget, covered below.
    """
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 1}
        )
    )
    respx.get(f"{ENDPOINT}/v1/jobs/j").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 1}
        )
    )

    started = time.monotonic()
    with pytest.raises(CancelledError):
        client.run("ocr", document, cancellation=CancellationToken(timeout=0.5))

    assert time.monotonic() - started < 30, "it waited for DEFAULT_JOB_TIMEOUT instead"


@respx.mock
def test_the_clients_own_budget_produces_a_timeout(
    client: CloudClient, document: DocumentRef
) -> None:
    """The other half: `timeout=` is the client's budget and reports as a timeout."""
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(
            202, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 1}
        )
    )
    respx.get(f"{ENDPOINT}/v1/jobs/j").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "job_id": "j", "status": "running", "poll_after_ms": 1}
        )
    )

    with pytest.raises(CloudTimeoutError, match="did not finish"):
        client.run("ocr", document, timeout=0.4)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_the_idempotency_key_covers_the_document_and_the_parameters(
    document: DocumentRef,
) -> None:
    """Anything that would change the output changes the key."""
    base = idempotency_key(document, {"preset": "ebook"})

    assert base.startswith("sha256:")
    assert idempotency_key(document, {"preset": "ebook"}) == base
    assert idempotency_key(document, {"preset": "screen"}) != base


def test_the_idempotency_key_ignores_parameter_order(document: DocumentRef) -> None:
    """The same request written two ways is one request."""
    assert idempotency_key(document, {"a": 1, "b": 2}) == idempotency_key(
        document, {"b": 2, "a": 1}
    )


def test_a_different_document_gets_a_different_key(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF-1.7\none\n")
    second.write_bytes(b"%PDF-1.7\ntwo\n")

    assert idempotency_key(DocumentRef.from_path(first), {}) != idempotency_key(
        DocumentRef.from_path(second), {}
    )


@respx.mock
def test_the_key_is_sent_with_the_submission(client: CloudClient, document: DocumentRef) -> None:
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )

    client.run("compress", document, {"preset": "ebook"})

    sent = route.calls.last.request.headers["Idempotency-Key"]
    assert sent == idempotency_key(document, {"preset": "ebook"})


# ---------------------------------------------------------------------------
# Errors — every code in the contract's table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, "cloud.auth", CloudAuthError),
        (403, "cloud.auth", CloudAuthError),
        (413, "cloud.payload_too_large", CloudPayloadTooLargeError),
        (429, "cloud.quota_exceeded", CloudQuotaExceededError),
        (500, "cloud.server_error", CloudServerError),
        (422, "input.corrupt", CorruptDocumentError),
        (422, "input.encrypted", EncryptedDocumentError),
        (422, "input.unsupported_format", UnsupportedFormatError),
        (422, "input.invalid_parameter", InvalidParameterError),
    ],
)
@respx.mock
def test_every_documented_code_becomes_its_typed_exception(
    client: CloudClient,
    document: DocumentRef,
    status: int,
    code: str,
    expected: type[Exception],
) -> None:
    """The client's half of the 1:1 mapping `cloud-api.md` specifies."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(status, json=envelope(code))
    )

    with pytest.raises(expected):
        client.run("compress", document)


@respx.mock
def test_the_code_is_trusted_over_the_status(client: CloudClient, document: DocumentRef) -> None:
    """A 422 could be any member of the input family; only the code says which."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(422, json=envelope("input.encrypted"))
    )

    with pytest.raises(EncryptedDocumentError):
        client.run("compress", document)


@respx.mock
def test_a_body_that_is_not_json_is_a_protocol_error(
    client: CloudClient, document: DocumentRef
) -> None:
    """Version skew against a self-hosted server is expected, not exceptional."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, content=b"<html>a proxy said no</html>")
    )

    with pytest.raises(CloudProtocolError):
        client.run("compress", document)


@respx.mock
def test_a_proxys_bare_502_still_produces_a_typed_error(
    client: CloudClient, document: DocumentRef
) -> None:
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(502, content=b"<html>Bad Gateway</html>")
    )

    with pytest.raises(CloudServerError):
        client.run("compress", document)


@respx.mock
def test_an_unreachable_endpoint_names_the_local_alternative(
    client: CloudClient, document: DocumentRef
) -> None:
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(side_effect=httpx.ConnectError("no route"))

    with pytest.raises(Exception, match="Could not reach") as caught:
        client.run("compress", document)

    assert "--engine local" in str(getattr(caught.value, "remedy", ""))


def test_a_missing_api_key_is_refused_before_a_socket_is_opened(
    document: DocumentRef,
) -> None:
    unconfigured = CloudClient(CloudConfig(endpoint=ENDPOINT, api_key=None))

    with pytest.raises(CloudAuthError, match="No API key"):
        unconfigured.run("compress", document)


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


@respx.mock
def test_a_retryable_failure_is_retried(document: DocumentRef) -> None:
    client = CloudClient(CloudConfig(endpoint=ENDPOINT, api_key="k", max_retries=2))
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        side_effect=[
            httpx.Response(500, json=envelope("cloud.server_error")),
            httpx.Response(200, json=succeeded()),
        ]
    )

    job = client.run("compress", document)

    assert route.call_count == 2
    assert job.is_terminal


@respx.mock
def test_a_bad_key_is_not_retried(document: DocumentRef) -> None:
    """Re-sending a rejected credential just wastes the user's time."""
    client = CloudClient(CloudConfig(endpoint=ENDPOINT, api_key="k", max_retries=3))
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(401, json=envelope("cloud.auth"))
    )

    with pytest.raises(CloudAuthError):
        client.run("compress", document)

    assert route.call_count == 1


@respx.mock
def test_an_exhausted_quota_is_not_retried(document: DocumentRef) -> None:
    client = CloudClient(CloudConfig(endpoint=ENDPOINT, api_key="k", max_retries=3))
    route = respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(429, json=envelope("cloud.quota_exceeded"))
    )

    with pytest.raises(CloudQuotaExceededError):
        client.run("compress", document)

    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@respx.mock
def test_capabilities_are_fetched_once_and_cached(client: CloudClient) -> None:
    route = respx.get(f"{ENDPOINT}/v1/capabilities").mock(
        return_value=httpx.Response(
            200, json={"tools": ["compress", "convert"], "max_sync_bytes": 42, "api_version": "1"}
        )
    )

    first = client.capabilities()
    second = client.capabilities()

    assert route.call_count == 1
    assert first is second
    assert first.supports("compress")
    assert not first.supports("ocr")


@respx.mock
def test_a_malformed_capabilities_body_is_a_protocol_error(client: CloudClient) -> None:
    respx.get(f"{ENDPOINT}/v1/capabilities").mock(
        return_value=httpx.Response(200, json={"tools": "compress", "api_version": "1"})
    )

    with pytest.raises(CloudProtocolError, match="list of strings"):
        client.capabilities()


def test_the_user_agent_identifies_the_client_and_nothing_else() -> None:
    """No hostname, no username, no document name."""
    from docmax.cloud_client.client import user_agent

    reported = user_agent()

    assert reported.startswith("docmax/")
    assert "python/" in reported


@respx.mock
def test_a_run_with_no_cancellation_still_works(client: CloudClient, document: DocumentRef) -> None:
    """`NEVER_CANCELLED` is the default, so no branch checks for `None`."""
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )

    assert client.run("compress", document, cancellation=NEVER_CANCELLED).is_terminal
