"""The permission vocabulary, shared by the two tools that speak it.

``protect`` grants permissions and ``permissions`` reports them. If each owned
its own list of names, a document could be protected with ``--allow copy`` and
then reported as allowing something spelled differently — so the names are
defined once, here, the way ``_pagespec`` owns page selections.

## What a PDF permission actually is

Permissions live *inside encryption*: they are a bit field in the encryption
dictionary, and a file with no encryption has no permission field at all. That
is why :func:`describe` reports everything as allowed for an unencrypted
document — not as a default, but because nothing is restricted.

It is also why they are advisory. The bits tell a conforming reader what the
document's owner would prefer; they are not enforced by cryptography, and a
reader that ignores them is not breaking anything it cannot break. DocMax says
so plainly rather than implying a guarantee the format does not make.

The eight names map onto the eight meaningful bits of ISO 32000-1 table 22.
They are named for what a user does — ``copy``, ``annotate`` — rather than for
the specification's wording, but each maps to exactly one standard bit and
invents none.

Private to ``tools``: no package of its own, so the registry's directory walk
never sees it. pypdf is imported inside the functions that need it, never at
module scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: User-facing name -> the ``pypdf.constants.UserAccessPermissions`` member it
#: is. Resolved by name rather than by hardcoding the bit values, so pypdf stays
#: the single source of truth for what each bit is worth.
_MEMBERS: Mapping[str, str] = {
    "print": "PRINT",
    "modify": "MODIFY",
    "copy": "EXTRACT",
    "annotate": "ADD_OR_MODIFY",
    "forms": "FILL_FORM_FIELDS",
    "accessibility": "EXTRACT_TEXT_AND_GRAPHICS",
    "assemble": "ASSEMBLE_DOC",
    "print-hq": "PRINT_TO_REPRESENTATION",
}

#: Every permission name, in the order help text and tables should list them.
NAMES: tuple[str, ...] = tuple(_MEMBERS)

#: What each name means, for ``--help`` and for the ``permissions`` table. Short
#: enough to sit in a terminal column.
DESCRIPTIONS: Mapping[str, str] = {
    "print": "Print the document.",
    "modify": "Change the content.",
    "copy": "Copy text and graphics out.",
    "annotate": "Add or change annotations.",
    "forms": "Fill in form fields.",
    "accessibility": "Extract content for accessibility tools.",
    "assemble": "Insert, rotate or delete pages.",
    "print-hq": "Print at full resolution rather than a degraded one.",
}


def parse(raw: Any) -> tuple[str, ...]:
    """Normalise what a user typed into permission names, or say what is wrong.

    Accepts one string, a comma-separated string, or a list of either, so a CLI
    can offer ``--allow print --allow copy`` and ``--allow print,copy`` without
    either form being second class. ``all`` and ``none`` are accepted as the two
    obvious wholesale answers.

    Order is not preserved and duplicates collapse: this describes a *set* of
    permissions, and ``print,print`` is not a different request from ``print``.
    """
    if raw is None:
        return ()

    items: list[Any] = [raw] if isinstance(raw, str) else list(raw)
    found: set[str] = set()

    for item in items:
        if not isinstance(item, str):
            raise _fail(item)
        for term in item.split(","):
            name = term.strip().lower()
            if not name:
                continue
            if name == "all":
                found.update(NAMES)
                continue
            if name == "none":
                continue
            if name not in _MEMBERS:
                raise _fail(name)
            found.add(name)

    return tuple(name for name in NAMES if name in found)


def flags_for(names: Iterable[str]) -> int:
    """The permission bits for ``names``, as the integer pypdf's ``encrypt`` wants.

    Granting nothing is a legitimate request, and it produces 0 rather than an
    error: a document that may only be opened and read is exactly what the
    strictest ``--allow none`` means.
    """
    from pypdf.constants import UserAccessPermissions

    bits = 0
    for name in names:
        member = _MEMBERS.get(name)
        if member is None:
            raise _fail(name)
        bits |= int(getattr(UserAccessPermissions, member))
    return bits


def describe(value: int | None) -> dict[str, bool]:
    """Turn a permission bit field into ``name -> allowed``.

    ``None`` means the document carries no encryption dictionary, so there is no
    permission field to read and nothing is restricted. Every name reports
    ``True`` — which is the truth about the file, not a guess.
    """
    from pypdf.constants import UserAccessPermissions

    if value is None:
        return dict.fromkeys(NAMES, True)

    bits = int(value)
    return {
        name: bool(bits & int(getattr(UserAccessPermissions, member)))
        for name, member in _MEMBERS.items()
    }


def _fail(name: Any) -> InvalidParameterError:
    return InvalidParameterError(
        f"{name!r} is not a permission.",
        remedy=f"Use one of: {', '.join(NAMES)} — or 'all' or 'none'.",
        context={"parameter": "permissions", "permission": name},
    )


__all__ = ["DESCRIPTIONS", "NAMES", "describe", "flags_for", "parse"]
