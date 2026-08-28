"""The TUI.

Weighted deliberately: most of what matters is plain data and plain functions —
which tools are offered, what fields a spec produces, what a filled-in form
becomes — and none of that needs a terminal. The Textual half is then thin
enough that a handful of `Pilot` tests prove it is wired up.

There are **no snapshot tests**. ADR 0005's restraint about the pickers applies
here too: a golden image of a terminal is fragile in the size of the terminal,
the version of the framework and the width of a font, and it would fail far more
often for reasons nobody cares about than for reasons anybody does.

The one structural claim asserted here is the one that keeps the design honest:
**the TUI contains no per-tool code.** Every screen is generated from the
registry, so a nineteenth tool appears with no edit.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.errors import (
    ConsentRequiredError,
    DocMaxError,
    InvalidParameterError,
    OutputExistsError,
)
from docmax.core.models import Engine, ToolResult
from docmax.core.registry import Param, get_tool
from docmax.tui import catalog, forms, runner

if TYPE_CHECKING:
    from collections.abc import Sequence

textual = pytest.importorskip("textual", reason="the TUI needs the `tui` extra")

TUI_SOURCE = Path(runner.__file__).parent


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_availability_is_answered_without_importing_textual() -> None:
    """Same discipline as every EngineStrategy: `find_spec`, never an import."""
    import docmax.tui as tui

    assert tui.is_available() is True
    assert tui.unavailable_reason() is None


def test_a_missing_textual_is_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never an ImportError traceback, and it names the install command."""
    import docmax.tui as tui
    from docmax.core.errors import LocalDependencyMissingError

    monkeypatch.setattr(tui, "is_available", lambda: False)
    with pytest.raises(LocalDependencyMissingError) as caught:
        tui.require_available()

    assert "textual" in caught.value.message
    assert "pip install" in (caught.value.remedy or "")


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


def test_the_tui_offers_exactly_what_the_cli_exposes() -> None:
    """The D7 seam, held by a test rather than by memory.

    `ocr` is registered and unimplemented until M8. The CLI never exposed it;
    the TUI must not either, and the day it ships this test is what makes the
    one-line deletion in `catalog.py` non-optional.
    """
    from docmax.cli.commands import app_commands
    from docmax.cli.main import app

    def names(commands: Any) -> set[str]:
        return {
            command.name or (command.callback.__name__.replace("_", "-"))
            for command in commands
            if command.callback is not None
        }

    exposed = names(app.registered_commands) | names(app_commands.registered_commands)
    offered = {spec.name for spec in catalog.offered_tools()}

    assert offered <= exposed
    assert exposed - offered <= {"doctor", "formats", "tui"}


def test_ocr_is_registered_and_not_offered() -> None:
    from docmax.core.registry import build_registry

    assert "ocr" in build_registry()
    assert not catalog.is_offered("ocr")
    assert "ocr" in catalog.UNIMPLEMENTED


def test_every_offered_tool_is_grouped() -> None:
    grouped = catalog.categories()
    flattened = [spec for specs in grouped.values() for spec in specs]
    assert sorted(spec.name for spec in flattened) == sorted(
        spec.name for spec in catalog.offered_tools()
    )


def test_crop_and_reorder_are_both_offered() -> None:
    """M7's two picker tools are reachable from the TUI as ordinary tools."""
    assert catalog.is_offered("crop")
    assert catalog.is_offered("reorder")


# ---------------------------------------------------------------------------
# Forms — the generated half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "kind"),
    [
        ("str", "text"),
        ("int", "integer"),
        ("float", "number"),
        ("bool", "boolean"),
        ("path", "path"),
    ],
)
def test_every_param_type_renders(type_: str, kind: str) -> None:
    """`Param.type_` is a small closed set precisely so this can be total."""
    field = forms.field_for(Param(name="p", description="d", type_=type_))
    assert field.kind == kind


def test_choices_become_a_dropdown_not_a_text_box() -> None:
    field = forms.field_for(Param(name="preset", description="d", type_="str", choices=("a", "b")))
    assert field.kind == "choice"
    assert field.choices == ("a", "b")


def test_an_unknown_type_still_renders_as_text() -> None:
    """A tool declaring a sixth type is a registry bug, and hiding eighteen
    working tools behind it would be the wrong response."""
    assert forms.field_for(Param(name="p", description="d", type_="mystery")).kind == "text"


def test_fields_keep_declaration_order() -> None:
    spec = get_tool("compress")
    assert [f.name for f in forms.fields_for(spec)] == [p.name for p in spec.params]


