"""``BatchScreen``: one tool, run over many documents, from the TUI.

Kept separate from ``test_tui.py`` (already several thousand lines) rather
than grown inside it — the structural, whole-package guarantees that file
already asserts (no per-tool code, no cross-interface import, the TUI offers
a subset of what the CLI exposes) walk every file under ``src/docmax/tui/``
regardless of which test file exercises the code, so they still cover
``BatchScreen`` unmodified. See ADR 0040 for why this screen is hand-written
rather than generated, and why it may call ``docmax.runners.batch`` directly.

Like ``RunScreen``'s own tests, a real ``run_batch`` is never exercised here
— it lives one layer down, in ``tests/unit/test_m9_batch.py``. What matters
here is only that the screen calls it with the right arguments, and renders
whatever it hands back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tui import catalog

textual = pytest.importorskip("textual", reason="the TUI needs the `tui` extra")


def _touch(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\n")
    return path


# ---------------------------------------------------------------------------
# Tool selection
# ---------------------------------------------------------------------------


def test_batch_screen_tool_select_offers_every_batchable_tool() -> None:
    """Built from the registry via ``catalog.batchable_tools()``, never a
    literal tuple — the same discipline every generated tool form already
    holds."""
    import asyncio

    from textual.widgets import Select

    from docmax.tui.app import BatchScreen, DocMaxApp

    async def scenario() -> tuple[str, ...]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()
            select = screen.query_one("#field-__tool__", Select)
            return tuple(str(value) for _label, value in select._options[1:])

    names = asyncio.run(scenario())
    assert set(names) == {spec.name for spec in catalog.batchable_tools()}


def test_batch_screen_excludes_report_only_tools_from_the_tool_select() -> None:
    """``get-info`` and ``permissions`` have nothing to mirror into an output
    directory — the same ``produces_output`` field ``RunScreen`` already
    reads to skip their output field entirely."""
    import asyncio

    from textual.widgets import Select

    from docmax.tui.app import BatchScreen, DocMaxApp

    async def scenario() -> tuple[str, ...]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()
            select = screen.query_one("#field-__tool__", Select)
            return tuple(str(value) for _label, value in select._options[1:])

    names = asyncio.run(scenario())
    assert "get-info" not in names
    assert "permissions" not in names


def test_choosing_a_tool_rebuilds_the_params_form() -> None:
    """Selecting a different tool swaps the whole parameter section — the
    one thing ``RunScreen`` never has to do, since its tool is fixed for the
    screen's lifetime."""
    import asyncio

    from textual.widgets import Select

    from docmax.tui.app import BatchScreen, DocMaxApp

    async def scenario() -> tuple[list[str], list[str]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()
            before = [field.name for field in screen._fields]

            screen.query_one("#field-__tool__", Select).value = "resize"
            await pilot.pause()
            after = [field.name for field in screen._fields]
            return before, after

    before, after = asyncio.run(scenario())
    assert before != after
    assert "width" in after
    assert "height" in after


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


def _isolate_ui_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `core.config.ui_state_file()` at an isolated location — the same
    helper `test_tui.py` uses, duplicated rather than imported cross-file (see
    the module docstring)."""
    monkeypatch.setattr(
        "docmax.core.config.platformdirs.user_config_dir", lambda *a, **k: str(tmp_path / "config")
    )


def test_batch_browse_inputs_always_asks_for_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batch is unconditionally many-in, unlike ``RunScreen`` where
    ``multiple`` depends on the chosen tool. Patches
    ``browser._native_dialog`` — the seam ``pick_files`` itself calls at run
    time — rather than ``pick_files``, which ``app.py`` already imported a
    bound reference to at module load."""
    import asyncio

    from docmax.tui import browser as browser_module
    from docmax.tui.app import BatchScreen, DocMaxApp

    seen: dict[str, Any] = {}

    def fake(*, multiple: bool, start: Path) -> str:
        seen["multiple"] = multiple
        return ""

    monkeypatch.setattr(browser_module, "_native_dialog", fake)

    async def scenario() -> None:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()
            await pilot.click("#browse-inputs")
            await pilot.pause(0.5)

    asyncio.run(scenario())
    assert seen["multiple"] is True


def test_batch_browse_output_dir_invokes_the_native_dialog_and_remembers_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end through ``BatchScreen._browse_output_dir``, the same way
    ``test_choosing_a_file_remembers_its_folder_for_the_next_browse`` proves
    it for ``RunScreen`` — the folder just chosen is offered as the *next*
    dialog's starting point, with no ``start=`` passed by the caller."""
    import asyncio

    from textual.widgets import Input

    from docmax.tui import browser as browser_module
    from docmax.tui.app import BatchScreen, DocMaxApp

    _isolate_ui_state(monkeypatch, tmp_path)
    chosen = tmp_path / "out"
    chosen.mkdir()
    calls: list[dict[str, Any]] = []

    def fake(*, start: Path) -> str:
        calls.append({"start": start})
        return str(chosen)

    monkeypatch.setattr(browser_module, "_native_directory_dialog", fake)

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()
            await pilot.click("#browse-output-dir")  # nothing remembered yet
            await pilot.pause(0.5)
            await pilot.click("#browse-output-dir")  # should now open in `chosen`
            await pilot.pause(0.5)
            return screen.query_one("#field-__output-dir__", Input).value

    value = asyncio.run(scenario())
    assert value == str(chosen)
    assert calls[0]["start"] == Path.home(), "nothing was remembered before the first pick"
    assert calls[1]["start"] == chosen, "the directory just chosen is offered next"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def test_clicking_run_calls_run_batch_and_renders_the_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from textual.widgets import DataTable, Input, Static

    import docmax.runners.batch as batch_module
    from docmax.runners.batch import BatchReport, ItemOutcome
    from docmax.tui import runner as runner_module
    from docmax.tui.app import BatchScreen, DocMaxApp

    monkeypatch.setattr(runner_module, "build_router", lambda: object())

    a = _touch(tmp_path / "a.pdf")
    b = _touch(tmp_path / "b.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    calls: list[dict[str, Any]] = []

    def fake_run_batch(
        pipeline: Any,
        sources: Any,
        output_dir: Any,
        *,
        router: Any,
        progress: Any,
        cancellation: Any,
        force: bool,
        dry_run: bool,
        on_outcome: Any = None,
    ) -> BatchReport:
        calls.append({"pipeline": pipeline, "sources": tuple(sources), "output_dir": output_dir})
        outcomes = tuple(
            ItemOutcome(
                source=source,
                destination=output_dir / f"{source.stem}.pdf",
                result=ToolResult(
                    outputs=(output_dir / f"{source.stem}.pdf",), engine_used=Engine.LOCAL
                ),
            )
            for source in sources
        )
        for outcome in outcomes:
            if on_outcome is not None:
                on_outcome(outcome)
        return BatchReport(outcomes=outcomes, cancelled=False)

    monkeypatch.setattr(batch_module, "run_batch", fake_run_batch)

    async def scenario() -> tuple[int, str, str]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = f"{a}, {b}"
            screen.query_one("#field-__output-dir__", Input).value = str(out_dir)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)

            table = screen.query_one("#batch-results", DataTable)
            return (
                table.row_count,
                str(screen.query_one("#status", Static).content),
                str(screen.query_one("#batch-summary", Static).content),
            )

    row_count, status, summary = asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0]["sources"] == (a, b)
    assert calls[0]["output_dir"] == out_dir
    assert row_count == 2
    assert "2 succeeded, 0 failed" in status
    assert summary == "2 succeeded, 0 failed"


