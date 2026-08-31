"""Zip archives — the one wire shape a directory-of-many-outputs cloud tool needs.

``docs/cloud-api.md`` carries exactly one file per job: one URL, one size, one
content type. Most cloud-capable tools write one file, so that shape is enough.
A tool whose output is a *directory* (``ToolSpec.produces_directory`` — ADR
0031; ``to-images`` is the first cloud tool shaped this way) has no single file
to send back, so its cloud engine and the reference server agree on one: the
job's output is a zip archive of exactly the files the local engine would have
written, and unpacking it on the way in is the one extra step
``tools/_cloud.py``'s shared flow takes when a tool says its output is a
directory.

Nothing here decides *whether* a tool's output is a directory. That is
``ToolSpec.produces_directory``, read by both ``CloudEngine`` and the server's
``RegistryRunner`` — so a bug in "is this tool directory-shaped" is one bug
rather than a client-side and a server-side copy that can disagree.

Private to ``tools``, like ``_pagespec`` and ``_formats``: no package of its
own, so the registry's directory walk never sees it.
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def zip_directory(directory: Path) -> bytes:
    """Every file directly inside ``directory``, zipped flat and sorted.

    Flat because every directory-producing tool today (``split``, ``to-images``)
    writes one level of files and no subdirectories — there is nothing to
    preserve a deeper structure for yet. Sorted so the same output always
    produces the same archive bytes, which costs nothing and rules out
    "the images were the same, the zip wasn't" as a source of confusion.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(child for child in directory.iterdir() if child.is_file()):
            archive.write(path, arcname=path.name)
    return buffer.getvalue()


def unzip_into(payload: bytes, directory: Path) -> None:
    """Extract every member of ``payload`` into ``directory``.

    ``directory`` is always a temp directory a caller staged with
    ``core.atomic.atomic_dir`` — never a real destination — so the single swap
    into place still happens exactly once, in ``atomic_dir``, after the
    caller's own validators have looked at what this wrote. ``zipfile``'s own
    extraction already refuses an absolute member path or one that climbs out
    of the target directory with ``..``, which is enough for content coming
    from a configured, consented-to cloud endpoint.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(path=directory)


__all__ = ["unzip_into", "zip_directory"]
