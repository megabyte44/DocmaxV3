"""The Textual application: tool screens, cross-cutting screens, and no
document logic.

``ToolListScreen`` and ``RunScreen`` do the same two things every time — read
a ``ToolSpec`` and call ``tui/runner.py``. There is no per-tool code, which is
what :mod:`docmax.tui.forms` exists to make possible and what
``tests/unit/test_tui.py`` asserts structurally. See ADR 0021.

``MenuScreen``, ``HelpScreen``, ``SystemCheckScreen`` and
``CloudStatusScreen`` are a different kind of screen — cross-cutting, not
per-tool, added for GitHub issue #39 (*"the TUI has no way to reach
non-tool commands"*). ADR 0021's "no per-tool code" constraint is about tool
screens specifically and does not forbid these; they still avoid a second
implementation of anything by reading ``tui/status.py`` (itself a thin read of
``tools/_binaries.py`` and ``core/config.py`` / ``core/consent.py`` — the same
sources ``docmax doctor`` and ``docmax cloud status`` render from) rather than
recomputing that data. Settings — item 4 of the issue — is intentionally not
here; the issue's own text scopes it separately, pending a design decision
about whether it is read/write or read-only.

## The threading rule

``EngineRouter.run`` blocks. Blocking Textual's event loop would freeze the UI
and make cancellation impossible — which is the one thing a long compress most
needs. So a run happens on a worker thread (``@work(thread=True)``), and
everything it wants to say comes back through ``call_from_thread``. The native
file dialog ``tui/browser.py`` opens for Browse blocks the same way — it is the
OS's own event loop, not Textual's, running until the user closes it — so it
gets the identical treatment.

That is safe by construction rather than by care: ``ProgressSink`` has required
implementations to *"tolerate being called from a worker thread"* since M0, and
``CancellationToken`` is built on ``threading.Event`` and names *"a key press in
the TUI"* as one of its sources.

## Cancellation

``ctrl+c`` on the run screen calls ``token.cancel()``. It does **not** raise
``KeyboardInterrupt`` — Textual owns the keyboard, and the CLI's ``SIGINT``
handler in ``cli/execution.py`` is deliberately not reused. The atomic writers
then discard the staged file and the destination is untouched, exactly as on the
command line.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Select,
    Static,
)

from docmax import __version__
from docmax.core.branding import APP_NAME
from docmax.tui import catalog, content, forms, runner, status
from docmax.tui.browser import (
    merge_paths,
    pick_files,
    pick_save_path,
    remember_directory,
    render_paths,
)

if TYPE_CHECKING:
    from pathlib import Path

    from textual.timer import Timer

    from docmax.core.errors import DocMaxError
    from docmax.core.models import ToolResult
    from docmax.core.protocols import MissingDependency
    from docmax.core.registry import ToolSpec
    from docmax.core.router import EngineRouter

#: The engine choices a run screen offers. ``auto`` is first and is the default,
#: because the router's ladder is the behaviour a user should get unless they
#: deliberately want otherwise.
_ENGINES = ("auto", "local", "cloud")

#: Status-line glyphs, keyed by state rather than by anything a tool chose.
#: ``RunScreen._set_status`` pairs each with a same-named CSS class
#: (``status-running`` etc.) so colour and icon always move together — see
#: issue #26: idle / running / succeeded / failed need to read as different
#: states, not just different words in the same line. A cancelled run reports
#: as ``"error"`` here: nothing was written, the same outcome a real failure
#: leaves, and the ask names four states, not five.
_STATUS_ICONS: dict[str, str] = {
    "idle": "",
    "running": "◐",
    "success": "✔",
    "error": "✘",
}


def _select(choices: tuple[str, ...], *, default: object, id_: str) -> Select[str]:
    """A ``Select`` whose "nothing chosen" state is spelled the same in every Textual.

    Textual has moved that sentinel more than once — ``Select.BLANK`` is
    literally ``False`` in 8.x, where the sentinel is ``Select.NULL`` — so this
    names neither. When there is a usable default it is passed; when there is
    not, ``value`` is simply omitted and the widget's own default applies.
    ``_selected`` reads the result back the same way, by asking whether the
    value is one of the strings we supplied.
    """
    options = [(choice, choice) for choice in choices]
    if isinstance(default, str) and default in choices:
        return Select(options, value=default, id=id_)
    return Select(options, id=id_)


def _selected(widget: Select[str], choices: tuple[str, ...]) -> str:
    """The chosen string, or ``""`` for no selection. Version-independent."""
    value = widget.value
    return value if isinstance(value, str) and value in choices else ""


def _unit_hint(field: forms.Field) -> str:
    """A short, concrete placeholder for a field's input box, or none at all.

    Not ``field.description``: a full sentence as a placeholder is either
    truncated illegibly by the box's own width, or -- if the box is widened
    to fit it -- defeats the point of having one. Most fields already put
    their unit in the label, e.g. ``"width (px)"``, so this reuses exactly
    that rather than asking every tool to declare a second short string for
    the same fact a label already states. A label with no such suffix gets no
    placeholder, which costs less than a misleading one.
    """
    label = field.label
    if label.endswith(")") and "(" in label:
        return label[label.rindex("(") + 1 : -1]
    return ""


def _slug(text: str) -> str:
    """A widget-id-safe fragment from a group or option label.

    Textual ids are just strings, so this only has to be stable and free of
    spaces -- "Resize method" and "Percentage" become "resize-method" and
    "percentage", which is what lets a group's id and its containers' ids be
    derived from the same label rather than invented separately.
    """
    return text.lower().replace(" ", "-")


#: Rendered into the status line by `RunScreen._succeeded`'s dry-run branch
#: already, so `format_details` leaves them out rather than showing them twice.
_DRY_RUN_KEYS = frozenset({"dry_run", "reason", "destination"})


def format_details(details: Mapping[str, Any]) -> str:
    """Render a tool's ``ToolResult.details`` as key/value lines.

    ``get-info`` is read-only — its whole answer is in ``details``, and
    ``outputs`` is always empty (issue #28). Rather than a get-info-specific
    panel, this renders *any* tool's ``details`` generically: whatever keys
    are there become lines, a nested mapping (``get-info``'s ``metadata``,
    say) is indented one level, and nothing here knows a tool's name. A tool
    that writes files and reports nothing extra in ``details`` simply gets no
    panel, since there is nothing to show.
    """
    lines: list[str] = []
    for key, value in details.items():
        if key in _DRY_RUN_KEYS:
            continue
        _format_detail_entry(lines, str(key), value, indent=0)
    return "\n".join(lines)


def _format_detail_entry(lines: list[str], key: str, value: Any, *, indent: int) -> None:
    prefix = "  " * indent
    label = key.replace("_", " ")
    if isinstance(value, Mapping):
        if not value:
            lines.append(f"{prefix}{label}: —")
            return
        lines.append(f"{prefix}{label}:")
        for sub_key, sub_value in value.items():
            _format_detail_entry(lines, str(sub_key), sub_value, indent=indent + 1)
        return
    lines.append(f"{prefix}{label}: {_format_detail_scalar(value)}")


def _format_detail_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _output_placeholder(spec: ToolSpec) -> str:
    """The output field's hint text — naming an extension only when one can be trusted.

    Every tool's output is required (see ``RunScreen._request``); this only
    decides what the placeholder may *imply*. ``spec.output_required`` tools
    (ADR 0033) have no default worth suggesting — ``convert``'s real extension
    is ``--to``, a parameter, and ``default_suffix`` would name the one format
    Pandoc can never write (ADR 0011). That was the bug issue #24 reported:
    the placeholder claimed a `.pdf` result no run of `convert` can ever
    produce, because it read `default_suffix` unconditionally rather than
    asking whether the tool's spec ever intended it to be read that way.
    """
    if spec.output_required:
        return "path to write the result to"
    return f"path to write the {spec.default_suffix} result to"


def _open_url(url: str) -> None:
    """Open ``url`` in the user's default browser. Never raises into the UI.

    ``webbrowser`` rather than a hand-rolled per-platform branch — the same
    stdlib choice ADR 0005 made for the pickers, and for the identical
    reason: it already knows how to reach the desktop's own opener on every
    platform DocMax supports, at no dependency cost.
    """
    import webbrowser
    from contextlib import suppress

    with suppress(Exception):
        webbrowser.open(url)


def _open_path(path: Path) -> None:
    """Open ``path`` in the OS's registered default application. Never raises
    into the UI.

    Not ``_open_url``/``webbrowser``: a produced result is as likely to be a
    ``.docx`` or a ``.png`` as anything a browser renders, and the point is
    to reach whatever application the OS already associates with that file
    type, not a browser tab. ``os.startfile`` / ``open`` / ``xdg-open`` are
    each already how that platform's own file manager does the same thing,
    at no dependency cost — the same reasoning ADR 0005 gave for
    ``webbrowser`` itself, applied to the platform's file opener instead of
    its browser.
    """
    import shutil
    import subprocess
    import sys
    from contextlib import suppress

    with suppress(Exception):
        if sys.platform == "win32":
            import os

            # S606: this is Windows' own ShellExecute-based file-association
            # opener -- the OS mechanism this function exists to reach, not a
            # shell command line built from a string.
            os.startfile(str(path))  # noqa: S606
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            resolved = shutil.which(opener)
            if resolved is None:
                return
            # S603/S607: `resolved` is an absolute path from `shutil.which`,
            # not a partial one, and argv is never a shell string -- the same
            # shape `_binaries.py` already runs subprocess calls in.
            subprocess.Popen([resolved, str(path)])  # noqa: S603


class Brand(Horizontal):
    """The one-line header every screen shares: mark, subtitle, version.

    A self-contained widget rather than a chunk of markup repeated in every
    screen's ``compose`` — the same reuse ``forms.py`` exists for, applied to
    chrome instead of a field.
    """

    DEFAULT_CSS = """
    Brand {
        height: 1;
        background: $panel;
    }
    Brand > #brand-title {
        width: auto;
        color: $accent;
        text-style: bold;
        padding: 0 2;
    }
    Brand > #brand-sub {
        width: 1fr;
        color: $text-muted;
    }
    Brand > #brand-version {
        width: auto;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(f"◆ {APP_NAME.upper()}", id="brand-title", markup=False)
        yield Static("PDF Toolkit", id="brand-sub", markup=False)
        yield Static(f"v{__version__}", id="brand-version", markup=False)


