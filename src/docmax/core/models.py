"""The value types every layer passes around.

``core`` owns these because all four layers speak them: an interface parses
input into a :class:`DocumentRef` and an :class:`OutputTarget`, a tool's strategy
consumes them, and whatever ran hands back a :class:`ToolResult`. Nothing here
imports a UI framework or a document library, so the types cost nothing to
import and can be used from anywhere.

:class:`OutputTarget` is the load-bearing one. No engine ever receives a raw
destination ``Path`` — it receives a target that has already been checked against
its inputs. That is what makes "the output was also the input" *unrepresentable*
rather than merely discouraged, which matters because v2 shipped it: ``convert
x.md --to md`` destroyed the source file, and ``batch_convert`` over a directory
did it in bulk. See ``docs/adr/0003-atomic-writes.md``.

Presentation belongs to the interfaces. Nothing here carries a colour, a format
string, an exit code, or an HTTP status — a model that knew about those could
only serve one interface, and there are four.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docmax.core.errors import (
    InPlaceOverwriteError,
    InputNotFoundError,
    InvalidParameterError,
    OutputExistsError,
    OutputNotWritableError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class Engine(StrEnum):
    """Which implementation performs the work.

    ``AUTO`` is a *request*, never a result. The router resolves it to ``LOCAL``
    or ``CLOUD`` before any strategy runs, and :attr:`ToolResult.engine_used`
    always names the one that actually ran — so "which engine did this?" has an
    answer that is never "it depends".
    """

    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A file we were asked to read, already proven to exist.

    Built through :meth:`from_path` so that a missing file fails once, at the
    boundary, with a typed error naming the path — rather than as an ``OSError``
    from somewhere inside a parser after work has already begun and a temp file
    has already been created.
    """

    path: Path

    @classmethod
    def from_path(cls, path: Path | str) -> DocumentRef:
        """Resolve and verify ``path``, or raise :class:`InputNotFoundError`.

        A directory is rejected here rather than later: ``is_file`` is the check,
        not ``exists``. Handing a directory to a PDF parser produces a far worse
        error message than this one.
        """
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise InputNotFoundError(
                f"No such file: {resolved}",
                context={"path": str(resolved)},
            )
        return cls(path=resolved)

    @property
    def suffix(self) -> str:
        """Lower-cased extension, e.g. ``.pdf``. Case is not a format."""
        return self.path.suffix.lower()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """A destination that has been checked and is safe to write.

    The checks live in :meth:`resolve` rather than in each tool, so there is one
    implementation of them instead of fifty. Engine signatures accept an
    ``OutputTarget``, which means passing a bare ``Path`` is a type error under
    strict mypy — that is the mechanism that stops the checks being skipped by
    accident rather than by intent.
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
        force: bool = False,
    ) -> OutputTarget:
        """Validate a requested destination against the inputs it came from.

        Raises :class:`InPlaceOverwriteError` if the destination is any input,
        and :class:`OutputExistsError` if it exists without ``force``. Neither is
        recoverable further down: by the time bytes are being written, the source
        may already be gone.

        With no ``requested`` path, the destination is derived from the first
        input. That derivation can itself land on an input — deriving ``a.pdf``
        from ``a.pdf`` — and the in-place check below catches it, which is why
        the check runs after the derivation rather than only on user input.
        """
        if requested is not None:
            destination = Path(requested).expanduser().resolve()
        elif inputs:
            first = inputs[0].path
            destination = first.with_name(f"{first.stem}{default_suffix}")
        else:
            raise InvalidParameterError(
                "No output path was given, and there is no input to derive one from.",
                remedy="Pass an explicit output path with -o.",
            )

        if destination in {ref.path for ref in inputs}:
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

    The interface layer reads :attr:`engine_used` to show a badge and otherwise
    does not know or care which strategy ran. That symmetry is the point of the
    dual-engine design: one result type, two implementations, and no caller
    written against a particular one.
    """

    outputs: tuple[Path, ...]
    engine_used: Engine
    duration_ms: int = 0
    #: Whatever actually did the work, in the same form from either engine —
    #: ``pypdf/4.2.0``, ``gs/10.03.0``. A result is traceable to an
    #: implementation regardless of whose machine produced it.
    engine_version: str | None = None
    #: Counts, page numbers, sizes. Never document *contents* — this travels
    #: into logs and into ``--json`` output.
    details: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["DocumentRef", "Engine", "OutputTarget", "ToolResult"]
