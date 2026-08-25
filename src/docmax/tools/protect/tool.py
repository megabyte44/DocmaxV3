"""Metadata for ``protect``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._permissions import NAMES as PERMISSIONS

#: The algorithms pypdf can write, strongest first. Exposed under the
#: specification's names rather than invented ones, so a user reading the PDF
#: standard recognises what DocMax produced.
#:
#: The RC4 pair is here because the format allows it and an old reader may need
#: it -- not because it is a reasonable default. RC4 is broken; see ``local.py``.
ALGORITHMS = ("AES-256", "AES-128", "RC4-128", "RC4-40")

#: The default. Strong, and standard since PDF 2.0. It needs `cryptography`,
#: which the base install does not carry -- ``local.py`` explains why that is a
#: better trade than defaulting to something weak that always works.
DEFAULT_ALGORITHM = "AES-256"

SPEC = register(
    ToolSpec(
        name="protect",
        summary="Encrypt a PDF with a password.",
        category="secure",
        module=__name__.rpartition(".")[0],
        # Pure pypdf. Uploading a document in order to encrypt it would mean
        # handing the plaintext to someone else first, which is close to the
        # opposite of what this tool is for.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="password",
                description="The password needed to open the document.",
                type_="str",
                required=True,
            ),
            Param(
                name="owner_password",
                description="The password that bypasses the permissions. Default: the same one.",
                type_="str",
            ),
            Param(
                name="allow",
                description="What a reader may do. Repeatable, or comma-separated.",
                type_="str",
                default="all",
                choices=PERMISSIONS,
            ),
            Param(
                name="algorithm",
                description="Encryption algorithm.",
                type_="str",
                default=DEFAULT_ALGORITHM,
                choices=ALGORITHMS,
            ),
        ),
    )
)

__all__ = ["ALGORITHMS", "DEFAULT_ALGORITHM", "SPEC"]
