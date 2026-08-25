"""The local engine for ``protect``.

## Two passwords, and what each one is for

A PDF carries two. The **user password** is the one that opens the document at
all. The **owner password** bypasses the permission bits and is what a reader
checks before letting you print a document marked "no printing".

Supplying only one sets both to the same value, which is the honest default: a
user who was not told there are two should not end up with an owner password
they do not know they have. Supplying an owner password *and* an empty user
password gives the other common arrangement -- anyone may open it, but the
permissions apply to everyone who does.

## Why AES-256 by default, at the cost of a dependency

pypdf can write four algorithms and only the RC4 pair works with no extra
package. RC4 is broken -- not "aging", broken -- and 40-bit RC4 is breakable on
a laptop in minutes. A tool called ``protect`` that quietly produced that would
be worse than one that refuses to run, because the user would believe the
document was protected.

So the default is AES-256, ``cryptography`` is required to write it, and a
missing package raises :class:`LocalDependencyMissingError` naming the install
line. ``--algorithm RC4-128`` remains available for a reader old enough to need
it, but it has to be asked for.

## What a permission bit is worth

Permissions are advisory. They are a bit field inside the encryption dictionary
telling a conforming reader what the owner would prefer, and nothing stops a
reader from ignoring them -- which is why ``--allow`` restricts what a *polite*
reader offers, and is not a security boundary. Encryption is the boundary; the
permissions are a request. ``_permissions.py`` says the same thing where the
vocabulary is defined, because it is the single most misunderstood thing about
this feature.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError, LocalDependencyMissingError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _permissions
from docmax.tools._pdf import (
    CRYPTO_DEPENDENCY,
    CRYPTO_INSTALL_HINT,
    open_pdf,
    page_count,
    save,
)
from docmax.tools.protect.tool import ALGORITHMS, DEFAULT_ALGORITHM
from docmax.tools.protect.validators import encrypts_to

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"

# `CRYPTO_DEPENDENCY` and `CRYPTO_INSTALL_HINT` come from `_pdf` rather than
# being declared here: reading an AES-encrypted file needs the same package that
# writing one does, and a user must not meet two different install lines for it.


class ProtectLocal:
    """Encrypt a document with pypdf."""

    def is_available(self) -> bool:
        # pypdf alone, deliberately. `cryptography` is needed for the *default*
        # algorithm but not for every one, so reporting this engine as
        # unavailable without it would hide the RC4 path entirely -- and would
        # make the router say "no engine can run" when one plainly can.
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
        """Encrypt ``docs[0]`` into ``target``.

        An already-encrypted input is refused by :func:`open_pdf`, which is the
        right answer: re-encrypting a document nobody can open would produce a
        file behind two passwords, one of which is unknown.
        """
        import time

        from pypdf import PdfWriter
        from pypdf.errors import DependencyError

        if not docs:
            raise InvalidParameterError(
                "Protect needs a document.",
                remedy="Pass the PDF to encrypt.",
            )

        user_password = _password(params)
        owner_password = _owner_password(params, user_password)
        allowed = _allowed(params)
        algorithm = _algorithm(params)

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)

        writer = PdfWriter()
        progress.start(f"Encrypting {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="protect")
            writer.add_page(reader.pages[index])
            progress.advance()

        try:
            writer.encrypt(
                user_password,
                owner_password,
                algorithm=algorithm,
                permissions_flag=_permissions.flags_for(allowed),  # type: ignore[arg-type]
            )
        except DependencyError as exc:
            # Belt and braces: `_algorithm` already refuses an AES request when
            # the package is absent, so this should be unreachable. It is here
            # because pypdf's DependencyError subclasses neither PyPdfError nor
            # ImportError -- nothing else in this file would give it a type, and
            # the router would report a missing package as an internal error.
            raise _missing_crypto(algorithm, exc) from exc

        # The staged file is checked for encryption as well as for its page
        # count. "protect produced a readable PDF that is not encrypted" is the
        # one failure this tool must never deliver, and it is invisible to
        # anyone who does not go looking.
        save(
            writer,
            target,
            validators=(encrypts_to(total, password=owner_password),),
        )

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": total,
                "algorithm": algorithm,
                "allowed": list(allowed),
                # Whether the two passwords differ is worth reporting; neither
                # value ever is. `details` travels into logs and --json.
                "distinct_owner_password": owner_password != user_password,
            },
        )


def _missing_crypto(algorithm: str, exc: Exception) -> LocalDependencyMissingError:
    return LocalDependencyMissingError(
        f"{algorithm} encryption needs the '{CRYPTO_DEPENDENCY}' package: {exc}",
        dependency=CRYPTO_DEPENDENCY,
        install_hint=CRYPTO_INSTALL_HINT,
        remedy=f"{CRYPTO_INSTALL_HINT} — or use --algorithm RC4-128, which is weaker.",
        context={"algorithm": algorithm},
    )


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _password(params: dict[str, Any]) -> str:
    """The user password. Required, and allowed to be empty on purpose.

    An empty user password with a distinct owner password is a real and useful
    arrangement -- open freely, but the permissions apply. So "" is accepted
    while a missing parameter is not: the first is a choice, the second is an
    omission.
    """
    value = params.get("password")
    if value is None:
        raise InvalidParameterError(
            "Protect needs a password.",
            remedy="Pass one with --password.",
            context={"parameter": "password"},
        )
    if not isinstance(value, str):
        raise InvalidParameterError(
            "password must be text.",
            remedy="Pass one with --password.",
            context={"parameter": "password"},
        )
    return value


def _owner_password(params: dict[str, Any], user_password: str) -> str:
    """The owner password, defaulting to the user password.

    Defaulting to the same value rather than to something generated is what
    stops a user ending up with an owner password they were never shown and
    cannot reproduce.
    """
    value = params.get("owner_password")
    if value is None:
        return user_password
    if not isinstance(value, str):
        raise InvalidParameterError(
            "owner_password must be text.",
            remedy="Pass one with --owner-password.",
            context={"parameter": "owner_password"},
        )
    return value


def _allowed(params: dict[str, Any]) -> tuple[str, ...]:
    """Which permissions to grant. Everything, unless the user narrowed it.

    Granting everything by default matters: a ``protect`` that quietly forbade
    printing would be a surprise found much later, by someone at a printer.
    ``--allow none`` is how you ask for the strict end, and it is explicit.
    """
    raw = params.get("allow")
    if raw is None or (not isinstance(raw, str) and not list(raw)):
        return _permissions.NAMES
    return _permissions.parse(raw)


def _algorithm(params: dict[str, Any]) -> str:
    """A named algorithm pypdf can write, checked before the document is opened.

    Validated here rather than left to pypdf, which raises a bare ``ValueError``
    part-way through -- after a writer has been built, and with a message that
    does not list what the alternatives are.
    """
    value = params.get("algorithm")
    if value is None:
        return DEFAULT_ALGORITHM
    if not isinstance(value, str):
        raise _bad_algorithm(value)

    name = value.strip().upper()
    if name not in ALGORITHMS:
        raise _bad_algorithm(value)

    if name.startswith("AES") and importlib.util.find_spec(CRYPTO_DEPENDENCY) is None:
        raise _missing_crypto(name, ImportError(f"no module named {CRYPTO_DEPENDENCY!r}"))

    return name


def _bad_algorithm(value: Any) -> InvalidParameterError:
    return InvalidParameterError(
        f"{value!r} is not an encryption algorithm.",
        remedy=f"Use one of: {', '.join(ALGORITHMS)}.",
        context={"parameter": "algorithm", "algorithm": value},
    )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ProtectLocal()


__all__ = ["ProtectLocal", "build"]
