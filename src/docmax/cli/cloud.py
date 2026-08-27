"""The ``docmax cloud`` command group: credentials, consent, and what is true now.

Five commands and no more. Everything here is about the *arrangement* with an
endpoint — who you are, what you have agreed to send it — and never about
running a tool, which is every other command's job.

## The key is written in plaintext, and that is said out loud

[ADR 0014](../../../docs/adr/0014-api-key-storage.md) records why: a keyring
means a compiled dependency and a platform backend the base install must not
carry, no backend at all on the headless Linux boxes self-hosters use, and — the
part that actually matters — an invitation to relax about a token that is still
handed to a process and still sent over a network. So the key sits in
``config.toml`` beside every other setting, the file is created ``0600`` where
the filesystem supports it, and ``login`` prints the path and the limitation
every time rather than burying it in a manual.

The rule that carries the weight is the other one: **the key never appears in
output.** Not on stdout, not on stderr, not in a `--json` envelope, not in an
exception, not in `ToolResult.details`. ``status`` reports a masked suffix and
nothing more, so "is the key I think is there actually there?" is answerable
without printing it.

## Consent is recorded here and enforced elsewhere

``ConsentStore`` is the record; ``EngineRouter`` is the gate. These commands
read and write the record. They never decide whether an upload may happen —
that stays in one place, which is what makes "no document is uploaded without
consent" a property of one function rather than of several.
"""

from __future__ import annotations

import os
import re
import stat
from contextlib import suppress
from typing import TYPE_CHECKING, Annotated

import typer

from docmax.cli.render import console, out
from docmax.core.branding import CLI_NAME
from docmax.core.config import config_file, consent_file, load
from docmax.core.consent import CONSENT_TERMS_VERSION, ConsentStore

if TYPE_CHECKING:
    from pathlib import Path

cloud_app = typer.Typer(
    name="cloud",
    help="Sign in to a cloud endpoint, and manage what you have agreed to send it.",
    no_args_is_help=True,
)

#: How much of a key ``status`` will show. Enough to tell two keys apart, far
#: too little to use.
_VISIBLE_SUFFIX = 4


@cloud_app.command()
def login(
    api_key: Annotated[
        str,
        typer.Option(
            "--api-key",
            help="The key for the endpoint. Read from a prompt if omitted.",
            prompt="API key",
            hide_input=True,
            show_default=False,
        ),
    ],
) -> None:
    """Store an API key for the configured cloud endpoint.

    The key is written to your config file **in plaintext**. Anything that can
    read your home directory can read it. See the note this prints.
    """
    destination = config_file()
    _write_api_key(destination, api_key)

    config = load()
    out.print(f"[green]Signed in[/green] for {config.cloud_endpoint}", soft_wrap=True)
    console.print(f"  [dim]Key written to {destination}[/dim]", soft_wrap=True)
    console.print(
        "  [yellow]Stored in plaintext.[/yellow] Anything that can read your home "
        "directory can read it — do not commit this file."
    )


@cloud_app.command()
def logout() -> None:
    """Remove the stored API key.

    Removes it from the config file only. A key set through the environment is
    not DocMax's to unset, and is reported rather than silently left in place.
    """
    destination = config_file()
    removed = _write_api_key(destination, None)

    if removed:
        out.print("[green]Signed out.[/green] The stored key was removed.")
    else:
        out.print("[dim]No stored key to remove.[/dim]")

    from docmax.cloud_client.config import API_KEY_ENV

    if os.environ.get(API_KEY_ENV):
        console.print(
            f"  [yellow]{API_KEY_ENV} is still set in your environment[/yellow], "
            "and takes precedence over the config file."
        )


@cloud_app.command()
def status() -> None:
    """Show the endpoint, whether a key is configured, and what you have agreed to send.

    Never prints the key. A masked suffix is enough to tell two keys apart and
    useless for anything else.
    """
    from rich.table import Table

    config = load()
    store = ConsentStore(consent_file(), endpoint=config.cloud_endpoint)
    granted = store.granted_tools()

    table = Table(title="Cloud", title_justify="left")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Endpoint", config.cloud_endpoint)
    table.add_row("API key", _masked(config.api_key))
    table.add_row(
        "Offline",
        "[yellow]yes — cloud is unreachable regardless of flags[/yellow]"
        if config.offline
        else "no",
    )
    table.add_row("Consented tools", ", ".join(granted) if granted else "[dim]none[/dim]")
    table.add_row("Config file", str(config_file()))
    out.print(table)

    if config.api_key and not granted:
        console.print(
            f"  [dim]A key is set but nothing is consented. Run "
            f"`{CLI_NAME} cloud consent <tool>`, or agree when asked.[/dim]"
        )


@cloud_app.command()
def consent(
    tool: Annotated[
        str | None,
        typer.Argument(help="The tool to consent for. Omit to list what is agreed."),
    ] = None,
) -> None:
    """Agree that this tool's documents may be uploaded to the configured endpoint.

    Consent is per tool and per endpoint: agreeing to send scans to a box on
    your LAN is not agreeing to send contracts to a service on the internet.
    Nothing here expires, and turning `offline` on and off again does not
    re-ask.
    """
    config = load()
    store = ConsentStore(consent_file(), endpoint=config.cloud_endpoint)

    if tool is None:
        granted = store.granted_tools()
        if not granted:
            out.print("[dim]Nothing is consented for this endpoint.[/dim]")
            return
        out.print(f"Consented for [bold]{config.cloud_endpoint}[/bold]:")
        for name in granted:
            out.print(f"  {name}")
        return

    _require_cloud_capable(tool)
    store.record(tool)
    out.print(
        f"[green]Agreed.[/green] {tool} may upload documents to {config.cloud_endpoint}",
        soft_wrap=True,
    )
    console.print(
        f"  [dim]Recorded in {consent_file()} — revoke it any time.[/dim]", soft_wrap=True
    )


