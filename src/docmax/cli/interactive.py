"""When a command may take over the terminal, and when it must refuse to.

Three things in M7 want a human in front of them: the two ADR 0005 pickers, and
the TUI. All three fail badly in the same two situations, so the check lives
here once rather than in each of them.

**Under ``--json``.** [ADR 0017](../../../docs/adr/0017-json-output-contract.md)
says stdout carries the envelope and nothing else. A picker prints a URL; a
Textual app writes a screenful of escape sequences. Either would corrupt the one
object a script is parsing, so both are refused rather than quietly degraded.

**With no terminal attached.** A picker waits for a browser that nobody will
open; a TUI draws to a pipe. ``cli/execution.py`` already reasons exactly this
way about the consent prompt — *"a prompt would either hang a script forever or
corrupt its stdout"* — and this is the same rule applied to the same situations.

The refusal is a typed error with a remedy naming the flag to use instead, so a
script that hits it is told what to do rather than left with a hang.
"""

from __future__ import annotations

import sys

from docmax.core.errors import InvalidParameterError


def is_interactive() -> bool:
    """Is there a person at a terminal who could answer?

    Both streams, not just one. ``stdout`` alone would let ``docmax crop ... |
    tee log`` open a browser and then write its result into a pipe nobody is
    reading; ``stdin`` alone would miss the case that matters most, which is
    output being redirected in CI.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def require_interactive_is_possible(what: str) -> None:
    """Refuse ``what`` when there is nobody to interact with, or JSON to protect.

    ``what`` is spelled as the user typed it — ``--interactive``, ``tui`` — so
    the message names the thing they actually ran.
    """
    from docmax.cli import json_output

    if json_output.enabled():
        raise InvalidParameterError(
            f"{what} cannot be used with --json.",
            remedy=(
                "--json emits one machine-readable object on stdout, which an "
                "interactive session would corrupt. Supply the value as a flag instead."
            ),
            context={"option": what, "reason": "json"},
        )

    if not is_interactive():
        raise InvalidParameterError(
            f"{what} needs a terminal, and this one is not interactive.",
            remedy=(
                "Supply the value as a flag instead — it works over SSH, in a script, and in CI."
            ),
            context={"option": what, "reason": "not a tty"},
        )


__all__ = ["is_interactive", "require_interactive_is_possible"]
