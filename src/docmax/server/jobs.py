"""Job records: what was asked for, how it went, and what came out.

A job exists so that work too slow for one request can still be reported on. Its
statuses are ``core.models.JobStatus`` — the same values the client parses —
because a status vocabulary that differs by one word between the two ends of a
contract is a bug waiting to be shipped.

The in-memory store is the reference implementation, with the same honest limit
as the storage backend: one process, no persistence.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from docmax.core.errors import DocMaxError, InputNotFoundError
from docmax.core.models import JobStatus

JOB_ID_PREFIX = "job_"

#: What the client is told to wait before asking again. The server owns this
#: number so that it can protect itself; the client honours it rather than
#: choosing an interval of its own.
DEFAULT_POLL_AFTER_MS = 2000


def new_job_id() -> str:
    return f"{JOB_ID_PREFIX}{secrets.token_hex(12)}"


@dataclass(slots=True)
class Job:
    """One unit of remote work."""

    job_id: str
    tool: str
    owner: str
    status: JobStatus = JobStatus.QUEUED
    file_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    duration_ms: int | None = None
    engine_version: str | None = None
    output_url: str | None = None
    output_size_bytes: int | None = None
    output_content_type: str | None = None
    error: DocMaxError | None = None

    def to_payload(self) -> dict[str, Any]:
        """The job as the contract describes it on the wire."""
        payload: dict[str, Any] = {
            "ok": self.status is not JobStatus.FAILED,
            "job_id": self.job_id,
            "status": self.status.value,
        }
        if not self.status.is_terminal:
            payload["poll_after_ms"] = DEFAULT_POLL_AFTER_MS
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.engine_version is not None:
            payload["engine_version"] = self.engine_version
        if self.output_url is not None:
            payload["output"] = {
                "url": self.output_url,
                "size_bytes": self.output_size_bytes or 0,
                "content_type": self.output_content_type or "application/octet-stream",
            }
        if self.error is not None:
            payload["error"] = {
                "code": self.error.code.value,
                "message": self.error.message,
                "remedy": self.error.remedy,
                "retryable": self.error.retryable,
            }
        return payload


class JobStore(Protocol):
    """Somewhere to keep job records while they are alive."""

    def create(
        self, tool: str, *, file_id: str | None, params: dict[str, Any], owner: str
    ) -> Job: ...

    def get(self, job_id: str, *, owner: str) -> Job:
        """The job, if ``owner`` is the caller that created it.

        A caller presenting *any* valid API key may reach this method — that
        much is checked before the request arrives — but a job belongs to
        whoever created it, not to whoever else can guess or has seen its id.
        A mismatch raises the identical "no such job" error an unknown id
        raises, in the same lookup, so a caller polling someone else's job
        learns nothing about whether it exists. See ADR 0035.
        """
        ...

    def find_by_idempotency_key(self, key: str, *, owner: str) -> Job | None:
        """The retry-safety half of the contract, scoped to one caller.

        Same input, same parameters, same key, same caller — so a retry after
        a dropped connection returns the original job instead of running and
        billing it a second time. Scoped to ``owner`` because an
        ``Idempotency-Key`` is a value the *client* chooses: without this, one
        caller supplying a key another caller already used — by collision or
        on purpose — would be handed back that other caller's job, including
        its output's ``file_id``. That is the sharpest form of "possible
        cross-user data exposure" this module can produce, and it is a
        property of the lookup, not of the key's own randomness.
        """
        ...

    def remember_idempotency_key(self, key: str, job: Job, *, owner: str) -> None: ...


@dataclass(slots=True)
class InMemoryJobStore:
    """The reference store: two dictionaries and no persistence."""

    _jobs: dict[str, Job] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], str] = field(default_factory=dict)

    def create(self, tool: str, *, file_id: str | None, params: dict[str, Any], owner: str) -> Job:
        job = Job(job_id=new_job_id(), tool=tool, owner=owner, file_id=file_id, params=dict(params))
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str, *, owner: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            raise InputNotFoundError(
                f"No such job: {job_id}",
                remedy="Job records are kept only while the job is alive.",
                context={"job_id": job_id},
            )
        return job

    def find_by_idempotency_key(self, key: str, *, owner: str) -> Job | None:
        job_id = self._by_key.get((owner, key))
        return self._jobs.get(job_id) if job_id else None

    def remember_idempotency_key(self, key: str, job: Job, *, owner: str) -> None:
        self._by_key[(owner, key)] = job.job_id


__all__ = [
    "DEFAULT_POLL_AFTER_MS",
    "JOB_ID_PREFIX",
    "InMemoryJobStore",
    "Job",
    "JobStore",
    "new_job_id",
]