def test_each_finished_item_appears_as_a_row_via_on_outcome(tmp_path: Path) -> None:
    """Drives ``_append_outcome`` directly, the same way
    ``test_progress_records_what_it_was_told`` drives ``CallbackProgress``
    without a real run — one success, one failure, each rendered."""
    import asyncio

    from textual.widgets import DataTable

    from docmax.core.errors import InternalError
    from docmax.runners.batch import ItemOutcome
    from docmax.tui.app import BatchScreen, DocMaxApp

    ok = ItemOutcome(
        source=tmp_path / "ok.pdf",
        destination=tmp_path / "out" / "ok.pdf",
        result=ToolResult(outputs=(tmp_path / "out" / "ok.pdf",), engine_used=Engine.LOCAL),
    )
    failed = ItemOutcome(source=tmp_path / "bad.pdf", error=InternalError("boom"))

    async def scenario() -> list[list[str]]:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()

            screen._append_outcome(ok)
            screen._append_outcome(failed)
            await pilot.pause()

            table = screen.query_one("#batch-results", DataTable)
            return [list(table.get_row_at(i)) for i in range(table.row_count)]

    rows = asyncio.run(scenario())
    assert len(rows) == 2
    assert "ok.pdf" in rows[0]
    assert str(tmp_path / "out" / "ok.pdf") in rows[0]
    assert "bad.pdf" in rows[1]
    assert any("boom" in str(cell) for cell in rows[1])


