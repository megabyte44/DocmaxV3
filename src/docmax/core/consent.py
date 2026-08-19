"""The record of what a user agreed to upload, and where.

The Cloud Engine puts a document on someone else's computer. The architecture's
first privacy rule is that this never happens without a recorded, per-tool
agreement — no record, and the operation stops. This module is that record.

The full reasoning is in [ADR 0008](../../../docs/adr/0008-consent-record.md).
The three decisions it settles, in short:

**Where.** A separate app-owned ``consent.json`` beside the user-owned
``config.toml``. DocMax writes one and reads the other, never both. Consent also
must not travel: people sync dotfiles between machines, and a preference should
follow them while a statement that *this person, on this machine* agreed to
upload documents should not.

**What invalidates it.** Revocation, a change of endpoint, or a bump of the
terms version. Notably *not* time, and *not* ``offline`` — offline makes cloud
unreachable, which is a different thing from unconsented, and flipping it back
must not re-prompt.

**Versioning.** One integer, :data:`CONSENT_TERMS_VERSION`, bumped by hand when
the data-handling terms materially change. A hash of the terms text would
re-prompt everyone for a corrected comma, which teaches people to dismiss the
prompt unread — the exact failure the prompt exists to prevent.

Everything here fails **closed**. A corrupt file, an unreadable one, or a schema
from the future means "no consent", never "consent". Being wrong that way costs
one prompt; being wrong the other way uploads a document nobody agreed to send.

``core`` may not prompt — this module records a decision and answers questions
about it. Asking the question belongs to the interface layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from docmax.core.atomic import atomic_write
from docmax.core.models import OutputTarget

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: Bump when the data-handling terms in ``docs/cloud-api.md`` change materially —
#: what is retained, what is logged, what a document may be used for. Every grant
#: below the current value stops counting and the user is asked again.
#:
#: Not for wording changes, and not for new tools. Re-prompting is an
#: interruption, and it should mean something when it happens.
CONSENT_TERMS_VERSION: Final = 1

#: Schema of ``consent.json`` itself, separate from the terms version so the
#: file format can change without implying the terms did.
SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    """One recorded agreement: this tool, this endpoint, these terms."""

    tool: str
    endpoint: str
    terms_version: int
    #: ISO-8601 UTC. Shown to a user who asks what they agreed to and when; it
    #: expires nothing.
    granted_at: str

    def covers(self, *, endpoint: str, terms_version: int) -> bool:
        """Does this grant still authorise an upload?

        A grant made for a different endpoint does not carry: agreeing to a
        self-hosted box on the LAN is not agreeing to a service on the internet,
        and the endpoint is user-configurable precisely so that distinction is
        real.

        A *newer* stored terms version than the running code's is honoured
        rather than rejected — it means an older DocMax is reading a record made
        by a newer one, and the user agreed to terms at least as current.
        """
        return self.endpoint == endpoint and self.terms_version >= terms_version

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "endpoint": self.endpoint,
            "terms_version": self.terms_version,
            "granted_at": self.granted_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConsentGrant | None:
        """Parse one entry, or ``None`` if it is not one. Never raises.

        A malformed entry is dropped rather than rejected wholesale: one bad
        record should cost its own tool a prompt, not lock the user out of every
        other grant they made.
        """
        try:
            tool = payload["tool"]
            endpoint = payload["endpoint"]
            terms_version = payload["terms_version"]
            granted_at = payload["granted_at"]
        except (KeyError, TypeError):
            return None
        if not isinstance(tool, str) or not isinstance(endpoint, str):
            return None
        if not isinstance(terms_version, int) or isinstance(terms_version, bool):
            return None
        if not isinstance(granted_at, str):
            return None
        return cls(tool=tool, endpoint=endpoint, terms_version=terms_version, granted_at=granted_at)


class ConsentStore:
    """Reads and writes the consent record for one endpoint.

    Constructed with the endpoint it is answering for, because every question it
    is asked is really "may we upload to *this* server?" — a store that did not
    know the endpoint would have to be told it at every call, and a caller that
    forgot would get a wrong answer that fails open.
    """

    __slots__ = ("_endpoint", "_path", "_terms_version")

    def __init__(
        self,
        path: Path,
        *,
        endpoint: str,
        terms_version: int = CONSENT_TERMS_VERSION,
    ) -> None:
        self._path = path
        self._endpoint = endpoint.rstrip("/")
        self._terms_version = terms_version

    # -- reading ------------------------------------------------------------

    def _load(self) -> dict[str, ConsentGrant]:
        """Every stored grant, keyed by tool. Returns empty on any problem."""
        try:
            raw = self._path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return {}
        except OSError:
            # Unreadable — permissions, a directory where a file should be. Fail
            # closed: the user is asked again rather than assumed to have agreed.
            return {}

        try:
            document = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

        if not isinstance(document, dict):
            return {}
        if document.get("version") != SCHEMA_VERSION:
            # Includes a schema from the future, which we cannot interpret and
            # must not guess at.
            return {}

        entries = document.get("grants")
        if not isinstance(entries, list):
            return {}

        grants: dict[str, ConsentGrant] = {}
        for entry in entries:
            grant = ConsentGrant.from_dict(entry) if isinstance(entry, dict) else None
            if grant is not None:
                grants[grant.tool] = grant
        return grants

    def has(self, tool: str) -> bool:
        """May we upload for ``tool``, to this endpoint, under these terms?"""
        grant = self._load().get(tool)
        return grant is not None and grant.covers(
            endpoint=self._endpoint, terms_version=self._terms_version
        )

    def grant_for(self, tool: str) -> ConsentGrant | None:
        """The stored grant, whether or not it still applies.

        For showing a user *why* they are being asked again — "you agreed on
        this date, for a different endpoint" is a far better prompt than asking
        with no explanation.
        """
        return self._load().get(tool)

    def granted_tools(self) -> tuple[str, ...]:
        """Tools with a currently-valid grant, in name order."""
        return tuple(
            sorted(
                tool
                for tool, grant in self._load().items()
                if grant.covers(endpoint=self._endpoint, terms_version=self._terms_version)
            )
        )

    # -- writing ------------------------------------------------------------

    def _write(self, grants: Mapping[str, ConsentGrant]) -> None:
        """Replace the file atomically.

        Through ``core.atomic`` like every other write in this project: a crash
        mid-record must not leave a truncated file, which would then fail closed
        for the wrong reason and silently discard every other grant.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": SCHEMA_VERSION,
            "grants": [grant.to_dict() for grant in sorted(grants.values(), key=_by_tool)],
        }
        payload = json.dumps(document, indent=2, sort_keys=False).encode("utf-8")
        with atomic_write(OutputTarget(destination=self._path, force=True)) as handle:
            handle.write(payload)
            handle.write(b"\n")

    def record(self, tool: str, *, now: datetime | None = None) -> ConsentGrant:
        """Record agreement for ``tool`` at this endpoint. Idempotent in effect.

        Re-recording refreshes the endpoint, terms version and timestamp, which
        is what makes "agree again after the terms changed" work without a
        separate update path.
        """
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        grant = ConsentGrant(
            tool=tool,
            endpoint=self._endpoint,
            terms_version=self._terms_version,
            granted_at=moment.isoformat(timespec="seconds"),
        )
        grants = self._load()
        grants[tool] = grant
        self._write(grants)
        return grant

    def revoke(self, tool: str) -> bool:
        """Withdraw consent for ``tool``. ``True`` if there was any to withdraw."""
        grants = self._load()
        if tool not in grants:
            return False
        del grants[tool]
        self._write(grants)
        return True

    def revoke_all(self) -> int:
        """Withdraw everything. Returns how many grants were removed.

        Equivalent to deleting the file, which is the other supported way and
        needs no command at all.
        """
        grants = self._load()
        if not grants:
            return 0
        self._write({})
        return len(grants)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(endpoint={self._endpoint!r}, "
            f"terms_version={self._terms_version}, granted={len(self.granted_tools())})"
        )


def _by_tool(grant: ConsentGrant) -> str:
    """Stable on-disk ordering, so the file does not churn between writes."""
    return grant.tool


__all__ = [
    "CONSENT_TERMS_VERSION",
    "SCHEMA_VERSION",
    "ConsentGrant",
    "ConsentStore",
]
