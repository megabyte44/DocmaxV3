"""The local engine for ``unlock``.

## This is not a password recovery tool

A password that already opens the document has to be supplied. Nothing here
guesses, brute-forces, or exploits a weakness in the encryption, and nothing
here ever will -- that is a decision about what this project is for, not a gap
waiting to be filled.

Either password works, because either one decrypts the content: the **user**
password opens the file, and the **owner** password does too while additionally
bypassing the permission bits. Which one was used is reported, because it
changes what the result means.

## Why removing permissions comes with removing the password

PDF permissions live inside the encryption dictionary. Take the encryption away
and the permission bits go with it -- there is nowhere left to store them. So an
unlocked copy always allows everything, and this is stated rather than left for
someone to discover.

That is also why unlocking with the *user* password is not a way around a
restriction the owner set: it produces a copy with no restrictions at all. The
bits were advisory in the first place (see ``_permissions.py``); this does not
break anything that was holding.

## An unencrypted input is copied, not refused

Running this on a document that was never locked writes an unencrypted copy and
reports ``was_encrypted: false``. Failing instead would break the obvious batch
-- ``for f in *.pdf`` over a folder where some files are locked and some are not
-- to report a condition that is not a problem.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools._pdf import open_encrypted_pdf, page_count, save
from docmax.tools.unlock.validators import decrypts_to

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class UnlockLocal:
    """Rebuild a document without its encryption, using a password that opens it."""

    def is_available(self) -> bool:
        return importlib.util.find_spec(DEPENDENCY) is not None

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return f"{DEPENDENCY} is not installed."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Write ``docs[0]`` to ``target`` with its encryption removed.

        The mechanism is a page-by-page rebuild into a fresh writer that is
        never told to encrypt anything. Decryption happens in the reader, so the
        pages handed over are already plaintext and there is no encryption
        dictionary to carry across.
        """
        import time

        from pypdf import PdfWriter

        if not docs:
            raise InvalidParameterError(
                "Unlock needs a document.",
                remedy="Pass the PDF to unlock.",
            )

        password = _password(params)

        started = time.monotonic()
        reader, opened_with = open_encrypted_pdf(docs[0], password)
        total = page_count(reader)

        writer = PdfWriter()
        progress.start(f"Unlocking {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="unlock")
            writer.add_page(reader.pages[index])
            progress.advance()

        save(writer, target, validators=(decrypts_to(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": total,
                "was_encrypted": opened_with is not None,
                # Which password matched. Never the password itself: `details`
                # travels into logs and --json.
                "opened_with": opened_with,
            },
        )


def _password(params: dict[str, Any]) -> str | None:
    """The password, if one was given.

    Optional rather than required, because whether one is *needed* is a property
    of the document rather than of the command. When the file is encrypted and
    none was supplied, :func:`open_encrypted_pdf` raises the error that says so
    and names the flag -- which is a better place for that check than here,
    where it would have to guess.
    """
    value = params.get("password")
    if value is None or isinstance(value, str):
        return value
    raise InvalidParameterError(
        "password must be text.",
        remedy="Pass one with --password.",
        context={"parameter": "password"},
    )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return UnlockLocal()


__all__ = ["UnlockLocal", "build"]
