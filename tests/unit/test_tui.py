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

    The allowed remainder is the set of CLI commands that are **not tools**, and
    which the TUI therefore has nothing to generate a form from. `doctor` and
    `formats` answer questions, `tui` and `mcp` are entry points into the other
    two interfaces, and M9's `pipeline`, `batch` and `watch` compose tools rather
    than being any — none of the seven is in the registry, which is what
    `offered <= exposed` above still asserts strictly. See ADR 0021, ADR 0023
    and ADR 0027.
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
    assert exposed - offered <= {
        "doctor",
        "formats",
        "tui",
        "mcp",
        "pipeline",
        "batch",
        "watch",
    }


def test_every_registered_tool_is_offered() -> None:
    """`UNIMPLEMENTED` held exactly one name — `ocr` — and M8 emptied it.

    ADR 0021 predicted this: *"fails the day `ocr` ships unless `UNIMPLEMENTED`
    is emptied."* It did, and this is the inverted assertion. The mechanism is
    kept for the next tool that lands as a skeleton, and this test is what stops
    it quietly refilling — a name added here without a matching CLI command
    would also break `test_the_tui_offers_exactly_what_the_cli_exposes`.
    """
    from docmax.core.registry import build_registry

    assert frozenset() == catalog.UNIMPLEMENTED
    assert catalog.is_offered("ocr")
    assert {spec.name for spec in catalog.offered_tools()} == set(build_registry())


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


def test_ocr_generates_a_form_with_no_tui_code() -> None:
    """M8 added a tool and not one line of TUI. That is ADR 0021 working."""
    fields = {field.name: field.kind for field in forms.fields_for(get_tool("ocr"))}

    assert fields == {"lang": "text", "dpi": "integer", "deskew": "boolean"}


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


# ---------------------------------------------------------------------------
# Composite fields (ADR 0032) — a comma-separated value with labelled parts
# ---------------------------------------------------------------------------


def test_field_for_copies_component_labels_from_the_param() -> None:
    param = Param(name="box", description="d", component_labels=("x", "y", "width", "height"))
    assert forms.field_for(param).components == ("x", "y", "width", "height")


def test_a_param_with_no_component_labels_has_an_empty_components_field() -> None:
    """Every param but `crop`'s `box` renders as the single field it always
    has — `components` defaults to empty, not `None`, so callers can test it
    with a plain truthiness check."""
    assert forms.field_for(Param(name="p", description="d")).components == ()


def test_crops_box_param_declares_its_four_components() -> None:
    """The registry is the one place this is declared — see ADR 0032."""
    spec = get_tool("crop")
    (box,) = spec.params
    assert box.name == "box"
    assert box.component_labels == ("x", "y", "width", "height")


def test_default_component_splits_a_default_that_fits_the_shape() -> None:
    field = forms.Field(
        name="box",
        label="box",
        kind="text",
        description="d",
        default="10,10,500,700",
        components=("x", "y", "width", "height"),
    )
    assert [field.default_component(i) for i in range(4)] == ["10", "10", "500", "700"]


def test_default_component_is_blank_when_there_is_no_default() -> None:
    field = forms.Field(
        name="box",
        label="box",
        kind="text",
        description="d",
        components=("x", "y", "width", "height"),
    )
    assert [field.default_component(i) for i in range(4)] == ["", "", "", ""]


def test_default_component_is_blank_when_the_default_does_not_fit_the_shape() -> None:
    """A default with the wrong number of parts is not partially guessed at —
    every part is left blank rather than misassigning a value to the wrong
    label."""
    field = forms.Field(
        name="box",
        label="box",
        kind="text",
        description="d",
        default="1,2,3",
        components=("x", "y", "width", "height"),
    )
    assert [field.default_component(i) for i in range(4)] == ["", "", "", ""]


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
        self,
        *,
        result: ToolResult | None = None,
        raises: Exception | None = None,
        missing: tuple[Any, ...] = (),
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.targets: list[tuple[str, str | None, bool]] = []
        self.consent = _FakeConsent()
        self.config = None
        self._result = result or ToolResult(outputs=(), engine_used=Engine.LOCAL)
        self._raises = raises
        self.resolved: list[str] = []
        #: What `missing_dependencies` reports — empty unless a test asks for
        #: the "a strategy can name exactly what's missing" path.
        self.missing = missing
        self.missing_dependencies_calls: list[tuple[str, Engine]] = []

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

    def missing_dependencies(self, tool: str, engine: Engine) -> tuple[Any, ...]:
        self.missing_dependencies_calls.append((tool, engine))
        return self.missing


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


def _write_pdf(path: Path, pages: int = 1) -> Path:
    """A real, parseable PDF — for the handful of tests that let the real
    `local.py` strategy actually run, rather than stopping at `_touch`'s stub
    before any real parsing happens."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _set_box(screen: Any, spec: str) -> None:
    """Fill crop's four labelled ``box`` inputs from an ``x,y,width,height``
    string. The composite-field UI (ADR 0032) replaced the single
    ``#field-box`` input these tests used to set directly with one input per
    label, ``#field-box-0`` through ``#field-box-3``."""
    from textual.widgets import Input

    for index, value in enumerate(spec.split(",")):
        screen.query_one(f"#field-box-{index}", Input).value = value.strip()


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
    """Requirement 2: every offered tool is present, as a button."""
    import asyncio

    from textual.widgets import Button

    from docmax.tui.app import DocMaxApp

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert type(app.screen).__name__ == "ToolListScreen"
            buttons = app.screen.query(".tool-button")
            assert len(buttons) == len(catalog.offered_tools()) == 19
            ids = {button.id for button in buttons}
            assert "tool-crop" in ids
            # `ocr` was the one name `UNIMPLEMENTED` ever held, and M8 shipped
            # it. Every registered tool is now listed.
            assert "tool-ocr" in ids
            assert all(isinstance(button, Button) for button in buttons)

    asyncio.run(scenario())


