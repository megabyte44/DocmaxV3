"""Typed exception hierarchy.

Two rules govern this module, and both are enforced by tests rather than by
convention:

1. **Library code never exits the process.** v2 shipped a single ``abort()``
   helper -- print, then ``sys.exit(1)`` -- called from 64 sites buried inside
   operation modules. The consequences were severe: the modules could not be
   used as a library at all, and because ``SystemExit`` does not inherit from
   ``Exception``, every ``except Exception`` handler in the batch runner and the
   folder watcher failed to catch it. One missing dependency terminated a
   200-file batch. Here, library code raises; only the CLI layer decides to
   exit. See ``tests/hygiene/test_no_sys_exit.py``.

2. **Every error is actionable.** Each carries a stable machine-readable
   ``code``, a human ``message``, and -- wherever a user can do something about
   it -- a ``remedy`` naming the exact next step. The UI renders the remedy. A
   user should never see a traceback for a condition we anticipated.

``retryable`` marks transient failures worth automatically re-attempting (mostly
network). ``user_fixable`` distinguishes "you can fix this" from "this is our
bug" and drives whether the UI suggests filing an issue.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from docmax.core.branding import CLI_NAME


class ErrorCode(StrEnum):
    """Stable, machine-readable error identifiers.

    These appear in ``--json`` output and are part of the public API: renaming
    one is a breaking change. Grouped by family with dotted prefixes so callers
    can match on a whole family (``code.startswith("cloud.")``).
    """

    # Family roots. Carried by the base class of each family so that a caller
    # raising the base directly is never mislabelled "internal", and so that
    # `code.startswith("input")` reliably matches the whole family.
    INPUT = "input"
    OUTPUT = "output"
    DEPENDENCY = "dependency"
    ENGINE = "engine"

    # -- input ---------------------------------------------------------------
    INPUT_NOT_FOUND = "input.not_found"
    INPUT_UNSUPPORTED_FORMAT = "input.unsupported_format"
    INPUT_CORRUPT = "input.corrupt"
    INPUT_ENCRYPTED = "input.encrypted"
    INPUT_INVALID_PARAMETER = "input.invalid_parameter"

    # -- output --------------------------------------------------------------
    OUTPUT_EXISTS = "output.exists"
    OUTPUT_IN_PLACE = "output.in_place_overwrite"
    OUTPUT_NOT_WRITABLE = "output.not_writable"
    OUTPUT_NO_DISK_SPACE = "output.no_disk_space"
    OUTPUT_VALIDATION_FAILED = "output.validation_failed"

    # -- dependencies --------------------------------------------------------
    DEP_MISSING = "dependency.missing"
    DEP_TOOL_FAILED = "dependency.tool_failed"
    DEP_TOOL_TIMEOUT = "dependency.tool_timeout"
    DEP_UNTRUSTED_BINARY = "dependency.untrusted_binary"

    # -- engine routing ------------------------------------------------------
    ENGINE_NOT_SUPPORTED = "engine.not_supported"
    ENGINE_NONE_AVAILABLE = "engine.none_available"
    ENGINE_CONSENT_REQUIRED = "engine.consent_required"

    # -- cloud ---------------------------------------------------------------
    CLOUD_UNAVAILABLE = "cloud.unavailable"
    CLOUD_AUTH = "cloud.auth"
    CLOUD_QUOTA = "cloud.quota_exceeded"
    CLOUD_PAYLOAD_TOO_LARGE = "cloud.payload_too_large"
    CLOUD_TIMEOUT = "cloud.timeout"
    CLOUD_SERVER = "cloud.server_error"
    CLOUD_PROTOCOL = "cloud.protocol_error"

    # -- identity --------------------------------------------------------------
    IDENTITY = "identity"
    IDENTITY_NOT_FOUND = "identity.not_found"

    # -- misc ----------------------------------------------------------------
    LICENSE_REQUIRED = "license.required"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class DocMaxError(Exception):
    """Base class for every error this package raises deliberately.

    Anything escaping a tool that is *not* a ``DocMaxError`` is a bug, and the
    router wraps it in :class:`InternalError` so no UI ever renders a raw
    traceback.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    #: Transient? Worth an automatic retry (network blips, 5xx, timeouts).
    retryable: ClassVar[bool] = False
    #: Can the user resolve this themselves? False means "this is our bug".
    user_fixable: ClassVar[bool] = True
    #: Fallback remedy when a call site does not supply a more specific one.
    default_remedy: ClassVar[str | None] = None

    def __init__(
        self,
        message: str,
        *,
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy if remedy is not None else self.default_remedy
        #: Structured detail for logs and ``--json``. Never contains file
        #: *contents* -- paths and counts only.
        self.context: dict[str, Any] = dict(context or {})

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``--json`` output and the cloud API error contract."""
        return {
            "code": self.code.value,
            "message": self.message,
            "remedy": self.remedy,
            "retryable": self.retryable,
            "user_fixable": self.user_fixable,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class InputError(DocMaxError):
    """Something is wrong with the document we were asked to read."""

    code = ErrorCode.INPUT


class InputNotFoundError(InputError):
    code = ErrorCode.INPUT_NOT_FOUND
    default_remedy = "Check the path and try again."


class UnsupportedFormatError(InputError):
    code = ErrorCode.INPUT_UNSUPPORTED_FORMAT
    default_remedy = f"Run `{CLI_NAME} formats` to list what this tool accepts."


class CorruptDocumentError(InputError):
    """Raised when a parser cannot make sense of the bytes.

    Property tests assert that malformed input produces *this* -- never a hang,
    never an uncaught library exception, never a truncated output file.
    """

    code = ErrorCode.INPUT_CORRUPT
    default_remedy = f"The file may be damaged. Try `{CLI_NAME} get-info` to inspect it."


class EncryptedDocumentError(InputError):
    code = ErrorCode.INPUT_ENCRYPTED
    default_remedy = f"Supply the password with --password, or run `{CLI_NAME} unlock` first."


class InvalidParameterError(InputError):
    """A parameter failed validation before any work began.

    Raised eagerly, so a bad ``--pages 1-a`` never reaches a parser and never
    produces a partial output. v2 called bare ``int()`` on user page ranges and
    surfaced a ValueError traceback.
    """

    code = ErrorCode.INPUT_INVALID_PARAMETER


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class OutputError(DocMaxError):
    """Something is wrong with where we were asked to write."""

    code = ErrorCode.OUTPUT


class OutputExistsError(OutputError):
    code = ErrorCode.OUTPUT_EXISTS
    default_remedy = "Pass --force to overwrite, or choose a different -o path."


class InPlaceOverwriteError(OutputError):
    """The requested output is also an input.

    This is the single most destructive bug class v2 shipped: ``convert x.md
    --to md`` silently destroyed the source, and ``merge a.pdf b.pdf -o a.pdf``
    consumed one of its own inputs. ``OutputTarget.resolve()`` makes it
    unreachable -- no engine is ever handed a raw destination path.
    """

    code = ErrorCode.OUTPUT_IN_PLACE
    default_remedy = "Choose an output path that is not one of the inputs."


class OutputNotWritableError(OutputError):
    code = ErrorCode.OUTPUT_NOT_WRITABLE
    default_remedy = "Check directory permissions, or pick a different -o path."


class InsufficientDiskSpaceError(OutputError):
    code = ErrorCode.OUTPUT_NO_DISK_SPACE
    default_remedy = "Free up space or write to a different volume."


class OutputValidationError(OutputError):
    """The operation produced a file that failed its own validators.

    The temp file is discarded and the destination is left untouched, so this
    error means "nothing was written", not "something broken was written".
    """

    code = ErrorCode.OUTPUT_VALIDATION_FAILED
    user_fixable = False


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class DependencyError(DocMaxError):
    """A local engine needs something that is missing or misbehaving."""

    code = ErrorCode.DEPENDENCY


class LocalDependencyMissingError(DependencyError):
    """A Python package or external binary the local engine requires is absent.

    Carries enough context for the router to offer the cloud engine instead --
    this is the exact moment the Cloud Engine justifies its existence.
    """

    code = ErrorCode.DEP_MISSING

    def __init__(
        self,
        message: str,
        *,
        dependency: str,
        install_hint: str | None = None,
        url: str | None = None,
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = {"dependency": dependency, **(context or {})}
        super().__init__(message, remedy=remedy or install_hint, context=ctx)
        self.dependency = dependency
        self.install_hint = install_hint
        #: The dependency's official install/documentation page, when the
        #: caller knows one — see ``tools/_binaries.py``'s ``Binary.homepage``
        #: and ``core.protocols.MissingDependency``. ``None`` means the
        #: project has not recorded one, not that none exists.
        self.url = url


class ExternalToolFailedError(DependencyError):
    """An external binary ran and returned non-zero."""

    code = ErrorCode.DEP_TOOL_FAILED
    user_fixable = False


class ExternalToolTimeoutError(DependencyError):
    """An external binary exceeded its timeout and was killed.

    Every subprocess call has a mandatory timeout. v2 had none anywhere, so a
    hung xelatex hung the whole application indefinitely.
    """

    code = ErrorCode.DEP_TOOL_TIMEOUT
    retryable = True


class UntrustedBinaryError(DependencyError):
    """A configured binary path failed verification before execution.

    Binary paths come from a user-writable config file. v2 executed whatever
    string it found there without checking the file existed, let alone that it
    was the tool it claimed to be.
    """

    code = ErrorCode.DEP_UNTRUSTED_BINARY
    default_remedy = f"Run `{CLI_NAME} doctor --fix` to re-discover tool paths."


# ---------------------------------------------------------------------------
# Engine routing
# ---------------------------------------------------------------------------


class EngineError(DocMaxError):
    """Raised by the router when it cannot satisfy an engine request."""

    code = ErrorCode.ENGINE


class EngineNotSupportedError(EngineError):
    """This tool does not implement the requested engine.

    Expected and normal: pypdf-only tools (merge, split, rotate, sanitize...)
    declare ``supported_engines = {"local"}`` because uploading a document to
    perform a millisecond-long pure-Python operation is strictly worse than
    doing it here.
    """

    code = ErrorCode.ENGINE_NOT_SUPPORTED


class NoEngineAvailableError(EngineError):
    """Neither engine can run. The message names both reasons."""

    code = ErrorCode.ENGINE_NONE_AVAILABLE


class ConsentRequiredError(EngineError):
    """Falling back to cloud would upload the user's document; they have not agreed.

    Never raised past the UI layer: the CLI turns it into a y/n prompt and the
    TUI into a modal. Consent is recorded per-tool. ``offline = true`` in config
    makes cloud unreachable regardless of flags, and this error is not even
    reached in that case -- :class:`NoEngineAvailableError` is.
    """

    code = ErrorCode.ENGINE_CONSENT_REQUIRED

    def __init__(
        self,
        message: str,
        *,
        tool: str,
        remedy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, remedy=remedy, context={"tool": tool, **(context or {})})
        self.tool = tool


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------


class CloudEngineUnavailableError(EngineError):
    """Base for every cloud failure. Callers may catch this to fall back to local."""

    code = ErrorCode.CLOUD_UNAVAILABLE
    retryable = True


class CloudAuthError(CloudEngineUnavailableError):
    code = ErrorCode.CLOUD_AUTH
    retryable = False
    default_remedy = f"Run `{CLI_NAME} cloud login` to configure your API key."


class CloudQuotaExceededError(CloudEngineUnavailableError):
    code = ErrorCode.CLOUD_QUOTA
    retryable = False
    default_remedy = "Wait for your quota to reset, or run locally with --engine local."


class CloudPayloadTooLargeError(CloudEngineUnavailableError):
    code = ErrorCode.CLOUD_PAYLOAD_TOO_LARGE
    retryable = False
    default_remedy = "Run locally with --engine local, or split the document first."


class CloudTimeoutError(CloudEngineUnavailableError):
    code = ErrorCode.CLOUD_TIMEOUT


class CloudServerError(CloudEngineUnavailableError):
    code = ErrorCode.CLOUD_SERVER
    user_fixable = False


class CloudProtocolError(CloudEngineUnavailableError):
    """The server replied with something the client cannot parse.

    Usually a version skew between client and endpoint -- especially relevant
    because the endpoint is user-configurable and may be self-hosted.
    """

    code = ErrorCode.CLOUD_PROTOCOL
    retryable = False
    user_fixable = False


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class IdentityError(DocMaxError):
    """Something is wrong with a caller identity — a user or a token.

    Raised by ``server/identity.py`` (ADR 0037) and by the CLI administration
    commands built on it. Never raised by ``core`` or ``tools`` -- a document
    engine has no notion of who called it, only ``owner`` strings it never
    inspects.
    """

    code = ErrorCode.IDENTITY


class IdentityNotFoundError(IdentityError):
    """No such user, or no such active token.

    The same message for "never existed" and "already revoked" -- a caller
    administering tokens learns that an id is not currently valid, not which
    of the two reasons made it so, mirroring the shape
    ``storage.py::InMemoryStorage._slot`` already uses for a caller who does
    not own a ``file_id``.
    """

    code = ErrorCode.IDENTITY_NOT_FOUND


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class LicenseRequiredError(DocMaxError):
    """A feature behind the open-core boundary was invoked without a licence.

    See ``docs/adr/0004-open-core-boundary.md``. Every document operation is
    free; this covers team/enterprise features only.
    """

    code = ErrorCode.LICENSE_REQUIRED


class CancelledError(DocMaxError):
    """The user cancelled (Ctrl-C, or a TUI cancel).

    Not a failure. Atomic writes guarantee the destination is untouched and the
    temp file is gone; a resumable batch records progress so ``--resume`` picks
    up where this left off.
    """

    code = ErrorCode.CANCELLED
    user_fixable = False


class InternalError(DocMaxError):
    """A bug. The router wraps any non-DocMaxError escaping a tool in this."""

    code = ErrorCode.INTERNAL
    user_fixable = False
    default_remedy = "This is a bug. Please report it with the command you ran."


__all__ = [
    "CancelledError",
    "CloudAuthError",
    "CloudEngineUnavailableError",
    "CloudPayloadTooLargeError",
    "CloudProtocolError",
    "CloudQuotaExceededError",
    "CloudServerError",
    "CloudTimeoutError",
    "ConsentRequiredError",
    "CorruptDocumentError",
    "DependencyError",
    "DocMaxError",
    "EncryptedDocumentError",
    "EngineError",
    "EngineNotSupportedError",
    "ErrorCode",
    "ExternalToolFailedError",
    "ExternalToolTimeoutError",
    "IdentityError",
    "IdentityNotFoundError",
    "InPlaceOverwriteError",
    "InputError",
    "InputNotFoundError",
    "InsufficientDiskSpaceError",
    "InternalError",
    "InvalidParameterError",
    "LicenseRequiredError",
    "LocalDependencyMissingError",
    "NoEngineAvailableError",
    "OutputError",
    "OutputExistsError",
    "OutputNotWritableError",
    "OutputValidationError",
    "UnsupportedFormatError",
    "UntrustedBinaryError",
]