@cloud_app.command()
def revoke(
    tool: Annotated[
        str | None,
        typer.Argument(help="The tool to revoke. Omit to revoke everything."),
    ] = None,
) -> None:
    """Withdraw consent, for one tool or for all of them."""
    config = load()
    store = ConsentStore(consent_file(), endpoint=config.cloud_endpoint)

    if tool is None:
        count = store.revoke_all()
        out.print(f"[green]Revoked[/green] {count} grant(s).")
        return

    if store.revoke(tool):
        out.print(f"[green]Revoked.[/green] {tool} may no longer upload documents.")
    else:
        out.print(f"[dim]{tool} was not consented for this endpoint.[/dim]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_cloud_capable(tool: str) -> None:
    """Refuse to record consent for a tool that cannot upload anything.

    Consent for a pypdf-only tool would be a promise about something that never
    happens, and it would sit in the record looking meaningful.
    """
    from docmax.core.models import Engine
    from docmax.core.registry import get_tool

    spec = get_tool(tool)
    if not spec.supports(Engine.CLOUD):
        raise typer.BadParameter(
            f"{tool!r} has no cloud engine — it always runs locally, so there is "
            "nothing to consent to."
        )


def _masked(api_key: str | None) -> str:
    if not api_key:
        return "[yellow]not configured[/yellow]"
    return f"[green]configured[/green] [dim](…{api_key[-_VISIBLE_SUFFIX:]})[/dim]"


def _write_api_key(path: Path, api_key: str | None) -> bool:
    """Set or remove ``[cloud] api_key`` in ``path``, leaving everything else alone.

    A targeted line edit rather than a re-serialise. The standard library reads
    TOML and does not write it, and rewriting the file from its parsed form
    would silently delete the user's comments and their ordering — a config file
    is something a person maintains, not a data store DocMax owns.

    Returns whether a key was present before.
    """
    from docmax.core.atomic import atomic_write
    from docmax.core.models import OutputTarget

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    updated, existed = _set_api_key(original, api_key)

    path.parent.mkdir(parents=True, exist_ok=True)
    # Through `atomic_write` like everything else: a crash mid-write must not
    # leave a truncated config file, which would lock the user out of every
    # setting rather than one.
    with atomic_write(OutputTarget(destination=path, force=True)) as handle:
        handle.write(updated.encode("utf-8"))

    _restrict(path)
    return existed


#: `api_key = ...` at the start of a line, however it is spaced.
_API_KEY_LINE = re.compile(r"^\s*api_key\s*=.*$", re.MULTILINE)
_CLOUD_SECTION = re.compile(r"^\s*\[cloud\]\s*$", re.MULTILINE)


def _set_api_key(original: str, api_key: str | None) -> tuple[str, bool]:
    """The edited file contents, and whether a key was there already.

    Split out from the writing so it can be tested as a pure function — the
    interesting cases are all about *shapes of file*, and none of them need a
    filesystem.
    """
    existing = _API_KEY_LINE.search(original)

    if api_key is None:
        if not existing:
            return original, False
        # Take the newline with the line, so removing a key does not leave a
        # blank line behind every time.
        start, end = existing.span()
        end = min(end + 1, len(original))
        return original[:start] + original[end:], True

    line = f'api_key = "{_escape(api_key)}"'
    if existing:
        return _API_KEY_LINE.sub(line, original, count=1), True

    section = _CLOUD_SECTION.search(original)
    if section:
        at = section.end()
        return f"{original[:at]}\n{line}{original[at:]}", False

    prefix = original if not original or original.endswith("\n") else original + "\n"
    return f"{prefix}\n[cloud]\n{line}\n", False


def _escape(value: str) -> str:
    """Escape for a TOML basic string. Backslash first, or it escapes the escapes."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _restrict(path: Path) -> None:
    """Owner-only, where the filesystem has a notion of it.

    Not a defence against a determined attacker on the same machine — it is the
    ordinary case of a world-readable home directory, and it costs one call.
    Windows inherits the profile's ACL and is left alone, which ADR 0014 states
    rather than papers over.
    """
    # `os.name`, not `sys.platform`: mypy narrows the latter to the platform it
    # is checking on, and then calls everything after this line dead code.
    if os.name == "nt":  # pragma: no cover - platform-specific
        return

    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def terms_summary(tool: str, endpoint: str) -> str:
    """The one-paragraph statement a user agrees to, used by the prompt.

    Here rather than in ``execution.py`` so the wording a user sees when asked
    and the wording behind `cloud consent` cannot drift apart.
    """
    return (
        f"Running {tool!r} in the cloud uploads your document to {endpoint}.\n"
        "  The endpoint deletes it when the job finishes or fails, never logs its "
        "contents, and never keeps it for training or analytics.\n"
        f"  This is recorded per tool and per endpoint (terms v{CONSENT_TERMS_VERSION}), "
        "and you can revoke it at any time."
    )


__all__ = ["cloud_app", "terms_summary"]
