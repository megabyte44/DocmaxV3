"""The only module permitted to write to a destination.

v2 wrote output straight where it was going: ``open(out, "wb")``,
``write_text``, ``cv2.imwrite``. Three failures shipped from that, and all three
are structural rather than careless — no amount of review catches them, because
each individual write looks correct in isolation:

* A crash or Ctrl-C mid-write left a truncated file where the user's document
  used to be. Half a PDF is not a PDF.
* A re-run destroyed the previous run's output before knowing whether the new
  one would succeed.
* Several paths wrote over their own input, so a failure took the source with it.

Everything here follows the same three steps: write to a temp file *beside* the
destination, check it, and only then ``os.replace()`` it into place. That call is
atomic on POSIX and on Windows, so a reader sees either the old file or the new
one and never a partial one. Any exception on the way deletes the temp and leaves
the destination untouched.

"Beside" is load-bearing. A temp file in the system temp directory would make the
final step a cross-device copy, which is neither atomic nor free — so the temp is
created in the destination's own directory. The cost is that a full volume fails
at write or validate time rather than mid-swap, which is the trade ADR 0003
accepts: :class:`InsufficientDiskSpaceError` beats a half-written file.

Validators run against the temp while the destination is still untouched, so "the
operation produced a broken file" is a condition the user never observes.

``tests/hygiene/test_no_direct_writes.py`` walks the AST of every library package
and fails on any write outside this module. See
``docs/adr/0003-atomic-writes.md``.
"""

from __future__ import annotations

import errno
import os
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from docmax.core.branding import CLI_NAME
from docmax.core.errors import (
    InsufficientDiskSpaceError,
    OutputNotWritableError,
    OutputValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from docmax.core.models import OutputTarget
    from docmax.core.protocols import Validator

#: Prefix for staged files. Derived rather than written out, because brand
#: literals live in ``core/branding.py`` alone and a hygiene test enforces it.
#: The leading dot keeps debris out of a user's file listing if a hard kill
#: (SIGKILL, power loss) ever prevents cleanup.
_STAGE_PREFIX = f".{CLI_NAME}-"

#: Out-of-space conditions. ``EDQUOT`` is a quota rather than a full disk, but
#: the user-facing advice is the same: this write needs room it cannot get.
_NO_SPACE_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})


def _fail_for_oserror(exc: OSError, destination: Path) -> Exception:
    """Translate a filesystem error into the typed error that names the fix."""
    if exc.errno in _NO_SPACE_ERRNOS:
        return InsufficientDiskSpaceError(
            f"Ran out of space while writing {destination}.",
            context={"path": str(destination)},
        )
    return OutputNotWritableError(
        f"Could not write to {destination}: {exc.strerror or exc}",
        context={"path": str(destination)},
    )


def _stage(destination: Path, *, directory: bool) -> Path:
    """Create an empty temp file or directory beside ``destination``.

    Creating it eagerly means the destination's directory is proven writable
    before any work begins, rather than after an expensive operation has already
    run.
    """
    parent = destination.parent
    try:
        if directory:
            return Path(tempfile.mkdtemp(dir=parent, prefix=_STAGE_PREFIX))
        handle, name = tempfile.mkstemp(dir=parent, prefix=_STAGE_PREFIX, suffix=".part")
        os.close(handle)
        return Path(name)
    except OSError as exc:
        raise _fail_for_oserror(exc, destination) from exc


def _discard(staged: Path) -> None:
    """Remove a staged file or tree. Never raises — it runs on the failure path.

    A cleanup error must not replace the error that caused the cleanup. Reporting
    a full disk as a permissions problem sends the user after the wrong thing
    entirely, and the original exception is the one they need.
    """
    with suppress(OSError):
        if staged.is_dir():
            _remove_tree(staged)
        else:
            staged.unlink(missing_ok=True)


def _remove_tree(root: Path) -> None:
    """Depth-first delete of a staged directory.

    Hand-rolled rather than ``shutil.rmtree`` to keep this module's imports to
    what it strictly needs; the trees involved are ones we just created, so none
    of ``rmtree``'s handling of hostile input is required. Symlinked directories
    are unlinked rather than descended into, so a staged tree containing a link
    cannot delete anything outside itself.
    """
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            _remove_tree(child)
        else:
            child.unlink(missing_ok=True)
    root.rmdir()


def _validate(produced: Path, validators: Sequence[Validator], destination: Path) -> None:
    """Run every validator against the staged output.

    Validators signal failure with :class:`OutputValidationError`. Anything else
    escaping one is a bug *in the validator*, and it is wrapped rather than
    propagated so that callers can still rely on the documented failure type.
    """
    for validator in validators:
        try:
            validator(produced)
        except OutputValidationError:
            raise
        except Exception as exc:
            raise OutputValidationError(
                f"A validator for {destination.name} failed unexpectedly: {exc}",
                context={
                    "path": str(destination),
                    "validator": getattr(validator, "__name__", repr(validator)),
                },
            ) from exc