class ToolListScreen(Screen[None]):
    """The workspace: every offered tool, grouped by category, plus a live
    preview of whatever is focused and a filter over the whole list.

    A full-width row per tool — name, then its summary — read as "there are
    only a handful of these" once there were nineteen, and a wide grid that
    spread them across the screen read as scattered rather than organised. A
    button names one tool and nothing else, stacked directly under its
    category heading; the summary is still the first thing the preview pane
    and ``RunScreen`` show, one keypress or one focus-change away, so nothing
    here is lost, only deferred until it is wanted.

    The nav column's structure — same ids, same classes, same vertical
    stacking order — is unchanged from before this screen grew a search box
    and a preview pane either side of it: ``tests/unit/test_tui.py`` pins
    that shape, deliberately, as the seam a future redesign should respect
    too.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "app.quit", "Quit", show=True),
        Binding("/", "focus_search", "Search", show=True),
        Binding("up", "cursor_up", "Navigate", show=True),
        Binding("down", "cursor_down", "Navigate", show=False),
        Binding("left", "previous_category", "Category", show=True),
        Binding("right", "next_category", "Category", show=False),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("m", "open_menu", "Menu", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._specs_by_name: dict[str, ToolSpec] = {}

    def compose(self) -> ComposeResult:
        yield Brand()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Input(placeholder="/ search tools…", id="search")
                with VerticalScroll(id="tools"):
                    for category, specs in catalog.categories().items():
                        yield Static(category.upper(), classes="category")
                        with Vertical(classes="tool-column"):
                            for spec in specs:
                                yield Button(
                                    spec.name, id=f"tool-{spec.name}", classes="tool-button"
                                )
            with VerticalScroll(id="preview"):
                yield Static("Select a tool", id="preview-title", classes="title", markup=False)
                yield Static(
                    "Use the arrow keys to browse, Enter to open, / to search.",
                    id="preview-body",
                    classes="hint",
                    markup=False,
                )
        yield Static(
            "↑↓ Navigate   ←→ Category   Enter Open   / Search   Esc Clear   m Menu   q Quit",
            id="help",
            classes="help-bar",
            markup=False,
        )

    def on_mount(self) -> None:
        self._specs_by_name = {spec.name: spec for spec in catalog.offered_tools()}

    @on(Button.Pressed, "Button.tool-button")
    def open_tool(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        name = identifier.removeprefix("tool-")
        if catalog.is_offered(name):
            self.app.push_screen(RunScreen(name))

    # -- live preview ---------------------------------------------------

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Show whatever tool just received focus, keyboard or mouse alike."""
        identifier = event.widget.id or ""
        name = identifier.removeprefix("tool-")
        spec = self._specs_by_name.get(name)
        if spec is not None:
            self._show_preview(spec)

    def _show_preview(self, spec: ToolSpec) -> None:
        engines = ", ".join(sorted(engine.value for engine in spec.supported_engines))
        output = "a directory" if spec.produces_directory else f"a {spec.default_suffix} file"
        inputs = "multiple documents" if spec.accepts_multiple_inputs else "one document"
        self.query_one("#preview-title", Static).update(spec.name)
        self.query_one("#preview-body", Static).update(
            "\n".join(
                [
                    spec.summary,
                    "",
                    f"category   {spec.category}",
                    f"engines    {engines}",
                    f"input      {inputs}",
                    f"output     {output}",
                    "",
                    "Enter to open this tool.",
                ]
            )
        )

    # -- nav menu (GitHub #39: help, system check, cloud/account) ----------

    def action_open_menu(self) -> None:
        """The one persistent way to reach the cross-cutting screens.

        Deliberately one keybinding opening one menu rather than three direct
        bindings — ``ToolListScreen`` already commits four of the low letters
        (``q``, arrows aside) to search and navigation, and a menu keeps room
        for more of these screens later without a fifth top-level binding
        each time. See GitHub issue #39.
        """
        self.app.push_screen(MenuScreen())

    # -- search / filter --------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
        search.focus()

    @on(Input.Changed, "#search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        self._filter(event.value.strip().lower())

    def _filter(self, query: str) -> None:
        headings = list(self.query(".category"))
        columns = list(self.query(".tool-column"))
        for heading, column in zip(headings, columns, strict=True):
            buttons = list(column.query(".tool-button"))
            visible = [self._matches(button, query) for button in buttons]
            for button, keep in zip(buttons, visible, strict=True):
                button.display = keep
            column.display = any(visible)
            heading.display = column.display

    def _matches(self, button: Widget, query: str) -> bool:
        if not query:
            return True
        name = (button.id or "").removeprefix("tool-")
        spec = self._specs_by_name.get(name)
        haystack = f"{name} {spec.summary if spec else ''}".lower()
        return query in haystack

    # -- keyboard navigation ----------------------------------------------

    def action_cursor_down(self) -> None:
        self.focus_next()

    def action_cursor_up(self) -> None:
        self.focus_previous()

    def action_next_category(self) -> None:
        self._jump_category(1)

    def action_previous_category(self) -> None:
        self._jump_category(-1)

    def _jump_category(self, direction: int) -> None:
        buttons = [button for button in self.query(".tool-button") if button.display]
        if not buttons:
            return
        boundaries = self._category_boundaries()
        focused = self.focused
        current = buttons.index(focused) if focused in buttons else 0
        segment = max(index for index, start in enumerate(boundaries) if start <= current)
        target = (segment + direction) % len(boundaries)
        buttons[boundaries[target]].focus()

    def _category_boundaries(self) -> list[int]:
        boundaries = []
        index = 0
        for column in self.query(".tool-column"):
            if not column.display:
                continue
            boundaries.append(index)
            index += len([button for button in column.query(".tool-button") if button.display])
        return boundaries or [0]


class MenuScreen(ModalScreen[None]):
    """The nav affordance GitHub issue #39 asks for.

    One keybinding (``m``, from ``ToolListScreen``) opens this; each button
    here opens one of the three cross-cutting screens the issue scoped —
    help, system check, cloud/account — and this modal gets out of the way. A
    modal rather than a fourth full screen because it does no work of its
    own: the same shape ``ConsentScreen`` already uses for "ask one question,
    act on the answer", not the shape ``RunScreen`` uses for "do something".

    Settings (item 4 in the issue) is deliberately not offered here — the
    issue's own text scopes it separately, pending a design decision about
    whether it is read/write or read-only.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("h", "open_help", "Help", show=True),
        Binding("s", "open_system_check", "System check", show=True),
        Binding("c", "open_cloud", "Cloud & account", show=True),
        Binding("escape", "close", "Close", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("Menu", classes="title")
            yield Static("Cross-cutting screens — not a tool.", classes="hint", markup=False)
            with Vertical(classes="menu-actions"):
                yield Button(f"Help — how to use {APP_NAME}", id="menu-help")
                yield Button("System check", id="menu-system-check")
                yield Button("Cloud & account", id="menu-cloud")
                yield Button("Close", id="menu-close")

    @on(Button.Pressed, "#menu-help")
    def _open_help(self) -> None:
        self.action_open_help()

    @on(Button.Pressed, "#menu-system-check")
    def _open_system_check(self) -> None:
        self.action_open_system_check()

    @on(Button.Pressed, "#menu-cloud")
    def _open_cloud(self) -> None:
        self.action_open_cloud()

    @on(Button.Pressed, "#menu-close")
    def _close(self) -> None:
        self.action_close()

    def action_open_help(self) -> None:
        self.dismiss(None)
        self.app.push_screen(HelpScreen())

    def action_open_system_check(self) -> None:
        self.dismiss(None)
        self.app.push_screen(SystemCheckScreen())

    def action_open_cloud(self) -> None:
        self.dismiss(None)
        self.app.push_screen(CloudStatusScreen())

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(Screen[None]):
    """Item 1 of GitHub #39: how to use DocMax, as static content.

    Explains the concepts a run screen's own field hints have no room for —
    the local/cloud choice, consent, overwrite, dry run — the way
    ``docmax --help`` explains them for the CLI. ``tui/content.py`` holds the
    text; this class only lays it out, which is what the issue meant by
    *"static content, lowest effort, no new core plumbing."*
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Brand()
        with VerticalScroll(id="help-content"), Vertical(classes="panel"):
            yield Static("Help", classes="title", markup=False)
            yield Static(
                "What each part of the TUI means, and how it maps to the CLI.",
                classes="hint",
                markup=False,
            )
            for section in content.HELP_SECTIONS:
                yield Static(section.heading, classes="help-heading", markup=False)
                yield Static(section.body, classes="help-body", markup=False)
        yield Static("Esc Back", id="help", classes="help-bar", markup=False)

    def action_back(self) -> None:
        self.app.pop_screen()


class SystemCheckScreen(Screen[None]):
    """Item 2 of GitHub #39: ``docmax doctor``'s own data, as a table.

    Reads ``tui/status.binary_statuses``, which calls straight into
    ``tools/_binaries.py`` — the exact declaration and lookup function
    ``doctor``'s own table and ``--json`` envelope both read. There is no
    second list of binaries here, and no re-implementation of ``find()``.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Brand()
        with VerticalScroll(id="system-check-content"), Vertical(classes="panel"):
            yield Static("System check", classes="title", markup=False)
            yield Static(
                "External programs some local engines depend on.",
                classes="hint",
                markup=False,
            )
            yield DataTable(id="system-check-table")
            yield Static("", id="system-check-summary", classes="hint", markup=False)
        yield Static("Esc Back", id="help", classes="help-bar", markup=False)

    def on_mount(self) -> None:
        table = self.query_one("#system-check-table", DataTable)
        table.add_columns("Binary", "Status", "Path", "Needed by", "Install hint")

        binaries = status.binary_statuses()
        missing = 0
        for binary in binaries:
            if binary.found:
                table.add_row(
                    binary.name, "found", binary.path or "—", ", ".join(binary.used_by), ""
                )
            else:
                missing += 1
                table.add_row(
                    binary.name,
                    "missing",
                    "—",
                    ", ".join(binary.used_by),
                    binary.install_hint,
                )

        summary = (
            "All external tools available."
            if missing == 0
            else f"{missing} tool(s) missing — see Install hint above, or run "
            "the Cloud Engine instead where a tool supports it."
        )
        self.query_one("#system-check-summary", Static).update(summary)

    def action_back(self) -> None:
        self.app.pop_screen()


class CloudStatusScreen(Screen[None]):
    """Item 3 of GitHub #39: ``docmax cloud status``'s own data.

    Explicitly labelled "API key configuration," not a profile — there is no
    login/account concept yet, only a bearer key. ``tui/status.cloud_status``
    reads the same ``Config`` and ``ConsentStore`` the CLI command does, and
    never returns the key itself, matching ``cli/cloud.py``'s rule that *"the
    key never appears in output."*
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Brand()
        with VerticalScroll(id="cloud-status-content"), Vertical(classes="panel"):
            yield Static("Cloud & account", classes="title", markup=False)
            yield Static(
                f"API key configuration — not a user profile. {APP_NAME} has no "
                "login or account feature yet.",
                classes="hint",
                markup=False,
            )
            yield DataTable(id="cloud-status-table")
            yield Static("", id="cloud-status-consent", classes="hint", markup=False)
        yield Static("Esc Back", id="help", classes="help-bar", markup=False)

    def on_mount(self) -> None:
        info = status.cloud_status()
        table = self.query_one("#cloud-status-table", DataTable)
        table.add_columns("Setting", "Value")
        table.add_row("Endpoint", info.endpoint)
        table.add_row(
            "API key",
            f"configured (…{info.api_key_suffix})" if info.api_key_configured else "not configured",
        )
        table.add_row("Offline", "yes — cloud is unreachable" if info.offline else "no")
        table.add_row("Config file", str(info.config_path))

        consented = ", ".join(info.consented_tools) if info.consented_tools else "none"
        self.query_one("#cloud-status-consent", Static).update(f"Consented tools: {consented}")

    def action_back(self) -> None:
        self.app.pop_screen()


class RunScreen(Screen[None]):
    """One tool: a generated form, a run button, progress, and a result.

    The form is built from the registry every time it is composed. Nothing about
    any particular tool is written down in this class.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+r", "run", "Run", show=True),
        Binding("ctrl+c", "cancel", "Cancel run", show=True, priority=True),
    ]

    #: A braille-dot spinner, cycled once per tick while a run is alive and
    #: indeterminate. Ten frames at the tick rate below make one full
    #: rotation take roughly a second — fast enough to read as motion, slow
    #: enough not to be a distraction next to the status text it replaces
    #: the static running icon with.
    _SPINNER_FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _SPINNER_INTERVAL: ClassVar[float] = 1 / 12

    def __init__(self, tool: str) -> None:
        super().__init__()
        self.tool = tool
        self._spec: ToolSpec | None = None
        self._fields: list[forms.Field] = []
        self._token: Any = None
        # Not `_running`: `textual.message_pump.MessagePump.__init__` already
        # owns that name for its own "is this widget's message pump alive"
        # bookkeeping, and sets it to `True` for the entire time the screen is
        # mounted — silently clobbering a same-named attribute of ours and
        # making `_start`'s "already running a tool" guard true from the
        # moment the screen mounts. That is a shadowing bug, not a shared
        # concept, so it gets a name Textual does not already use.
        self._run_in_progress = False
        # The "is anything alive" indicator. `ProgressSink.start`'s own
        # `total=None` already means "indeterminate" — this reuses that
        # signal rather than asking a tool or an engine what kind of thing
        # it is, which is what keeps it generic. `True` until the first
        # `progress.start` call says otherwise: before that call arrives, a
        # run is exactly as indeterminate as the cloud case this exists for.
        self._indeterminate = False
        self._status_description = ""
        self._status_state = "idle"
        self._run_started_at: float | None = None
        self._spinner_index = 0
        self._spinner_timer: Timer | None = None
        #: The paths a successful run just wrote, kept only so the result
        #: card's "Open" button (below) knows what to open — never read for
        #: anything the run itself needed, so a run that produced nothing
        #: (a dry run, a report-only tool) simply leaves this empty.
        self._last_outputs: tuple[Path, ...] = ()

    @property
    def spec(self) -> ToolSpec:
        if self._spec is None:
            from docmax.core.registry import get_tool

            self._spec = get_tool(self.tool)
        return self._spec

    def compose(self) -> ComposeResult:
        spec = self.spec
        self._fields = forms.fields_for(spec)

        yield Brand()
        with VerticalScroll(id="form"), Vertical(classes="panel"):
            yield Static(spec.name, classes="title", markup=False)
            yield Static(spec.summary, classes="hint", markup=False)

            plural = "s, comma-separated" if spec.accepts_multiple_inputs else ""
            yield Label(f"input{plural}")
            with Horizontal(classes="input-row"):
                yield Input(placeholder=f"path to the document{plural}", id="field-__inputs__")
                yield Button("Browse…", id="browse-inputs")
            yield Static("", id="input-hint", classes="hint", markup=False)

            # A report-only tool (`ToolSpec.produces_output=False`, ADR 0036)
            # never writes anything, so it has no output field, no label, and
            # no Browse button to ask for one — the same distinction the CLI
            # already makes by simply not declaring `-o` for these tools. See
            # `_request` below for the matching skip of the "required" check.
            if spec.produces_output:
                yield Label("output (required)")
                with Horizontal(classes="input-row"):
                    yield Input(
                        placeholder=_output_placeholder(spec),
                        id="field-__output__",
                    )
                    yield Button("Browse…", id="browse-output")

            rendered_groups: set[str] = set()
            for field in self._fields:
                if field.group:
                    if field.group in rendered_groups:
                        continue
                    rendered_groups.add(field.group)
                    yield from self._render_group(field.group)
                    continue
                yield from self._render_field(field)

            yield Label("engine")
            yield _select(_ENGINES, default="auto", id_="field-__engine__")

            with Horizontal(classes="actions"):
                yield Button("Run", variant="primary", id="run")
                yield Button("Dry run", id="dry-run")
                if spec.produces_output:
                    yield Button("Overwrite output", id="force")
                yield Button("Cancel run", variant="warning", id="cancel", disabled=True)

            yield Static("", id="status", classes="status-idle", markup=False)
            yield Static("", id="details", classes="details", markup=False)
            # The result card: appears only once a run has actually succeeded
            # (`_show_result_actions`, below), and hidden again the moment
            # another run starts (`_start`) so it can never point at a stale
            # or half-overwritten output. "Open" is itself hidden whenever
            # there is nothing to open — a dry run or a report-only tool
            # (`ToolSpec.produces_output=False`, ADR 0036) — which is the
            # same `bool(outputs)` check `_succeeded` already makes for the
            # status line.
            with Horizontal(id="result-actions", classes="result-actions"):
                yield Button("Open file", id="open-output")
                yield Button("Re-run", id="rerun")
        yield Static(
            "Ctrl+R Run   Ctrl+C Cancel   Esc Back   Tab Next field",
            id="help",
            classes="help-bar",
            markup=False,
        )

    def on_mount(self) -> None:
        self.query_one("#result-actions", Horizontal).display = False

    # -- actions ------------------------------------------------------------

    def action_back(self) -> None:
        if not self._run_in_progress:
            self.app.pop_screen()

    def action_run(self) -> None:
        self._start(dry_run=False, force=self._force)

    def action_cancel(self) -> None:
        """Ask the run to stop. Never raises; never kills the app."""
        if self._token is not None:
            self._token.cancel()
            self._set_status("Stopping…", state="running")

    _force = False

    @on(Button.Pressed, "#run")
    def _on_run(self) -> None:
        self._start(dry_run=False, force=self._force)

    @on(Button.Pressed, "#dry-run")
    def _on_dry_run(self) -> None:
        self._start(dry_run=True, force=self._force)

    @on(Button.Pressed, "#force")
    def _on_force(self) -> None:
        self._force = not self._force
        button = self.query_one("#force", Button)
        button.variant = "success" if self._force else "default"
        self._set_status(
            "The output will be overwritten if it exists."
            if self._force
            else "The output will not be overwritten.",
            state="idle",
        )

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#open-output")
    def _on_open_output(self) -> None:
        """Open the file a successful run just wrote — or, when it wrote more
        than one (``split``'s many pages, ``to-images``'s many frames), the
        folder holding them, since opening several files at once is not one
        action. Generic over `_last_outputs`, so no branch here names a
        tool.
        """
        if not self._last_outputs:
            return
        target = self._last_outputs[0]
        _open_path(target if len(self._last_outputs) == 1 else target.parent)

    @on(Button.Pressed, "#rerun")
    def _on_rerun(self) -> None:
        """Same request, run again — the form still holds every value the
        first run used, so this is exactly what pressing "Run" does."""
        self._start(dry_run=False, force=self._force)

    @on(Button.Pressed, "#browse-inputs")
    def _on_browse_inputs(self) -> None:
        if self._browsing:
            return
        self._browsing = True
        self.query_one("#browse-inputs", Button).disabled = True
        self._set_status("Opening the file browser…", state="running")
        self._browse()

    @work(thread=True, exclusive=True)
    def _browse(self) -> None:
        """Open the OS's native file dialog off the event loop.

        The dialog blocks the calling thread until it closes, exactly like
        ``EngineRouter.run`` — so it gets the same treatment: a worker thread,
        and every result marshalled back through ``call_from_thread``. Nothing
        here runs the tool; browsing only ever fills in a field, so a failure
        here is shown and dropped rather than fed into the run machinery.

        ``multiple`` comes straight from the spec — the one thing this method
        is allowed to know about the tool — so a multi-input tool gets a
        multi-select dialog and everything else gets a single-select one, with
        no name of either kind of tool written down anywhere.

        A real choice is remembered here, on the worker thread, rather than in
        ``_apply_browsed_paths`` on the UI thread — the same reasoning that
        puts the dialog itself on a worker: writing the small state file is
        disk I/O, and disk I/O does not belong on the thread Textual is
        redrawing from.
        """
        from docmax.core.errors import DocMaxError

        try:
            chosen = pick_files(multiple=self.spec.accepts_multiple_inputs)
        except DocMaxError as exc:
            self.app.call_from_thread(self._browse_failed, exc)
            return
        if chosen:
            remember_directory(chosen[0].parent)
        self.app.call_from_thread(self._apply_browsed_paths, chosen)

    _browsing = False

    def _browse_failed(self, exc: DocMaxError) -> None:
        self._browsing = False
        self.query_one("#browse-inputs", Button).disabled = False
        self._show(exc)

    def _apply_browsed_paths(self, chosen: list[Path] | None) -> None:
        """Write what Browse returned into the field.

        A multi-input tool's field is *added to*, not replaced — Browse may be
        used more than once, one document per trip, and a second trip must not
        discard the first. A single-input tool keeps the older, simpler
        behaviour: there is only ever one slot, so a new choice replaces it.
        Both branches read the same ``accepts_multiple_inputs`` flag that chose
        the dialog's own single/multi mode, rather than a second decision that
        could disagree with it.

        Runs even when the dialog was cancelled (``chosen`` is ``None``): the
        field must stay exactly as it was, and clearing the "opening…" status
        and re-enabling the button is not conditional on having got a path.
        """
        self._browsing = False
        self.query_one("#browse-inputs", Button).disabled = False
        if not chosen:
            self._set_status("", state="idle")
            return
        field = self.query_one("#field-__inputs__", Input)
        if self.spec.accepts_multiple_inputs:
            field.value = merge_paths(field.value, chosen)
        else:
            field.value = render_paths(chosen)
        self._update_input_hint()
        self._set_status("", state="idle")

    @on(Button.Pressed, "#browse-output")
    def _on_browse_output(self) -> None:
        if self._browsing_output:
            return
        self._browsing_output = True
        self.query_one("#browse-output", Button).disabled = True
        self._set_status("Opening the save dialog…", state="running")
        self._browse_output()

    @work(thread=True, exclusive=True)
    def _browse_output(self) -> None:
        """Open the OS's native *save* dialog off the event loop.

        Otherwise identical in shape to ``_browse``: the dialog blocks the
        calling thread, so it gets a worker and ``call_from_thread`` too. The
        dialog opens in the first input's own folder when there is one —
        purely where it starts browsing, never a destination it chooses on
        the user's behalf; the user still names the file, and
        ``OutputTarget.resolve`` is exactly as strict about whatever they pick
        as it is about anything typed by hand.

        A real choice is remembered here, on the worker thread, for the same
        reason ``_browse`` does — see its docstring.
        """
        from docmax.core.errors import DocMaxError

        start = forms.first_input_directory(self.query_one("#field-__inputs__", Input).value)
        try:
            chosen = pick_save_path(start=start)
        except DocMaxError as exc:
            self.app.call_from_thread(self._browse_output_failed, exc)
            return
        if chosen is not None:
            remember_directory(chosen.parent)
        self.app.call_from_thread(self._apply_browsed_output, chosen)

    _browsing_output = False

    def _browse_output_failed(self, exc: DocMaxError) -> None:
        self._browsing_output = False
        self.query_one("#browse-output", Button).disabled = False
        self._show(exc)

    def _apply_browsed_output(self, chosen: Path | None) -> None:
        """Write what the save dialog returned into the output field.

        Runs even when the dialog was cancelled (``chosen`` is ``None``): the
        field must stay exactly as it was, and clearing the "opening…" status
        and re-enabling the button is not conditional on having got a path.
        Always replaces rather than merging — unlike the input field, output
        is exactly one path for every tool, so there is no multi-input case to
        distinguish.
        """
        self._browsing_output = False
        self.query_one("#browse-output", Button).disabled = False
        if chosen is not None:
            self.query_one("#field-__output__", Input).value = str(chosen)
        self._set_status("", state="idle")

    def _render_field(self, field: forms.Field) -> ComposeResult:
        """One field: its label, its widget, and -- only if it has one worth
        showing -- a single short hint. Used both for an ordinary field and
        for each field inside a mode group's active container, so a grouped
        field looks exactly like an ungrouped one.

        The one thing this deliberately does not do any more: repeat
        ``field.description`` twice, once as the input's placeholder and once
        as the line underneath it. A placeholder that is a full sentence is
        truncated by the field's own width into something less readable than
        no placeholder at all, and the line below already says the whole
        thing legibly -- so the placeholder is now the short unit hint a label
        like ``width (px)`` already implies, via :func:`_unit_hint`, and
        the paragraph appears exactly once.
        """
        required = " (required)" if field.required else ""
        yield Label(f"{field.label}{required}")
        if field.components:
            # A comma-separated value with more than one meaning is
            # unguessable blind -- one labelled input per part, joined
            # back into the single value the tool actually reads.
            # See ADR 0032.
            with Horizontal(classes="component-row"):
                for index, component in enumerate(field.components):
                    with Vertical(classes="component"):
                        yield Label(component, classes="component-label")
                        yield Input(
                            value=field.default_component(index),
                            placeholder=component,
                            id=f"field-{field.name}-{index}",
                        )
        elif field.kind == "choice":
            yield _select(field.choices, default=field.default, id_=f"field-{field.name}")
        else:
            yield Input(
                value=field.default_text(),
                placeholder=_unit_hint(field),
                id=f"field-{field.name}",
            )
        # A field with nothing to add beyond its label costs a blank line if
        # drawn anyway -- `quality`'s hint is worth keeping, an empty one is not.
        if field.description:
            yield Static(field.description, classes="hint", markup=False)

    def _render_group(self, group: str) -> ComposeResult:
        """A set of mutually exclusive fields -- ``resize``'s Percentage
        versus Dimensions -- as one selector plus one visible answer at a
        time.

        Driven entirely by ``Param.group`` / ``Param.group_option``
        (``core/registry.py``): nothing here names ``resize`` or any other
        tool, so a second tool that declares a group gets this rendering for
        free, exactly as a plain parameter already does. The alternative --
        showing every field for every answer at once -- is the clutter this
        exists to avoid: a user choosing "resize by percentage" should not
        also be shown the width, height and fit fields that answer a
        different question.
        """
        options: dict[str, list[forms.Field]] = {}
        for field in self._fields:
            if field.group == group:
                options.setdefault(field.group_option, []).append(field)

        names = tuple(options)
        default_option = names[0]
        select_id = f"mode-{_slug(group)}"

        yield Label(group)
        yield _select(names, default=default_option, id_=select_id)

        for option, fields in options.items():
            # `Vertical`'s own default CSS is `height: 1fr`, which inside
            # this screen's scrolling form resolves to a sliver too short to
            # hold a label and its input both -- the label fits, the input is
            # clipped by `overflow: hidden`, and a user sees a caption with no
            # box underneath it. `.mode-group` overrides that to `height: auto`,
            # the same fix `.component` already applies for the same reason.
            container = Vertical(id=f"{select_id}-{_slug(option)}", classes="mode-group")
            with container:
                for field in fields:
                    yield from self._render_field(field)
            if option != default_option:
                container.display = False

    @on(Select.Changed)
    def _on_mode_changed(self, event: Select.Changed) -> None:
        """Show the chosen answer's fields, hide the rest of the group.

        A hidden field also has its typed value cleared, not merely its
        display turned off: switching from Percentage after typing 50 into
        it, then setting a width, must not leave that 50 to resurface as a
        contradictory ``scale`` alongside ``width`` when the form is
        submitted -- the field the user is no longer looking at is not one
        they are still answering.
        """
        select_id = event.select.id or ""
        if not select_id.startswith("mode-"):
            return
        chosen = event.value if isinstance(event.value, str) else ""
        prefix = f"{select_id}-"
        for container in self.query(Vertical):
            container_id = container.id or ""
            if not container_id.startswith(prefix):
                continue
            active = container_id == prefix + _slug(chosen)
            container.display = active
            if not active:
                for widget in container.query(Input):
                    widget.value = ""

    @on(Input.Changed, "#field-__inputs__")
    def _on_inputs_changed(self) -> None:
        self._update_input_hint()

    def _update_input_hint(self) -> None:
        """Flag a typo while the user is still looking at the field.

        Advisory only: ``DocumentRef.from_path`` remains the check that
        actually gates a run, so a path that is momentarily wrong (mid-edit,
        or valid only once resolved some other way) never blocks Run — it just
        looks wrong until it isn't.

        The message is the primary signal, but a line of text below the field
        is easy to miss — issue #25 asked for the field itself to say
        something is wrong, not just the hint underneath it. So the same
        ``bool(message)`` also toggles an ``invalid`` class on both the input
        and its hint, which is what ``.input-row Input.invalid`` and
        ``.hint.invalid`` in ``DocMaxApp.CSS`` turn into a red border and red
        text — Textual's own pattern for this (a CSS class driven by widget
        state), not a bespoke widget.
        """
        raw = self.query_one("#field-__inputs__", Input).value
        message = forms.describe_missing_paths(raw)
        is_invalid = bool(message)
        # Nothing wrong with the paths, so the line is free to say something
        # useful instead. A tool that declares `describe_inputs` uses it to
        # state the fact its parameters depend on -- `resize` says how many
        # pixels across the image actually is, which is the number its width
        # and height fields are asking the user to reason about. Read
        # generically: the TUI never learns which tool this is, and the
        # Pillow call that produces it stays in the tool package.
        if not is_invalid:
            message = forms.describe_inputs(self.spec, raw) or ""
        input_widget = self.query_one("#field-__inputs__", Input)
        hint = self.query_one("#input-hint", Static)
        input_widget.set_class(is_invalid, "invalid")
        hint.set_class(is_invalid, "invalid")
        hint.update(message)

    # -- running ------------------------------------------------------------

    def _start(self, *, dry_run: bool, force: bool) -> None:
        if self._run_in_progress:
            return
        try:
            request = self._request(dry_run=dry_run, force=force)
        except Exception as exc:
            self._show(exc)
            return

        from docmax.core.cancellation import CancellationToken

        self._token = CancellationToken()
        self._run_in_progress = True
        self._indeterminate = True
        self._run_started_at = time.monotonic()
        self._spinner_index = 0
        self._start_spinner()
        self.query_one("#cancel", Button).disabled = False
        self.query_one("#run", Button).disabled = True
        self._set_status("Running…", state="running")
        self._set_details("")
        self.query_one("#result-actions", Horizontal).display = False
        self._execute(request)

    def _request(self, *, dry_run: bool, force: bool) -> runner.RunRequest:
        """Gather the form into a request, or raise the typed error saying why not.

        Output is required, the same as it is for every command the CLI
        exposes — see ``cli/main.py``'s ``merge`` docstring and ADR 0028's
        rejection of "let an omitted output default beside the input" as *"the
        M9 watcher defect in another costume."* ``OutputTarget.resolve`` can
        derive a destination from the first input, but for any tool whose
        output shares its input's extension — which is most of them, since
        DocMax is mostly PDF-to-PDF — that derived path *is* the first input,
        and the derivation exists only to be refused as
        ``InPlaceOverwriteError``. Asking here, before a request is even built,
        tells the user at the boundary instead of after a run that could never
        have written anything.

        A report-only tool (``spec.produces_output=False``, ADR 0036) has no
        output field on the form at all (see ``compose`` above) — there is
        nothing here to require, so this reads no ``#field-__output__``
        widget and leaves the request's ``output`` at its default, ``None``.
        """
        from pathlib import Path

        from docmax.core.errors import InvalidParameterError
        from docmax.core.models import Engine

        spec = self.spec

        raw = self.query_one("#field-__inputs__", Input).value.strip()
        if not raw:
            raise InvalidParameterError(
                "This tool needs a document.",
                remedy="Type the path to the file you want to work on.",
                context={"parameter": "input"},
            )
        inputs = tuple(Path(part.strip()) for part in raw.split(",") if part.strip())

        output: Path | None = None
        if spec.produces_output:
            output_text = self.query_one("#field-__output__", Input).value.strip()
            if not output_text:
                raise InvalidParameterError(
                    "This tool needs an output path.",
                    remedy="Type the path to write the result to.",
                    context={"parameter": "output"},
                )
            output = Path(output_text)

        engine_value = _selected(self.query_one("#field-__engine__", Select), _ENGINES)
        engine = None if engine_value in ("", "auto") else Engine(engine_value)

        values = {field.name: self._value_of(field) for field in self._fields}

        return runner.RunRequest(
            tool=self.tool,
            inputs=inputs,
            output=output,
            engine=engine,
            force=force,
            dry_run=dry_run,
            params=forms.collect(self._fields, values),
        )

    def _value_of(self, field: forms.Field) -> str:
        if field.components:
            return self._composite_value_of(field)
        widget = self.query_one(f"#field-{field.name}")
        if isinstance(widget, Select):
            return _selected(widget, field.choices)
        if isinstance(widget, Input):
            return widget.value
        return ""

    def _composite_value_of(self, field: forms.Field) -> str:
        """The comma-joined value of a labelled multi-input field.

        Blank overall — every part still empty — reads as "not supplied",
        matching ``forms.collect``'s existing rule for a plain field left
        empty. A value with only *some* parts filled in is joined and handed
        to the tool's own parser exactly as if it had been typed into a
        single field: the parser's error already names the missing part,
        which is a second implementation of that message this need not be.
        """
        parts = [
            self.query_one(f"#field-{field.name}-{index}", Input).value.strip()
            for index in range(len(field.components))
        ]
        if not any(parts):
            return ""
        return ",".join(parts)

    @work(thread=True, exclusive=True)
    def _execute(self, request: runner.RunRequest) -> None:
        """The run itself, off the event loop.

        Everything this thread wants to say goes through ``call_from_thread``,
        which is Textual's contract for touching widgets from anywhere but the
        UI thread.
        """
        from docmax.core.errors import ConsentRequiredError, DocMaxError

        # No `on_advance`/`on_finish`: with the progress bar gone, nothing
        # visual consumes a per-step advance any more — the spinner and
        # elapsed-time indicator `_progress_start` drives are read off the
        # wall clock (`_tick_spinner`), not off step counts.
        progress = runner.CallbackProgress(
            on_start=lambda description, total: self.app.call_from_thread(
                self._progress_start, description, total
            ),
        )

        router = runner.build_router()
        try:
            try:
                result = runner.run(
                    request, router=router, progress=progress, cancellation=self._token
                )
            except ConsentRequiredError as exc:
                # The modal `errors.py` has specified since M0. Asked on the UI
                # thread and waited for here, so the run continues on the same
                # worker rather than being restarted from the top.
                if not self.app.call_from_thread(self._ask_consent, exc):
                    self.app.call_from_thread(self._show, exc)
                    return
                runner.grant_consent(exc.tool, router=router)
                result = runner.run(
                    request, router=router, progress=progress, cancellation=self._token
                )
        except DocMaxError as exc:
            dependencies = self._dependency_check(exc, router)
            if dependencies:
                self.app.call_from_thread(self._show_dependency_missing, dependencies)
            else:
                self.app.call_from_thread(self._show, exc)
            return
        finally:
            self.app.call_from_thread(self._finished)

        self.app.call_from_thread(self._succeeded, result)

    # -- callbacks, all on the UI thread ------------------------------------

    def _progress_start(self, description: str, total: int | None) -> None:
        # `total=None` is the sink's own spelling of "indeterminate" — see
        # `ProgressSink.start`. Read here rather than re-decided, so the
        # spinner+elapsed indicator this drives always agrees with which
        # state a step is actually in.
        self._indeterminate = total is None
        self._set_status(description, state="running")

    def _finished(self) -> None:
        self._run_in_progress = False
        self._token = None
        self._indeterminate = False
        self._run_started_at = None
        self._stop_spinner()
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#run", Button).disabled = False
        self._flush_repaint()

    # -- the "alive" indicator -----------------------------------------------
    #
    # A bar with no percentage and no motion a viewer can point to reads as
    # "nothing is happening," even once Textual's own indeterminate animation
    # is running — a terminal repaints on its own schedule, and a user
    # watching a cloud call that can sit on one step for a while has no way
    # to tell a live pulse from a frozen one at a glance. This replaces the
    # static "running" icon with a spinner glyph that visibly advances, plus
    # a running elapsed-time count, next to whatever the current step's own
    # description already says. It only appears while a step is
    # indeterminate — a determinate one already moves its own bar toward a
    # known total, which is motion a user can already see.

    def _start_spinner(self) -> None:
        if self._spinner_timer is None:
            self._spinner_timer = self.set_interval(self._SPINNER_INTERVAL, self._tick_spinner)

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_spinner(self) -> None:
        self._spinner_index += 1
        self._render_status()

    def on_unmount(self) -> None:
        """Belt and braces: `_finished` already stops it on every normal
        exit, but the app can be quit (`ctrl+q`) while a run is still in
        progress, which unmounts this screen without ever reaching it."""
        self._stop_spinner()

    def _succeeded(self, result: ToolResult) -> None:
        if result.details.get("dry_run"):
            self._set_status(
                f"Dry run — would use the {result.engine_used.value} engine "
                f"({result.details.get('reason', 'no reason given')}), "
                f"writing {result.details.get('destination', '—')}.",
                state="success",
            )
            # Nothing was written, so there is nothing to open — the card
            # still appears, with only "Re-run" live, so switching from a
            # dry run to the real thing is one click either way.
            self._show_result_actions(())
            self._flush_repaint()
            return
        written = ", ".join(str(path) for path in result.outputs) or "nothing"
        self._set_status(f"Wrote {written}  ·  {result.engine_used.value} engine", state="success")
        # `outputs` is a written file, if any; `details` is everything else a
        # tool reported. A read-only tool like `get-info` writes nothing and
        # carries its entire answer in `details` — rendering it here, always
        # and generically, is issue #28's fix: no branch names `get-info`, so
        # any current or future tool that answers through `details` is shown
        # the same way, and a tool with nothing beyond a written path gets no
        # panel at all, since `format_details` returns "" for empty details.
        self._set_details(format_details(result.details))
        self._show_result_actions(result.outputs)
        self._flush_repaint()

    def _show_result_actions(self, outputs: tuple[Path, ...]) -> None:
        """Reveal the result card after a successful run. ``outputs`` decides
        only whether "Open" is worth showing — "Re-run" always is, once
        there has been a run to repeat."""
        self._last_outputs = outputs
        open_button = self.query_one("#open-output", Button)
        open_button.display = bool(outputs)
        open_button.label = "Open file" if len(outputs) == 1 else "Open folder"
        self.query_one("#result-actions", Horizontal).display = True

    def _ask_consent(self, exc: DocMaxError) -> bool:
        from docmax.core.errors import ConsentRequiredError

        tool = exc.tool if isinstance(exc, ConsentRequiredError) else self.tool
        return bool(self.app.push_screen_wait(ConsentScreen(tool, exc.message)))

    def _dependency_check(
        self, exc: DocMaxError, router: EngineRouter
    ) -> tuple[MissingDependency, ...]:
        """Turn "the local engine cannot run" into a named, actionable list.

        Generic across every tool, by construction: a
        :class:`~docmax.core.errors.LocalDependencyMissingError` already
        names exactly one thing, directly on the exception. A
        :class:`~docmax.core.errors.NoEngineAvailableError` — what a router
        actually raises when a tool's local engine is unavailable, since
        ``EngineRouter.resolve`` checks availability *before* a strategy ever
        runs — carries no such structured field, so the router is asked what
        the *local* engine specifically is missing, the same
        :meth:`~docmax.core.router.EngineRouter.missing_dependencies` any
        future caller would use. Neither branch compares ``self.tool``, or
        any tool name, against a string; a strategy that has nothing
        structured to report yields an empty tuple either way, and the
        caller falls back to the ordinary error modal.
        """
        from docmax.core.errors import LocalDependencyMissingError, NoEngineAvailableError
        from docmax.core.models import Engine
        from docmax.core.protocols import MissingDependency

        if isinstance(exc, LocalDependencyMissingError):
            return (MissingDependency(name=exc.dependency, reason=exc.message, url=exc.url),)
        if isinstance(exc, NoEngineAvailableError):
            return router.missing_dependencies(self.tool, Engine.LOCAL)
        return ()

    def _show_dependency_missing(self, dependencies: tuple[MissingDependency, ...]) -> None:
        self._set_status("A dependency is missing.", state="error")
        self._set_details("")
        self.app.push_screen(DependencyMissingScreen(self.tool, dependencies))
        self._flush_repaint()

    def _show(self, exc: BaseException) -> None:
        """Display any failure as a message and a remedy. Never a traceback."""
        from docmax.core.errors import CancelledError, DocMaxError, InternalError

        if isinstance(exc, CancelledError):
            self._set_status(f"{exc.message} Nothing was written.", state="error")
            self._flush_repaint()
            return
        if not isinstance(exc, DocMaxError):
            # The router wraps anything escaping a tool, so reaching here means
            # something failed on this side of it. It still gets a panel rather
            # than a stack trace: a user should never see one for a condition we
            # anticipated, and an unanticipated one becomes a bug report.
            exc = InternalError(str(exc) or exc.__class__.__name__)
        self._set_status(exc.message, state="error")
        self._set_details("")
        self.app.push_screen(ErrorScreen(exc))
        self._flush_repaint()

    def _set_status(self, text: str, *, state: str = "idle") -> None:
        """Set the status line's text and its state-driven visual treatment.

        ``state`` is one of ``idle`` / ``running`` / ``success`` / ``error``.
        It selects a CSS class — colour and weight — and (outside the
        indeterminate-running case ``_render_status`` handles) a small icon
        glyph from ``_STATUS_ICONS``, so idle, running, succeeded and failed
        read as genuinely different states rather than the same plain line
        with different words in it. Driven entirely by ``state``, never by
        anything about a particular tool.
        """
        self._status_description = text
        self._status_state = state
        self._render_status()

    def _render_status(self) -> None:
        """Paint ``#status`` from the description, state and spinner phase.

        The one seam every source of a status update goes through — a plain
        ``_set_status`` call, and each spinner tick — so the indeterminate
        case can never fall out of sync with the icon-and-colour treatment
        every other state already gets.
        """
        widget = self.query_one("#status", Static)
        widget.set_classes(f"status-{self._status_state}")
        text = self._status_description
        if (
            self._status_state == "running"
            and self._run_in_progress
            and self._indeterminate
            and self._run_started_at is not None
        ):
            glyph = self._SPINNER_FRAMES[self._spinner_index % len(self._SPINNER_FRAMES)]
            elapsed = int(time.monotonic() - self._run_started_at)
            widget.update(f"{glyph}  {text}  ({elapsed}s)" if text else f"{glyph}  ({elapsed}s)")
            return
        icon = _STATUS_ICONS.get(self._status_state, "")
        widget.update(f"{icon}  {text}" if icon and text else text)

    def _set_details(self, text: str) -> None:
        self.query_one("#details", Static).update(text)

    def _flush_repaint(self) -> None:
        """Force the screen to paint right now, instead of on its own time.

        ``Widget.update()``/``refresh()`` only *mark* a widget dirty --
        Textual's own docstring says the repaint "will be done on the next
        idle event," which in practice means ``Screen._on_idle`` defers to
        ``Screen._update_timer``: a timer paused between frames, resumed on
        idle, that fires on its own schedule. That is invisible while
        something keeps nudging the screen soon after -- a determinate
        step's own repeated ``on_advance`` calls do that for free. A
        synchronous cloud run (ADR 0016) is the opposite case: it reports
        exactly one indeterminate step and then finishes, so `_finished`,
        `_succeeded` and `_show` are the *last* things this screen is ever
        told. Whatever they change is the only thing left queued, with
        nothing left to drag it onto the screen -- so it sits there until
        some unrelated later event (Cancel, a keypress, a resize) happens to
        pump the message loop and pull the paint along with it. That is
        GitHub issue #36: "Running..." outliving the run itself, on a run
        screen that never again does anything to shake the dust off.

        ``Screen._on_timer_update`` is what that timer calls when it does
        fire, and ``RunScreen`` already *is* a ``Screen`` -- calling it here
        collapses "eventually" into "now" without reimplementing the
        compositor logic it already owns. This is not a novel trick:
        Textual's own test harness (``Pilot.pause``) calls the identical
        method for the identical reason, to make a pending repaint
        deterministic rather than timing-dependent.
        """
        self._on_timer_update()


class ConsentScreen(ModalScreen[bool]):
    """*"the CLI renders this as a y/n prompt and the TUI as a modal"* — errors.py, M0."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "refuse", "Keep local", show=True)]

    def __init__(self, tool: str, message: str) -> None:
        super().__init__()
        self.tool = tool
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("Upload to the cloud?", classes="title")
            yield Static(self.message, markup=False)
            yield Static(
                f"Agreeing is remembered for {self.tool!r} until you revoke it.",
                classes="hint",
                markup=False,
            )
            with Horizontal(classes="actions"):
                yield Button("Upload", variant="primary", id="agree")
                yield Button("Keep it local", id="refuse")

    @on(Button.Pressed, "#agree")
    def _agree(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#refuse")
    def _refuse(self) -> None:
        self.dismiss(False)

    def action_refuse(self) -> None:
        self.dismiss(False)


class ErrorScreen(ModalScreen[None]):
    """A typed error, as its message and its remedy. Never a stack trace.

    An error is the moment a run stops mattering to a user the most, so this
    modal is built to outweigh a routine status line: a heavier border in
    ``$error`` rather than the ``$accent`` every other modal uses, and an icon
    beside the error code rather than the code standing alone. Nothing here
    is keyed on which tool failed or which error code it is — the weight
    comes from the fact that it *is* an error, not from what kind.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close", show=True)]

    def __init__(self, error: DocMaxError) -> None:
        super().__init__()
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal error"):
            with Horizontal(classes="error-heading"):
                yield Static("✘", classes="error-icon", markup=False)
                yield Static(self.error.code.value, classes="title", markup=False)
            yield Static(self.error.message, classes="error-message", markup=False)
            if self.error.remedy:
                yield Static(f"→ {self.error.remedy}", classes="remedy", markup=False)
            with Horizontal(classes="actions"):
                yield Button("Close", variant="primary", id="close")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class DependencyMissingScreen(ModalScreen[str]):
    """*"Dependency Required"* — one row per :class:`~docmax.core.protocols.MissingDependency`.

    Built entirely from what it is handed: ``RunScreen._dependency_check``
    resolves a failure into a tuple of these, generically, without either of
    them ever comparing a tool name against a string. A tool with two missing
    binaries — OCR, on a machine with neither Tesseract nor Poppler — gets
    two rows and two buttons rather than one arbitrarily chosen page, because
    the second one is exactly as necessary as the first. See ADR 0036.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "back", "Back", show=True)]

    def __init__(self, tool: str, dependencies: tuple[MissingDependency, ...]) -> None:
        super().__init__()
        self.tool = tool
        self.dependencies = dependencies

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal warning"):
            yield Static("\N{WARNING SIGN} Dependency Required", classes="title")
            for index, dependency in enumerate(self.dependencies):
                yield Static(dependency.reason, markup=False)
                if dependency.url:
                    yield Button(
                        f"Open {dependency.name} Installation Page",
                        variant="primary",
                        id=f"open-install-{index}",
                    )
            with Horizontal(classes="actions"):
                yield Button("Back", id="dependency-back")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "dependency-back":
            self.dismiss("back")
            return
        if identifier.startswith("open-install-"):
            index = int(identifier.removeprefix("open-install-"))
            url = self.dependencies[index].url
            if url:
                _open_url(url)

    def action_back(self) -> None:
        self.dismiss("back")


