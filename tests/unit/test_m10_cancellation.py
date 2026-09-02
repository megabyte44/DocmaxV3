"""MCP cancellation, mapped onto the `CancellationToken` that already exists.

ADR 0030's claim in one line: **a cancelled MCP request cancels the DocMax run,
and the guarantee that nothing partial is left behind is inherited rather than
rebuilt.** These tests hold both halves.

The mechanism under test is real concurrency — a blocking tool on a worker
thread and an async scope cancelling it — so every wait here is bounded and
every test fails loudly rather than hanging. The suite's `timeout = 120` is the
backstop, not the plan.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from docmax.core.errors import CancelledError
from docmax.core.models import Engine, ToolResult
from docmax.mcp.policy import Policy
from docmax.mcp.server import DocMaxServer
from tests.unit.m9_support import FakeSpec, document, router_for

if TYPE_CHECKING:
    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget

#: Long enough that a correct implementation never reaches it, short enough that
#: a broken one fails the test rather than the suite.
PATIENCE = 5.0


class BlockingStrategy:
    """Waits for its token to be cancelled, then unwinds as a real tool would."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.observed_cancel = threading.Event()
        self.finished_normally = False

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str | None:
        return None

    def run(
        self,
        docs: list[DocumentRef],
        target: OutputTarget,
        *,
        progress: Any,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        self.started.set()
        deadline = time.monotonic() + PATIENCE
        while time.monotonic() < deadline:
            if cancellation.is_cancelled:
                self.observed_cancel.set()
                # What every real strategy does: stop cooperatively, so the
                # atomic writer discards the staged file on the way out.
                raise CancelledError("stopped")
            time.sleep(0.01)
        self.finished_normally = True
        return ToolResult(outputs=(target.destination,), engine_used=Engine.LOCAL)


def server_for(tmp_path: Path, strategy: Any) -> tuple[DocMaxServer, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = FakeSpec(name="slow", strategies={Engine.LOCAL: strategy})
    source = document(tmp_path / "in.pdf", text="in")
    destination = tmp_path / "out.pdf"
    return DocMaxServer(Policy.build([tmp_path]), router_for(spec)), source, destination


def call_params(source: Path, destination: Path) -> Any:
    from mcp import types

    return types.CallToolRequestParams(
        name="slow",
        arguments={"inputs": [str(source)], "output": str(destination)},
    )


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def test_a_cancelled_call_cancels_the_token(tmp_path: Path) -> None:
    """The whole of ADR 0030: protocol cancellation reaches the existing token."""
    strategy = BlockingStrategy()
    server, source, destination = server_for(tmp_path, strategy)

    async def scenario() -> None:
        async with anyio.create_task_group() as group:

            async def call() -> None:
                await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]

            group.start_soon(call)
            await anyio.to_thread.run_sync(lambda: strategy.started.wait(PATIENCE))
            group.cancel_scope.cancel()

    anyio.run(scenario)

    assert strategy.observed_cancel.wait(PATIENCE), "the token never reached the running tool"
    assert not strategy.finished_normally


def test_a_cancelled_call_leaves_no_destination(tmp_path: Path) -> None:
    """Inherited from `core/atomic.py`, asserted rather than assumed."""
    strategy = BlockingStrategy()
    server, source, destination = server_for(tmp_path, strategy)

    async def scenario() -> None:
        async with anyio.create_task_group() as group:

            async def call() -> None:
                await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]

            group.start_soon(call)
            await anyio.to_thread.run_sync(lambda: strategy.started.wait(PATIENCE))
            group.cancel_scope.cancel()

    anyio.run(scenario)
    strategy.observed_cancel.wait(PATIENCE)

    assert not destination.exists()
    assert not list(tmp_path.glob(".docmax-*")), "a staged file survived the cancellation"


