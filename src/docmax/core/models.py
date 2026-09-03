"""The value types every layer passes around.

``core`` owns these because all four layers speak them: the CLI parses argv into
a ``DocumentRef`` and an ``OutputTarget``, a tool's strategy consumes them, the
cloud client serialises them onto the wire, and the server deserialises them
back into the same shapes. Nothing here imports a UI framework or a document
library, so the types cost nothing to import.

``OutputTarget`` is the load-bearing one. No engine ever receives a raw
destination ``Path``: it receives a target that has already been checked against
its inputs. That is what makes "the output was also the input" unrepresentable
rather than merely discouraged — see ``docs/adr/0003-atomic-writes.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from docmax.core.errors import (
    InPlaceOverwriteError,
    InputNotFoundError,
    InvalidParameterError,
    OutputExistsError,
    OutputNotWritableError,
)


class Engine(StrEnum):
    """Which implementation performs the work.

    ``AUTO`` is a *request*, never a result: the router resolves it to ``LOCAL``
    or ``CLOUD`` before any strategy runs, and ``ToolResult.engine_used`` always
    names the one that actually ran.
    """

    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


class JobStatus(StrEnum):
    """Lifecycle of one unit of remote work.

    Lives in ``core`` rather than in the client because both halves of the cloud
    contract speak it: the client parses these values off the wire and the
    server writes them onto it. One definition, so the two cannot drift.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED}


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A file we were asked to read, already proven to exist.

    Constructed through :meth:`from_path` so that a missing file fails once, at
    the boundary, with a typed error — rather than as an ``IOError`` from
    somewhere deep inside a parser after work has already begun.
    """

    path: Path

    @classmethod
    def from_path(cls, path: Path | str) -> DocumentRef:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise InputNotFoundError(
                f"No such file: {resolved}",
                context={"path": str(resolved)},
            )
        return cls(path=resolved)

    @property
    def suffix(self) -> str:
        """Lower-cased extension, e.g. ``.pdf``."""
        return self.path.suffix.lower()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


def _is_one_of(destination: Path, inputs: Sequence[DocumentRef]) -> bool:
    """Is ``destination`` one of ``inputs``, as the *filesystem* understands it?

    Comparing the resolved paths is not enough, and the difference is the most
    destructive failure class in the project. ``Path.resolve()`` returns the
    name as stored on disk on Windows, so ``-o DOC.PDF`` against an input of
    ``doc.pdf`` normalises to one path and is caught. On macOS it does not:
    ``resolve()`` makes the path absolute and follows symlinks, but leaves the
    case exactly as typed — so on a case-insensitive APFS volume the two strings
    differ while naming the same file, and a string comparison waves the write
    through onto the input.

    ``samefile`` asks the operating system instead, comparing device and inode.
    That also covers hard links and Windows junctions, neither of which any
    amount of path normalisation would catch.

    It needs both sides to exist. Inputs always do — ``DocumentRef.from_path``
    guarantees it — but the destination usually does not, and a destination that
    does not exist cannot be an input, so the string comparison is exactly right
    for that case.
    """
    if not destination.exists():
        return destination in {ref.path for ref in inputs}

    for ref in inputs:
        try:
            if destination.samefile(ref.path):
                return True
        except OSError:  # pragma: no cover - the input vanished mid-call
            continue
    return False


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """A destination that has been checked and is safe to write.

    The checks live in :meth:`resolve` rather than in each tool, so there is one
    implementation of them instead of fifty. Engine signatures accept an
    ``OutputTarget``; passing a bare ``Path`` is a type error under strict mypy,
    which is what stops the checks being skipped by accident.
    """

    destination: Path
    force: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        inputs: Sequence[DocumentRef],
        requested: Path | str | None = None,
        default_suffix: str = ".pdf",
        produces_directory: bool = False,
        force: bool = False,
        enforced_suffix: str | None = None,
    ) -> OutputTarget:
        """Validate a requested destination against the inputs it came from.

        Raises :class:`InPlaceOverwriteError` if the destination is one of the
        inputs, and :class:`OutputExistsError` if it exists without ``force``.
        Neither condition is recoverable further down: by the time bytes are
        being written, the source may already be gone.

        An extensionless ``requested`` gets ``default_suffix`` appended for a
        file-producing tool — a user who types ``-o merged`` and gets a file
        Windows opens in Notepad has not been served by treating that case
        differently from the derive-from-input branch below, which has always
        appended it. A ``requested`` that already carries *any* suffix, right
        or wrong, is left exactly as given: this fills in one missing piece,
        it does not validate what the user chose. ``.tar.gz``-style double
        suffixes are unaffected either way, since ``Path.suffix`` already sees
        a non-empty ``.gz``.

        ``enforced_suffix`` is the one case where a suffix the user typed is
        *replaced* rather than respected. It arrives only when a parameter has
        named the output format outright — ``convert-image --to png`` — and it
        exists because the alternatives are worse: writing PNG bytes into a
        file called ``.jpg`` produces a file whose name lies about it, and
        refusing the combination makes the user retype a path to say a thing
        they already said. Correcting the extension is the only outcome that
        is both truthful and what was asked for. The correction happens
        *before* the in-place and already-exists checks below, so the file
        actually written is the one that was checked.

        ``produces_directory`` (``ToolSpec.produces_directory`` — ``split``,
        ``to-images``; see ADR 0031) turns that appending off entirely. A
        directory's name almost never has a dot in it, and "no suffix" is the
        *correct*, ordinary shape for one — appending ``default_suffix`` to
        ``-o parts`` would turn a real directory into a file-shaped path named
        ``parts.pdf`` that the tool then creates as a directory anyway,
        silently going where the caller did not ask it to.
        """
        # A parameter that names the format outright decides the extension,
        # for the derived path and the typed one alike.
        suffix = enforced_suffix or default_suffix

        if requested is not None:
            destination = Path(requested).expanduser().resolve()
            if not produces_directory and not destination.suffix:
                destination = destination.with_suffix(suffix)
            elif (
                not produces_directory
                and enforced_suffix is not None
                and destination.suffix.lower() != enforced_suffix.lower()
            ):
                destination = destination.with_suffix(enforced_suffix)
        elif inputs:
            first = inputs[0].path
            name = first.stem if produces_directory else f"{first.stem}{suffix}"
            destination = first.with_name(name)
        else:
            raise InvalidParameterError(
                "No output path was given, and there is no input to derive one from.",
                remedy="Pass an explicit output path with -o.",
            )

        if _is_one_of(destination, inputs):
            raise InPlaceOverwriteError(
                f"The output path is also an input: {destination}",
                context={"path": str(destination)},
            )

        parent = destination.parent
        if not parent.is_dir():
            raise OutputNotWritableError(
                f"The output directory does not exist: {parent}",
                context={"path": str(parent)},
            )

        if destination.exists() and not force:
            raise OutputExistsError(
                f"The output path already exists: {destination}",
                context={"path": str(destination)},
            )

        return cls(destination=destination, force=force)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What an operation produced, described identically by both engines.

    The UI layer reads ``engine_used`` to display a badge and otherwise does not
    know or care which strategy ran. That symmetry is the point of the dual
    engine design: one result type, two implementations.
    """

    outputs: tuple[Path, ...]
    engine_used: Engine
    duration_ms: int = 0
    #: e.g. ``gs/10.03.0`` — whatever actually did the work, local or remote.
    engine_version: str | None = None
    #: Counts, page numbers, sizes. Never document contents.
    details: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["DocumentRef", "Engine", "JobStatus", "OutputTarget", "ToolResult"]
