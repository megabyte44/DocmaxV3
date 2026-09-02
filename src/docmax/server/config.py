"""How one deployment of the server is configured.

Environment variables only, because that is what every deployment target agrees
on. Defaults are chosen so that starting the server with no configuration at all
gives you something safe rather than something convenient: it binds to
localhost, and it accepts no API keys until you name some.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from docmax.core.branding import ENV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Bumped when a change to the wire format is not backwards compatible. The
#: client reads this from ``GET /v1/capabilities`` and can then say "this
#: endpoint speaks a version I do not" instead of failing field by field.
API_VERSION = "1"

_PREFIX = f"{ENV_PREFIX}SERVER_"

HOST_ENV = f"{_PREFIX}HOST"
PORT_ENV = f"{_PREFIX}PORT"
KEYS_ENV = f"{_PREFIX}API_KEYS"
LOG_LEVEL_ENV = f"{_PREFIX}LOG_LEVEL"
#: [ADR 0037](../../../docs/adr/0037-server-token-identity.md). Unset by
#: default: a deployment may run on ``KEYS_ENV`` alone, forever, with no
#: durable store at all.
IDENTITY_DB_ENV = f"{_PREFIX}IDENTITY_DB"

#: Module-level, not read back off the class: a slots dataclass replaces its
#: class attributes with slot descriptors, so ``cls.host`` is not the default.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "info"


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """One deployment's knobs."""

    #: Localhost by default. Exposing a service to the network is a decision
    #: someone should have to make on purpose.
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    #: Accepted bearer tokens. Empty means the server rejects every request,
    #: which is the correct default for a service that handles documents:
    #: an open-by-default endpoint is a mistake you make once.
    api_keys: frozenset[str] = field(default_factory=frozenset)

    #: A durable token/user store — [ADR 0037](../../../docs/adr/0037-server-token-identity.md).
    #: ``None`` means ``api_keys`` is the whole identity model, exactly as it
    #: was before this field existed. Set to layer issued, revocable tokens on
    #: top of the static allowlist, without retiring it.
    identity_db_path: Path | None = None

    #: Above this, a client must use the presigned upload path. Reported to
    #: clients through ``/v1/capabilities`` so they never have to guess.
    max_sync_bytes: int = 10 * 1024 * 1024

    #: The ceiling for the upload path itself.
    max_upload_bytes: int = 512 * 1024 * 1024

    #: How long a job's stored bytes may live before being reaped, in seconds.
    #: The contract says documents are deleted on completion; this is the
    #: backstop for jobs that never complete.
    retention_seconds: int = 3600

    log_level: str = DEFAULT_LOG_LEVEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServerSettings:
        source = os.environ if env is None else env
        keys = source.get(KEYS_ENV, "")
        identity_db = source.get(IDENTITY_DB_ENV)
        return cls(
            host=source.get(HOST_ENV, DEFAULT_HOST),
            port=int(source.get(PORT_ENV, DEFAULT_PORT)),
            api_keys=frozenset(key.strip() for key in keys.split(",") if key.strip()),
            identity_db_path=Path(identity_db) if identity_db else None,
            log_level=source.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL),
        )


__all__ = ["API_VERSION", "ServerSettings"]