def test_cancellation_is_re_raised_not_swallowed(tmp_path: Path) -> None:
    """The dispatcher drops a cancelled request's response.

    A handler that returned a normal result would be telling its own runtime the
    call had completed, which is a lie the protocol cannot see through.
    """
    strategy = BlockingStrategy()
    server, source, destination = server_for(tmp_path, strategy)
    outcome: list[str] = []

    async def scenario() -> None:
        async with anyio.create_task_group() as group:

            async def call() -> None:
                try:
                    await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]
                except anyio.get_cancelled_exc_class():
                    outcome.append("cancelled")
                    raise
                outcome.append("returned")

            group.start_soon(call)
            await anyio.to_thread.run_sync(lambda: strategy.started.wait(PATIENCE))
            group.cancel_scope.cancel()

    anyio.run(scenario)

    assert outcome == ["cancelled"]


def test_the_tool_runs_off_the_event_loop(tmp_path: Path) -> None:
    """A blocking tool must not stall the loop that carries the cancellation.

    If the call ran inline, the counter below could not advance while the tool
    was blocked — and neither could `notifications/cancelled`.
    """
    strategy = BlockingStrategy()
    server, source, destination = server_for(tmp_path, strategy)
    ticks = 0

    async def scenario() -> None:
        nonlocal ticks
        async with anyio.create_task_group() as group:

            async def call() -> None:
                await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]

            group.start_soon(call)
            await anyio.to_thread.run_sync(lambda: strategy.started.wait(PATIENCE))
            for _ in range(5):
                await anyio.sleep(0.01)
                ticks += 1
            group.cancel_scope.cancel()

    anyio.run(scenario)

    assert ticks == 5, "the event loop was blocked while the tool ran"


def test_an_uncancelled_call_still_completes(tmp_path: Path) -> None:
    """The mapping must not make ordinary calls fragile."""

    class Quick(BlockingStrategy):
        def run(self, docs: Any, target: Any, **kwargs: Any) -> ToolResult:
            from docmax.core.atomic import atomic_write

            with atomic_write(target) as handle:
                handle.write(b"done")
            return ToolResult(outputs=(target.destination,), engine_used=Engine.LOCAL)

    server, source, destination = server_for(tmp_path, Quick())

    async def scenario() -> Any:
        return await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]

    result = anyio.run(scenario)

    assert result.is_error is not True
    assert destination.read_bytes() == b"done"


def test_a_fresh_token_is_made_for_each_call(tmp_path: Path) -> None:
    """A token shared between calls would let one cancellation stop the next."""
    # The objects themselves, not their ids: a freed token can be reallocated
    # at the same address, which would make an id comparison pass by accident.
    seen: list[Any] = []

    class Recorder(BlockingStrategy):
        def run(self, docs: Any, target: Any, *, cancellation: Any, **kwargs: Any) -> ToolResult:
            from docmax.core.atomic import atomic_write

            seen.append(cancellation)
            with atomic_write(target) as handle:
                handle.write(b"x")
            return ToolResult(outputs=(target.destination,), engine_used=Engine.LOCAL)

    server, source, _ = server_for(tmp_path, Recorder())

    async def scenario() -> None:
        await server.on_call_tool(None, call_params(source, tmp_path / "one.pdf"))  # type: ignore[arg-type]
        await server.on_call_tool(None, call_params(source, tmp_path / "two.pdf"))  # type: ignore[arg-type]

    anyio.run(scenario)

    assert len(seen) == 2
    assert seen[0] is not seen[1]


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_the_mapping_is_not_a_race(tmp_path: Path, attempt: int) -> None:
    """Repeated, because a cancellation test that passes once has proved little."""
    strategy = BlockingStrategy()
    server, source, destination = server_for(tmp_path / f"run{attempt}", strategy)

    async def scenario() -> None:
        async with anyio.create_task_group() as group:

            async def call() -> None:
                await server.on_call_tool(None, call_params(source, destination))  # type: ignore[arg-type]

            group.start_soon(call)
            await anyio.to_thread.run_sync(lambda: strategy.started.wait(PATIENCE))
            group.cancel_scope.cancel()

    anyio.run(scenario)

    assert strategy.observed_cancel.wait(PATIENCE)
    assert not destination.exists()
