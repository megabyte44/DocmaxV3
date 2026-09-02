"""``InMemoryStorage`` — id ownership, and the format check ahead of it.

GH #52: a caller confused about which MCP surface it is talking to (local
stdio, which takes real paths, vs. remote `/v1/mcp`, which only ever takes a
`file_id` from `POST /v1/uploads`) sent a path to the remote endpoint and got
"No upload is registered as {file_id}." — technically correct, but nothing in
it says *why*, or what to do instead. The fix is the format check in
`InMemoryStorage._slot`, exercised directly here rather than through the full
MCP/HTTP stack `test_m11_mcp.py` already covers for ownership.
"""

from __future__ import annotations

import pytest

from docmax.core.errors import InputNotFoundError
from docmax.server.storage import InMemoryStorage

OWNER = "owner-a"


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


def test_a_path_looking_file_id_gets_a_remedy_naming_the_upload_step(
    storage: InMemoryStorage,
) -> None:
    with pytest.raises(InputNotFoundError) as excinfo:
        storage.get("/mnt/user-data/uploads/128003236_ex-05.pdf", owner=OWNER)

    assert "not a file_id" in str(excinfo.value)
    assert "POST /v1/uploads" in (excinfo.value.remedy or "")


def test_an_unknown_but_well_formed_file_id_still_gets_the_generic_not_found(
    storage: InMemoryStorage,
) -> None:
    with pytest.raises(InputNotFoundError) as excinfo:
        storage.get("f_deadbeefdeadbeefdeadbeef", owner=OWNER)

    assert "No upload is registered" in str(excinfo.value)


def test_a_non_owner_gets_the_identical_not_found_a_missing_id_gets(
    storage: InMemoryStorage,
) -> None:
    """ADR 0029's shape: ownership mismatch must not be distinguishable from
    "does not exist" — the format check above must not weaken that, since it
    runs before ownership is even checked.
    """
    file_id = storage.reserve(filename="a.pdf", size_bytes=5, owner=OWNER)
    storage.put(file_id, b"%PDF-", owner=OWNER)

    with pytest.raises(InputNotFoundError) as excinfo:
        storage.get(file_id, owner="someone-else")

    assert str(excinfo.value) == f"No upload is registered as {file_id}."
