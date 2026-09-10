"""Running installer subprocesses: pip extras and OS package managers.

Distinct from :func:`docmax.tools._binaries.run`: that primitive is for short
tool invocations whose whole output is captured for one error message. An
install can run for minutes — `pip` building `opencv-python-headless` from a
slow mirror — and a user staring at a silent terminal that long will assume it
hung. So this streams output line by line as it arrives instead of buffering
it, and has no short fixed timeout: an install's duration is the package
manager's to decide, not ours to guess at.

This is the one place in `tools` allowed to change something other than a
document. `_binaries.run` only ever calls a program the user already
installed, on a document the user handed it; running an installer is a
different kind of action, and folding it into `_binaries.py` — whose own
docstring is specific to *finding and running* a program a local engine needs
at document-processing time — would blur that.

Never imports `rich` or `typer`. `docmax setup` and the TUI's install buttons
both call this module directly — `docmax.cli` and `docmax.tui` are peers that
may not import each other, so the logic they share (what's missing, and how
to install one thing) lives here rather than in either interface, exactly as
`_binaries.py` already does for finding things. A caller wanting live output
supplies its own `on_output` callback rather than this module choosing how to
print one.

**Verification, not trust.** A package manager can exit `0` having installed
the wrong thing, or already having it, and can exit non-zero after actually
succeeding (a post-install hook failing while the binary it placed on `PATH`
works fine). So `ok` (the process's exit code) and `verified` are reported
separately, and a caller must never treat `ok` alone as success.
`install_binary` sets `verified` itself, via the same `_binaries.find` every
routing decision already uses. `install_pip_extra` cannot: an extra has no
single import name this module could generically check, so it always reports
`verified=False` and leaves the real check to the caller, who re-runs the
affected tool's own `is_available()` through the registry.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.models import Engine
from docmax.tools import _binaries

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.registry import ToolSpec
    from docmax.tools._binaries import Binary


@dataclass(frozen=True, slots=True)
class PipExtra:
    """One pip extra (`pyproject.toml`'s `[project.optional-dependencies]`),
    and what it costs to install.

    Mirrors `Binary` on purpose: same shape, same reason -- a `ToolSpec` names
    an extra by a bare string (`pip_extra`), and this is the one place that
    string's cost and contents are recorded, so `setup` and the TUI's install
    buttons read it generically rather than each hand-writing a description.
    """

    #: Matches `ToolSpec.pip_extra` and the extra's own name in `pyproject.toml`.
    name: str
    #: The packages it installs, as a person would recognise them -- not
    #: necessarily one-to-one with PyPI's own list (`rembg[cpu]` is one entry
    #: here because that is the one line `pip` is actually given).
    packages: tuple[str, ...]
    #: Approximate download size, hand-maintained like `Binary.size_hint` --
    #: not measured, and not re-verified against a live index. Good enough to
    #: warn someone before a 200 MB install starts on a slow connection; wrong
    #: by itself is not a reason to add a network call before showing it.
    size_hint: str = ""


#: One entry per extra any `ToolSpec.pip_extra` names -- kept in sync by
#: `tests/unit/test_registry.py::test_pip_extra_catalogue_matches_the_registry`,
#: the same discipline `EXTERNAL_BINARIES` gets from
#: `test_binary_catalogue_matches_the_registry`.
PIP_EXTRAS: tuple[PipExtra, ...] = (
    PipExtra(name="images", packages=("Pillow", "img2pdf"), size_hint="~5 MB"),
    PipExtra(name="ocr", packages=("opencv-python-headless", "numpy"), size_hint="~90 MB"),
    PipExtra(name="tables", packages=("pdfplumber", "pandas", "openpyxl"), size_hint="~35 MB"),
    PipExtra(
        name="remove-bg",
        packages=("rembg[cpu]",),
        # rembg itself is small; onnxruntime and a background-removal model
        # (fetched on first real use, not at install time) are the real cost.
        size_hint="~200 MB (plus a one-time model download on first use)",
    ),
    PipExtra(name="crypto", packages=("cryptography",), size_hint="~4 MB"),
)

_PIP_EXTRAS_BY_NAME = {extra.name: extra for extra in PIP_EXTRAS}


def describe_extra(name: str) -> PipExtra:
    """The declaration for `name`. Raises `KeyError` for an unknown extra."""
    return _PIP_EXTRAS_BY_NAME[name]


@dataclass(frozen=True, slots=True)
class InstallResult:
    """What happened, and whether it actually worked.

    `dry_run` callers get `ok=True, verified=False` and an empty
    `stdout_tail` — nothing ran, so there is nothing to have verified.
    """

    ok: bool
    verified: bool
    command: tuple[str, ...]
    stdout_tail: str


def install_binary(
    binary: Binary,
    manager: str,
    *,
    dry_run: bool,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None = None,
) -> InstallResult:
    """Run `binary`'s install command for `manager`, then re-check with `find`.

    `manager` is supplied by the caller — normally `_binaries.manager_available()`
    — rather than detected again here, so a caller that already confirmed a
    manager is on `PATH` cannot silently get a different answer this time.
    """
    argv = binary.install_argv_for(manager)
    if argv is None:
        raise ValueError(f"{binary.name!r} has no install command for manager {manager!r}")

    if dry_run:
        return InstallResult(ok=True, verified=False, command=argv, stdout_tail="")

    streamed = _stream(argv, cancellation=cancellation, on_output=on_output)
    verified = _binaries.find(binary.name) is not None
    return InstallResult(
        ok=streamed.ok,
        verified=verified,
        command=streamed.command,
        stdout_tail=streamed.stdout_tail,
    )


def pip_extra_argv(extra: str) -> tuple[str, ...]:
    """The command :func:`install_pip_extra` runs for `extra`.

    Exposed so a caller — `setup`'s own plan display — can show the exact
    command before running it, without a second, hand-typed copy of the
    same argv drifting from the one that actually runs.
    """
    return (sys.executable, "-m", "pip", "install", f"{DIST_NAME}[{extra}]")


def install_pip_extra(
    extra: str,
    *,
    dry_run: bool,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None = None,
) -> InstallResult:
    """`pip install <dist>[extra]`.

    `verified` is always `False` here: unlike a `Binary`, an extra has no
    single import name this module can generically check — it can bring in
    several packages, or the wrong strategy for a tool's own reasons. The
    caller re-checks the affected tool's own `is_available()` through the
    registry, exactly as every routing decision already does, rather than
    this module guessing at what to import.
    """
    argv = pip_extra_argv(extra)

    if dry_run:
        return InstallResult(ok=True, verified=False, command=argv, stdout_tail="")

    streamed = _stream(argv, cancellation=cancellation, on_output=on_output)
    return InstallResult(
        ok=streamed.ok, verified=False, command=streamed.command, stdout_tail=streamed.stdout_tail
    )


def _stream(
    argv: tuple[str, ...],
    *,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None,
) -> InstallResult:
    """Run `argv` to completion, forwarding each output line as it arrives.

    `verified` is always `False` here — that check belongs to the caller, who
    knows what "worked" means for this particular install. Ctrl-C reaches a
    blocked install exactly as it reaches a blocked document operation in
    `_binaries.run`: the process is registered with `cancellation.on_cancel`
    and killed directly, since a cooperative token cannot interrupt a blocked
    read on its own.
    """
    try:
        # S603: argv is our own declared install_argv tuple, or
        # [sys.executable, "-m", "pip", ...] — never a shell string, never
        # user-supplied text, and shell= is never used.
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return InstallResult(ok=False, verified=False, command=argv, stdout_tail=str(exc))

    release = cancellation.on_cancel(process.kill)
    lines: list[str] = []
    try:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                lines.append(line)
                if on_output is not None:
                    on_output(line)
        process.wait()
    finally:
        release()

    # A killed process reports a non-zero code, so a cancelled install would
    # otherwise be reported as a failed one rather than as the user's choice.
    cancellation.raise_if_cancelled(operation="setup")

    tail = "\n".join(lines[-40:])
    return InstallResult(ok=process.returncode == 0, verified=False, command=argv, stdout_tail=tail)


# ---------------------------------------------------------------------------
# What's missing, and running one item -- shared by `docmax setup` and the
# TUI's install buttons, neither of which may import the other.
# ---------------------------------------------------------------------------


def missing_binaries(specs: Sequence[ToolSpec]) -> dict[str, tuple[str, ...]]:
    """Binary name -> the tool names among `specs` that need it and lack it."""
    needed: dict[str, set[str]] = {}
    for spec in specs:
        for name in spec.requires_binaries:
            needed.setdefault(name, set()).add(spec.name)
    return {
        name: tuple(sorted(tools)) for name, tools in needed.items() if _binaries.find(name) is None
    }


def missing_extras(specs: Sequence[ToolSpec]) -> dict[str, tuple[str, ...]]:
    """Extra name -> the tool names among `specs` whose local engine needs it.

    Reuses each tool's own `is_available()` -- the same check every routing
    decision already makes -- rather than a second, parallel "is this package
    importable" check that could drift from what the engine itself trusts.
    """
    needed: dict[str, set[str]] = {}
    for spec in specs:
        if spec.pip_extra is None or not spec.supports(Engine.LOCAL):
            continue
        if spec.load_strategy(Engine.LOCAL).is_available():
            continue
        needed.setdefault(spec.pip_extra, set()).add(spec.name)
    return {extra: tuple(sorted(tools)) for extra, tools in needed.items()}


def build_plan(
    missing_binaries: dict[str, tuple[str, ...]],
    missing_extras: dict[str, tuple[str, ...]],
    manager: str | None,
) -> list[dict[str, Any]]:
    """One entry per missing thing: what would run, or the hint when nothing can.

    A plain dict, not a dataclass, because the two callers want different
    subsets rendered differently -- the CLI formats it into Rich-markup text,
    the TUI into `Static`/`Button` widgets -- and neither needs a type beyond
    "has these keys" to do it.
    """
    plan: list[dict[str, Any]] = []
    for name in sorted(missing_binaries):
        binary = _binaries.describe(name)
        argv = binary.install_argv_for(manager) if manager else None
        plan.append(
            {
                "kind": "binary",
                "name": name,
                "used_by": missing_binaries[name],
                "size_hint": binary.size_hint,
                "command": list(argv) if argv else None,
                "fallback": None if argv else binary.install_hint(),
            }
        )
    for extra in sorted(missing_extras):
        plan.append(
            {
                "kind": "extra",
                "name": extra,
                "used_by": missing_extras[extra],
                "size_hint": describe_extra(extra).size_hint,
                "command": list(pip_extra_argv(extra)),
                "fallback": None,
            }
        )
    return plan


def run_item(
    item: dict[str, Any],
    *,
    manager: str | None,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one `build_plan` entry and fold verification into the same record.

    `item['command'] is None` means neither this nor the user can do anything
    automatically here -- reported unverified without attempting a subprocess
    that has no argv to run. Never prints anything itself: a caller wanting a
    header line or a confirmation prompt around this owns that, since a CLI's
    console and a TUI's widgets have nothing in common to print through.
    """
    from docmax.core.registry import get_tool

    if item["command"] is None:
        return {**item, "installed": False, "verified": False, "stdout_tail": ""}

    if item["kind"] == "binary":
        # item["command"] is None whenever manager is (see build_plan), and
        # that case already returned above -- so manager is not None here.
        assert manager is not None
        binary = _binaries.describe(item["name"])
        outcome = install_binary(
            binary, manager, dry_run=False, cancellation=cancellation, on_output=on_output
        )
    else:
        outcome = install_pip_extra(
            item["name"], dry_run=False, cancellation=cancellation, on_output=on_output
        )
        # install_pip_extra cannot verify itself (see its own docstring): an
        # extra has no single import name to generically re-check. This is
        # that check, through the same is_available() every routing decision
        # already trusts, for whichever tool motivated installing it.
        verified = any(
            get_tool(name).load_strategy(Engine.LOCAL).is_available() for name in item["used_by"]
        )
        outcome = InstallResult(
            ok=outcome.ok,
            verified=verified,
            command=outcome.command,
            stdout_tail=outcome.stdout_tail,
        )

    return {
        **item,
        "installed": outcome.ok,
        "verified": outcome.verified,
        "stdout_tail": outcome.stdout_tail,
    }


__all__ = [
    "PIP_EXTRAS",
    "InstallResult",
    "PipExtra",
    "build_plan",
    "describe_extra",
    "install_binary",
    "install_pip_extra",
    "missing_binaries",
    "missing_extras",
    "pip_extra_argv",
    "run_item",
]
