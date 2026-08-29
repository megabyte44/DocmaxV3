"""The short-lived localhost server every picker runs on.

[ADR 0005](../../../docs/adr/0005-gui-pickers.md) chose ``http.server`` plus
``webbrowser``, both standard library, over pywebview and Qt. This is that
server: it binds an ephemeral port on the loopback interface, serves one page
and the document that page displays, waits for the browser to post a value back,
and shuts down.

## What it is allowed to do

Serve two GETs and accept one POST. It has no route that writes a file, and the
package it lives in is covered by ``tests/hygiene/test_no_direct_writes.py`` and
``test_no_sys_exit.py``, which is what turns ADR 0005's "a picker never touches
the filesystem" from a promise into a property.

It does not print, either. A caller that wants the URL on screen — because the
browser did not open, or because this is an SSH session — passes ``announce``.
Keeping ``print`` out of here is what lets the same picker serve the CLI, which
writes to a Rich console, and the TUI, which cannot write to the terminal at all
while Textual owns it.

## Why the token

The port is ephemeral but it is still a port on this machine, and while the
picker is open the document is readable over it. Every route is therefore behind
a URL-safe random token generated per run, so another local process cannot
enumerate ports and read the file. The document never leaves the loopback
interface, and the server lives for as long as one interaction.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from docmax.core.branding import CLI_NAME
from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: How long a picker waits for an answer before giving up. Long enough to read a
#: page and think about it; short enough that a forgotten browser tab does not
#: wedge a terminal until the next reboot.
DEFAULT_TIMEOUT_SECONDS = 300.0

#: How often the waiting thread wakes to check. Small enough that Ctrl-C is
#: answered promptly rather than at the end of a five-minute sleep.
_POLL_SECONDS = 0.2

_PDF_MEDIA_TYPE = "application/pdf"
_HTML_MEDIA_TYPE = "text/html; charset=utf-8"
_JSON_MEDIA_TYPE = "application/json"

#: Names the serving thread so a hung process is legible in a debugger. Built
#: from ``CLI_NAME`` because ``core/branding.py`` is the only module allowed to
#: spell the brand out; ``tests/hygiene/test_branding.py`` enforces it.
_THREAD_NAME = f"{CLI_NAME}-picker"

_OK = b'{"ok": true}'
_NOT_OK = b'{"ok": false}'

#: Refused before it is read into memory. A picker displays one document; a file
#: larger than this is not one a browser was going to render usefully anyway,
#: and reading it would cost more than the interaction.
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024


class PickerCancelledError(Exception):
    """The user closed the picker without choosing. Not an error; a decision.

    Deliberately not a :class:`~docmax.core.errors.DocMaxError`. Cancelling a
    picker is the same kind of event as pressing Ctrl-C at a prompt, and the
    interface that started the picker decides what it means — the CLI turns it
    into the cancelled exit code, the TUI returns to the form.
    """


@dataclass(frozen=True, slots=True)
class Choice:
    """Whatever the page posted back, as plain JSON-shaped data.

    A picker returns *parameters*, so this is where the type discipline
    restarts: the payload is untrusted browser input, and every picker parses it
    into a typed value of its own rather than passing a dict onward.
    """

    payload: dict[str, Any]


def run_picker(
    *,
    document: Path,
    page: str,
    announce: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> Choice:
    """Serve ``page``, open a browser at it, and return what the user chose.

    Blocks until the page posts a value, the user cancels, or ``timeout``
    elapses. Always shuts the server down, including when the caller is
    interrupted — a picker that left a port open holding a document would be a
    worse failure than the one that caused it.
    """
    import http.server
    import webbrowser

    body = _read_document(document)
    token = secrets.token_urlsafe(24)
    answered = threading.Event()
    cancelled = threading.Event()
    result: dict[str, Any] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        # BaseHTTPRequestHandler logs every request to stderr, which in a CLI
        # session lands in the middle of the user's own output.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, media_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(payload)))
            # The document is on a loopback port for a few seconds. It has no
            # business in a browser cache afterwards.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _route(self) -> str | None:
            prefix = "/" + token
            if self.path == prefix:
                return "/"
            if self.path.startswith(prefix + "/"):
                return self.path[len(prefix) :]
            return None

        def do_GET(self) -> None:
            route = self._route()
            if route in ("/", "/index.html"):
                self._send(200, _HTML_MEDIA_TYPE, page.encode("utf-8"))
            elif route == "/document.pdf":
                self._send(200, _PDF_MEDIA_TYPE, body)
            else:
                self._send(404, _JSON_MEDIA_TYPE, _NOT_OK)

        def do_POST(self) -> None:
            import json

            route = self._route()
            if route not in ("/choice", "/cancel"):
                self._send(404, _JSON_MEDIA_TYPE, _NOT_OK)
                return

            if route == "/cancel":
                cancelled.set()
                self._send(200, _JSON_MEDIA_TYPE, _OK)
                answered.set()
                return

            length = int(self.headers.get("Content-Length") or 0)
            try:
                raw = self.rfile.read(length) if length else b"{}"
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send(400, _JSON_MEDIA_TYPE, _NOT_OK)
                return

            if not isinstance(parsed, dict):
                self._send(400, _JSON_MEDIA_TYPE, _NOT_OK)
                return

            result.update(parsed)
            self._send(200, _JSON_MEDIA_TYPE, _OK)
            answered.set()

    # Port 0 asks the OS for a free one, and 127.0.0.1 keeps it off every other
    # interface. Neither is negotiable: a picker binding 0.0.0.0 would put the
    # user's document on their network.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    # `server_address` is loosely typed (the host can be bytes for some address
    # families), and the loopback host is not in question here — we asked for it.
    port = int(server.server_address[1])
    url = f"http://127.0.0.1:{port}/{token}/"

    thread = threading.Thread(target=server.serve_forever, name=_THREAD_NAME, daemon=True)
    thread.start()
    try:
        opened = webbrowser.open(url) if open_browser else False
        if announce is not None:
            # Over SSH, and anywhere `webbrowser` finds nothing to launch, this
            # is the whole interaction. ADR 0005 calls that degradation
            # acceptable, and it is only acceptable if the URL is visible.
            announce(url if opened else f"{url}  (open this yourself)")
        _wait(answered, timeout=timeout)
    finally:
        server.shutdown()
        server.server_close()

    if cancelled.is_set():
        raise PickerCancelledError

    return Choice(payload=dict(result))


def _wait(answered: threading.Event, *, timeout: float) -> None:
    """Wait in short slices, so an interrupt is answered rather than queued."""
    import time

    deadline = time.monotonic() + timeout
    while not answered.is_set():
        if time.monotonic() >= deadline:
            raise InvalidParameterError(
                f"The picker was not answered within {int(timeout)} seconds.",
                remedy="Run it again, or supply the value as a flag instead.",
                context={"timeout_seconds": timeout},
            )
        answered.wait(_POLL_SECONDS)


def _read_document(document: Path) -> bytes:
    """The bytes the browser will display, or the typed error saying why not.

    Read once, up front, and held in memory for the life of the picker. The
    alternative — streaming from the path on each request — would keep a handle
    open on a file the user may be about to replace, and a picker has no
    business holding one.
    """
    try:
        size = document.stat().st_size
    except OSError as exc:
        raise InvalidParameterError(
            f"{document.name} could not be read: {exc}",
            remedy="Check the path and try again.",
            context={"path": str(document)},
        ) from exc

    if size > MAX_DOCUMENT_BYTES:
        raise InvalidParameterError(
            f"{document.name} is too large to preview ({size // (1024 * 1024)} MB).",
            remedy="Supply the value as a flag instead of using the picker.",
            context={"path": str(document), "bytes": size},
        )

    try:
        return document.read_bytes()
    except OSError as exc:
        raise InvalidParameterError(
            f"{document.name} could not be read: {exc}",
            remedy="Check the path and try again.",
            context={"path": str(document)},
        ) from exc


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_DOCUMENT_BYTES",
    "Choice",
    "PickerCancelledError",
    "run_picker",
]
