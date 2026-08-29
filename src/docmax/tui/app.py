"""The Textual application: three screens, two modals, and no document logic.

Every screen here does the same two things — read a ``ToolSpec`` and call
``tui/runner.py``. There is no per-tool code, which is what
:mod:`docmax.tui.forms` exists to make possible and what
``tests/unit/test_tui.py`` asserts structurally.

## The threading rule

``EngineRouter.run`` blocks. Blocking Textual's event loop would freeze the UI
and make cancellation impossible — which is the one thing a long compress most
needs. So a run happens on a worker thread (``@work(thread=True)``), and
everything it wants to say comes back through ``call_from_thread``.

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

from typing import TYPE_CHECKING, Any, ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Select,
    Static,
)

from docmax.core.branding import APP_NAME
from docmax.tui import catalog, forms, runner

if TYPE_CHECKING:
    from docmax.core.errors import DocMaxError
    from docmax.core.models import ToolResult
    from docmax.core.registry import ToolSpec

#: The engine choices a run screen offers. ``auto`` is first and is the default,
#: because the router's ladder is the behaviour a user should get unless they
#: deliberately want otherwise.
_ENGINES = ("auto", "local", "cloud")


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


class ToolListScreen(Screen[None]):
    """Every offered tool, grouped by category. The application's home."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "choose", "Run", show=True),
        Binding("q", "app.quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="tools"):
            for category, specs in catalog.categories().items():
                yield Static(category.upper(), classes="category")
                yield ListView(
                    *(
                        ListItem(
                            Label(f"{spec.name}\n  {spec.summary}", markup=False),
                            id=f"tool-{spec.name}",
                        )
                        for spec in specs
                    ),
                    classes="tool-list",
                )
        yield Footer()

    @on(ListView.Selected)
    def open_tool(self, event: ListView.Selected) -> None:
        identifier = event.item.id or ""
        name = identifier.removeprefix("tool-")
        if catalog.is_offered(name):
            self.app.push_screen(RunScreen(name))

    def action_choose(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_select_cursor()


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
        self._running = False

    @property
    def spec(self) -> ToolSpec:
        if self._spec is None:
            from docmax.core.registry import get_tool

            self._spec = get_tool(self.tool)
        return self._spec

    def compose(self) -> ComposeResult:
        spec = self.spec
        self._fields = forms.fields_for(spec)

        yield Header()
        with VerticalScroll(id="form"):
            yield Static(f"{spec.name} — {spec.summary}", classes="title", markup=False)

            plural = "s, comma-separated" if spec.accepts_multiple_inputs else ""
            yield Label(f"input{plural}")
            yield Input(placeholder=f"path to the document{plural}", id="field-__inputs__")

            yield Label("output")
            yield Input(
                placeholder=f"leave empty to derive a {spec.default_suffix} name",
                id="field-__output__",
            )

            for field in self._fields:
                required = " (required)" if field.required else ""
                yield Label(f"{field.label}{required}")
                if field.kind == "choice":
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
            yield Static("", id="status", markup=False)
        yield Footer()

    # -- actions ------------------------------------------------------------

    def action_back(self) -> None:
        if not self._running:
            self.app.pop_screen()

    def action_run(self) -> None:
        self._start(dry_run=False, force=self._force)

    def action_cancel(self) -> None:
        """Ask the run to stop. Never raises; never kills the app."""
        if self._token is not None:
            self._token.cancel()
            self._set_status("Stopping…")

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
            else "The output will not be overwritten."
        )

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    # -- running ------------------------------------------------------------

    def _start(self, *, dry_run: bool, force: bool) -> None:
        if self._running:
            return
        try:
            request = self._request(dry_run=dry_run, force=force)
        except Exception as exc:
            self._show(exc)
            return

        from docmax.core.cancellation import CancellationToken

        self._token = CancellationToken()
        self._running = True
        self.query_one("#cancel", Button).disabled = False
        self.query_one("#run", Button).disabled = True
        self._set_status("Running…")
        self._execute(request)

    def _request(self, *, dry_run: bool, force: bool) -> runner.RunRequest:
        """Gather the form into a request, or raise the typed error saying why not."""
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
        engine_value = _selected(self.query_one("#field-__engine__", Select), _ENGINES)
        engine = None if engine_value in ("", "auto") else Engine(engine_value)

        values = {field.name: self._value_of(field) for field in self._fields}

        return runner.RunRequest(
            tool=self.tool,
            inputs=inputs,
            output=Path(output_text) if output_text else None,
            engine=engine,
            force=force,
            dry_run=dry_run,
            params=forms.collect(self._fields, values),
        )

    def _value_of(self, field: forms.Field) -> str:
        widget = self.query_one(f"#field-{field.name}")
        if isinstance(widget, Select):
            return _selected(widget, field.choices)
        if isinstance(widget, Input):
            return widget.value
        return ""

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
        self._set_status(description)

    def _progress_advance(self, amount: int) -> None:
        self.query_one("#progress", ProgressBar).advance(amount)

    def _progress_finish(self) -> None:
        self.query_one("#progress", ProgressBar).update(progress=0, total=None)

    def _finished(self) -> None:
        self._running = False
        self._token = None
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#run", Button).disabled = False

    def _succeeded(self, result: ToolResult) -> None:
        if result.details.get("dry_run"):
            self._set_status(
                f"Dry run — would use the {result.engine_used.value} engine "
                f"({result.details.get('reason', 'no reason given')}), "
                f"writing {result.details.get('destination', '—')}."
            )
            return
        written = ", ".join(str(path) for path in result.outputs) or "nothing"
        self._set_status(f"Wrote {written}  ·  {result.engine_used.value} engine")

    def _ask_consent(self, exc: DocMaxError) -> bool:
        from docmax.core.errors import ConsentRequiredError

        tool = exc.tool if isinstance(exc, ConsentRequiredError) else self.tool
        return bool(self.app.push_screen_wait(ConsentScreen(tool, exc.message)))

    def _show(self, exc: BaseException) -> None:
        """Display any failure as a message and a remedy. Never a traceback."""
        from docmax.core.errors import CancelledError, DocMaxError, InternalError

        if isinstance(exc, CancelledError):
            self._set_status(f"{exc.message} Nothing was written.")
            return
        if not isinstance(exc, DocMaxError):
            # The router wraps anything escaping a tool, so reaching here means
            # something failed on this side of it. It still gets a panel rather
            # than a stack trace: a user should never see one for a condition we
            # anticipated, and an unanticipated one becomes a bug report.
            exc = InternalError(str(exc) or exc.__class__.__name__)
        self._set_status(exc.message)
        self.app.push_screen(ErrorScreen(exc))

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)


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
    """A typed error, as its message and its remedy. Never a stack trace."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close", show=True)]

    def __init__(self, error: DocMaxError) -> None:
        super().__init__()
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal error"):
            yield Static(self.error.code.value, classes="title", markup=False)
            yield Static(self.error.message, markup=False)
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
    """The application shell."""

    TITLE = APP_NAME
    CSS = """
    .title { text-style: bold; padding: 1 0; }
    .category { color: $text-muted; text-style: bold; padding: 1 0 0 0; }
    .hint { color: $text-muted; padding: 0 0 1 0; }
    .remedy { color: $accent; padding: 1 0 0 0; }
    .actions { height: auto; padding: 1 0; }
    .actions Button { margin-right: 1; }
    .modal {
        width: 70;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    .modal.error { border: thick $error; }
    ModalScreen { align: center middle; }
    #form { padding: 0 2; }
    #tools { padding: 0 2; }
    #progress { padding: 1 0; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
    ]

    def on_mount(self) -> None:
        self.push_screen(ToolListScreen())


__all__ = ["ConsentScreen", "DocMaxApp", "ErrorScreen", "RunScreen", "ToolListScreen"]
