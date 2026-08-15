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

    def reserve(self, *, filename: str, size_bytes: int) -> str:
        """Claim an id for an upload that has not arrived yet."""
        ...

    def put(self, file_id: str, payload: bytes) -> None: ...

    def get(self, file_id: str) -> bytes: ...

    def filename(self, file_id: str) -> str:
        """The name it was uploaded under. Tools route on the suffix."""
        ...

    def discard(self, file_id: str) -> None:
        """Delete. Called on completion *and* on failure, never skipped."""
        ...


@dataclass(slots=True)
class _Slot:
    filename: str
    expected_bytes: int
    created_at: float
    payload: bytes | None = None


@dataclass(slots=True)
class InMemoryStorage:
    """The reference backend: a dictionary, and no persistence by design."""

    max_bytes: int = 512 * 1024 * 1024
    _slots: dict[str, _Slot] = field(default_factory=dict)

    def reserve(self, *, filename: str, size_bytes: int) -> str:
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
        )
        return file_id

    def put(self, file_id: str, payload: bytes) -> None:
        slot = self._slot(file_id)
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

    def get(self, file_id: str) -> bytes:
        payload = self._slot(file_id).payload
        if payload is None:
            raise InputNotFoundError(
                f"Nothing has been uploaded for {file_id}.",
                remedy="PUT the document to the upload URL before starting the job.",
                context={"file_id": file_id},
            )
        return payload

    def filename(self, file_id: str) -> str:
        return self._slot(file_id).filename

    def discard(self, file_id: str) -> None:
        self._slots.pop(file_id, None)

    def reap(self, older_than_seconds: float) -> int:
        """Delete anything left behind by a job that never finished."""
        cutoff = time.time() - older_than_seconds
        stale = [key for key, slot in self._slots.items() if slot.created_at < cutoff]
        for key in stale:
            del self._slots[key]
        return len(stale)

    def _in_use(self) -> int:
        return sum(len(slot.payload) for slot in self._slots.values() if slot.payload)

    def _slot(self, file_id: str) -> _Slot:
        try:
            return self._slots[file_id]
        except KeyError as exc:
            raise InputNotFoundError(
                f"No upload is registered as {file_id}.",
                remedy="Request a new upload URL; tickets expire.",
                context={"file_id": file_id},
            ) from exc


__all__ = ["FILE_ID_PREFIX", "InMemoryStorage", "Storage", "new_file_id"]
