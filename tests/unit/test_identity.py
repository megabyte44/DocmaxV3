"""`SqliteIdentityStore` — issuance, hashing, and the "no distinguishing signal" rule.

ADR 0037. `test_m11_mcp.py` covers this store wired into the running server
(REST and MCP auth both resolving through it); this file is the store on its
own, the same split `test_storage.py` makes for `InMemoryStorage`.
"""

from __future__ import annotations

import ast
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from docmax.core.errors import IdentityNotFoundError
from docmax.server.identity import TOKEN_PREFIX, SqliteIdentityStore
from tests.paths import library_sources, relative

#: Everything `IdentityStore` offers beyond `verify()` -- issuance and
#: revocation. `verify()` is the one method a request path may call; these
#: are administration, and ADR 0037 §4 is explicit that a tool call or an MCP
#: client must never reach them.
_ADMINISTRATION_VERBS = frozenset(
    {"create_user", "create_token", "revoke", "list_tokens", "list_users"}
)

#: This module and its own CLI are the two places allowed to call them.
_EXEMPT = frozenset({"server/identity.py", "server/identity_cli.py"})


@pytest.fixture
def store(tmp_path: Path) -> SqliteIdentityStore:
    return SqliteIdentityStore(tmp_path / "identity.db")


def test_a_token_verifies_to_the_user_that_created_it(store: SqliteIdentityStore) -> None:
    user_id = store.create_user(label="jane")
    token = store.create_token(user_id=user_id)

    assert store.verify(token) == user_id


def test_the_raw_token_starts_with_the_documented_prefix(store: SqliteIdentityStore) -> None:
    user_id = store.create_user()
    token = store.create_token(user_id=user_id)

    assert token.startswith(TOKEN_PREFIX)


def test_two_tokens_for_the_same_user_both_verify_to_it(store: SqliteIdentityStore) -> None:
    """The point of a user owning more than one token."""
    user_id = store.create_user()
    a = store.create_token(user_id=user_id, label="laptop")
    b = store.create_token(user_id=user_id, label="ci")

    assert store.verify(a) == user_id
    assert store.verify(b) == user_id
    assert a != b


@pytest.mark.parametrize(
    "bad_token",
    [
        "not-a-token-at-all",
        "dmx_live_" + "0" * 64,  # well-formed, never issued
        "",
    ],
)
def test_verify_returns_none_for_anything_it_never_issued(
    store: SqliteIdentityStore, bad_token: str
) -> None:
    assert store.verify(bad_token) is None


def test_a_revoked_token_stops_verifying(store: SqliteIdentityStore) -> None:
    user_id = store.create_user()
    token = store.create_token(user_id=user_id)
    tokens = store.list_tokens(user_id)
    assert len(tokens) == 1

    store.revoke(tokens[0].token_id)

    assert store.verify(token) is None


def test_a_revoked_token_and_an_unknown_token_are_indistinguishable(
    store: SqliteIdentityStore,
) -> None:
    """ADR 0037's explicit requirement: no signal a caller can use to tell
    "never existed" apart from "existed, then was revoked".
    """
    user_id = store.create_user()
    token = store.create_token(user_id=user_id)
    token_id = store.list_tokens(user_id)[0].token_id
    store.revoke(token_id)

    revoked_result = store.verify(token)
    unknown_result = store.verify(TOKEN_PREFIX + "f" * 64)

    assert revoked_result is unknown_result is None


def test_revoking_an_already_revoked_token_raises_not_found(store: SqliteIdentityStore) -> None:
    user_id = store.create_user()
    store.create_token(user_id=user_id)
    token_id = store.list_tokens(user_id)[0].token_id
    store.revoke(token_id)

    with pytest.raises(IdentityNotFoundError):
        store.revoke(token_id)


def test_revoking_an_unknown_token_id_raises_not_found(store: SqliteIdentityStore) -> None:
    with pytest.raises(IdentityNotFoundError):
        store.revoke("t_does_not_exist")