def test_collect_converts_each_type() -> None:
    fields = [
        forms.Field(name="s", label="s", kind="text", description=""),
        forms.Field(name="i", label="i", kind="integer", description=""),
        forms.Field(name="f", label="f", kind="number", description=""),
        forms.Field(name="b", label="b", kind="boolean", description=""),
    ]
    params = forms.collect(fields, {"s": "hi", "i": "3", "f": "1.5", "b": "yes"})
    assert params == {"s": "hi", "i": 3, "f": 1.5, "b": True}


def test_an_empty_optional_field_is_omitted_not_passed_as_none() -> None:
    """Every tool reads `params.get(name, default)`. An explicit None would
    override the default the tool intended — `rotate --by` would become 0."""
    fields = [forms.Field(name="by", label="by", kind="integer", description="")]
    assert forms.collect(fields, {"by": "   "}) == {}


def test_an_empty_required_field_is_a_typed_error() -> None:
    fields = [forms.Field(name="box", label="box", kind="text", description="d", required=True)]
    with pytest.raises(InvalidParameterError):
        forms.collect(fields, {"box": ""})


@pytest.mark.parametrize(
    ("kind", "text"),
    [("integer", "three"), ("number", "x"), ("boolean", "maybe"), ("choice", "nope")],
)
def test_a_bad_value_is_a_typed_error_with_a_remedy(kind: str, text: str) -> None:
    """`DOCMAX_OFFLINE=maybe` read as false would send documents. The same
    reasoning applies to a form field."""
    field = forms.Field(
        name="p",
        label="p",
        kind=kind,
        description="d",
        choices=("a", "b") if kind == "choice" else (),
    )
    with pytest.raises(InvalidParameterError) as caught:
        forms.collect([field], {"p": text})
    assert caught.value.remedy


def test_a_path_field_is_not_resolved_here() -> None:
    """`DocumentRef.from_path` and `OutputTarget.resolve` own that, and doing it
    twice is how two implementations of the in-place check start to differ."""
    field = forms.Field(name="p", label="p", kind="path", description="")
    assert forms.collect([field], {"p": "~/a.pdf"}) == {"p": "~/a.pdf"}


def test_every_offered_tool_produces_a_form() -> None:
    for spec in catalog.offered_tools():
        fields = forms.fields_for(spec)
        assert len(fields) == len(spec.params)
        assert all(field.kind in forms.KINDS for field in fields)


# ---------------------------------------------------------------------------
# The runner — against a fake router, as the router's own tests are against fakes
# ---------------------------------------------------------------------------


