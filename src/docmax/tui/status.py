"""Data for the TUI's cross-cutting screens — system check and cloud status.

Plain data, no ``textual`` import — the same discipline ``forms.py`` and
``catalog.py`` already keep, so the interesting half of a screen (what it
shows) is testable with no terminal and no event loop.

Both functions here read the *exact* sources ``docmax doctor`` and
``docmax cloud status`` already render from: ``tools/_binaries.py`` for the
former, ``core/config.py`` and ``core/consent.py`` for the latter. Neither
function lives in ``cli/`` — ``docmax.tui`` may not import ``docmax.cli`` (see
ADR 0020 and ``test_the_tui_imports_no_other_interface``) — so what is shared
between the CLI command and the TUI screen is the *data source* underneath
both, not the command itself. Reading the same source is what keeps the two
renderers from disagreeing; it does not require reaching into one interface
from another.

See GitHub issue #39: the TUI has no way to reach ``doctor`` or
``cloud status`` today. These are the data half of the fix; ``tui/app.py``
supplies the rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class BinaryStatus:
    """One external program's status, exactly as ``docmax doctor`` reports it."""

    name: str
    found: bool
    path: str | None
    used_by: tuple[str, ...]
    install_hint: str


def binary_statuses() -> list[BinaryStatus]:
    """Every external binary DocMax knows about, and whether it is on PATH.

    Reads ``tools/_binaries.EXTERNAL_BINARIES`` and calls ``find()`` for
    each — the same declaration and the same function ``doctor``'s table and
    its ``--json`` envelope both read. There is no second list of binaries
    here to fall out of step with the one ``doctor`` uses.
    """
    from docmax.tools import _binaries

    statuses = []
    for binary in _binaries.EXTERNAL_BINARIES:
        found_path = _binaries.find(binary.name)
        statuses.append(
            BinaryStatus(
                name=binary.name,
                found=found_path is not None,
                path=found_path,
                used_by=binary.used_by,
                install_hint=binary.install_hint(),
            )
        )
    return statuses


@dataclass(frozen=True, slots=True)
class CloudStatus:
    """The cloud arrangement, exactly as ``docmax cloud status`` reports it.

    Explicitly not a user profile — there is no login/account concept yet,
    only a bearer API key. See ``cli/cloud.py``'s module docstring and GitHub
    issue #39, which asks for this to be labelled "API key config" rather
    than anything that implies a real account exists.
    """

    endpoint: str
    api_key_configured: bool
    api_key_suffix: str | None
    offline: bool
    consented_tools: tuple[str, ...]
    config_path: Path


#: How much of a key to show. Mirrors ``cli/cloud.py``'s own choice, so the
#: two renderers agree even though each formats its own display string —
#: enough to tell two keys apart, useless for anything else.
_VISIBLE_SUFFIX = 4


def cloud_status() -> CloudStatus:
    """The same data ``docmax cloud status`` renders, gathered the same way.

    Never returns the key itself — only whether one is set and a masked
    suffix — matching the rule ``cli/cloud.py`` states outright: *"the key
    never appears in output."*
    """
    from docmax.core.config import config_file, consent_file, load
    from docmax.core.consent import ConsentStore

    config = load()
    store = ConsentStore(consent_file(), endpoint=config.cloud_endpoint)

    return CloudStatus(
        endpoint=config.cloud_endpoint,
        api_key_configured=bool(config.api_key),
        api_key_suffix=config.api_key[-_VISIBLE_SUFFIX:] if config.api_key else None,
        offline=config.offline,
        consented_tools=store.granted_tools(),
        config_path=config_file(),
    )


__all__ = ["BinaryStatus", "CloudStatus", "binary_statuses", "cloud_status"]