def test_categories_render_as_headings_over_their_own_tools() -> None:
    """Requirement 1: category headings stay, each grouping exactly the tools
    ``catalog.categories()`` puts under it — generic over whatever the
    registry currently reports, not a fixed, hand-written list."""
    import asyncio

    from docmax.tui.app import DocMaxApp
    from docmax.tui.catalog import categories

    async def scenario() -> dict[str, list[str]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            headings = [_text_of(widget) for widget in screen.query(".category")]
            columns = list(screen.query(".tool-column"))
            assert len(headings) == len(columns)
            return {
                heading: [button.id or "" for button in column.query(".tool-button")]
                for heading, column in zip(headings, columns, strict=True)
            }

    rendered = asyncio.run(scenario())
    expected = {
        category.upper(): [f"tool-{spec.name}" for spec in specs]
        for category, specs in categories().items()
    }
    assert rendered == expected


def test_tools_within_a_category_are_stacked_vertically() -> None:
    """Requirement 3: tools in the same category are stacked, one per row —
    a wide terminal must not spread `edit`'s four tools — crop, pages,
    reorder, rotate — across the screen the way the grid layout once did."""
    import asyncio

    from docmax.tui.app import DocMaxApp

    async def scenario() -> list[int]:
        app = DocMaxApp()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            return [
                screen.query_one(f"#tool-{name}").region.y
                for name in ("crop", "pages", "reorder", "rotate")
            ]

    rows = asyncio.run(scenario())
    assert len(set(rows)) == len(rows), f"expected four distinct rows, got {rows}"
    assert rows == sorted(rows), "expected top-to-bottom order matching declaration order"


def test_tool_button_width_fits_its_own_name() -> None:
    """Requirement 4/5: compact and readable — a short name gets a short
    button, a long one gets a wider one, neither padded to a shared column."""
    import asyncio

    from textual.widgets import Button

    from docmax.tui.app import DocMaxApp

    async def scenario() -> tuple[int, int]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            short = screen.query_one("#tool-ocr", Button).size.width
            long = screen.query_one("#tool-from-images", Button).size.width
            return short, long

    short_width, long_width = asyncio.run(scenario())
    assert short_width < long_width
    assert short_width <= len("ocr") + 4  # a little padding, not a fixed wide column


def test_every_tool_buttons_label_is_its_own_name() -> None:
    """Requirement 5: tool names must be visible — each button's rendered
    label is the exact tool name it opens, for every offered tool, not a
    sample."""
    import asyncio

    from textual.widgets import Button

    from docmax.tui.app import DocMaxApp

    async def scenario() -> dict[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return {
                button.id or "": str(button.label)
                for button in app.screen.query(Button).filter(".tool-button")
            }

    labels = asyncio.run(scenario())
    for spec in catalog.offered_tools():
        assert labels[f"tool-{spec.name}"] == spec.name


def test_clicking_a_tool_button_opens_its_run_screen() -> None:
    """Requirement 3."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#tool-merge")
            await pilot.pause()
            assert isinstance(app.screen, RunScreen)
            return app.screen.tool

    assert asyncio.run(scenario()) == "merge"


def test_enter_opens_the_focused_tool() -> None:
    """Requirement 4: keyboard-only, no mouse."""
    import asyncio

    from textual.widgets import Button

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.screen.query_one("#tool-crop", Button).focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, RunScreen)
            return app.screen.tool

    assert asyncio.run(scenario()) == "crop"


def test_tab_moves_focus_across_tools_in_the_offered_order() -> None:
    """Requirement 5: keyboard navigation, preserving the existing tool
    ordering (category, then name) rather than some grid-shaped order of its
    own."""
    import asyncio

    from docmax.tui.app import DocMaxApp

    async def scenario() -> list[str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            buttons = list(app.screen.query(".tool-button"))
            buttons[0].focus()
            await pilot.pause()
            assert app.screen.focused is not None
            focused = [app.screen.focused.id or ""]
            for _ in range(len(buttons) - 1):
                await pilot.press("tab")
                await pilot.pause()
                assert app.screen.focused is not None
                focused.append(app.screen.focused.id or "")
            return focused

    focused = asyncio.run(scenario())
    assert focused == [f"tool-{spec.name}" for spec in catalog.offered_tools()]


def test_the_catalog_scrolls_when_the_window_is_too_small() -> None:
    """Requirement 6."""
    import asyncio

    from textual.containers import VerticalScroll

    from docmax.tui.app import DocMaxApp

    async def scenario() -> tuple[int, int]:
        app = DocMaxApp()
        async with app.run_test(size=(60, 15)) as pilot:
            await pilot.pause()
            buttons = app.screen.query(".tool-button")
            assert len(buttons) == 19, "every tool stays reachable, just off-screen"
            container = app.screen.query_one("#tools", VerticalScroll)
            return container.max_scroll_y, len(buttons)

    max_scroll_y, button_count = asyncio.run(scenario())
    assert max_scroll_y > 0
    assert button_count == 19


def test_every_offered_tool_opens_a_form() -> None:
    """Eighteen tools, one screen class, no per-tool code.

    A param with ``component_labels`` (see ADR 0032) renders one labelled
    input per label instead of the single field every other param gets — this
    checks for whichever shape the registry itself declares, rather than
    assuming one.
    """
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
                    if param.component_labels:
                        for index in range(len(param.component_labels)):
                            assert len(app.screen.query(f"#field-{param.name}-{index}")) == 1
                    else:
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
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            request = screen._request(dry_run=False, force=True)
            runner.run(request, router=router)  # type: ignore[arg-type]
            seen.extend(router.calls)

    asyncio.run(scenario())

    (call,) = seen
    assert call["tool"] == "crop"
    assert call["box"] == "10,10,100,100"
    assert router.targets == [("crop", str(tmp_path / "out.pdf"), True)]


def test_box_renders_as_four_labelled_inputs_not_one_blind_field() -> None:
    """ADR 0032: `crop`'s `box` param declares `component_labels`, so the form
    renders one labelled input per label instead of a single `x,y,width,height`
    field nobody can fill in without reading the docs first."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> tuple[list[str], list[str]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            assert len(screen.query("#field-box")) == 0, "no single blind field remains"

            labels = [_text_of(widget) for widget in screen.query(".component-label")]
            placeholders = [
                screen.query_one(f"#field-box-{i}", Input).placeholder for i in range(4)
            ]
            return labels, placeholders

    labels, placeholders = asyncio.run(scenario())
    assert labels == ["x", "y", "width", "height"]
    assert placeholders == ["x", "y", "width", "height"]


def test_leaving_every_box_component_empty_is_not_supplied(tmp_path: Path) -> None:
    """Requirement: the composite field's "empty" rule matches every other
    field's — nothing typed reads as "not supplied", which for a required
    field like `box` is the usual missing-parameter error."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            await pilot.pause()

            with pytest.raises(InvalidParameterError) as caught:
                screen._request(dry_run=False, force=False)
            assert caught.value.context["parameter"] == "box"

    asyncio.run(scenario())


def test_a_partially_filled_box_still_reaches_the_router_joined(tmp_path: Path) -> None:
    """Filling in only some of the four inputs is not blocked by the TUI —
    the joined value reaches the router exactly as a hand-typed
    `10,,500,700` always did, and `tools/_box.py`'s own parser is what gives
    the actionable error naming the empty part. Duplicating that check here
    would be a second implementation of the same message."""
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
            screen.query_one("#field-box-0", Input).value = "10"
            screen.query_one("#field-box-2", Input).value = "500"
            screen.query_one("#field-box-3", Input).value = "700"
            await pilot.pause()

            request = screen._request(dry_run=False, force=True)
            runner.run(request, router=router)  # type: ignore[arg-type]
            seen.extend(router.calls)

    asyncio.run(scenario())

    (call,) = seen
    assert call["box"] == "10,,500,700"


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


def test_output_placeholder_names_no_extension_for_a_tool_that_cannot_derive_one() -> None:
    """ADR 0033 / issue #24: `convert`'s placeholder must not claim a `.pdf`
    result, since no run of `convert` can ever produce one — Pandoc cannot
    write PDF (ADR 0011), and the real extension depends on `--to`, a
    parameter. An ordinary tool (`crop`) keeps naming its extension, since
    that hint is accurate for it.
    """
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    async def placeholder_for(tool: str) -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen(tool)
            app.push_screen(screen)
            await pilot.pause()
            return screen.query_one("#field-__output__", Input).placeholder

    convert_placeholder = asyncio.run(placeholder_for("convert"))
    crop_placeholder = asyncio.run(placeholder_for("crop"))

    assert convert_placeholder == "path to write the result to"
    assert ".pdf" not in convert_placeholder
    assert ".pdf" in crop_placeholder


def test_a_form_with_no_output_is_a_typed_error_not_a_crash(tmp_path: Path) -> None:
    """Output is required, the same as every command the CLI exposes.

    `merge`'s own CLI docstring explains why: a derived destination for a
    PDF-to-PDF tool lands on the first input, and `OutputTarget` refuses it as
    `InPlaceOverwriteError`. ADR 0028 rejected the alternative — "let an
    omitted output default beside the input" — outright, calling it "the M9
    watcher defect in another costume." Asked here, before a request is even
    built, rather than after a run that could never have written anything.
    """
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one("#field-__inputs__", Input).value = str(_touch(tmp_path / "in.pdf"))
            await pilot.pause()
            with pytest.raises(InvalidParameterError) as caught:
                screen._request(dry_run=False, force=False)
            assert caught.value.context["parameter"] == "output"

    asyncio.run(scenario())


def test_merge_with_no_output_is_a_typed_error_not_a_silent_collision(tmp_path: Path) -> None:
    """The exact scenario reported: two PDFs selected, Output left empty.

    Before this fix, leaving Output empty for a multi-input, PDF-producing
    tool like `merge` derived a destination equal to the first input — a
    guaranteed `InPlaceOverwriteError` the user would only see after clicking
    Run. This pins the fix's visible behaviour: a clear, immediate, typed
    error naming the missing field, before anything is derived or refused.
    """
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    a = _touch(tmp_path / "Linux Exp-5.pdf")
    b = _touch(tmp_path / "Linux Exp-8.pdf")

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            await pilot.pause()
            with pytest.raises(InvalidParameterError) as caught:
                screen._request(dry_run=False, force=False)
            assert caught.value.context["parameter"] == "output"

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


# ---------------------------------------------------------------------------
# Clicking Run for real — the worker actually has to start
#
# Every test above either calls `_request()`/`runner.run()` directly or sets
# `_run_in_progress`/`_token` by hand. None of them drove a real click through
# a real `@work(thread=True)` worker and waited for it — which is exactly the
# path a regression lived on undetected: `_running` (the attribute's old name)
# collided with `textual.message_pump.MessagePump`'s own attribute of the same
# name, which is `True` for the entire time a screen is mounted. `_start`'s
# "already running" guard read that and returned immediately, every time,
# forever — so clicking Run silently did nothing at all, for any tool, from
# the moment the screen appeared. These tests exist so that class of bug
# cannot recur unnoticed.
# ---------------------------------------------------------------------------


def test_clicking_run_actually_executes_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, RunScreen

    router = FakeRouter()
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert len(router.calls) == 1, "the real worker must have called the router exactly once"
    assert "Wrote" in status


def test_a_finished_run_is_repainted_before_the_callback_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GH #36: the run screen stayed on "Running..." past the point a
    synchronous cloud job (ADR 0016) had actually finished -- the file was
    already on disk, but nothing repainted the screen until an unrelated
    later event (Cancel) happened to pump the message loop.

    `Static.update()` only *marks* a widget dirty; the actual paint is
    deferred to `Screen._update_timer`, a timer that fires on its own
    throttled schedule. That is invisible whenever something else keeps
    nudging the screen soon after -- a determinate step's own repeated
    `on_advance` calls do that for free -- but `_succeeded` (like `_finished`
    and `_show`) is the *last* thing a synchronous cloud run ever tells this
    screen, so there is nothing left to drag that deferred paint onto the
    terminal. This checks the invariant `_flush_repaint` exists to
    guarantee, at the one point that actually distinguishes "scheduled" from
    "done": *inside* `_succeeded`, immediately after it updates the status,
    with no `await` yet having run that could let some other task paint it
    first. Before the fix this was still `True` here every time.
    """
    import asyncio

    from textual.widgets import Input

    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, RunScreen

    out = tmp_path / "out.pdf"
    router = FakeRouter(result=ToolResult(outputs=(out,), engine_used=Engine.CLOUD))
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")
    dirty_when_succeeded_returns: list[bool] = []

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(out)
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            original_succeeded = screen._succeeded

            def traced(result: ToolResult) -> None:
                original_succeeded(result)
                # No `await` has happened since `original_succeeded` was
                # entered, so nothing else could have painted the screen in
                # between -- this is exactly what was left `True` (and so,
                # unpainted) until Cancel was pressed.
                dirty_when_succeeded_returns.append(
                    bool(screen._repaint_required or screen._dirty_widgets)
                )

            screen._succeeded = traced  # type: ignore[method-assign]

            await pilot.click("#run")
            await pilot.pause(0.5)

    asyncio.run(scenario())
    assert dirty_when_succeeded_returns == [False], (
        "the screen must already be fully painted the instant _succeeded "
        "returns -- leaving it dirty is what let 'Running...' outlive the "
        "run until an unrelated later event pumped the repaint"
    )


def test_clicking_run_with_a_failing_router_shows_the_error_modal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same regression: a failure must reach the error
    modal by way of a real click too, not only when `_show` is called directly."""
    import asyncio

    from textual.widgets import Input

    from docmax.core.errors import InPlaceOverwriteError
    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    router = FakeRouter(raises=InPlaceOverwriteError("boom", context={"path": "x"}))
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            assert isinstance(app.screen, ErrorScreen)

    asyncio.run(scenario())


def test_merging_two_pdfs_via_the_real_tui_writes_the_merged_file(tmp_path: Path) -> None:
    """The exact scenario reported, resolved end-to-end: two PDFs, an explicit
    output, Run clicked for real — the real registry, the real router, the
    real `merge` tool, no fakes. This is the claim the whole fix rests on: the
    file really gets written where the user said.

    Also GH #36's own explicit ask: confirm the write itself isn't gated on
    Cancel, by checking a real file mtime rather than assuming it from
    reading the code. `Cancel run` is never clicked anywhere in this
    scenario, and the output's mtime is checked against a timestamp taken
    before `Run` -- so a passing assertion here rules out "the write only
    happens once Cancel is pressed" directly, on disk, not by inference.
    """
    import asyncio
    import time

    from pypdf import PdfReader, PdfWriter
    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    def write_pdf(path: Path, pages: int = 1) -> Path:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        with path.open("wb") as handle:
            writer.write(handle)
        return path

    a = write_pdf(tmp_path / "Linux Exp-5.pdf", 2)
    b = write_pdf(tmp_path / "Linux Exp-8.pdf", 3)
    out = tmp_path / "merged.pdf"

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            screen.query_one("#field-__output__", Input).value = str(out)
            await pilot.pause()

            before_run = time.time()
            await pilot.click("#run")
            await pilot.pause(1.0)

            # No Cancel click anywhere in this scenario -- if the write were
            # really gated on Cancel, `out` would not exist at all here.
            assert out.is_file(), "the output must exist without Cancel ever being pressed"
            assert out.stat().st_mtime >= before_run - 1, (
                "the output's mtime must fall after Run was clicked, proving the write "
                "happened as part of this run and was not somehow already stale"
            )

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert "Wrote" in status
    assert out.is_file()
    assert len(PdfReader(str(out)).pages) == 5


def test_an_extensionless_typed_output_still_gets_a_real_pdf(tmp_path: Path) -> None:
    """The follow-up bug this fix addresses: typing `merged` with no
    extension wrote valid PDF bytes to a file named `merged`, and Windows
    opened it in Notepad because nothing said it was a PDF. The fix lives in
    `OutputTarget.resolve`, so it applies here with no TUI-specific code —
    `_request` still just reads the field text verbatim."""
    import asyncio

    from pypdf import PdfReader, PdfWriter
    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    def write_pdf(path: Path, pages: int = 1) -> Path:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        with path.open("wb") as handle:
            writer.write(handle)
        return path

    a = write_pdf(tmp_path / "a.pdf", 2)
    b = write_pdf(tmp_path / "b.pdf", 3)
    out = tmp_path / "merged.pdf"

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "merged")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert "Wrote" in status
    assert out.is_file()
    assert not (tmp_path / "merged").exists(), "no extensionless file should exist alongside it"
    assert len(PdfReader(str(out)).pages) == 5


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
            screen._run_in_progress = True

            await pilot.press("ctrl+c")
            await pilot.pause()

            assert token.is_cancelled

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# The "is it alive" indicator (issue #38)
#
# `ProgressBar(total=None)` on the run screen used to render nothing at all:
# `#progress { padding: 1 0 }` padded the *inside* of a widget whose own
# `DEFAULT_CSS` fixes `height: 1`, leaving zero rows for its content — so it
# occupied space in the layout but painted no bar, determinate or not. Fixed
# by using `margin` (outside the box) instead of `padding` (inside it).
#
# Separately, even a correctly-rendering indeterminate bar has no percentage
# and nothing a user can point to as motion between two glances at a static
# terminal. `_progress_start`'s own `total=None` — the sink's existing
# spelling of "indeterminate" — now drives a spinner glyph and a running
# elapsed-time count next to the status text, replacing the static "running"
# icon issue #26 added, with no tool name involved in the decision.
# ---------------------------------------------------------------------------


def test_the_progress_bar_renders_with_nonzero_height() -> None:
    """Regression for the CSS bug: `#progress` must leave room for its own
    content, not squeeze it to zero rows with inward padding."""
    import asyncio

    from textual.widgets import ProgressBar

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> int:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            bar = screen.query_one("#progress", ProgressBar)
            return bar.content_size.height

    assert asyncio.run(scenario()) > 0


def test_an_indeterminate_step_shows_a_spinner_and_elapsed_time() -> None:
    """The exact case the issue names: a cloud call's one indeterminate step
    (`progress.start(..., total=None)`) must make the status line say
    something unambiguous is alive — a spinner glyph plus a running elapsed
    count — next to the step's own description, not just an unmoving bar."""
    import asyncio
    import time

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen._run_in_progress = True
            screen._run_started_at = time.monotonic()
            screen._progress_start("Running ocr on https://api.example.com", None)
            await pilot.pause()

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert "Running ocr on https://api.example.com" in status
    assert "(0s)" in status
    assert any(glyph in status for glyph in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def test_a_determinate_step_shows_no_animated_spinner_or_elapsed_time() -> None:
    """A step with a known total already moves its own bar — the animated
    spinner and elapsed count exist for the case a bar cannot show motion
    toward anything, not as a permanent decoration on every running status."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen._run_in_progress = True
            screen._progress_start("Rendering 3 of 5 page(s)", 5)
            await pilot.pause()

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert "Rendering 3 of 5 page(s)" in status
    assert not any(glyph in status for glyph in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    assert not any(f"({n}s)" in status for n in range(5))


def test_the_spinner_timer_stops_when_the_run_finishes() -> None:
    """`_finished` must stop the interval driving the spinner, or a screen
    left mounted after a run completes keeps re-rendering `#status` forever
    against an elapsed time that no longer means anything."""
    import asyncio
    import time

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> tuple[bool, bool]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen._run_in_progress = True
            screen._run_started_at = time.monotonic()
            screen._start_spinner()
            await pilot.pause()
            running = screen._spinner_timer is not None

            screen._finished()
            await pilot.pause()
            return running, screen._spinner_timer is None

    was_running, stopped_after_finish = asyncio.run(scenario())
    assert was_running
    assert stopped_after_finish


def test_the_final_status_carries_no_leftover_spinner_or_elapsed_suffix() -> None:
    """`_finished` runs before `_succeeded`/`_show` set the last word on a
    run, so a completed message must be exactly what they gave it — not
    prefixed with a stale spinner frame or trailing elapsed count from the
    step that just ended."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen._run_in_progress = True
            screen._progress_start("Running ocr on https://api.example.com", None)
            await pilot.pause()

            screen._finished()
            screen._set_status("Wrote out.pdf  ·  local engine", state="success")
            await pilot.pause()

            return str(screen.query_one("#status", Static).content)

    assert asyncio.run(scenario()) == "✔  Wrote out.pdf  ·  local engine"


# ---------------------------------------------------------------------------
# Browsing for input files — pure helpers
# ---------------------------------------------------------------------------


def test_render_paths_joins_with_comma_and_space(tmp_path: Path) -> None:
    from docmax.tui.browser import render_paths

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    assert render_paths([a]) == str(a)
    assert render_paths([a, b]) == f"{a}, {b}"


def test_merge_paths_appends_to_an_empty_field(tmp_path: Path) -> None:
    from docmax.tui.browser import merge_paths

    a = tmp_path / "a.pdf"
    assert merge_paths("", [a]) == str(a)


def test_merge_paths_adds_new_paths_after_the_existing_ones(tmp_path: Path) -> None:
    """Requirement 5: a second trip to the dialog must add to the field, not
    replace it."""
    from docmax.tui.browser import merge_paths

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    assert merge_paths(str(a), [b]) == f"{a}, {b}"


def test_merge_paths_does_not_duplicate_an_already_present_path(tmp_path: Path) -> None:
    from docmax.tui.browser import merge_paths

    a = tmp_path / "a.pdf"
    assert merge_paths(str(a), [a]) == str(a)


def test_merge_paths_leaves_an_existing_path_in_its_original_position(tmp_path: Path) -> None:
    """Ordering is preserved: re-choosing `a` must not move it to the end."""
    from docmax.tui.browser import merge_paths

    a, b, c = tmp_path / "a.pdf", tmp_path / "b.pdf", tmp_path / "c.pdf"
    assert merge_paths(f"{a}, {b}", [a, c]) == f"{a}, {b}, {c}"


def test_default_start_is_the_users_home_directory() -> None:
    """The dialog's default `initialdir` is the platform's home, not the
    process's cwd — a TUI started from a project checkout must not open the
    dialog inside that checkout."""
    from docmax.tui.browser import default_start

    assert default_start() == Path.home()


def test_describe_missing_paths_is_empty_for_a_real_file(tmp_path: Path) -> None:
    real = _touch(tmp_path / "in.pdf")
    assert forms.describe_missing_paths(str(real)) == ""


def test_describe_missing_paths_flags_a_nonexistent_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.pdf"
    assert "does not exist" in forms.describe_missing_paths(str(missing))


def test_describe_missing_paths_flags_a_directory(tmp_path: Path) -> None:
    assert "directory" in forms.describe_missing_paths(str(tmp_path))


def test_describe_missing_paths_checks_every_comma_separated_part(tmp_path: Path) -> None:
    real = _touch(tmp_path / "in.pdf")
    missing = tmp_path / "nope.pdf"
    problem = forms.describe_missing_paths(f"{real}, {missing}")
    assert str(real) not in problem
    assert "does not exist" in problem


# ---------------------------------------------------------------------------
# pick_files / _native_dialog — no display required
#
# `_native_dialog` is the one seam this module has for testing without an OS
# window: nothing that opens a real dialog can be driven by a simulated
# keypress the way a Textual widget can, since it is a window the OS owns and
# not one Textual draws — the same reason `pickers/`'s own tests replace a
# real browser with an `announce` callback instead of one.
# ---------------------------------------------------------------------------


def test_pick_files_returns_none_for_a_cancelled_single_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tk's own convention: a cancelled single-file dialog returns `""`."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "_native_dialog", lambda **_: "")
    assert browser_module.pick_files(multiple=False, start=tmp_path) is None


def test_pick_files_returns_none_for_a_cancelled_multi_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tk's own convention: a cancelled multi-file dialog returns `()`."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "_native_dialog", lambda **_: ())
    assert browser_module.pick_files(multiple=True, start=tmp_path) is None


def test_pick_files_wraps_a_single_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import docmax.tui.browser as browser_module

    a = tmp_path / "a.pdf"
    monkeypatch.setattr(browser_module, "_native_dialog", lambda **_: str(a))
    assert browser_module.pick_files(multiple=False, start=tmp_path) == [a]


def test_pick_files_wraps_a_multi_result_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmax.tui.browser as browser_module

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    monkeypatch.setattr(browser_module, "_native_dialog", lambda **_: (str(a), str(b)))
    assert browser_module.pick_files(multiple=True, start=tmp_path) == [a, b]


def test_pick_files_defaults_to_the_users_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `start=` override and nothing remembered reaches the dialog as the
    platform's home — the fallback issue #29 must preserve on a fresh
    install or a cleared state file."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "remembered_start", lambda: None)
    seen: list[Path] = []

    def fake(*, multiple: bool, start: Path) -> str:
        seen.append(start)
        return ""

    monkeypatch.setattr(browser_module, "_native_dialog", fake)
    browser_module.pick_files(multiple=False)

    assert seen == [Path.home()]


def test_pick_files_opens_at_the_remembered_directory_when_no_start_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue #29: with no explicit `start=`, the dialog opens where a file was
    last chosen from or saved to, rather than always at home."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "remembered_start", lambda: tmp_path)
    seen: list[Path] = []

    def fake(*, multiple: bool, start: Path) -> str:
        seen.append(start)
        return ""

    monkeypatch.setattr(browser_module, "_native_dialog", fake)
    browser_module.pick_files(multiple=False)

    assert seen == [tmp_path]


def test_an_explicit_start_beats_the_remembered_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller-supplied `start=` still wins over whatever was last
    remembered — `RunScreen` relies on this to anchor the save dialog to the
    first input's own folder ahead of anything remembered globally."""
    import docmax.tui.browser as browser_module

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    remembered = tmp_path / "remembered"
    remembered.mkdir()
    monkeypatch.setattr(browser_module, "remembered_start", lambda: remembered)
    seen: list[Path] = []

    def fake(*, multiple: bool, start: Path) -> str:
        seen.append(start)
        return ""

    monkeypatch.setattr(browser_module, "_native_dialog", fake)
    browser_module.pick_files(multiple=False, start=explicit)

    assert seen == [explicit]


def test_native_dialog_raises_a_typed_error_when_there_is_no_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tk can fail this way on a headless machine; it must never be a
    traceback, the same discipline `docmax.tui.require_available` applies to a
    missing `textual`."""
    import tkinter

    from docmax.core.errors import LocalDependencyMissingError
    from docmax.tui.browser import _native_dialog

    def boom(*args: object, **kwargs: object) -> None:
        raise tkinter.TclError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(tkinter, "Tk", boom)

    with pytest.raises(LocalDependencyMissingError):
        _native_dialog(multiple=False, start=tmp_path)


# ---------------------------------------------------------------------------
# remembered_start / remember_directory — the app-local "last folder" state
# behind issue #29, at the seam `pick_files`/`pick_save_path` actually use.
# `tests/unit/test_ui_state.py` covers the on-disk record itself in detail;
# these confirm `tui/browser.py` reaches it correctly, isolated from whatever
# real state happens to exist on the machine running the suite.
# ---------------------------------------------------------------------------


def _isolate_ui_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `core.config.ui_state_file()` at an isolated location.

    Without this, `remember_directory`/`remembered_start` would read and
    write the real developer machine's config directory during a test run —
    the same reasoning `test_config.py::test_locating_the_config_creates_nothing`
    applies to `config_dir()` itself, by patching `platformdirs` rather than
    an environment variable so it holds on Windows and macOS too.
    """
    monkeypatch.setattr(
        "docmax.core.config.platformdirs.user_config_dir", lambda *a, **k: str(tmp_path / "config")
    )


def test_remembered_start_is_none_with_nothing_remembered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_ui_state(monkeypatch, tmp_path)
    from docmax.tui.browser import remembered_start

    assert remembered_start() is None


def test_remember_directory_round_trips_through_remembered_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_ui_state(monkeypatch, tmp_path)
    from docmax.tui.browser import remember_directory, remembered_start

    folder = tmp_path / "documents"
    folder.mkdir()

    remember_directory(folder)

    assert remembered_start() == folder


def test_remember_directory_is_best_effort_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Remembering the folder is a convenience picked up automatically, not
    the reason the user opened the dialog — a failure to persist it (a full
    disk, an unwritable config directory) must not turn a successful file
    choice into a reported error."""
    import docmax.core.ui_state as ui_state_module
    from docmax.core.errors import OutputNotWritableError
    from docmax.tui.browser import remember_directory

    def boom(path: Path, directory: Path) -> None:
        raise OutputNotWritableError(f"Could not write to {path}", context={"path": str(path)})

    monkeypatch.setattr(ui_state_module, "save_last_directory", boom)

    remember_directory(tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# The run screen's wiring
# ---------------------------------------------------------------------------


def _mock_dialog(
    monkeypatch: pytest.MonkeyPatch, *results: str | tuple[str, ...]
) -> list[dict[str, object]]:
    """Replace ``docmax.tui.browser._native_dialog`` with a fake.

    Returns one of ``results`` per call, in order, repeating the last one if
    called more times than there are results (or ``""`` if none were given —
    a bare cancel). Records the keyword arguments of every call. A single
    ``tuple`` result stands for one multi-select dialog returning several
    files at once; several separate ``str`` results stand for separate trips
    to the dialog, each choosing one file.
    """
    import docmax.tui.browser as browser_module

    calls: list[dict[str, object]] = []
    remaining = list(results)

    def fake(*, multiple: bool, start: Path) -> str | tuple[str, ...]:
        calls.append({"multiple": multiple, "start": start})
        if remaining:
            return remaining.pop(0)
        return results[-1] if results else ""

    monkeypatch.setattr(browser_module, "_native_dialog", fake)
    return calls


async def _click_browse(pilot: Any) -> None:
    """Click Browse and give the worker thread time to call back.

    The dialog runs on a real OS thread (see ``RunScreen._browse``), so
    ``pilot.pause()``'s "wait for cpu idle" is not enough to observe its
    result — the callback arrives from a different thread on its own
    schedule. A real-time pause is, matching the ``0.5`` used everywhere
    else in this file for the same "real worker thread callback" wait (see
    ``test_clicking_run_actually_executes_the_worker``); ``0.2`` was too
    short on Windows CI's slower thread scheduling and flaked there.
    """
    await pilot.click("#browse-inputs")
    await pilot.pause(0.5)


def test_the_run_screen_has_a_browse_button() -> None:
    """The input control has a browse/select mechanism."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            assert len(screen.query("#browse-inputs")) == 1

    asyncio.run(scenario())


def test_browsing_invokes_the_native_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Requirement 1: Browse invokes the picker."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    calls = _mock_dialog(monkeypatch, str(tmp_path / "a.pdf"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)

    asyncio.run(scenario())
    assert len(calls) == 1


def test_browsing_asks_for_one_file_or_several_from_the_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shaped by `ToolSpec.accepts_multiple_inputs`, not a tool name.

    `crop` takes one input; `merge` takes several. Neither name may appear
    inside `docmax.tui` itself — `test_the_tui_names_no_tool_except_the_unimplemented_one`
    guards that — but a test is free to name them to prove the *wiring* reads
    the spec instead of hardcoding either shape.
    """
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    calls = _mock_dialog(monkeypatch, str(tmp_path / "a.pdf"))

    async def multiple_for(tool: str) -> bool:
        calls.clear()
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen(tool)
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)
        return bool(calls[0]["multiple"])

    assert asyncio.run(multiple_for("crop")) is False
    assert asyncio.run(multiple_for("merge")) is True


def test_selecting_one_file_populates_the_input_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 2."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    chosen = _touch(tmp_path / "only.pdf")
    _mock_dialog(monkeypatch, str(chosen))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)
            return screen.query_one("#field-__inputs__", Input).value

    assert asyncio.run(scenario()) == str(chosen)


def test_selecting_several_files_populates_the_multi_input_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 3: one multi-select dialog choosing two files populates a
    multi-input tool's field with both, comma-separated."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    first = _touch(tmp_path / "a.pdf")
    second = _touch(tmp_path / "b.pdf")
    _mock_dialog(monkeypatch, (str(first), str(second)))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)
            return screen.query_one("#field-__inputs__", Input).value

    assert asyncio.run(scenario()) == f"{first}, {second}"


def test_choosing_a_file_remembers_its_folder_for_the_next_browse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #29: a file chosen from one folder is offered as the *next*
    dialog's starting point, with no `start=` passed by the caller — proven
    end to end through `RunScreen._browse`, not just at `pick_files` itself."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    _isolate_ui_state(monkeypatch, tmp_path)
    folder = tmp_path / "documents"
    folder.mkdir()
    chosen = _touch(folder / "a.pdf")
    calls = _mock_dialog(monkeypatch, str(chosen))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)  # nothing remembered yet
            await _click_browse(pilot)  # should now open in `folder`

    asyncio.run(scenario())

    assert calls[0]["start"] == Path.home(), "nothing was remembered before the first pick"
    assert calls[1]["start"] == folder, "the folder just chosen from is offered next"


def test_cancelling_the_dialog_leaves_the_form_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 4, for a single-input tool (Tk's cancel convention: `""`)."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    _mock_dialog(monkeypatch, "")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            field.value = "untouched.pdf"
            await pilot.pause()

            await _click_browse(pilot)

            return field.value

    assert asyncio.run(scenario()) == "untouched.pdf"


def test_cancelling_a_multi_input_dialog_leaves_the_form_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 4, for a multi-input tool (Tk's cancel convention: `()`,
    not `""`)."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    _mock_dialog(monkeypatch, ())

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            field.value = "untouched.pdf"
            await pilot.pause()

            await _click_browse(pilot)

            return field.value

    assert asyncio.run(scenario()) == "untouched.pdf"


def test_browsing_twice_for_a_multi_input_tool_accumulates_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 5.

    Browse, select one document; Browse again, select a second. Both must end
    up in the field — a second trip must add, not replace.
    """
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    first = _touch(tmp_path / "a.pdf")
    second = _touch(tmp_path / "b.pdf")
    _mock_dialog(monkeypatch, str(first), str(second))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            await _click_browse(pilot)
            assert screen.query_one("#field-__inputs__", Input).value == str(first)

            await _click_browse(pilot)
            return screen.query_one("#field-__inputs__", Input).value

    assert asyncio.run(scenario()) == f"{first}, {second}"


def test_reselecting_an_already_added_file_does_not_duplicate_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    first = _touch(tmp_path / "a.pdf")
    _mock_dialog(monkeypatch, str(first), str(first))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            await _click_browse(pilot)
            await _click_browse(pilot)

            return screen.query_one("#field-__inputs__", Input).value

    assert asyncio.run(scenario()) == str(first)


def test_single_input_tools_still_replace_on_a_second_browse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-input tools keep replacing, not accumulating."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    _touch(tmp_path / "a.pdf")
    second = _touch(tmp_path / "b.pdf")
    _mock_dialog(monkeypatch, str(tmp_path / "a.pdf"), str(second))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            await _click_browse(pilot)
            await _click_browse(pilot)

            return screen.query_one("#field-__inputs__", Input).value

    assert asyncio.run(scenario()) == str(second)


def test_browsing_writes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 7, checked the same way ADR 0005's pickers are: watch the disk."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    chosen = _touch(tmp_path / "only.pdf")
    _mock_dialog(monkeypatch, str(chosen))
    before = sorted(tmp_path.rglob("*"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse(pilot)

    asyncio.run(scenario())
    assert sorted(tmp_path.rglob("*")) == before


def test_a_typed_error_from_the_dialog_is_shown_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tk missing or no display must reach the user as the usual error modal,
    and Browse must recover rather than staying disabled behind a dead worker."""
    import asyncio

    from textual.widgets import Button

    from docmax.core.errors import LocalDependencyMissingError
    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    def boom(*, multiple: bool, start: Path) -> str:
        raise LocalDependencyMissingError(
            "The file browser needs a display, and none is available.",
            dependency="tkinter",
        )

    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "_native_dialog", boom)

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            await _click_browse(pilot)
            assert isinstance(app.screen, ErrorScreen)

            app.pop_screen()
            await pilot.pause()
            assert screen.query_one("#browse-inputs", Button).disabled is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Browsing for an output path
#
# The output field's own Browse button, wired to `tkinter.filedialog
# .asksaveasfilename` — the save-dialog counterpart of the input Browse
# button's open dialog. Generic across every tool: `RunScreen` never asks
# whether it is looking at `merge` or anything else, only whether the output
# field already has a path in it (to replace) and whether the input field
# names a folder worth starting the dialog in.
# ---------------------------------------------------------------------------


def _mock_save_dialog(monkeypatch: pytest.MonkeyPatch, result: str) -> list[Path]:
    """Replace ``docmax.tui.browser._native_save_dialog`` with a fake.

    Always returns ``result`` and records the ``start`` directory of every
    call — the save-dialog analogue of ``_mock_dialog``, and mocked at the
    same seam for the same reason: a native save dialog is a window the OS
    owns, not something Pilot can click into.
    """
    import docmax.tui.browser as browser_module

    calls: list[Path] = []

    def fake(*, start: Path) -> str:
        calls.append(start)
        return result

    monkeypatch.setattr(browser_module, "_native_save_dialog", fake)
    return calls


async def _click_browse_output(pilot: Any) -> None:
    """Click the output Browse button and give the worker thread time to
    call back — see ``_click_browse``'s docstring for why a real pause is
    needed rather than ``pilot.pause()``'s "wait for cpu idle"."""
    await pilot.click("#browse-output")
    await pilot.pause(0.5)


def test_the_run_screen_has_a_browse_output_button() -> None:
    """Requirement 1."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            assert len(screen.query("#browse-output")) == 1

    asyncio.run(scenario())


def test_browsing_output_invokes_the_native_save_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 2."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    calls = _mock_save_dialog(monkeypatch, str(tmp_path / "out.pdf"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse_output(pilot)

    asyncio.run(scenario())
    assert len(calls) == 1


def test_selecting_an_output_path_populates_the_output_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 3."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    chosen = tmp_path / "out.pdf"
    _mock_save_dialog(monkeypatch, str(chosen))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse_output(pilot)
            return screen.query_one("#field-__output__", Input).value

    assert asyncio.run(scenario()) == str(chosen)


def test_a_browsed_output_folder_is_remembered_for_the_next_save_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #29, the save-dialog half: a folder saved to once is offered as
    the *next* save dialog's starting point — proven end to end through
    `RunScreen._browse_output`. The inputs field stays empty throughout, so
    `first_input_directory` cannot be what is supplying the folder."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    _isolate_ui_state(monkeypatch, tmp_path)
    folder = tmp_path / "results"
    folder.mkdir()
    calls = _mock_save_dialog(monkeypatch, str(folder / "out.pdf"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse_output(pilot)  # nothing remembered yet
            await _click_browse_output(pilot)  # should now open in `folder`

    asyncio.run(scenario())

    assert calls[0] == Path.home(), "nothing was remembered before the first pick"
    assert calls[1] == folder, "the folder just saved to is offered next"


def test_the_save_dialog_opens_in_the_first_inputs_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worked example from the request: Downloads, because that is where
    the selected inputs already are — never a destination chosen on the
    user's behalf, only where the dialog starts browsing."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    inputs_dir = tmp_path / "Downloads"
    inputs_dir.mkdir()
    a = _touch(inputs_dir / "Linux Exp-5.pdf")
    b = _touch(inputs_dir / "Linux Exp-8.pdf")
    calls = _mock_save_dialog(monkeypatch, str(inputs_dir / "merged.pdf"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            await pilot.pause()
            await _click_browse_output(pilot)

    asyncio.run(scenario())
    assert calls == [inputs_dir]


def test_cancelling_the_save_dialog_leaves_output_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 4. Tk's cancel convention for a save dialog: ``""``."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    _mock_save_dialog(monkeypatch, "")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            field = screen.query_one("#field-__output__", Input)
            field.value = "untouched.pdf"
            await pilot.pause()

            await _click_browse_output(pilot)

            return field.value

    assert asyncio.run(scenario()) == "untouched.pdf"


def test_browsing_output_writes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 5, checked the same way ADR 0005's pickers are: watch the disk."""
    import asyncio

    from docmax.tui.app import DocMaxApp, RunScreen

    _mock_save_dialog(monkeypatch, str(tmp_path / "out.pdf"))
    before = sorted(tmp_path.rglob("*"))

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            await _click_browse_output(pilot)

    asyncio.run(scenario())
    assert sorted(tmp_path.rglob("*")) == before
    assert not (tmp_path / "out.pdf").exists(), "the save dialog must not create the file"


def test_pick_save_path_returns_none_for_a_cancelled_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "_native_save_dialog", lambda **_: "")
    assert browser_module.pick_save_path(start=tmp_path) is None


def test_pick_save_path_wraps_the_chosen_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmax.tui.browser as browser_module

    chosen = tmp_path / "result.pdf"
    monkeypatch.setattr(browser_module, "_native_save_dialog", lambda **_: str(chosen))
    assert browser_module.pick_save_path(start=tmp_path) == chosen


def test_pick_save_path_defaults_to_the_users_home_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `start=` override and nothing remembered reaches the dialog as the
    platform's home — the fallback issue #29 must preserve on a fresh
    install or a cleared state file."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "remembered_start", lambda: None)
    seen: list[Path] = []

    def fake(*, start: Path) -> str:
        seen.append(start)
        return ""

    monkeypatch.setattr(browser_module, "_native_save_dialog", fake)
    browser_module.pick_save_path()

    assert seen == [Path.home()]


def test_pick_save_path_opens_at_the_remembered_directory_when_no_start_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue #29: with no explicit `start=`, the save dialog opens where a
    file was last chosen from or saved to, rather than always at home."""
    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "remembered_start", lambda: tmp_path)
    seen: list[Path] = []

    def fake(*, start: Path) -> str:
        seen.append(start)
        return ""

    monkeypatch.setattr(browser_module, "_native_save_dialog", fake)
    browser_module.pick_save_path()

    assert seen == [tmp_path]


def test_first_input_directory_is_none_for_an_empty_field() -> None:
    assert forms.first_input_directory("") is None


def test_first_input_directory_reads_the_first_comma_separated_path(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    assert forms.first_input_directory(f"{a}, {b}") == tmp_path


def test_multi_input_merge_can_use_the_browsed_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    a = _touch(tmp_path / "a.pdf")
    b = _touch(tmp_path / "b.pdf")
    out = tmp_path / "merged.pdf"
    _mock_save_dialog(monkeypatch, str(out))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            await pilot.pause()
            await _click_browse_output(pilot)

            return screen.query_one("#field-__output__", Input).value

    assert asyncio.run(scenario()) == str(out)


def test_single_input_tools_can_also_browse_for_an_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 9: the output picker is generic, not merge-specific."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _touch(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    _mock_save_dialog(monkeypatch, str(out))

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()
            await _click_browse_output(pilot)

            return screen.query_one("#field-__output__", Input).value

    assert asyncio.run(scenario()) == str(out)


def test_a_typed_error_from_the_save_dialog_is_shown_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The save dialog's own version of the earlier open-dialog test: Tk
    missing or no display must reach the user as the usual error modal, and
    the button must recover rather than staying disabled behind a dead worker."""
    import asyncio

    from textual.widgets import Button

    from docmax.core.errors import LocalDependencyMissingError
    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    def boom(*, start: Path) -> str:
        raise LocalDependencyMissingError(
            "The file browser needs a display, and none is available.",
            dependency="tkinter",
        )

    import docmax.tui.browser as browser_module

    monkeypatch.setattr(browser_module, "_native_save_dialog", boom)

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            await _click_browse_output(pilot)
            assert isinstance(app.screen, ErrorScreen)

            app.pop_screen()
            await pilot.pause()
            assert screen.query_one("#browse-output", Button).disabled is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# A browsed output path still goes through every existing safety check
# ---------------------------------------------------------------------------


def test_a_browsed_output_that_collides_with_an_input_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 7 (collision half). The save dialog can return any path it
    likes, including one of the inputs — the picker does not know what an
    input is, and must not. `OutputTarget.resolve` is what actually refuses
    it, exactly as it would for a path typed by hand."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    a = _touch(tmp_path / "a.pdf")
    b = _touch(tmp_path / "b.pdf")
    _mock_save_dialog(monkeypatch, str(a))  # the dialog "chose" an input

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("merge")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            await pilot.pause()
            await _click_browse_output(pilot)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            assert isinstance(app.screen, ErrorScreen)
            assert app.screen.error.code.value == "output.in_place_overwrite"

    asyncio.run(scenario())


def test_a_browsed_output_that_already_exists_needs_force(tmp_path: Path) -> None:
    """Requirement 6 and 7 (existing-output half), via a real click and the
    real registry/router — no mocked dialog needed, since this exercises what
    happens *after* a path reaches the field, whichever way it got there."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    source = _touch(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(out)
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            assert isinstance(app.screen, ErrorScreen)
            assert app.screen.error.code.value == "output.exists"

    asyncio.run(scenario())


def test_force_permits_overwriting_a_browsed_output_path(tmp_path: Path) -> None:
    """The other half: `force` still works for a path that reached the field
    via Browse — the button is a plain `Input` either way, and `_request`
    reads it the same way regardless of how it was filled in."""
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _write_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(out)
            _set_box(screen, "10,10,100,100")
            await pilot.pause()
            await pilot.click("#force")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            return str(screen.query_one("#status", Static).content)

    status = asyncio.run(scenario())
    assert "Wrote" in status


def test_the_input_hint_flags_a_nonexistent_typed_path() -> None:
    """Requirement 5, for a path typed by hand rather than browsed to."""
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = "/no/such/file.pdf"
            await pilot.pause()

            return _text_of(screen.query_one("#input-hint", Static))

    assert "does not exist" in asyncio.run(scenario())


def test_the_input_hint_is_empty_for_a_typed_path_that_exists(tmp_path: Path) -> None:
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    real = _touch(tmp_path / "in.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(real)
            await pilot.pause()

            return _text_of(screen.query_one("#input-hint", Static))

    assert asyncio.run(scenario()) == ""


def test_a_nonexistent_typed_path_turns_the_input_field_red() -> None:
    """Issue #25: a message below the field is easy to miss — the field
    itself (and its hint) must carry the same signal, via the ``invalid`` CSS
    class rather than a bespoke widget."""
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> tuple[bool, bool]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = "/no/such/file.pdf"
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            hint = screen.query_one("#input-hint", Static)
            return field.has_class("invalid"), hint.has_class("invalid")

    field_invalid, hint_invalid = asyncio.run(scenario())
    assert field_invalid is True
    assert hint_invalid is True


def test_a_typed_path_that_exists_does_not_mark_the_field_invalid(tmp_path: Path) -> None:
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    real = _touch(tmp_path / "in.pdf")

    async def scenario() -> tuple[bool, bool]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(real)
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            hint = screen.query_one("#input-hint", Static)
            return field.has_class("invalid"), hint.has_class("invalid")

    field_invalid, hint_invalid = asyncio.run(scenario())
    assert field_invalid is False
    assert hint_invalid is False


def test_fixing_a_bad_path_clears_the_invalid_state(tmp_path: Path) -> None:
    """The class must come back off, not just never go on — typing a bad path
    and then correcting it must clear both the field and hint's red state."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    real = _touch(tmp_path / "in.pdf")

    async def scenario() -> bool:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            field.value = "/no/such/file.pdf"
            await pilot.pause()
            assert field.has_class("invalid")

            field.value = str(real)
            await pilot.pause()
            return field.has_class("invalid")

    assert asyncio.run(scenario()) is False


def test_the_input_field_starts_without_the_invalid_class() -> None:
    """An empty, untouched field is not yet wrong — it just has nothing in it
    — so it must not open in the red state."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> bool:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()
            return screen.query_one("#field-__inputs__", Input).has_class("invalid")

    assert asyncio.run(scenario()) is False


def test_a_browsed_valid_path_does_not_leave_the_field_marked_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browse fills the field with a real, existing path — the invalid class
    must reflect that, not linger from whatever the field held before."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    chosen = _touch(tmp_path / "only.pdf")
    _mock_dialog(monkeypatch, str(chosen))

    async def scenario() -> bool:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            field = screen.query_one("#field-__inputs__", Input)
            field.value = "/no/such/file.pdf"
            await pilot.pause()
            assert field.has_class("invalid")

            await _click_browse(pilot)
            return field.has_class("invalid")

    assert asyncio.run(scenario()) is False


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


# ---------------------------------------------------------------------------
# The details panel (issue #28)
#
# `get-info` is read-only: `outputs` is always empty and the answer travels in
# `ToolResult.details`. Before this fix, `RunScreen._succeeded` rendered only
# `result.outputs`, so a successful `get-info` run reduced to "Wrote nothing"
# with the page count, size and encryption flag silently dropped. The fix is
# `format_details`, a pure function generic over whatever `details` holds, and
# a `#details` panel that renders it — no branch anywhere names `get-info`.
# ---------------------------------------------------------------------------


def test_format_details_renders_flat_key_value_pairs() -> None:
    from docmax.tui.app import format_details

    rendered = format_details({"pages": 3, "size_bytes": 1024})
    assert rendered == "pages: 3\nsize bytes: 1024"


def test_format_details_formats_booleans_as_yes_no() -> None:
    from docmax.tui.app import format_details

    assert format_details({"encrypted": True}) == "encrypted: yes"
    assert format_details({"encrypted": False}) == "encrypted: no"


def test_format_details_formats_none_as_an_em_dash() -> None:
    from docmax.tui.app import format_details

    assert format_details({"pages": None}) == "pages: —"


def test_format_details_indents_a_nested_mapping() -> None:
    """`get-info`'s own `metadata` value is exactly this shape: a dict inside
    `details`. Nothing here knows that key's name — any nested mapping is
    indented the same way."""
    from docmax.tui.app import format_details

    rendered = format_details({"metadata": {"Title": "Report", "Author": "A"}})
    assert rendered == "metadata:\n  Title: Report\n  Author: A"


def test_format_details_shows_an_em_dash_for_an_empty_nested_mapping() -> None:
    from docmax.tui.app import format_details

    assert format_details({"metadata": {}}) == "metadata: —"


def test_format_details_omits_the_keys_the_dry_run_status_line_already_shows() -> None:
    """`_succeeded`'s dry-run branch already renders `reason` and
    `destination` into the status line; repeating them in the details panel
    underneath would just be noise."""
    from docmax.tui.app import format_details

    rendered = format_details(
        {"dry_run": True, "reason": "offline", "destination": "/tmp/out.pdf", "pages": 3}
    )
    assert rendered == "pages: 3"


def test_format_details_is_empty_for_empty_details() -> None:
    from docmax.tui.app import format_details

    assert format_details({}) == ""


def test_the_details_panel_exists_and_starts_empty() -> None:
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()
            return _text_of(screen.query_one("#details", Static))

    assert asyncio.run(scenario()) == ""


def test_a_run_whose_result_carries_details_shows_them_generically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself, against a fake router so it is exercised without
    a real document: a tool that writes nothing but reports `details` must not
    be reduced to "Wrote nothing" with everything else dropped."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, RunScreen

    result = ToolResult(
        outputs=(),
        engine_used=Engine.LOCAL,
        details={"pages": 3, "size_bytes": 2048, "encrypted": False},
    )
    router = FakeRouter(result=result)
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> tuple[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()

            from textual.widgets import Input

            # `get-info` declares `produces_output=False` (ADR 0036), so its
            # generated form has no output field at all — nothing to fill in
            # here beyond the input.
            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            status = str(screen.query_one("#status", Static).content)
            details = _text_of(screen.query_one("#details", Static))
            return status, details

    status, details = asyncio.run(scenario())
    assert "Wrote nothing" in status
    assert "pages: 3" in details
    assert "size bytes: 2048" in details
    assert "encrypted: no" in details


def test_details_are_shown_alongside_a_written_file_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel is not exclusive to read-only tools: a tool that both writes
    a file and reports extra `details` gets both shown, not one or the other."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, RunScreen

    written = tmp_path / "out.pdf"
    result = ToolResult(outputs=(written,), engine_used=Engine.LOCAL, details={"pages": 5})
    router = FakeRouter(result=result)
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> tuple[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("crop")
            app.push_screen(screen)
            await pilot.pause()

            from textual.widgets import Input

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(written)
            _set_box(screen, "10,10,100,100")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            status = str(screen.query_one("#status", Static).content)
            details = _text_of(screen.query_one("#details", Static))
            return status, details

    status, details = asyncio.run(scenario())
    assert "Wrote" in status
    assert "nothing" not in status
    assert "pages: 5" in details


def test_a_dry_run_does_not_show_the_details_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dry-run status line already carries `reason` and `destination`;
    the panel underneath must stay empty rather than repeating them."""
    import asyncio

    from textual.widgets import Static

    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, RunScreen

    result = ToolResult(
        outputs=(),
        engine_used=Engine.LOCAL,
        details={"dry_run": True, "reason": "offline", "destination": str(tmp_path / "out.pdf")},
    )
    router = FakeRouter(result=result)
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()

            from textual.widgets import Input

            # `get-info` declares `produces_output=False` (ADR 0036): no
            # output field on this form to fill in.
            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            await pilot.click("#dry-run")
            await pilot.pause(0.5)

            return _text_of(screen.query_one("#details", Static))

    assert asyncio.run(scenario()) == ""


def test_the_details_panel_is_cleared_on_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale panel from a previous success must not linger under a new
    run's error modal."""
    import asyncio

    from textual.widgets import Static

    from docmax.core.errors import InPlaceOverwriteError
    from docmax.tui import runner as runner_module
    from docmax.tui.app import DocMaxApp, ErrorScreen, RunScreen

    router = FakeRouter(raises=InPlaceOverwriteError("boom", context={"path": "x"}))
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()
            screen._set_details("stale details from a previous run")

            from textual.widgets import Input

            # `get-info` declares `produces_output=False` (ADR 0036): no
            # output field on this form to fill in.
            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            assert isinstance(app.screen, ErrorScreen)
            return _text_of(screen.query_one("#details", Static))

    assert asyncio.run(scenario()) == ""


def test_get_info_run_through_the_real_tui_shows_the_answer_it_found(tmp_path: Path) -> None:
    """The exact scenario issue #28 reported, resolved end-to-end: the real
    registry, the real router, the real `get-info` local strategy, Run
    clicked for real — no fakes. `get-info` writes nothing, so before this
    fix the status line read "Wrote nothing" and the page count, size and
    encryption flag were never shown anywhere. This is the claim the fix
    rests on: they are visible after a real run."""
    import asyncio

    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _write_pdf(tmp_path / "report.pdf", pages=3)

    async def scenario() -> tuple[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()

            # `get-info` declares `produces_output=False` (ADR 0036): its
            # generated form has no output field at all, so there is nothing
            # to fill in beyond the input.
            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            status = str(screen.query_one("#status", Static).content)
            details = _text_of(screen.query_one("#details", Static))
            return status, details

    status, details = asyncio.run(scenario())
    assert "Wrote nothing" in status
    assert "pages: 3" in details
    assert "encrypted: no" in details
    assert f"name: {source.name}" in details


# ---------------------------------------------------------------------------
# tui/status.py — the data behind System check and Cloud & account
#
# Pure functions, no textual import: proves each reads the exact source its
# CLI counterpart (`docmax doctor` / `docmax cloud status`) reads, rather than
# a second list or a re-implementation of either.
# ---------------------------------------------------------------------------


def test_binary_statuses_matches_the_declaration_doctor_reads() -> None:
    from docmax.tools import _binaries
    from docmax.tui import status

    statuses = status.binary_statuses()

    assert [s.name for s in statuses] == [b.name for b in _binaries.EXTERNAL_BINARIES]
    assert [s.used_by for s in statuses] == [b.used_by for b in _binaries.EXTERNAL_BINARIES]


def test_binary_statuses_found_reflects_binaries_find(monkeypatch: pytest.MonkeyPatch) -> None:
    """No second lookup: `found`/`path` come straight from `_binaries.find`."""
    from docmax.tools import _binaries
    from docmax.tui import status

    monkeypatch.setattr(_binaries, "find", lambda name: f"/usr/bin/{name}")

    statuses = status.binary_statuses()

    assert all(s.found for s in statuses)
    assert all(s.path == f"/usr/bin/{s.name}" for s in statuses)


def test_binary_statuses_reports_a_missing_binary_with_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docmax.tools import _binaries
    from docmax.tui import status

    monkeypatch.setattr(_binaries, "find", lambda name: None)

    statuses = status.binary_statuses()

    assert all(not s.found for s in statuses)
    assert all(s.path is None for s in statuses)
    assert all(s.install_hint for s in statuses)


def test_cloud_status_reports_no_key_and_no_consent_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import docmax.core.config as config_module
    from docmax.core.config import Config
    from docmax.tui import status

    monkeypatch.setattr(config_module, "load", lambda: Config(source=None))
    monkeypatch.setattr(config_module, "config_file", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config_module, "consent_file", lambda: tmp_path / "consent.json")

    info = status.cloud_status()

    assert info.api_key_configured is False
    assert info.api_key_suffix is None
    assert info.consented_tools == ()
    assert info.offline is False
    assert info.config_path == tmp_path / "config.toml"


def test_cloud_status_never_returns_the_key_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rule `cli/cloud.py` states outright: the key never appears in output."""
    import docmax.core.config as config_module
    from docmax.core.config import Config
    from docmax.tui import status

    monkeypatch.setattr(config_module, "load", lambda: Config(api_key="s3cret-token", source=None))
    monkeypatch.setattr(config_module, "config_file", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config_module, "consent_file", lambda: tmp_path / "consent.json")

    info = status.cloud_status()

    assert info.api_key_configured is True
    assert info.api_key_suffix == "oken"
    assert "s3cret-token" not in repr(info)


# ---------------------------------------------------------------------------
# tui/content.py — the help screen's static text
# ---------------------------------------------------------------------------


def test_help_sections_cover_the_concepts_the_issue_named() -> None:
    from docmax.tui.content import HELP_SECTIONS

    headings = {section.heading for section in HELP_SECTIONS}
    assert {"Local vs. cloud engine", "Consent", "System check"} <= headings
    assert all(section.body for section in HELP_SECTIONS)


# ---------------------------------------------------------------------------
# The nav menu (GitHub #39): help, system check, cloud & account
# ---------------------------------------------------------------------------


def test_the_help_bar_advertises_the_menu_keybinding() -> None:
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return _text_of(app.screen.query_one("#help", Static))

    assert "m Menu" in asyncio.run(scenario())


def test_pressing_m_opens_the_menu() -> None:
    """`m` is a plain (non-priority) screen binding, exactly like `q` — so
    typing "merge" into search is never hijacked. It opens the menu once
    focus is off the search box, which is where it normally is once someone
    has navigated to a tool."""
    import asyncio

    from docmax.tui.app import DocMaxApp, MenuScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.screen.query(".tool-button").first().focus()
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, MenuScreen)

    asyncio.run(scenario())


def test_the_menu_offers_exactly_help_system_check_and_cloud() -> None:
    """Items 1-3 only. Settings (item 4) is explicitly out of scope for this
    pass — the issue's own text scopes it separately."""
    import asyncio

    from textual.widgets import Button

    from docmax.tui.app import DocMaxApp, MenuScreen

    async def scenario() -> set[str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            return {button.id or "" for button in app.screen.query(Button)}

    ids = asyncio.run(scenario())
    assert ids == {"menu-help", "menu-system-check", "menu-cloud", "menu-close"}


def test_escape_closes_the_menu_without_navigating() -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, MenuScreen, ToolListScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ToolListScreen)

    asyncio.run(scenario())


def test_clicking_help_in_the_menu_opens_the_help_screen() -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, HelpScreen, MenuScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            await pilot.click("#menu-help")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)

    asyncio.run(scenario())


def test_clicking_system_check_in_the_menu_opens_the_system_check_screen() -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, MenuScreen, SystemCheckScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            await pilot.click("#menu-system-check")
            await pilot.pause()
            assert isinstance(app.screen, SystemCheckScreen)

    asyncio.run(scenario())


def test_clicking_cloud_in_the_menu_opens_the_cloud_status_screen() -> None:
    import asyncio

    from docmax.tui.app import CloudStatusScreen, DocMaxApp, MenuScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            await pilot.click("#menu-cloud")
            await pilot.pause()
            assert isinstance(app.screen, CloudStatusScreen)

    asyncio.run(scenario())


def test_the_menu_keybindings_open_screens_without_the_mouse() -> None:
    """`h`/`s`/`c` on the menu itself, not only the mouse."""
    import asyncio

    from docmax.tui.app import (
        CloudStatusScreen,
        DocMaxApp,
        HelpScreen,
        MenuScreen,
        SystemCheckScreen,
    )

    async def opened_by(key: str) -> type:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(MenuScreen())
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            return type(app.screen)

    assert asyncio.run(opened_by("h")) is HelpScreen
    assert asyncio.run(opened_by("s")) is SystemCheckScreen
    assert asyncio.run(opened_by("c")) is CloudStatusScreen


# -- Help screen --------------------------------------------------------


def test_the_help_screen_shows_every_section() -> None:
    import asyncio

    from textual.widgets import Static

    from docmax.tui.app import DocMaxApp, HelpScreen
    from docmax.tui.content import HELP_SECTIONS

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(HelpScreen())
            await pilot.pause()
            return " ".join(_text_of(widget) for widget in app.screen.query(Static))

    rendered = asyncio.run(scenario())
    for section in HELP_SECTIONS:
        assert section.heading in rendered
        assert section.body in rendered


def test_escape_on_the_help_screen_returns_to_the_tool_list() -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, HelpScreen, ToolListScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(HelpScreen())
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ToolListScreen)

    asyncio.run(scenario())


# -- System check screen -------------------------------------------------


def test_the_system_check_screen_lists_every_known_binary() -> None:
    import asyncio

    from textual.widgets import DataTable

    from docmax.tools import _binaries
    from docmax.tui.app import DocMaxApp, SystemCheckScreen

    async def scenario() -> int:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = SystemCheckScreen()
            app.push_screen(screen)
            await pilot.pause()
            return screen.query_one("#system-check-table", DataTable).row_count

    assert asyncio.run(scenario()) == len(_binaries.EXTERNAL_BINARIES)


def test_the_system_check_screen_shows_a_missing_binarys_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from textual.widgets import DataTable

    from docmax.tools import _binaries
    from docmax.tui.app import DocMaxApp, SystemCheckScreen

    monkeypatch.setattr(_binaries, "find", lambda name: None)

    async def scenario() -> list[list[Any]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = SystemCheckScreen()
            app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#system-check-table", DataTable)
            return [list(table.get_row_at(i)) for i in range(table.row_count)]

    rows = asyncio.run(scenario())
    assert all(row[1] == "missing" for row in rows)
    assert all(row[4] for row in rows), "every missing binary carries its install hint"


def test_escape_on_the_system_check_screen_returns_to_the_tool_list() -> None:
    import asyncio

    from docmax.tui.app import DocMaxApp, SystemCheckScreen, ToolListScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(SystemCheckScreen())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ToolListScreen)

    asyncio.run(scenario())


# -- Cloud & account screen -----------------------------------------------


def test_the_cloud_status_screen_shows_the_endpoint_and_is_labelled_not_a_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from textual.widgets import DataTable, Static

    import docmax.core.config as config_module
    from docmax.core.config import Config
    from docmax.tui.app import CloudStatusScreen, DocMaxApp

    monkeypatch.setattr(config_module, "load", lambda: Config(source=None))
    monkeypatch.setattr(config_module, "config_file", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config_module, "consent_file", lambda: tmp_path / "consent.json")

    async def scenario() -> tuple[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = CloudStatusScreen()
            app.push_screen(screen)
            await pilot.pause()
            labels = " ".join(_text_of(widget) for widget in app.screen.query(Static))
            table = screen.query_one("#cloud-status-table", DataTable)
            cells = " ".join(
                str(value) for row in range(table.row_count) for value in table.get_row_at(row)
            )
            return labels, cells

    labels, cells = asyncio.run(scenario())
    assert "not a user profile" in labels
    assert "not configured" in cells


def test_the_cloud_status_screen_masks_a_configured_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from textual.widgets import DataTable

    import docmax.core.config as config_module
    from docmax.core.config import Config
    from docmax.tui.app import CloudStatusScreen, DocMaxApp

    monkeypatch.setattr(config_module, "load", lambda: Config(api_key="s3cret-token", source=None))
    monkeypatch.setattr(config_module, "config_file", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config_module, "consent_file", lambda: tmp_path / "consent.json")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = CloudStatusScreen()
            app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#cloud-status-table", DataTable)
            rows = [table.get_row_at(i) for i in range(table.row_count)]
            return " ".join(str(value) for row in rows for value in row)

    rendered = asyncio.run(scenario())
    assert "s3cret-token" not in rendered
    assert "oken" in rendered


def test_escape_on_the_cloud_status_screen_returns_to_the_tool_list() -> None:
    import asyncio

    from docmax.tui.app import CloudStatusScreen, DocMaxApp, ToolListScreen

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(CloudStatusScreen())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ToolListScreen)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Report-only tools (ADR 0036) — a tool that declares `produces_output=False`
# has no output field to leave empty in the first place.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["get-info", "permissions"])
def test_report_only_tools_declare_produces_output_false(tool: str) -> None:
    assert get_tool(tool).produces_output is False


@pytest.mark.parametrize("tool", ["get-info", "permissions"])
def test_a_report_only_tools_form_has_no_output_field(tool: str) -> None:
    """The generic fix: `RunScreen.compose` reads `spec.produces_output`
    rather than checking which tool this is."""
    import asyncio

    from textual.widgets import Button, Input, Label

    from docmax.tui.app import DocMaxApp, RunScreen

    async def scenario() -> tuple[list[str], list[str], list[str]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen(tool)
            app.push_screen(screen)
            await pilot.pause()

            labels = [_text_of(widget) for widget in screen.query(Label)]
            output_field_ids = [
                widget.id or "" for widget in screen.query(Input) if widget.id == "field-__output__"
            ]
            button_ids = [widget.id or "" for widget in screen.query(Button)]
            return labels, output_field_ids, button_ids

    labels, output_field_ids, button_ids = asyncio.run(scenario())

    assert not any("output" in label.lower() for label in labels)
    assert output_field_ids == []
    assert "browse-output" not in button_ids
    assert "force" not in button_ids


@pytest.mark.parametrize("tool", ["get-info", "permissions"])
def test_a_report_only_tools_request_has_no_output(tool: str, tmp_path: Path) -> None:
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> Path | None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen(tool)
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            request = screen._request(dry_run=False, force=False)
            return request.output

    assert asyncio.run(scenario()) is None


@pytest.mark.parametrize("tool", ["get-info", "permissions"])
def test_a_report_only_tool_runs_with_only_an_input(tool: str, tmp_path: Path) -> None:
    """No output field to fill in, and no `InvalidParameterError` for leaving
    one empty — the run reaches the router with nothing but the input."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui.app import DocMaxApp, RunScreen

    router = FakeRouter(result=ToolResult(outputs=(), engine_used=Engine.LOCAL, details={"ok": 1}))
    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen(tool)
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            request = screen._request(dry_run=False, force=False)
            runner.run(request, router=router)  # type: ignore[arg-type]

    asyncio.run(scenario())
    (call,) = router.calls
    assert call["tool"] == tool


def test_get_info_via_the_real_tui_reports_without_an_output_path(tmp_path: Path) -> None:
    """The exact regression this fixes, end to end: the real registry, the
    real router, the real `get-info` tool, no fakes — clicking Run with only
    an input filled in succeeds and shows the report."""
    import asyncio

    from pypdf import PdfWriter
    from textual.widgets import Input, Static

    from docmax.tui.app import DocMaxApp, RunScreen

    source = tmp_path / "doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)

    async def scenario() -> tuple[str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = RunScreen("get-info")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(1.0)

            status = str(screen.query_one("#status", Static).content)
            details = _text_of(screen.query_one("#details", Static))
            return status, details

    status, details = asyncio.run(scenario())
    assert "Wrote nothing" in status, "get-info correctly writes nothing"
    assert "pages: 2" in details


# ---------------------------------------------------------------------------
# Missing dependencies — generic across every tool, never `if tool == "ocr"`
# (ADR 0036).
# ---------------------------------------------------------------------------


def test_dependency_check_reads_a_local_dependency_missing_error_directly() -> None:
    """A `LocalDependencyMissingError` already names exactly one thing — no
    need to ask the router anything."""
    from docmax.core.errors import LocalDependencyMissingError
    from docmax.tui.app import RunScreen

    screen = RunScreen("ocr")
    router = FakeRouter()
    exc = LocalDependencyMissingError(
        "ocr needs tesseract, which is not installed.",
        dependency="tesseract",
        install_hint="apt install tesseract-ocr",
        url="https://tesseract-ocr.github.io/tessdoc/Installation.html",
    )

    (dependency,) = screen._dependency_check(exc, router)  # type: ignore[arg-type]

    assert dependency.name == "tesseract"
    assert dependency.url == "https://tesseract-ocr.github.io/tessdoc/Installation.html"
    assert router.missing_dependencies_calls == []


def test_dependency_check_asks_the_router_for_a_no_engine_available_error() -> None:
    """The realistic case: `EngineRouter.resolve` checks availability before
    a strategy ever runs, so what actually reaches the TUI is
    `NoEngineAvailableError`, not `LocalDependencyMissingError` — the router
    is asked what the *local* engine specifically is missing, generically."""
    from docmax.core.errors import NoEngineAvailableError
    from docmax.core.protocols import MissingDependency
    from docmax.tui.app import RunScreen

    screen = RunScreen("ocr")
    reported = (
        MissingDependency(name="tesseract", reason="OCR needs tesseract.", url="https://x.test"),
    )
    router = FakeRouter(missing=reported)
    exc = NoEngineAvailableError("Cannot run 'ocr': not installed.", context={"tool": "ocr"})

    result = screen._dependency_check(exc, router)  # type: ignore[arg-type]

    assert result == reported
    assert router.missing_dependencies_calls == [("ocr", Engine.LOCAL)]


def test_dependency_check_falls_back_to_nothing_for_an_unrelated_error() -> None:
    """Every other `DocMaxError` gets the ordinary error modal, unaffected —
    this is what keeps the mechanism additive rather than a rewrite of every
    failure path."""
    from docmax.core.errors import InPlaceOverwriteError
    from docmax.tui.app import RunScreen

    screen = RunScreen("crop")
    router = FakeRouter()

    result = screen._dependency_check(InPlaceOverwriteError("boom"), router)  # type: ignore[arg-type]

    assert result == ()
    assert router.missing_dependencies_calls == []


def test_dependency_check_is_empty_when_the_router_has_nothing_structured_to_say() -> None:
    """Most tools implement no `missing_dependencies` method at all — the
    router already answers that with an empty tuple, and the TUI must fall
    back to the ordinary error modal rather than showing an empty dialog."""
    from docmax.core.errors import NoEngineAvailableError
    from docmax.tui.app import RunScreen

    screen = RunScreen("crop")
    router = FakeRouter()
    exc = NoEngineAvailableError("Cannot run 'crop'.", context={"tool": "crop"})

    assert screen._dependency_check(exc, router) == ()  # type: ignore[arg-type]


def test_a_missing_dependency_shows_the_dependency_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end flow: Run is clicked, the router raises
    `NoEngineAvailableError`, and — because the resolved-to-fail engine's
    strategy can name what is missing — the TUI shows `DependencyMissingScreen`
    instead of the generic error modal."""
    import asyncio

    from textual.widgets import Input, Static

    from docmax.core.errors import NoEngineAvailableError
    from docmax.core.protocols import MissingDependency
    from docmax.tui import runner as runner_module
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp, RunScreen

    reported = (
        MissingDependency(
            name="Tesseract",
            reason="OCR cannot run because Tesseract is not installed.",
            url="https://tesseract-ocr.github.io/tessdoc/Installation.html",
        ),
    )
    router = FakeRouter(
        raises=NoEngineAvailableError("Cannot run 'ocr': not installed.", context={"tool": "ocr"}),
        missing=reported,
    )
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        # `ocr` has more form fields than the fixtures above (lang, dpi,
        # deskew) — a taller viewport keeps `#run` on screen for `pilot.click`
        # without needing to scroll it into view first.
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            screen = RunScreen("ocr")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            assert isinstance(app.screen, DependencyMissingScreen)
            return " ".join(_text_of(widget) for widget in app.screen.query(Static))

    rendered = asyncio.run(scenario())
    assert "Dependency Required" in rendered
    assert "Tesseract" in rendered
    assert "OCR cannot run" in rendered


def test_the_dependency_dialog_shows_one_button_per_missing_dependency() -> None:
    """OCR on a machine with neither Tesseract nor Poppler gets two rows and
    two buttons, not one arbitrarily chosen page."""
    import asyncio

    from textual.widgets import Button

    from docmax.core.protocols import MissingDependency
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp

    dependencies = (
        MissingDependency(name="tesseract", reason="needs tesseract", url="https://a.test"),
        MissingDependency(name="pdftoppm", reason="needs pdftoppm", url="https://b.test"),
    )

    async def scenario() -> list[str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(DependencyMissingScreen("ocr", dependencies))
            await pilot.pause()
            return [str(button.label) for button in app.screen.query(Button)]

    labels = asyncio.run(scenario())
    assert any("tesseract" in label for label in labels)
    assert any("pdftoppm" in label for label in labels)


def test_a_dependency_without_a_url_gets_no_installation_button() -> None:
    import asyncio

    from textual.widgets import Button

    from docmax.core.protocols import MissingDependency
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp

    dependencies = (MissingDependency(name="mystery", reason="needs mystery", url=None),)

    async def scenario() -> list[str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(DependencyMissingScreen("ocr", dependencies))
            await pilot.pause()
            return [widget.id or "" for widget in app.screen.query(Button)]

    button_ids = asyncio.run(scenario())
    assert not any(identifier.startswith("open-install-") for identifier in button_ids)
    assert "dependency-back" in button_ids


def test_the_open_installation_page_button_opens_the_official_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import docmax.tui.app as app_module
    from docmax.core.protocols import MissingDependency
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp

    opened: list[str] = []
    monkeypatch.setattr(app_module, "_open_url", opened.append)

    dependencies = (
        MissingDependency(
            name="Tesseract",
            reason="needs tesseract",
            url="https://tesseract-ocr.github.io/tessdoc/Installation.html",
        ),
    )

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(DependencyMissingScreen("ocr", dependencies))
            await pilot.pause()
            await pilot.click("#open-install-0")
            await pilot.pause()

    asyncio.run(scenario())
    assert opened == ["https://tesseract-ocr.github.io/tessdoc/Installation.html"]


def test_the_dependency_dialogs_back_button_returns_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from textual.widgets import Input

    from docmax.core.errors import NoEngineAvailableError
    from docmax.core.protocols import MissingDependency
    from docmax.tui import runner as runner_module
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp, RunScreen

    reported = (MissingDependency(name="tesseract", reason="needs tesseract", url=None),)
    router = FakeRouter(
        raises=NoEngineAvailableError("Cannot run 'ocr'.", context={"tool": "ocr"}),
        missing=reported,
    )
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")
    out = str(tmp_path / "out.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        # A taller viewport keeps `#run` on screen: `ocr`'s form has more
        # fields (lang, dpi, deskew) than the fixtures used elsewhere.
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            screen = RunScreen("ocr")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = out
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)
            assert isinstance(app.screen, DependencyMissingScreen)

            await pilot.click("#dependency-back")
            await pilot.pause()

            assert isinstance(app.screen, RunScreen)
            return str(screen.query_one("#field-__output__", Input).value)

    assert asyncio.run(scenario()) == out


def test_escape_from_the_dependency_dialog_also_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from textual.widgets import Input

    from docmax.core.errors import NoEngineAvailableError
    from docmax.core.protocols import MissingDependency
    from docmax.tui import runner as runner_module
    from docmax.tui.app import DependencyMissingScreen, DocMaxApp, RunScreen

    reported = (MissingDependency(name="tesseract", reason="needs tesseract", url=None),)
    router = FakeRouter(
        raises=NoEngineAvailableError("Cannot run 'ocr'.", context={"tool": "ocr"}),
        missing=reported,
    )
    monkeypatch.setattr(runner_module, "build_router", lambda: router)

    source = _touch(tmp_path / "in.pdf")

    async def scenario() -> bool:
        app = DocMaxApp()
        # A taller viewport keeps `#run` on screen: `ocr`'s form has more
        # fields (lang, dpi, deskew) than the fixtures used elsewhere.
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            screen = RunScreen("ocr")
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(source)
            screen.query_one("#field-__output__", Input).value = str(tmp_path / "out.pdf")
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)
            assert isinstance(app.screen, DependencyMissingScreen)

            await pilot.press("escape")
            await pilot.pause()

            return isinstance(app.screen, RunScreen)

    assert asyncio.run(scenario())
