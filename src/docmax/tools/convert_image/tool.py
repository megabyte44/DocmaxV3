"""Metadata for ``convert-image``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. Pillow is not looked for here;
that happens in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._formats import image, image_for_suffix, readable_image_names

if TYPE_CHECKING:
    from collections.abc import Mapping


def suffix_for(params: Mapping[str, Any], current: str) -> str | None:
    """The extension the result must carry, given the run's parameters.

    ``--to png`` names the format outright, so the destination's own
    extension is no longer a second opinion about it: ``-o convert.jpg``
    becomes ``convert.png`` before anything is written or checked. Without
    this the user has to say the same thing twice and is refused when the two
    spellings disagree.

    ``current`` is the suffix the destination already has, and ``None`` means
    "leave it alone". That is the answer whenever ``--to`` was not supplied,
    and also when the extension is *already* this format under another of its
    spellings -- ``.jpeg`` is not rewritten to ``.jpg``, because they name one
    format and the difference is the user's to keep.

    An unrecognised name is not this function's error to raise -- the router
    calls it while merely *resolving a path*, and the run has not begun. It
    returns ``None`` and lets ``local.py`` refuse the value with the typed
    error and the full vocabulary, so the message a user sees is the same one
    whatever the destination happened to be.
    """
    value = params.get("to")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        wanted = image(value.strip())
    except Exception:
        return None
    if current and image_for_suffix(current) is wanted:
        return None
    return wanted.suffix


SPEC = register(
    ToolSpec(
        name="convert-image",
        summary="Convert an image to another format (PNG, JPEG, TIFF, BMP, GIF).",
        category="image",
        module=__name__.rpartition(".")[0],
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        # `output_required` is pinned to `convert` and `metadata` alone
        # (ADR 0033, tests/unit/test_registry.py) -- both because neither can
        # derive a trustworthy destination at all. convert-image differs: the
        # CLI's `-o` is already mandatory (no default), and a destination
        # extension that doesn't name a format `run()` recognises is refused
        # there rather than written mislabelled.
        default_suffix=".jpg",
        # `--to` decides the extension when it is given. ADR 0010's
        # vocabulary, consumed generically by `EngineRouter.target_for`
        # so the CLI and the TUI cannot disagree about it.
        suffix_for_params=suffix_for,
        # No `quality` parameter: that is compress-image's job. This tool
        # changes format, not size, and writes each format at a fixed
        # high-fidelity setting rather than asking the user to tune one. A
        # user who wants both converts, then compresses.
        params=(
            Param(
                name="to",
                # `--to` on a command line, "output format" in a form: the
                # flag name reads correctly only where the verb is already in
                # the command. Presentational only -- the wire name stays
                # `to`, so the CLI, MCP schema and API validation are
                # untouched.
                label="output format",
                description=(
                    "The image format to convert to. Optional: when omitted "
                    "the format is taken from the extension of -o."
                ),
                type_="str",
                default=None,
                # Optional rather than required, unlike `convert`'s own `--to`.
                # Pandoc cannot infer a document format from an extension, so
                # that tool must ask; an image extension names its format
                # reliably, so demanding both would make `-o out.png --to png`
                # the only accepted spelling of the obvious. Supplying both is
                # still allowed -- and a disagreement between them is refused
                # in `local.py` rather than resolved silently, because either
                # choice would write a file whose name lies about its bytes.
                required=False,
                # Read from the shared table, never retyped. ADR 0010.
                choices=readable_image_names(),
            ),
        ),
    )
)

__all__ = ["SPEC"]