class DocMaxApp(App[None]):
    """The application shell.

    Styling leans on Textual's own design tokens (``$accent``, ``$panel``,
    ``$text-muted``, …) and a bundled theme, rather than hardcoded colours —
    the same reason ``core/branding.py`` is the only place that names the
    product: one seam to change, not colours scattered through every screen.
    """

    TITLE = APP_NAME
    CSS = """
    Screen { background: $background; }

    .title { text-style: bold; color: $foreground; padding: 0 0 1 0; }
    .hint { color: $text-muted; padding: 0 0 1 0; }
    .remedy { color: $accent; padding: 1 0 0 0; }

    /* -- workspace: sidebar + preview -------------------------------- */

    #body { height: 1fr; }

    #sidebar {
        width: 38;
        border-right: solid $panel-lighten-1;
        padding: 0 1;
    }
    #search { margin: 1 1 1 0; }
    #tools { padding: 0 1 1 0; }

    .category {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 1;
    }
    .tool-column { height: auto; padding: 0 0 0 1; }
    /* Button's built-in default style sets border-top/border-bottom as
       separate longhand rules of equal-or-higher specificity than a plain
       class selector, so a shorthand `border: none` here is silently lost.
       Matching on type-plus-class (`Button.tool-button`) wins the tie and
       turns the button into a flat row instead of a boxed widget. */
    Button.tool-button {
        width: auto;
        min-width: 0;
        margin: 0;
        padding: 0 2;
        border-top: none;
        border-bottom: none;
        background: transparent;
        color: $foreground;
    }
    Button.tool-button:focus {
        text-style: bold;
        color: $text;
        background: $accent 30%;
    }
    Button.tool-button:hover {
        color: $accent;
        border-top: none;
        background: transparent;
    }

    #preview {
        width: 1fr;
        padding: 1 3;
    }
    #preview-title { color: $accent; text-style: bold; padding: 0 0 1 0; }

    .help-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }

    /* -- run screen: a single card ------------------------------------ */

    #form { padding: 1 2; }
    .panel {
        height: auto;
        border: round $panel-lighten-2;
        background: $surface;
        padding: 1 2;
    }
    .actions { height: auto; padding: 1 0; }
    .actions Button { margin-right: 1; }
    /* Hidden by default (`RunScreen.on_mount`); shown by `_show_result_actions`
       once a run has actually succeeded, hidden again by `_start` the moment
       the next one begins. */
    .result-actions { height: auto; padding: 1 0 0 0; }
    .result-actions Button { margin-right: 1; }
    .input-row { height: auto; }
    .input-row Input { width: 1fr; }
    .input-row Button { margin-left: 1; }
    /* issue #25: a typed path that doesn't resolve says so in the field
       itself, not only in the hint line underneath it. */
    .input-row Input.invalid { border: round $error; }
    .hint.invalid { color: $error; }
    .component-row { height: auto; }
    .component { width: 1fr; margin-right: 1; height: auto; }
    .mode-group { height: auto; }
    .component-label { color: $text-muted; text-style: bold; }
    #details { color: $text-muted; padding: 1 0 0 0; height: auto; }

    /* Status line: idle / running / succeeded / failed read as distinct
       states — colour and weight driven by ``RunScreen._set_status``'s
       ``state``, never by anything about a particular tool. See issue #26. */
    #status { padding: 0 0 1 0; }
    #status.status-idle { color: $text-muted; text-style: none; }
    #status.status-running { color: $warning; text-style: bold; }
    #status.status-success { color: $success; text-style: bold; }
    #status.status-error { color: $error; text-style: bold; }

    /* -- modals -------------------------------------------------------- */

    .modal {
        width: 70;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    /* Heavier than the round/$accent border every other modal gets: an error
       is deliberately the most visually weighty thing the TUI shows. */
    .modal.error { border: heavy $error; background: $error 10%; }
    .error-heading { height: auto; padding: 0 0 1 0; }
    .error-icon {
        width: auto;
        color: $error;
        text-style: bold;
        padding: 0 1 0 0;
    }
    .modal.error .title { color: $error; padding: 0; }
    .error-message { text-style: bold; padding: 0 0 1 0; }
    /* Same weight as `.modal.error`, in `$warning` rather than `$error`:
       a missing dependency is a stop-and-fix condition, but not a failure
       this run caused — see `DependencyMissingScreen` (ADR 0036). */
    .modal.warning { border: heavy $warning; background: $warning 10%; }
    .modal.warning .title { color: $warning; padding: 0; }
    ModalScreen { align: center middle; }

    .menu-actions { height: auto; padding: 1 0 0 0; }
    .menu-actions Button { width: 1fr; margin-bottom: 1; }

    /* -- help, system check, cloud status: static/table content -------- */

    #help-content, #system-check-content, #cloud-status-content { padding: 1 2; }
    .help-heading {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 0;
    }
    .help-body { color: $foreground; padding: 0 0 1 0; }
    #system-check-table, #cloud-status-table { margin: 1 0; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
    ]

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.push_screen(ToolListScreen())


__all__ = [
    "CloudStatusScreen",
    "ConsentScreen",
    "DependencyMissingScreen",
    "DocMaxApp",
    "ErrorScreen",
    "HelpScreen",
    "MenuScreen",
    "RunScreen",
    "SystemCheckScreen",
    "ToolListScreen",
]
