"""Thin HTTP client for the Cloud Engine.

Fully open source and fully optional. The endpoint is configurable, so this
client talks to the hosted service by default and to a self-hosted deployment
if you point it elsewhere.

Cloud exists for one reason: to let someone use a tool without installing its
heavy local dependencies. It is never a default and never silent — see the
consent rules in ``core/router.py`` and the wire contract in ``docs/cloud-api.md``.

The layout mirrors that contract:

    config.py   which endpoint, which key, and the TLS rule
    models.py   the wire types, parsed defensively
    errors.py   error envelope -> typed exception, the inverse of server/errors.py
    client.py   requests, retries, idempotency, polling
"""

from __future__ import annotations

from docmax.cloud_client.client import CloudClient, idempotency_key, user_agent
from docmax.cloud_client.config import CloudConfig
from docmax.cloud_client.models import Capabilities, CloudJob, CloudOutput, JobStatus, UploadTicket

__all__ = [
    "Capabilities",
    "CloudClient",
    "CloudConfig",
    "CloudJob",
    "CloudOutput",
    "JobStatus",
    "UploadTicket",
    "idempotency_key",
    "user_agent",
]
