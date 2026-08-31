"""The Textual application: three screens, two modals, and no document logic.

Every screen here does the same two things — read a ``ToolSpec`` and call
``tui/runner.py``. There is no per-tool code, which is what
:mod:`docmax.tui.forms` exists to make possible and what
``tests/unit/test_tui.py`` asserts structurally.

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
    Input,
    Label,
    ProgressBar,
    Select,
    Static,
)

from docmax import __version__
from docmax.core.branding import APP_NAME
from docmax.tui import catalog, forms, runner
from docmax.tui.browser import merge_paths, pick_files, pick_save_path, render_paths

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.errors import DocMaxError
    from docmax.core.models import ToolResult
    from docmax.core.registry import ToolSpec

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
            "↑↓ Navigate   ←→ Category   Enter Open   / Search   Esc Clear   q Quit",
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

            yield Label("output (required)")
            with Horizontal(classes="input-row"):
                yield Input(
                    placeholder=f"path to write the {spec.default_suffix} result to",
                    id="field-__output__",
                )
                yield Button("Browse…", id="browse-output")

            for field in self._fields:
                required = " (required)" if field.required else ""
                yield Label(f"{field.label}{required}")
                if field.components:
                    # A comma-separated value with more than one meaning is
                    # unguessable blind — one labelled input per part, joined
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
                        placeholder=field.description,
                        id=f"field-{field.name}",
                    )
                yield Static(field.description, classes="hint", markup=False)

            yield Label("engine")
            yield _select(_ENGINES, default="auto", id_="field-__engine__")

            with Horizontal(classes="actions"):
                yield Button("Run", variant="primary", id="run")
                yield Button("Dry run", id="dry-run")
                yield Button("Overwrite output", id="force")
                yield Button("Cancel run", variant="warning", id="cancel", disabled=True)

            yield ProgressBar(id="progress", show_eta=False)
            yield Static("", id="status", classes="status-idle", markup=False)
            yield Static("", id="details", classes="details", markup=False)
        yield Static(
            "Ctrl+R Run   Ctrl+C Cancel   Esc Back   Tab Next field",
            id="help",
            classes="help-bar",
            markup=False,
        )

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
        """
        from docmax.core.errors import DocMaxError

        try:
            chosen = pick_files(multiple=self.spec.accepts_multiple_inputs)
        except DocMaxError as exc:
            self.app.call_from_thread(self._browse_failed, exc)
            return
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
        """
        from docmax.core.errors import DocMaxError

        start = forms.first_input_directory(self.query_one("#field-__inputs__", Input).value)
        try:
            chosen = pick_save_path(start=start)
        except DocMaxError as exc:
            self.app.call_from_thread(self._browse_output_failed, exc)
            return
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

    @on(Input.Changed, "#field-__inputs__")
    def _on_inputs_changed(self) -> None:
        self._update_input_hint()

    def _update_input_hint(self) -> None:
        """Flag a typo while the user is still looking at the field.

        Advisory only: ``DocumentRef.from_path`` remains the check that
        actually gates a run, so a path that is momentarily wrong (mid-edit,
        or valid only once resolved some other way) never blocks Run — it just
        looks wrong until it isn't.
        """
        raw = self.query_one("#field-__inputs__", Input).value
        self.query_one("#input-hint", Static).update(forms.describe_missing_paths(raw))

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
        self.query_one("#cancel", Button).disabled = False
        self.query_one("#run", Button).disabled = True
        self._set_status("Running…", state="running")
        self._set_details("")
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
        """
        from pathlib import Path

        from docmax.core.errors import InvalidParameterError
        from docmax.core.models import Engine

        raw = self.query_one("#field-__inputs__", Input).value.strip()
        if not raw:
            raise InvalidParameterError(
                "This tool needs a document.",
                remedy="Type the path to the file you want to work on.",
                context={"parameter": "input"},
            )
        inputs = tuple(Path(part.strip()) for part in raw.split(",") if part.strip())

        output_text = self.query_one("#field-__output__", Input).value.strip()
        if not output_text:
            raise InvalidParameterError(
                "This tool needs an output path.",
                remedy="Type the path to write the result to.",
                context={"parameter": "output"},
            )

        engine_value = _selected(self.query_one("#field-__engine__", Select), _ENGINES)
        engine = None if engine_value in ("", "auto") else Engine(engine_value)

        values = {field.name: self._value_of(field) for field in self._fields}

        return runner.RunRequest(
            tool=self.tool,
            inputs=inputs,
            output=Path(output_text),
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

        progress = runner.CallbackProgress(
            on_start=lambda description, total: self.app.call_from_thread(
                self._progress_start, description, total
            ),
            on_advance=lambda amount: self.app.call_from_thread(self._progress_advance, amount),
            on_finish=lambda: self.app.call_from_thread(self._progress_finish),
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
            self.app.call_from_thread(self._show, exc)
            return
        finally:
            self.app.call_from_thread(self._finished)

        self.app.call_from_thread(self._succeeded, result)

    # -- callbacks, all on the UI thread ------------------------------------

    def _progress_start(self, description: str, total: int | None) -> None:
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=total, progress=0)
        self._set_status(description, state="running")

    def _progress_advance(self, amount: int) -> None:
        self.query_one("#progress", ProgressBar).advance(amount)

    def _progress_finish(self) -> None:
        self.query_one("#progress", ProgressBar).update(progress=0, total=None)

    def _finished(self) -> None:
        self._run_in_progress = False
        self._token = None
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#run", Button).disabled = False

    def _succeeded(self, result: ToolResult) -> None:
        if result.details.get("dry_run"):
            self._set_status(
                f"Dry run — would use the {result.engine_used.value} engine "
                f"({result.details.get('reason', 'no reason given')}), "
                f"writing {result.details.get('destination', '—')}.",
                state="success",
            )
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

    def _ask_consent(self, exc: DocMaxError) -> bool:
        from docmax.core.errors import ConsentRequiredError

        tool = exc.tool if isinstance(exc, ConsentRequiredError) else self.tool
        return bool(self.app.push_screen_wait(ConsentScreen(tool, exc.message)))

    def _show(self, exc: BaseException) -> None:
        """Display any failure as a message and a remedy. Never a traceback."""
        from docmax.core.errors import CancelledError, DocMaxError, InternalError

        if isinstance(exc, CancelledError):
            self._set_status(f"{exc.message} Nothing was written.", state="error")
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

    def _set_status(self, text: str, *, state: str = "idle") -> None:
        """Set the status line's text and its state-driven visual treatment.

        ``state`` is one of ``idle`` / ``running`` / ``success`` / ``error``.
        It selects a CSS class — colour and weight — and a small icon glyph
        from ``_STATUS_ICONS``, so idle, running, succeeded and failed read as
        genuinely different states rather than the same plain line with
        different words in it. Driven entirely by ``state``, never by
        anything about a particular tool.
        """
        widget = self.query_one("#status", Static)
        widget.set_classes(f"status-{state}")
        icon = _STATUS_ICONS.get(state, "")
        widget.update(f"{icon}  {text}" if icon and text else text)

    def _set_details(self, text: str) -> None:
        self.query_one("#details", Static).update(text)


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
    .input-row { height: auto; }
    .input-row Input { width: 1fr; }
    .input-row Button { margin-left: 1; }
    .component-row { height: auto; }
    .component { width: 1fr; margin-right: 1; height: auto; }
    .component-label { color: $text-muted; text-style: bold; }
    #progress { padding: 1 0; }
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
    ModalScreen { align: center middle; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
    ]

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.push_screen(ToolListScreen())


__all__ = ["ConsentScreen", "DocMaxApp", "ErrorScreen", "RunScreen", "ToolListScreen"]
