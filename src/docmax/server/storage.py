"""Where an uploaded document lives between arriving and being processed.

The contract's data-handling terms are the specification for this module, not
an aspiration: documents are deleted on completion or failure, contents are
never logged, and nothing is retained for any secondary purpose. A backend that
does not do that is not implementing this API.

The in-memory backend is the reference one, and it is honest about its limits:
it holds bytes in the process, so it does not survive a restart and does not
work across replicas. A disk- or object-store-backed implementation satisfies
the same protocol — and a disk-backed one writes through ``core/atomic.py``,
because that is the only module in this project permitted to touch a path.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

from docmax.core.errors import (
    CloudPayloadTooLargeError,
    InputNotFoundError,
    InsufficientDiskSpaceError,
)

FILE_ID_PREFIX = "f_"


def new_file_id() -> str:
    return f"{FILE_ID_PREFIX}{secrets.token_hex(12)}"


class Storage(Protocol):
    """Somewhere to keep bytes for the lifetime of one job."""

    def reserve(self, *, filename: str, size_bytes: int, owner: str) -> str:
        """Claim an id for an upload that has not arrived yet.

        ``owner`` is the caller's own identity — its API key today; see
        [ADR 0035](../../../docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
        on why a bearer token is the whole identity model for now. Every other
        method on this protocol checks it against the id's own record, in the
        same lookup that finds the record — not as a separate call — so there
        is no window between "does this exist" and "is it mine" for a second
        request to land in.
        """
        ...

    def put(self, file_id: str, payload: bytes, *, owner: str) -> None: ...

    def get(self, file_id: str, *, owner: str | None) -> bytes:
        """The bytes, if ``owner`` matches the one that reserved this id.

        ``owner=None`` skips the check. That is not a bypass hatch for a
        caller who forgot to look one up — it is what
        ``routes/outputs.py`` deliberately passes, because that route takes no
        API key at all: the file id's own unguessability is its access
        control there, by design, and a caller with no key has no owner to
        compare.
        """
        ...

    def filename(self, file_id: str, *, owner: str | None) -> str:
        """The name it was uploaded under. Tools route on the suffix."""
        ...

    def discard(self, file_id: str, *, owner: str | None) -> None:
        """Delete. Called on completion *and* on failure, never skipped."""
        ...


@dataclass(slots=True)
class _Slot:
    filename: str
    expected_bytes: int
    created_at: float
    owner: str
    payload: bytes | None = None


@dataclass(slots=True)
class InMemoryStorage:
    """The reference backend: a dictionary, and no persistence by design."""

    max_bytes: int = 512 * 1024 * 1024
    _slots: dict[str, _Slot] = field(default_factory=dict)

    def reserve(self, *, filename: str, size_bytes: int, owner: str) -> str:
        if size_bytes > self.max_bytes:
            raise CloudPayloadTooLargeError(
                f"That document is {size_bytes} bytes; this endpoint accepts {self.max_bytes}.",
                context={"size_bytes": size_bytes, "limit": self.max_bytes},
            )
        file_id = new_file_id()
        self._slots[file_id] = _Slot(
            filename=filename,
            expected_bytes=size_bytes,
            created_at=time.time(),
            owner=owner,
        )
        return file_id

    def put(self, file_id: str, payload: bytes, *, owner: str) -> None:
        slot = self._slot(file_id, owner=owner)
        if len(payload) > self.max_bytes:
            raise CloudPayloadTooLargeError(
                f"That upload is {len(payload)} bytes; this endpoint accepts {self.max_bytes}.",
                context={"size_bytes": len(payload), "limit": self.max_bytes},
            )
        if self._in_use() + len(payload) > self.max_bytes:
            raise InsufficientDiskSpaceError(
                "This endpoint has no room for another upload right now.",
                remedy="Retry shortly, or run locally with --engine local.",
            )
        slot.payload = payload

    def get(self, file_id: str, *, owner: str | None) -> bytes:
        payload = self._slot(file_id, owner=owner).payload
        if payload is None:
            raise InputNotFoundError(
                f"Nothing has been uploaded for {file_id}.",
                remedy="PUT the document to the upload URL before starting the job.",
                context={"file_id": file_id},
            )
        return payload

    def filename(self, file_id: str, *, owner: str | None) -> str:
        return self._slot(file_id, owner=owner).filename

    def discard(self, file_id: str, *, owner: str | None) -> None:
        try:
            self._slot(file_id, owner=owner)
        except InputNotFoundError:
            return
        del self._slots[file_id]

    def reap(self, older_than_seconds: float) -> int:
        """Delete anything left behind by a job that never finished."""
        cutoff = time.time() - older_than_seconds
        stale = [key for key, slot in self._slots.items() if slot.created_at < cutoff]
        for key in stale:
            del self._slots[key]
        return len(stale)

    def _in_use(self) -> int:
        return sum(len(slot.payload) for slot in self._slots.values() if slot.payload)

    def _slot(self, file_id: str, *, owner: str | None) -> _Slot:
        """The one lookup every method above goes through.

        The ownership check happens *inside* the same dict access that proves
        the id exists — one atomic step, not "does it exist" followed by "is
        it mine" as two calls a second request could land between. A mismatch
        raises the identical "no such id" error an unknown id raises: a caller
        who does not own ``file_id`` learns nothing about whether it exists,
        the same shape ADR 0029 chose for a path outside the local roots.

        The prefix check ahead of the dict lookup is a pure format check, not
        a lookup — it reveals nothing about what any *particular* id maps to,
        so it is safe to answer more specifically than "no such id" without
        weakening that guarantee. It exists because a filesystem path is the
        one wrong input this shape can't tell apart from a typo without help:
        remote MCP has no shared filesystem with the caller (ADR 0035), but a
        caller confused about which MCP surface it is talking to sends one
        anyway, and lands on this exact "no such id" message with nothing in
        it pointing at the fix. See GH #52.
        """
        if not file_id.startswith(FILE_ID_PREFIX):
            raise InputNotFoundError(
                f"{file_id!r} is not a file_id.",
                remedy=(
                    "This endpoint takes a file_id returned by POST /v1/uploads, "
                    "not a filesystem path — upload the document first, then pass "
                    "the file_id it returns here."
                ),
                context={"file_id": file_id},
            )
        try:
            slot = self._slots[file_id]
        except KeyError as exc:
            raise InputNotFoundError(
                f"No upload is registered as {file_id}.",
                remedy="Request a new upload URL; tickets expire.",
                context={"file_id": file_id},
            ) from exc
        if owner is not None and slot.owner != owner:
            raise InputNotFoundError(
                f"No upload is registered as {file_id}.",
                remedy="Request a new upload URL; tickets expire.",
                context={"file_id": file_id},
            )
        return slot


__all__ = ["FILE_ID_PREFIX", "InMemoryStorage", "Storage", "new_file_id"]
