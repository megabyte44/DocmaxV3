"""The boundary contracts.

A protocol is structural, so most of what matters about these is checked by mypy
rather than at runtime — a strategy that gets ``run``'s signature wrong is a type
error, not a test failure. What is worth asserting here is the behaviour the
protocols *promise* on top of their shape, which no type checker can see.

The stand-in implementations below are also the ones a contributor should copy
when writing a real strategy: nothing inherits from anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.errors import CancelledError
from docmax.core.models import DocumentRef, Engine, OutputTarget, ToolResult
from docmax.core.protocols import EngineStrategy, NullProgress, ProgressSink

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class RecordingProgress:
    """A sink that remembers what it was told. Satisfies ProgressSink structurally."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append(("start", (description, total)))

    def advance(self, amount: int = 1) -> None:
        self.events.append(("advance", amount))

    def finish(self) -> None:
        self.events.append(("finish", None))


class StubStrategy:
    """A minimal engine. Declares no base class, exactly like a real one."""

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str | None:
        return None

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        progress.start("stubbing", total=1)
        cancellation.raise_if_cancelled(operation="stub")
        progress.advance()
        progress.finish()
        return ToolResult(outputs=(target.destination,), engine_used=Engine.LOCAL)


# ---------------------------------------------------------------------------
# ProgressSink
# ---------------------------------------------------------------------------


def test_null_progress_satisfies_the_protocol() -> None:
    """`runtime_checkable` only verifies method names; that is the useful part here."""
    assert isinstance(NullProgress(), ProgressSink)


def test_a_plain_class_satisfies_the_protocol_without_inheriting() -> None:
    """Structural typing is the mechanism the whole layering rests on.

    The MRO check is the point: an implementation never imports this module in
    order to satisfy the contract, so a strategy in ``tools/`` creates no import
    edge back to ``core``. (``issubclass`` would answer ``True`` here — a
    method-only runtime protocol supports it — which is why it cannot be used to
    show the absence of inheritance.)
    """
    assert isinstance(RecordingProgress(), ProgressSink)
    assert ProgressSink not in RecordingProgress.__mro__
    assert RecordingProgress.__bases__ == (object,)


def test_null_progress_accepts_every_call() -> None:
    """It exists to delete a branch, so it must never be the thing that raises.

    There is nothing to assert on the return values — the protocol types them as
    ``None`` — so the assertion is that none of these calls blows up, including
    the indeterminate form and a `finish` with no matching `start`.
    """
    sink = NullProgress()

    sink.start("work", total=10)
    sink.advance(5)
    sink.finish()
    sink.start("indeterminate")
    sink.finish()


def test_null_progress_carries_no_state() -> None:
    """A shared do-nothing sink must not accumulate anything per call."""
    assert not hasattr(NullProgress(), "__dict__")


# ---------------------------------------------------------------------------
# EngineStrategy
# ---------------------------------------------------------------------------


def test_a_strategy_reports_availability_without_importing_anything(tmp_path: Path) -> None:
    strategy: EngineStrategy = StubStrategy()

    assert strategy.is_available() is True
    assert strategy.unavailable_reason() is None


def test_a_strategy_drives_the_progress_sink_it_is_given(tmp_path: Path) -> None:
    """Engines call a sink; they never construct one."""
    progress = RecordingProgress()
    strategy: EngineStrategy = StubStrategy()

    result = strategy.run(
        [],
        OutputTarget(destination=tmp_path / "out.pdf"),
        progress=progress,
        cancellation=NEVER_CANCELLED,
    )

    assert [name for name, _ in progress.events] == ["start", "advance", "finish"]
    assert result.engine_used is Engine.LOCAL


def test_a_strategy_observes_the_cancellation_it_is_given(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    strategy: EngineStrategy = StubStrategy()

    with pytest.raises(CancelledError, match="stub"):
        strategy.run(
            [],
            OutputTarget(destination=tmp_path / "out.pdf"),
            progress=NullProgress(),
            cancellation=token,
        )