class FakeRouter:
    """Records what it was asked to do. A TUI test needing pypdf would be
    evidence of a design failure, exactly as it is for the router's own tests."""

    def __init__(
        self, *, result: ToolResult | None = None, raises: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.targets: list[tuple[str, str | None, bool]] = []
        self.consent = _FakeConsent()
        self.config = None
        self._result = result or ToolResult(outputs=(), engine_used=Engine.LOCAL)
        self._raises = raises
        self.resolved: list[str] = []

    def target_for(
        self, tool: str, docs: Sequence[Any], *, requested: str | None = None, force: bool = False
    ) -> object:
        self.targets.append((tool, requested, force))
        return object()

    def run(self, tool: str, docs: Sequence[Any], target: object, **kwargs: Any) -> ToolResult:
        self.calls.append({"tool": tool, "docs": list(docs), **kwargs})
        if self._raises is not None:
            raising, self._raises = self._raises, None
            raise raising
        return self._result

    def resolve(self, tool: str, *, requested: Engine | None = None) -> object:
        self.resolved.append(tool)
        from docmax.core.router import Routing

        return Routing(Engine.LOCAL, "because this is a fake")


class _FakeConsent:
    def __init__(self) -> None:
        self.recorded: list[str] = []

    def record(self, tool: str) -> None:
        self.recorded.append(tool)

    def has(self, tool: str) -> bool:
        return tool in self.recorded


def test_a_run_reaches_the_router_with_the_form_values(tmp_path: Path) -> None:
    router = FakeRouter()
    request = runner.RunRequest(
        tool="crop",
        inputs=(_touch(tmp_path / "in.pdf"),),
        output=tmp_path / "out.pdf",
        params={"box": "10,10,100,100"},
    )

    runner.run(request, router=router)  # type: ignore[arg-type]

    (call,) = router.calls
    assert call["tool"] == "crop"
    assert call["box"] == "10,10,100,100"
    assert call["dry_run"] is False
    assert router.targets == [("crop", str(tmp_path / "out.pdf"), False)]


def test_the_destination_is_resolved_by_the_router_not_the_tui(tmp_path: Path) -> None:
    """Resolving it here would be a second implementation of the in-place and
    already-exists checks."""
    router = FakeRouter()
    runner.run(
        runner.RunRequest(tool="crop", inputs=(_touch(tmp_path / "in.pdf"),)),
        router=router,  # type: ignore[arg-type]
    )
    assert router.targets == [("crop", None, False)]


def test_progress_and_cancellation_reach_the_router(tmp_path: Path) -> None:
    from docmax.core.cancellation import CancellationToken

    router = FakeRouter()
    token = CancellationToken()
    progress = runner.CallbackProgress()

    runner.run(
        runner.RunRequest(tool="crop", inputs=(_touch(tmp_path / "in.pdf"),)),
        router=router,  # type: ignore[arg-type]
        progress=progress,
        cancellation=token,
    )

    (call,) = router.calls
    assert call["progress"] is progress
    assert call["cancellation"] is token


def test_a_typed_error_comes_back_typed(tmp_path: Path) -> None:
    router = FakeRouter(raises=OutputExistsError("it exists", remedy="pass --force"))
    with pytest.raises(OutputExistsError):
        runner.run(
            runner.RunRequest(tool="crop", inputs=(_touch(tmp_path / "in.pdf"),)),
            router=router,  # type: ignore[arg-type]
        )


def test_progress_never_raises() -> None:
    """A broken progress bar must not lose someone's merged document."""

    def explode(*_args: Any) -> None:
        raise RuntimeError("the widget is gone")

    progress = runner.CallbackProgress(on_start=explode, on_advance=explode, on_finish=explode)
    progress.start("x", total=3)
    progress.advance()
    progress.finish()

    assert progress.completed == 1


def test_progress_records_what_it_was_told() -> None:
    seen: list[tuple[str, int | None]] = []
    progress = runner.CallbackProgress(on_start=lambda d, t: seen.append((d, t)))
    progress.start("Cropping 3 page(s)", total=3)
    progress.advance(2)

    assert seen == [("Cropping 3 page(s)", 3)]
    assert progress.total == 3
    assert progress.completed == 2


def test_granting_consent_records_it() -> None:
    router = FakeRouter()
    assert runner.grant_consent("compress", router=router) is True  # type: ignore[arg-type]
    assert router.consent.recorded == ["compress"]


def test_consent_cannot_be_granted_with_nowhere_to_record_it() -> None:
    """Agreeing would be a promise nothing kept."""
    router = FakeRouter()
    router.consent = None  # type: ignore[assignment]
    assert runner.grant_consent("compress", router=router) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The structural claim
# ---------------------------------------------------------------------------


def test_the_tui_names_no_tool_except_the_unimplemented_one() -> None:
    """No per-tool code anywhere in the TUI.

    Every screen is generated from the registry, so the only tool name that may
    appear as a literal in this package is the one `catalog.py` documents as
    registered-but-not-implemented.
    """
    tool_names = {spec.name for spec in catalog.offered_tools()}
    offenders: list[str] = []

    for path in sorted(TUI_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in tool_names:
                offenders.append(f"{path.name}:{node.lineno} — {node.value!r}")

    assert not offenders, "the TUI must not name a tool: " + "; ".join(offenders)


def test_the_tui_imports_no_other_interface() -> None:
    """`cli`, `tui`, `server` and `mcp` are peers. import-linter enforces this
    too; asserting it here means the failure names the file."""
    forbidden = ("docmax.cli", "docmax.server", "docmax.mcp")
    offenders: list[str] = []

    for path in sorted(TUI_SOURCE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        offenders.extend(f"{path.name}: {name}" for name in forbidden if f"import {name}" in text)

    assert not offenders, offenders


def test_the_cli_reaches_only_the_tui_entry_point() -> None:
    """The narrow half of ADR 0020.

    ``.importlinter`` ignores exactly one import pair so that ``docmax tui`` can
    start the app. That allowance must stay an *entry point*: the CLI importing
    ``docmax.tui.app``, ``.runner``, ``.forms`` or ``.catalog`` would be one
    interface reaching into another's internals, which is the traffic the
    independence contract exists to forbid.
    """
    cli_source = Path(TUI_SOURCE).parent / "cli"
    offenders: list[str] = []

    for path in sorted(cli_source.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("docmax.tui."):
                offenders.append(f"{path.name}:{node.lineno} — {node.module}")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno} — {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("docmax.tui.")
                )

    assert not offenders, "the CLI may import docmax.tui and nothing below it: " + "; ".join(
        offenders
    )


def _touch(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\n")
    return path


def _text_of(widget: Any) -> str:
    """The text a ``Static`` is showing, whatever Textual calls it this year.

    8.x renamed ``renderable`` to ``content``. Both are tried rather than
    pinning the test to one version, because what is being asserted is that the
    message and the remedy are on screen — not which attribute holds them.
    """
    for attribute in ("content", "renderable"):
        value = getattr(widget, attribute, None)
        if value is not None:
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# The Textual half — Pilot, no snapshots
# ---------------------------------------------------------------------------


def test_the_app_starts_and_lists_tools() -> None:
    import asyncio

    from textual.widgets import ListItem

    from docmax.tui.app import DocMaxApp

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert type(app.screen).__name__ == "ToolListScreen"
            items = app.screen.query(ListItem)
            assert len(items) == len(catalog.offered_tools())
            ids = {item.id for item in items}
            assert "tool-crop" in ids
            assert "tool-ocr" not in ids

    asyncio.run(scenario())


def test_every_offered_tool_opens_a_form() -> None:
    """Eighteen tools, one screen class, no per-tool code."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for spec in catalog.offered_tools():
                app.push_screen(RunScreen(spec.name))
                await pilot.pause()
                assert isinstance(app.screen, RunScreen)
                for param in spec.params:
                    assert len(app.screen.query(f"#field-{param.name}")) == 1
                app.pop_screen()
                await pilot.pause()

    asyncio.run(scenario())


def test_selecting_parameters_reaches_the_normal_execution_path(tmp_path: Path) -> None:
    """The claim the whole milestone rests on: what the user types in the TUI
    arrives at `EngineRouter.run` as ordinary parameters."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _touch(tmp_path / "in.pdf")
    router = FakeRouter()
    seen: list[dict[str, Any]] = []

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            screen.query_one("#field-box", Input).value = "10,10,100,100"
            await pilot.pause()

            request = screen._request(dry_run=False, force=True)
            runner.run(request, router=router)  # type: ignore[arg-type]
            seen.extend(router.calls)

    asyncio.run(scenario())

    (call,) = seen
    assert call["tool"] == "crop"
    assert call["box"] == "10,10,100,100"
    assert router.targets == [("crop", str(tmp_path / "out.pdf"), True)]


def test_a_form_with_no_input_is_a_typed_error_not_a_crash(tmp_path: Path) -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            with pytest.raises(InvalidParameterError):
                screen._request(dry_run=False, force=False)

    asyncio.run(scenario())


def test_an_error_is_shown_as_a_modal_with_a_remedy() -> None:
    """A user should never see a traceback for an anticipated condition."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, ErrorScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            error = OutputExistsError("it already exists", remedy="Pass --force to overwrite.")
            app.push_screen(ErrorScreen(error))
            await pilot.pause()

            rendered = " ".join(_text_of(widget) for widget in app.screen.query(Static))
            assert "it already exists" in rendered
            assert "Pass --force to overwrite." in rendered
            assert "output.exists" in rendered
            assert "Traceback" not in rendered

    asyncio.run(scenario())


def test_a_non_docmax_failure_is_wrapped_rather_than_raised() -> None:
    """Reaching the UI with an untyped exception must still not be a traceback."""
    import asyncio

    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen._show(RuntimeError("something nobody anticipated"))
            await pilot.pause()

            assert isinstance(app.screen, ErrorScreen)
            assert isinstance(app.screen.error, DocMaxError)

    asyncio.run(scenario())


def test_cancelling_a_run_cancels_the_token() -> None:
    """ctrl+c asks the operation to stop; it does not raise KeyboardInterrupt,
    because Textual owns the keyboard."""
    import asyncio

    from docmax.core.cancellation import CancellationToken
    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            token = CancellationToken()
            screen._token = token
            screen._running = True

            await pilot.press("ctrl+c")
            await pilot.pause()

            assert token.is_cancelled

    asyncio.run(scenario())


def test_the_consent_modal_answers_yes_and_no() -> None:
    """`errors.py` has said since M0 that the TUI renders this as a modal."""
    import asyncio

    from docmax.tui.app import ConsentScreen, DocMaxApp

    answers: list[bool | None] = []

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            error = ConsentRequiredError("uploading to example.test", tool="compress")

            for button, expected in (("#agree", True), ("#refuse", False)):
                app.push_screen(ConsentScreen("compress", error.message), answers.append)
                await pilot.pause()
                await pilot.click(button)
                await pilot.pause()
                assert answers[-1] is expected

    asyncio.run(scenario())