def test_cancelling_a_batch_run_cancels_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading
    import time

    from textual.widgets import Input

    import docmax.runners.batch as batch_module
    from docmax.runners.batch import BatchReport
    from docmax.tui import runner as runner_module
    from docmax.tui.app import BatchScreen, DocMaxApp

    monkeypatch.setattr(runner_module, "build_router", lambda: object())

    a = _touch(tmp_path / "a.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    started = threading.Event()
    captured: dict[str, Any] = {}

    def slow_run_batch(
        pipeline: Any,
        sources: Any,
        output_dir: Any,
        *,
        router: Any,
        progress: Any,
        cancellation: Any,
        force: bool,
        dry_run: bool,
        on_outcome: Any = None,
    ) -> BatchReport:
        captured["token"] = cancellation
        started.set()
        time.sleep(1.0)
        return BatchReport(outcomes=(), cancelled=cancellation.is_cancelled)

    monkeypatch.setattr(batch_module, "run_batch", slow_run_batch)

    async def scenario() -> bool:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(a)
            screen.query_one("#field-__output-dir__", Input).value = str(out_dir)
            await pilot.pause()

            await pilot.click("#run")
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 2.0)
            await pilot.click("#cancel")
            await pilot.pause(0.2)
            return bool(captured["token"].is_cancelled)

    assert asyncio.run(scenario())


def test_a_whole_batch_preflight_error_shows_the_error_modal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``plan_batch``-style failure (missing output directory, colliding
    destinations, ...) is a ``DocMaxError`` raised before any item runs —
    rendered exactly like ``RunScreen``'s own ``ErrorScreen``, with no
    batch-specific error UI."""
    import asyncio

    from textual.widgets import Input

    import docmax.runners.batch as batch_module
    from docmax.tui import runner as runner_module
    from docmax.tui.app import BatchScreen, DocMaxApp, ErrorScreen

    monkeypatch.setattr(runner_module, "build_router", lambda: object())

    def failing_run_batch(*args: Any, **kwargs: Any) -> Any:
        raise InvalidParameterError(
            "The output directory does not exist: /nope", remedy="Create it first."
        )

    monkeypatch.setattr(batch_module, "run_batch", failing_run_batch)

    a = _touch(tmp_path / "a.pdf")

    async def scenario() -> str:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()

            screen.query_one("#field-__inputs__", Input).value = str(a)
            screen.query_one("#field-__output-dir__", Input).value = "/nope"
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.5)
            assert isinstance(app.screen, ErrorScreen)
            return app.screen.error.message

    message = asyncio.run(scenario())
    assert "does not exist" in message


def test_running_with_no_tool_documents_or_output_dir_is_refused_before_any_worker_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three required-field checks (`_gather`) — no input, no output
    directory — are asked before a worker ever starts, the same boundary
    ``RunScreen._request`` holds for a single tool."""
    import asyncio

    import docmax.runners.batch as batch_module
    from docmax.tui.app import BatchScreen, DocMaxApp, ErrorScreen

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("run_batch must not be called when the form is incomplete")

    monkeypatch.setattr(batch_module, "run_batch", boom)

    async def scenario() -> bool:
        app = DocMaxApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause()

            await pilot.click("#run")
            await pilot.pause(0.2)
            return isinstance(app.screen, ErrorScreen)

    assert asyncio.run(scenario())