def _commit(staged: Path, destination: Path) -> None:
    """Swap a staged *file* into place. The atomic step."""
    try:
        os.replace(staged, destination)
    except OSError as exc:
        raise _fail_for_oserror(exc, destination) from exc


def _commit_tree(staged: Path, destination: Path) -> None:
    """Swap a staged *directory* into place, as close to atomically as possible.

    ``os.replace`` will not put a directory over an existing one — POSIX requires
    the target to be empty, Windows refuses outright. So an existing destination
    is moved aside first and deleted only once the new tree is in place.

    The window in which neither tree is at the destination is two rename calls
    wide. A failure inside it leaves the old tree beside the destination rather
    than deleted, and the code below puts it back.
    """
    previous: Path | None = None
    try:
        if destination.exists():
            previous = destination.with_name(f"{_STAGE_PREFIX}{destination.name}.previous")
            _discard(previous)
            os.replace(destination, previous)
        os.replace(staged, destination)
    except OSError as exc:
        if previous is not None and not destination.exists():
            with suppress(OSError):
                os.replace(previous, destination)
        raise _fail_for_oserror(exc, destination) from exc

    if previous is not None:
        _discard(previous)


@contextmanager
def atomic_write(
    target: OutputTarget,
    *,
    validators: Sequence[Validator] = (),
) -> Iterator[BinaryIO]:
    """Yield a binary handle whose bytes reach ``target`` only if all goes well.

    For output this process writes itself::

        with atomic_write(target, validators=(page_count_is(10),)) as handle:
            writer.write(handle)

    The handle is flushed and ``fsync``-ed before the swap, so the rename cannot
    outrun the data on a machine that loses power immediately afterwards.
    """
    destination = target.destination
    staged = _stage(destination, directory=False)

    try:
        with staged.open("wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        _discard(staged)
        raise _fail_for_oserror(exc, destination) from exc
    except BaseException:
        # BaseException, not Exception: Ctrl-C is the case this exists for, and
        # KeyboardInterrupt does not inherit from Exception.
        _discard(staged)
        raise

    try:
        _validate(staged, validators, destination)
        _commit(staged, destination)
    except BaseException:
        _discard(staged)
        raise


@contextmanager
def atomic_path(
    target: OutputTarget,
    *,
    validators: Sequence[Validator] = (),
) -> Iterator[Path]:
    """Yield a temp path for something *else* to write, then swap it into place.

    For output produced by an external program — Ghostscript, Pandoc, xelatex —
    which needs a filename rather than a file object::

        with atomic_path(target, validators=(is_a_pdf,)) as staged:
            run([gs, ..., f"-sOutputFile={staged}", str(source)], timeout=...)

    A program that unlinks and recreates the path is fine. One that exits zero
    having written nothing is not, and is caught here rather than delivered:
    Ghostscript does exactly that under some failures, and replacing a real
    document with an empty file is worse than failing.
    """
    destination = target.destination
    staged = _stage(destination, directory=False)

    try:
        yield staged

        if not staged.exists():
            raise OutputValidationError(
                f"Nothing was written for {destination.name}.",
                remedy="This is a bug in the engine that ran. Please report it.",
                context={"path": str(destination)},
            )
        if staged.stat().st_size == 0:
            raise OutputValidationError(
                f"The operation produced an empty file for {destination.name}.",
                remedy="This is a bug in the engine that ran. Please report it.",
                context={"path": str(destination)},
            )

        _validate(staged, validators, destination)
        _commit(staged, destination)
    except BaseException:
        _discard(staged)
        raise


@contextmanager
def atomic_dir(
    target: OutputTarget,
    *,
    validators: Sequence[Validator] = (),
) -> Iterator[Path]:
    """Yield a temp directory that becomes ``target`` as a unit.

    For operations with many outputs — ``split``, ``to-images`` — where a
    cancelled run must not leave a directory holding the first forty pages of a
    thousand. The validator receives the staged *directory*, so a check like "one
    file per page" runs before any of it is visible.

    The cost, accepted in ADR 0003, is that the whole tree is staged on the
    destination's filesystem before the swap.
    """
    destination = target.destination
    staged = _stage(destination, directory=True)

    try:
        yield staged
        _validate(staged, validators, destination)
        _commit_tree(staged, destination)
    except BaseException:
        _discard(staged)
        raise


__all__ = ["atomic_dir", "atomic_path", "atomic_write"]
