"""The ``docmax mcp`` command group: serve locally, or wire a client to it.

``docmax mcp`` alone still does exactly what it always has — speak JSON-RPC on
stdio for whichever client's own configuration starts it. That configuration
was, until now, a JSON snippet in the README that a person had to find,
understand, and paste by hand into the right file in the right shape. Wrong
shape, wrong path, or a typo in the merge (most people hand-edit a file that
already has other entries in it) all fail silently from DocMax's point of
view — the client just never offers the tools, with no error to look up.

``docmax mcp connect`` is the fix: it finds the client's own config file and
merges a ``docmax`` entry into it, touching nothing else already there. Every
write goes through ``core/atomic.py`` like everywhere else in this codebase,
so a crash mid-write cannot leave a client's config half-written and unusable.

``connect --remote`` targets the HTTP bridge (ADR 0035) instead of stdio, for
a client that talks to a server running somewhere else. It reuses whatever
``docmax cloud login`` already stored — endpoint and API key — and writes the
key into the client's own config file rather than printing it anywhere.
ADR 0014's "the key never appears in output" is about the terminal and the
``--json`` envelope; a second local file, exactly as readable by the same
person's account as the config file the key already lived in, is not output.

Only clients whose config shape is documented and stable are handled —
Claude Code's project-scoped ``.mcp.json`` for both stdio and remote entries,
plus Claude Desktop and Cursor's stdio-only config files for the local case.
A client not on this list gets the same manual snippet the README always
showed, never a guess at a schema this codebase cannot verify.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import platformdirs
import typer

from docmax.cli import commands
from docmax.cli.render import console, out
from docmax.core.branding import CLI_NAME

mcp_app = typer.Typer(
    name="mcp",
    help="Serve the tools over MCP, or connect an AI app to that server.",
    invoke_without_command=True,
    no_args_is_help=False,
)

_RootOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--root",
        help="A directory the server may read and write. Repeatable. Default: the working directory.",
    ),
]
_AllowCloudOption = Annotated[
    bool,
    typer.Option("--allow-cloud", help="Permit cloud engines for tools you already agreed to."),
]


@mcp_app.callback()
def mcp(
    ctx: typer.Context,
    root: _RootOption = None,
    allow_cloud: _AllowCloudOption = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Serve the tools over MCP, so an AI agent can drive DocMax locally.

    Speaks JSON-RPC on stdin and stdout, so it is started by an MCP client's
    configuration rather than by hand — see `docmax mcp connect` to write that
    configuration instead of copying it in by hand. Every tool the registry
    knows is offered, with a schema generated from its own declaration — the
    same router, the same validators, the same atomic writes as every other
    way in.

    **It may only touch what you allow.** Reads and writes are confined to
    `--root` (the working directory by default) and an agent cannot reach
    outside it. Existing files are never overwritten, and cloud engines are
    unavailable unless you pass `--allow-cloud` *and* have already agreed to
    that tool with `docmax cloud agree` — an agent cannot consent on your
    behalf.

    Needs the `mcp` extra. Without it this reports the exact install line
    rather than an import error.
    """
    if ctx.invoked_subcommand is not None:
        # `connect` handles its own options; this callback only owns the
        # no-subcommand (serve) case, exactly as the root app's own callback
        # owns `_no_command`.
        return

    from docmax.cli import json_output
    from docmax.cli.render import render_error
    from docmax.core.errors import DocMaxError, InvalidParameterError
    from docmax.mcp import require_available, serve

    json_output.note(json_out)

    try:
        if json_output.enabled():
            raise InvalidParameterError(
                f"`{CLI_NAME} mcp` speaks JSON-RPC on stdout, so --json cannot apply to it.",
                remedy=f"Run `{CLI_NAME} mcp` without --json; the protocol is the output.",
            )
        require_available()
        serve(root, allow_cloud=allow_cloud)
    except DocMaxError as exc:
        render_error(exc)
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# Known clients
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientTarget:
    """One MCP client this command knows how to wire up, resolved for this run."""

    name: str
    path: Path
    #: Evidence the app is actually on this machine, so `connect` never
    #: creates a directory tree for something that is not installed. Always
    #: `True` for a project-scoped file — writing one costs nothing and takes
    #: effect only if the client is ever pointed at this directory.
    detected: bool
    #: Whether `--remote` is implemented for this client's config shape.
    supports_remote: bool


