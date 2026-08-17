"""Where the Cloud Engine lives and how we authenticate to it.

The endpoint is configuration, not a constant, because a self-hosted deployment
is a first-class case: point this elsewhere and everything above it is
identical. That is also why the TLS rule is enforced here rather than assumed —
a user-supplied endpoint is the one place a document could be sent over
plaintext by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from docmax.core.branding import DEFAULT_CLOUD_ENDPOINT, ENV_PREFIX
from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Mapping

ENDPOINT_ENV = f"{ENV_PREFIX}CLOUD_ENDPOINT"
API_KEY_ENV = f"{ENV_PREFIX}API_KEY"

#: Plaintext is allowed only against these, so self-hosted development works
#: without a certificate while a real endpoint still cannot be misconfigured
#: into shipping documents in the clear.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The contract's threshold between the synchronous path and presigned upload.
#: Overridden by whatever ``GET /v1/capabilities`` reports, since the server is
#: the authority on its own limits.
DEFAULT_MAX_SYNC_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CloudConfig:
    """Everything the client needs to talk to an endpoint."""

    endpoint: str = DEFAULT_CLOUD_ENDPOINT
    api_key: str | None = None
    connect_timeout: float = 10.0
    #: Generous, because OCR of a long document legitimately takes minutes.
    #: A timeout is still mandatory: v2 had none anywhere and hung indefinitely.
    read_timeout: float = 120.0
    max_retries: int = 3
    max_sync_bytes: int = DEFAULT_MAX_SYNC_BYTES

    def __post_init__(self) -> None:
        split = urlsplit(self.endpoint)
        if split.scheme not in {"http", "https"} or not split.hostname:
            raise InvalidParameterError(
                f"The cloud endpoint is not a valid URL: {self.endpoint!r}",
                remedy="Set a full URL, e.g. https://example.invalid",
                context={"endpoint": self.endpoint},
            )
        if split.scheme == "http" and split.hostname not in LOCAL_HOSTS:
            raise InvalidParameterError(
                f"Refusing a plaintext endpoint: {self.endpoint!r}",
                remedy="Use https://. Plaintext is permitted only for a local endpoint.",
                context={"endpoint": self.endpoint},
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CloudConfig:
        """Read the endpoint and key from the environment.

        The config file is the other source and takes lower precedence; that
        merge belongs to ``core/config.py`` in M1, which will construct this.
        """
        source = os.environ if env is None else env
        return cls(
            endpoint=source.get(ENDPOINT_ENV, DEFAULT_CLOUD_ENDPOINT),
            api_key=source.get(API_KEY_ENV) or None,
        )

    @property
    def is_configured(self) -> bool:
        """An API key is required from day one — anonymous access is not offered."""
        return bool(self.api_key)


__all__ = ["API_KEY_ENV", "ENDPOINT_ENV", "CloudConfig"]
