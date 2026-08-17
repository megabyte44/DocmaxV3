"""The cloud engine for ``ocr``.

The whole point of the tools layer sitting above ``cloud_client``: this module
is where a tool decides *what* to send, and the client below it decides how. It
holds no HTTP, no retry logic, and no knowledge of the wire format.

What it must not do is decide *whether* to send. Consent is the router's
business, checked before this strategy is ever constructed, and a test asserts
no path reaches ``cloud_client`` without passing that check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.cloud_client import CloudClient, CloudConfig
from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

TOOL_NAME = "ocr"


class OcrCloud:
    """Post the document to an endpoint that already has Tesseract installed."""

    def __init__(self, client: CloudClient | None = None) -> None:
        self._client = client or CloudClient()

    def is_available(self) -> bool:
        """Configured, and offered by this endpoint.

        Deliberately not a reachability check: availability is asked on every
        routing decision, and a network round trip there would make every
        command slower for the sake of a question the request itself answers.
        """
        return self._client.config.is_configured

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return "No API key is configured for the cloud endpoint."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Upload, wait, and write the result. Lands in M6.

        The remaining piece is the write: ``fetch_output`` returns bytes, and
        they go to ``core/atomic.py`` along with this tool's validators — so a
        cloud result is checked by the same code as a local one and lands under
        the same crash-safety guarantee.
        """
        raise NotImplementedError

    def _result(self, target: OutputTarget, *, duration_ms: int, version: str | None) -> ToolResult:
        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.CLOUD,
            duration_ms=duration_ms,
            engine_version=version,
        )


def build(config: CloudConfig | None = None) -> EngineStrategy:
    return OcrCloud(CloudClient(config))


__all__ = ["OcrCloud", "build"]
