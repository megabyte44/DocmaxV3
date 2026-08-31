"""Static help content for the TUI's help screen.

Plain data, not a widget — kept out of ``app.py`` for the same reason
``status.py`` is: so what a screen shows is testable with no terminal. There
is no core plumbing behind this, deliberately — the issue that asked for it
(GitHub #39) scoped help as *"static content, lowest effort, no new core
plumbing"*.

No tool name appears here. ``test_the_tui_names_no_tool_except_the_
unimplemented_one`` would fail on one if it did, and the content is about
concepts that apply across every tool — the local/cloud choice, consent,
overwrite, dry run — never about any single one of them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HelpSection:
    """One concept explained: a heading and a paragraph."""

    heading: str
    body: str


#: Same concepts ``docmax --help`` and each run screen's own controls already
#: assume a user understands — written out once, here, since a run screen has
#: room for a field's own hint but not for the "why" behind it.
HELP_SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        "Local vs. cloud engine",
        "Most tools run locally, using whatever is installed on this machine. "
        "Some can also run against a cloud endpoint instead — choose one on a "
        "tool's own engine field, or leave it on auto and the router decides "
        "from what is actually available. Nothing is ever uploaded without "
        "your consent, tool by tool.",
    ),
    HelpSection(
        "Consent",
        "Before a document is uploaded for the first time, you are asked to "
        "agree — once per tool, per endpoint. That agreement is remembered "
        "until you revoke it. Cloud & account, in this menu, shows what you "
        "have currently agreed to.",
    ),
    HelpSection(
        "Overwriting output",
        "A run refuses to overwrite a file that already exists unless you "
        "say so. 'Overwrite output' on a tool's screen grants that "
        "permission for one run — the same thing --force does on the "
        "command line.",
    ),
    HelpSection(
        "Dry run",
        "Shows which engine would be used and why, and writes nothing. Use "
        "it to check a run before committing to it.",
    ),
    HelpSection(
        "System check",
        "Some local engines depend on external programs — Ghostscript, "
        "Tesseract, Poppler, Pandoc among them. System check, in this menu, "
        "reports which are installed and how to get the ones that are not.",
    ),
    HelpSection(
        "Navigating the catalog",
        "/ searches the tool catalog, the arrow keys move through it, Enter "
        "opens whatever is focused, and m opens this menu from anywhere on "
        "that screen.",
    ),
)


__all__ = ["HELP_SECTIONS", "HelpSection"]
