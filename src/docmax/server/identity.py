"""A durable caller identity for ``docmax.server`` — ADR 0037.

Before this module, a "caller" of ``docmax.server`` was a raw bearer-token
string compared against `ServerSettings.api_keys` — a `frozenset[str]` parsed
once, from one environment variable, at process start. Revoking access meant
editing that variable and restarting the process, and a token was not
attributable to anyone: the token *was* the identity.

`IdentityStore` adds the piece that model was missing without replacing it:
a **user** may hold more than one **token**, a token can be revoked without a
restart, and only a token's hash is ever written to disk — the raw value is
returned once, at creation, and is not recoverable afterward.

The static env-var allowlist keeps working exactly as it does today; see
[ADR 0037 §5](../../../docs/adr/0037-server-token-identity.md). This module
is the *other* backend `security.require_api_key` and
`mcpauth.ApiKeyVerifier` consult, not a replacement for the first one.

## Why this writes to a file outside `core/atomic.py`

`core/atomic.py`'s guarantee — stage beside the destination, validate, then
`os.replace()` — is built for *document* writes: one call, one whole file,
replaced in a single step so a reader never observes a half-written
destination. A SQLite database earns the identical guarantee a different way:
every statement here commits inside a transaction, which is SQLite's own
atomicity mechanism, not `atomic.py`'s. Routing an `INSERT` through
`os.replace()` would not make it safer — it would replace a live,
possibly-concurrently-read database file on every write, which corrupts
concurrent readers rather than protecting them. See ADR 0037 §3.

`tests/hygiene/test_no_direct_writes.py`'s AST scan does not fire on
`sqlite3` calls at all — they are not `open(..., "w")`, `.write_text()`, or
any of the other patterns it looks for — so this module needs no entry in
that test's `EXEMPT` set to pass it. The note lives here, and in that test's
own module docstring, so the omission reads as a considered decision rather
than a gap nobody noticed.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from docmax.core.branding import CLI_NAME
from docmax.core.errors import IdentityNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

#: `cloud-api.md`'s Auth section has shown this shape as an example since
#: before this module existed (`Authorization: Bearer dmx_live_...`); this is
#: what makes that example literal.
TOKEN_PREFIX = "dmx_live_"  # noqa: S105 -- a prefix, not a credential

_USER_ID_PREFIX = "u_"
_TOKEN_ID_PREFIX = "t_"  # noqa: S105 -- a prefix, not a credential


def _new_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_hex(32)}"


def _new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(12)}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class UserInfo:
    user_id: str
    label: str | None
    created_at: float
    disabled_at: float | None


@dataclass(slots=True, frozen=True)
class TokenInfo:
    """What administration ever sees about a token. Never the raw value."""

    token_id: str
    user_id: str
    label: str | None
    created_at: float
    revoked_at: float | None


class IdentityStore(Protocol):
    """A user owns zero or more tokens; a token resolves to exactly one user.

    `verify()` is the one method the request path calls; the rest is
    administration, reached only from the CLI (ADR 0037 §4) and never from
    `core`, `tools`, or the tool registry.
    """

    def create_user(self, *, label: str | None = None) -> str: ...

    def create_token(self, *, user_id: str, label: str | None = None) -> str:
        """Return the raw token. This is the only moment it is ever available."""
        ...

    def verify(self, token: str) -> str | None:
        """The owning user's id, or ``None`` for unknown, malformed, or revoked.

        The three cases return the identical ``None`` — a caller presenting a
        revoked token learns nothing that distinguishes it from a token that
        never existed, the same reasoning `storage.py::InMemoryStorage._slot`
        already applies to a `file_id` mismatch.
        """
        ...

    def revoke(self, token_id: str) -> None: ...

    def list_tokens(self, user_id: str) -> list[TokenInfo]: ...

    def list_users(self) -> list[UserInfo]: ...


class SqliteIdentityStore:
    """The reference `IdentityStore`: one file, stdlib `sqlite3`, no new dependency.

    The same reasoning [ADR 0014](../../../docs/adr/0014-api-key-storage.md)
    already gave for rejecting `keyring` applies here — this is a
    self-hosted, single-operator-oriented product, and a compiled dependency
    or an external database server buys correctness this format does not
    need. Deliberately not WAL: the default rollback-journal mode leaves
    exactly one file at rest, which is what lets an operator back this store
    up by copying it.
    """

    __slots__ = ("_conn", "_lock", "_path")

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        # One connection, guarded by a lock: `require_api_key` is a sync
        # FastAPI dependency, which Starlette runs in a thread pool, so more
        # than one thread reaches this store concurrently. `check_same_thread`
        # is disabled to allow that, and the lock is what keeps it correct --
        # SQLite serialises writes on one connection regardless, but a lock
        # avoids "database is locked" under concurrent access from Python's
        # side rather than discovering that failure mode empirically.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        _restrict(path)

    def create_user(self, *, label: str | None = None) -> str:
        user_id = _new_id(_USER_ID_PREFIX)
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (id, label, created_at, disabled_at) VALUES (?, ?, ?, NULL)",
                (user_id, label, time.time()),
            )
            self._conn.commit()
        return user_id

    def create_token(self, *, user_id: str, label: str | None = None) -> str:
        with self._lock:
            known = self._conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
            if known is None:
                raise IdentityNotFoundError(
                    f"No such user: {user_id!r}",
                    remedy=f"Run `python -m {CLI_NAME}.server.identity_cli list` to see existing users.",
                    context={"user_id": user_id},
                )
            token = _new_token()
            token_id = _new_id(_TOKEN_ID_PREFIX)
            self._conn.execute(
                "INSERT INTO tokens (id, user_id, token_hash, label, created_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (token_id, user_id, _hash(token), label, time.time()),
            )
            self._conn.commit()
        return token

    def verify(self, token: str) -> str | None:
        # A format check ahead of the query, not a lookup -- it reveals
        # nothing about any *particular* token, so it costs nothing to answer
        # before touching the database. See storage.py::_slot for the same
        # reasoning applied to `file_id`.
        if not token.startswith(TOKEN_PREFIX):
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, revoked_at FROM tokens WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
        if row is None or row[1] is not None:
            return None
        return str(row[0])

    def revoke(self, token_id: str) -> None:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (time.time(), token_id),
            )
            self._conn.commit()
            revoked = cursor.rowcount
        if revoked == 0:
            raise IdentityNotFoundError(
                f"No such active token: {token_id!r}",
                remedy=(
                    f"Run `python -m {CLI_NAME}.server.identity_cli list --user <id>` "
                    "to see active tokens."
                ),
                context={"token_id": token_id},
            )

    def list_tokens(self, user_id: str) -> list[TokenInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, user_id, label, created_at, revoked_at FROM tokens "
                "WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [TokenInfo(*row) for row in rows]

    def list_users(self) -> list[UserInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, label, created_at, disabled_at FROM users ORDER BY created_at"
            ).fetchall()
        return [UserInfo(*row) for row in rows]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    label TEXT,
    created_at REAL NOT NULL,
    disabled_at REAL
);
CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
"""


def _restrict(path: Path) -> None:
    """Owner-only, where the filesystem has a notion of it.

    The identical caveat [ADR 0014](../../../docs/adr/0014-api-key-storage.md)
    already states for `config.toml`: not a defence against a determined
    attacker on the same machine, protection against the ordinary case of a
    world-readable home directory. Windows inherits the profile's ACL and is
    left alone.
    """
    if os.name == "nt":  # pragma: no cover - platform-specific
        return
    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


__all__ = [
    "TOKEN_PREFIX",
    "IdentityStore",
    "SqliteIdentityStore",
    "TokenInfo",
    "UserInfo",
]