def known_clients(project_root: Path) -> list[ClientTarget]:
    """Every client this command can wire up, in the order they are reported."""
    claude_desktop_dir = Path(platformdirs.user_config_dir("Claude", roaming=True))
    cursor_dir = Path.home() / ".cursor"

    return [
        ClientTarget(
            name="claude-desktop",
            path=claude_desktop_dir / "claude_desktop_config.json",
            detected=claude_desktop_dir.is_dir(),
            supports_remote=False,
        ),
        ClientTarget(
            name="cursor",
            path=cursor_dir / "mcp.json",
            detected=cursor_dir.is_dir(),
            supports_remote=False,
        ),
        ClientTarget(
            name="claude-code",
            path=project_root / ".mcp.json",
            detected=True,
            supports_remote=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def local_entry(roots: list[Path], *, allow_cloud: bool) -> dict[str, Any]:
    """The stdio entry: how a client should start `docmax mcp` itself."""
    args: list[str] = ["mcp"]
    for one_root in roots:
        args.extend(["--root", str(one_root)])
    if allow_cloud:
        args.append("--allow-cloud")
    return {"command": CLI_NAME, "args": args}


def remote_entry(*, endpoint: str, api_key: str) -> dict[str, Any]:
    """The HTTP entry: Claude Code's documented shape for a remote MCP server.

    The bearer token lands in the client's own config file, never in anything
    this command prints — see the module docstring.
    """
    return {
        "type": "http",
        "url": f"{endpoint}/v1/mcp",
        "headers": {"Authorization": f"Bearer {api_key}"},
    }


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def load_document(path: Path) -> dict[str, Any]:
    """The client's existing config, or an empty document if there is none.

    A file that exists but is not a JSON object is left alone rather than
    guessed at — `typer.BadParameter` names the file so the person can look at
    it themselves.
    """
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"{path} is not valid JSON, so `connect` will not touch it. "
            "Fix or remove it, then run this again."
        ) from exc
    if not isinstance(document, dict):
        raise typer.BadParameter(
            f"{path} is not a JSON object at the top level, so `connect` will not touch it."
        )
    return document


def merged(document: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """``document`` with ``mcpServers.docmax`` set to ``entry``.

    Returns the new document and whether a `docmax` entry was already there —
    everything else in `mcpServers`, and every other top-level key, survives
    untouched.
    """
    servers = document.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    existed = CLI_NAME in servers
    servers[CLI_NAME] = entry
    return {**document, "mcpServers": servers}, existed


def write_document(path: Path, document: dict[str, Any]) -> None:
    from docmax.core.atomic import atomic_write
    from docmax.core.models import OutputTarget

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2) + "\n"
    with atomic_write(OutputTarget(destination=path, force=True)) as handle:
        handle.write(text.encode("utf-8"))


def manual_snippet(entry: dict[str, Any]) -> str:
    """The same copy-pasteable block the README shows, for a client not on the list."""
    return json.dumps({"mcpServers": {CLI_NAME: entry}}, indent=2)


@dataclass(frozen=True)
class PlanItem:
    """One client's write, computed but not yet applied."""

    client: str
    path: Path
    action: str
    entry: dict[str, Any]
    document: dict[str, Any]

    def as_report(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "path": str(self.path),
            "action": self.action,
            "entry": self.entry,
        }


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


@mcp_app.command("connect")
def connect(
    root: _RootOption = None,
    allow_cloud: _AllowCloudOption = False,
    remote: Annotated[
        bool,
        typer.Option(
            "--remote", help="Wire up the cloud MCP bridge instead of the local stdio server."
        ),
    ] = False,
    client: Annotated[
        list[str] | None,
        typer.Option(
            "--client",
            help="Limit to one or more clients by name (claude-desktop, cursor, claude-code). "
            "Default: every client detected on this machine.",
        ),
    ] = None,
    project: Annotated[
        bool,
        typer.Option(
            "--project/--no-project",
            help="Also write Claude Code's project-scoped .mcp.json in the target directory.",
        ),
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would change; write nothing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Wire an AI app to this server, instead of hand-editing its config.

    Detects which known clients are on this machine — their config directory
    already existing is the evidence — and merges a `docmax` entry into each
    one's `mcpServers`, leaving every other entry in the file untouched. A
    client not detected (or not on the known list at all) is skipped, and if
    nothing was detected the same JSON block the README shows is printed
    instead, so nobody is left with no next step.

    `--remote` writes the cloud bridge's URL and your already-configured API
    key (`docmax cloud login`) into Claude Code's `.mcp.json` instead — the
    only client here with a documented remote-server shape. Other clients
    print instructions rather than a guessed-at config.
    """
    from docmax.cli import json_output

    json_output.note(json_out)

    roots = [p.resolve() for p in root] if root else [Path.cwd()]
    project_root = roots[0]

    if remote:
        from docmax.core.config import load as load_config

        config = load_config()
        if not config.api_key:
            raise typer.BadParameter(
                f"No cloud API key configured. Run `{CLI_NAME} cloud login` first, "
                "then `connect --remote` again."
            )
        entry = remote_entry(endpoint=config.cloud_endpoint, api_key=config.api_key)
    else:
        entry = local_entry(roots, allow_cloud=allow_cloud)

    candidates = known_clients(project_root)
    if client:
        wanted = set(client)
        unknown = wanted - {c.name for c in candidates}
        if unknown:
            raise typer.BadParameter(f"Unknown client(s): {', '.join(sorted(unknown))}.")
        candidates = [c for c in candidates if c.name in wanted]
    if not project:
        candidates = [c for c in candidates if c.name != "claude-code"]

    targets = [c for c in candidates if c.detected and (not remote or c.supports_remote)]

    if not targets:
        _report_nothing_to_do(entry, remote=remote, candidates=candidates)
        return

    plan: list[PlanItem] = []
    for target in targets:
        document = load_document(target.path)
        new_document, existed = merged(document, entry)
        plan.append(
            PlanItem(
                client=target.name,
                path=target.path,
                action="update" if existed else "create",
                entry=entry,
                document=new_document,
            )
        )

    if not json_output.enabled():
        out.print("[bold]Plan:[/bold]")
        for item in plan:
            out.print(f"  {item.client}: {item.action} {item.path}")

    if dry_run:
        if json_output.enabled():
            _emit_report(plan, wrote=False)
        return

    if not yes:
        from docmax.cli.interactive import is_interactive

        if json_output.enabled() or not is_interactive():
            message = "Refusing to write without --yes: nothing to confirm with."
            if json_output.enabled():
                from docmax.cli.render import _emit
                from docmax.core.errors import InvalidParameterError

                _emit(
                    json_output.failure(
                        InvalidParameterError(
                            message, remedy=f"Re-run with `{CLI_NAME} mcp connect -y`."
                        )
                    )
                )
            else:
                console.print(f"\n[yellow]{message}[/yellow]")
            raise typer.Exit(1)
        console.print()
        if not typer.confirm("Write these files?", default=False):
            raise typer.Exit(1)

    for item in plan:
        write_document(item.path, item.document)

    if json_output.enabled():
        _emit_report(plan, wrote=True)
    else:
        out.print()
        for item in plan:
            out.print(f"  [green]{item.client}: wrote {item.path}[/green]")


def _report_nothing_to_do(
    entry: dict[str, Any], *, remote: bool, candidates: list[ClientTarget]
) -> None:
    from docmax.cli import json_output

    if json_output.enabled():
        _emit_report([], wrote=False)
        return

    if remote and not any(c.supports_remote for c in candidates):
        out.print(
            "[yellow]No client here has a documented remote-server shape yet.[/yellow] "
            "Add this to its MCP configuration by hand:"
        )
    else:
        out.print("[yellow]No known client detected on this machine.[/yellow] Add this by hand:")
    out.print(manual_snippet(entry))


def _emit_report(plan: list[PlanItem], *, wrote: bool) -> None:
    from docmax.cli import json_output
    from docmax.cli.render import _emit

    _emit(json_output.report({"items": [item.as_report() for item in plan], "wrote": wrote}))


__all__ = ["connect", "mcp", "mcp_app"]
