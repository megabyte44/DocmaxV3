"""The wire types from ``docs/cloud-api.md``, parsed defensively.

Every response is validated into one of these rather than passed around as a
dict. The endpoint is user-configurable, so a response that does not match the
contract is an expected condition — version skew against a self-hosted server —
and it has to surface as :class:`CloudProtocolError` naming the field, not as a
``KeyError`` three call frames later.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from docmax.core.errors import CloudProtocolError

# Re-exported. The status vocabulary is shared with the server, so it lives in
# core and neither half of the contract owns it.
from docmax.core.models import JobStatus


def _field(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise CloudProtocolError(
            f"The endpoint's response is missing the required field {key!r}.",
            context={"field": key},
        )
    return payload[key]


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = _field(payload, key)
    if not isinstance(value, str):
        raise CloudProtocolError(
            f"Expected {key!r} to be a string, got {type(value).__name__}.",
            context={"field": key},
        )
    return value


def _integer(payload: Mapping[str, Any], key: str, default: int | None = None) -> int:
    if key not in payload and default is not None:
        return default
    value = _field(payload, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CloudProtocolError(
            f"Expected {key!r} to be an integer, got {type(value).__name__}.",
            context={"field": key},
        )
    return value


def as_mapping(payload: object, *, what: str = "response") -> Mapping[str, Any]:
    """Narrow a decoded JSON body to an object, or fail with the contract error."""
    if not isinstance(payload, Mapping):
        raise CloudProtocolError(
            f"Expected a JSON object for the {what}, got {type(payload).__name__}.",
            context={"what": what},
        )
    return payload


@dataclass(frozen=True, slots=True)
class CloudOutput:
    """Where the finished document can be fetched from."""

    url: str
    size_bytes: int
    content_type: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CloudOutput:
        return cls(
            url=_string(payload, "url"),
            size_bytes=_integer(payload, "size_bytes", 0),
            content_type=_string(payload, "content_type"),
        )


@dataclass(frozen=True, slots=True)
class CloudJob:
    """One unit of remote work, whether it finished in one request or many."""

    job_id: str
    status: JobStatus
    #: The server decides its own polling interval; the client obeys it rather
    #: than picking one, so a busy endpoint can slow its callers down.
    poll_after_ms: int = 2000
    output: CloudOutput | None = None
    duration_ms: int | None = None
    engine_version: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CloudJob:
        raw_output = payload.get("output")
        output: CloudOutput | None = None
        if raw_output:
            output = CloudOutput.from_payload(as_mapping(raw_output, what="output"))

        raw_status = payload.get("status")
        if raw_status is None:
            # The synchronous 200 has no status field: it is finished by the
            # time it answers, and carries the output directly.
            status = JobStatus.SUCCEEDED if output else JobStatus.RUNNING
        else:
            try:
                status = JobStatus(raw_status)
            except ValueError as exc:
                raise CloudProtocolError(
                    f"Unknown job status {raw_status!r}.",
                    context={"status": str(raw_status)},
                ) from exc

        return cls(
            job_id=_string(payload, "job_id"),
            status=status,
            poll_after_ms=_integer(payload, "poll_after_ms", 2000),
            output=output,
            duration_ms=payload.get("duration_ms"),
            engine_version=payload.get("engine_version"),
        )


@dataclass(frozen=True, slots=True)
class UploadTicket:
    """Permission to upload one large file, straight to storage.

    Documents over the sync threshold never travel through the API itself —
    ``expires_in`` is how long this permission lasts, and the client treats a
    stale ticket as a reason to ask for a new one rather than to retry.
    """

    upload_url: str
    file_id: str
    expires_in: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> UploadTicket:
        return cls(
            upload_url=_string(payload, "upload_url"),
            file_id=_string(payload, "file_id"),
            expires_in=_integer(payload, "expires_in", 900),
        )


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What one endpoint can actually do.

    Fetched once and cached. A self-hosted server offering three of the five
    cloud tools then degrades to "that tool has no cloud engine here", decided
    before a request is made rather than as a per-call failure.
    """

    tools: frozenset[str]
    max_sync_bytes: int
    api_version: str

    def supports(self, tool: str) -> bool:
        return tool in self.tools

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Capabilities:
        tools = _field(payload, "tools")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise CloudProtocolError(
                "Expected 'tools' to be a list of strings.",
                context={"field": "tools"},
            )
        return cls(
            tools=frozenset(tools),
            max_sync_bytes=_integer(payload, "max_sync_bytes"),
            api_version=_string(payload, "api_version"),
        )


__all__ = [
    "Capabilities",
    "CloudJob",
    "CloudOutput",
    "JobStatus",
    "UploadTicket",
    "as_mapping",
]