def test_creating_a_token_for_an_unknown_user_raises_not_found(
    store: SqliteIdentityStore,
) -> None:
    with pytest.raises(IdentityNotFoundError):
        store.create_token(user_id="u_does_not_exist")


def test_the_raw_token_is_never_retrievable_after_creation(store: SqliteIdentityStore) -> None:
    user_id = store.create_user()
    token = store.create_token(user_id=user_id, label="laptop")

    infos = store.list_tokens(user_id)
    assert len(infos) == 1
    # TokenInfo carries no raw-value field at all -- this asserts the shape
    # rather than merely that one field is empty.
    assert not hasattr(infos[0], "token")
    assert not hasattr(infos[0], "value")
    assert not hasattr(infos[0], "raw")
    assert infos[0].label == "laptop"
    assert token not in repr(infos[0])


def test_the_raw_token_never_appears_in_the_database_file(
    store: SqliteIdentityStore, tmp_path: Path
) -> None:
    """Only the hash is written to disk -- the file itself is checked, not just the API."""
    user_id = store.create_user()
    token = store.create_token(user_id=user_id)

    raw_bytes = (tmp_path / "identity.db").read_bytes()
    assert token.encode("utf-8") not in raw_bytes


def test_list_users_reflects_every_created_user(store: SqliteIdentityStore) -> None:
    a = store.create_user(label="a")
    b = store.create_user(label="b")

    users = {info.user_id for info in store.list_users()}
    assert {a, b} <= users


def test_list_tokens_is_scoped_to_one_user(store: SqliteIdentityStore) -> None:
    alice = store.create_user()
    bob = store.create_user()
    store.create_token(user_id=alice, label="alice-token")
    store.create_token(user_id=bob, label="bob-token")

    alice_tokens = store.list_tokens(alice)
    assert len(alice_tokens) == 1
    assert alice_tokens[0].label == "alice-token"


def test_the_store_persists_across_a_reconnect(tmp_path: Path) -> None:
    """The whole point of a durable store: a restart must not lose it."""
    path = tmp_path / "identity.db"
    first = SqliteIdentityStore(path)
    user_id = first.create_user()
    token = first.create_token(user_id=user_id)

    second = SqliteIdentityStore(path)
    assert second.verify(token) == user_id


def test_the_database_is_a_single_file_at_rest(tmp_path: Path) -> None:
    """No WAL/SHM sidecars -- ADR 0037's "one file to back up" claim, checked."""
    path = tmp_path / "identity.db"
    store_instance = SqliteIdentityStore(path)
    store_instance.create_user()

    siblings = {p.name for p in tmp_path.iterdir()}
    assert siblings == {"identity.db"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions do not apply on Windows")
def test_the_database_file_is_created_owner_only_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "identity.db"
    SqliteIdentityStore(path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_token_administration_is_unreachable_from_tool_execution() -> None:
    """No document engine, tool handler, or MCP call path can mint or revoke
    a credential -- mirroring the guarantee
    `test_the_mcp_route_never_references_consentstore` already holds for
    consent, for the same reason: an agent driving DocMax through a tool call
    must never be the thing that can also grant access to other callers.
    """
    offences: list[str] = []
    for source in library_sources():
        rel = relative(source).replace("\\", "/")
        if rel.endswith(tuple(_EXEMPT)):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _ADMINISTRATION_VERBS:
                offences.append(f"{rel}:{node.lineno}: .{node.attr}(...)")

    assert not offences, offences


def test_the_schema_has_no_column_that_could_hold_a_raw_token(tmp_path: Path) -> None:
    """A defence against a future column reintroducing what hashing removed."""
    path = tmp_path / "identity.db"
    SqliteIdentityStore(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
    assert columns == {"id", "user_id", "token_hash", "label", "created_at", "revoked_at"}
