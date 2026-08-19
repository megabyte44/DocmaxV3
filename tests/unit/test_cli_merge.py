"""The CLI's half of the contract: parse, delegate, render, exit.

What is being pinned here is mostly a *negative*: that the CLI does not decide
anything. It parses arguments, hands them to the router, and renders whatever
comes back. The moment it starts choosing engines or resolving destinations
itself, there are two implementations of a rule that must never differ — which
is the failure the router was written to prevent.

So the assertions are about **what reaches the router** and **what the user
sees**, not about merging. Merging is `test_merge.py`'s job.

The router is injected throughout. None of these tests may read the developer's
real config file or consent record, and a test that did would pass or fail
depending on whose machine it ran on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from docmax.cli.execution import EXIT_CANCELLED, EXIT_FAILURE
from docmax.cli.main import app
from docmax.core.config import Config
from docmax.core.errors import CorruptDocumentError, EngineNotSupportedError
from docmax.core.models import DocumentRef, Engine, OutputTarget, ToolResult
from docmax.core.router import EngineRouter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.protocols import EngineStrategy, ProgressSink

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Rich styles output even under CliRunner; the assertions are about words."""
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    """Everything the user saw, from both streams.

    Results go to stdout and diagnostics to stderr — deliberately, so that
    `docmax ... --json | jq` is not polluted by a progress bar or an error
    panel. Most assertions here care only that the user was told something, so
    they read both; `test_errors_do_not_pollute_stdout` pins the split itself.
    """
    return plain(result.stdout) + plain(result.stderr)


def write_pdf(path: Path, pages: int = 1) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


# ---------------------------------------------------------------------------
# A router that records, built on the real one
# ---------------------------------------------------------------------------


@dataclass
class RecordingStrategy:
    """Captures what the CLI ultimately caused to be passed down."""

    raises: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

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
        self.calls.append(
            {
                "docs": list(docs),
                "target": target,
                "progress": progress,
                "cancellation": cancellation,
                "params": params,
            }
        )
        if self.raises is not None:
            raise self.raises
        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            details={"pages": 7},
        )


@dataclass
class FakeSpec:
    name: str = "merge"
    default_suffix: str = ".pdf"
    strategies: dict[Engine, RecordingStrategy] = field(default_factory=dict)

    @property
    def supported_engines(self) -> frozenset[Engine]:
        return frozenset(self.strategies)

    def supports(self, engine: Engine) -> bool:
        return engine in self.strategies

    def load_strategy(self, engine: Engine) -> EngineStrategy:
        if engine not in self.strategies:
            raise EngineNotSupportedError(f"no {engine.value} engine", context={"tool": self.name})
        return self.strategies[engine]


@pytest.fixture
def strategy() -> RecordingStrategy:
    return RecordingStrategy()


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch, strategy: RecordingStrategy) -> RecordingStrategy:
    """Install a router whose only tool is a recorder.

    A *real* `EngineRouter` with a fake spec, not a mock of the router: the
    point is to check what the CLI hands over, and a mocked router would let a
    CLI that quietly re-implemented resolution still pass.
    """
    spec = FakeSpec(strategies={Engine.LOCAL: strategy})
    router = EngineRouter(
        config=Config(),
        consent=None,
        lookup=lambda name: spec,  # type: ignore[arg-type,return-value]
    )
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)
    return strategy


@pytest.fixture
def real_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """The genuine registry and router, but a config that reads no real file."""
    router = EngineRouter(config=Config(), consent=None)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path]:
    return write_pdf(tmp_path / "a.pdf", 2), write_pdf(tmp_path / "b.pdf", 3)


# ---------------------------------------------------------------------------
# The command reaches the router
# ---------------------------------------------------------------------------


def test_merge_is_offered(routed: RecordingStrategy) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "merge" in plain(result.stdout)


def test_a_successful_merge_exits_zero(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = sources

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf")])

    assert result.exit_code == 0, shown(result)
    assert len(routed.calls) == 1, "the CLI reached the router exactly once"


def test_every_input_reaches_the_router_in_order(routed: RecordingStrategy, tmp_path: Path) -> None:
    """Order is the user's instruction; the CLI must not sort or dedupe it."""
    third = write_pdf(tmp_path / "third.pdf")
    first = write_pdf(tmp_path / "first.pdf")
    second = write_pdf(tmp_path / "second.pdf")

    result = runner.invoke(
        app,
        ["merge", str(third), str(first), str(second), "-o", str(tmp_path / "out.pdf")],
    )

    assert result.exit_code == 0, shown(result)
    names = [doc.path.name for doc in routed.calls[0]["docs"]]
    assert names == ["third.pdf", "first.pdf", "second.pdf"]


def test_the_destination_reaches_the_router(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = sources
    out = tmp_path / "combined.pdf"

    runner.invoke(app, ["merge", str(a), str(b), "-o", str(out)])

    assert routed.calls[0]["target"].destination == out


def test_the_outline_flag_reaches_the_tool(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = sources

    runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf"), "--no-outline"])

    assert routed.calls[0]["params"] == {"outline": False}


def test_the_result_is_reported(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """The engine is named: it is the one thing a user cannot see in the file."""
    a, b = sources
    out = tmp_path / "out.pdf"

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out)])

    text = shown(result)
    assert "Wrote" in text
    assert "local engine" in text
    assert "7 pages" in text


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_output_is_required(sources: tuple[Path, Path]) -> None:
    """Merge can never derive a destination: it would land on the first input."""
    a, b = sources

    result = runner.invoke(app, ["merge", str(a), str(b)])

    assert result.exit_code == 2, "Typer's usage exit code"
    assert "--output" in shown(result)


def test_an_unknown_option_is_a_usage_error(sources: tuple[Path, Path], tmp_path: Path) -> None:
    a, _ = sources

    result = runner.invoke(app, ["merge", str(a), "-o", str(tmp_path / "out.pdf"), "--nonsense"])

    assert result.exit_code == 2


def test_an_unknown_engine_is_a_usage_error(sources: tuple[Path, Path], tmp_path: Path) -> None:
    """Typer validates the choice, so a typo never reaches the router."""
    a, _ = sources

    result = runner.invoke(
        app, ["merge", str(a), "-o", str(tmp_path / "out.pdf"), "--engine", "magic"]
    )

    assert result.exit_code == 2


def test_no_inputs_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["merge", "-o", str(tmp_path / "out.pdf")])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Routing — the CLI decides nothing
# ---------------------------------------------------------------------------


def test_an_explicit_engine_is_passed_to_the_router(
    monkeypatch: pytest.MonkeyPatch, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """The CLI forwards the request; the router decides whether it is possible."""
    seen: dict[str, Any] = {}
    strategy = RecordingStrategy()
    spec = FakeSpec(strategies={Engine.LOCAL: strategy})
    router = EngineRouter(
        config=Config(),
        consent=None,
        lookup=lambda name: spec,  # type: ignore[arg-type,return-value]
    )
    original = EngineRouter.run

    def spy(self: EngineRouter, tool: str, *args: Any, **kwargs: Any) -> ToolResult:
        seen["requested"] = kwargs.get("requested")
        return original(self, tool, *args, **kwargs)

    monkeypatch.setattr(EngineRouter, "run", spy)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)

    a, b = sources
    runner.invoke(
        app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf"), "--engine", "local"]
    )

    assert seen["requested"] is Engine.LOCAL


def test_no_engine_argument_forwards_none(
    monkeypatch: pytest.MonkeyPatch, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """Absent means "you decide" — the CLI must not substitute a default of its own."""
    seen: dict[str, Any] = {}
    strategy = RecordingStrategy()
    spec = FakeSpec(strategies={Engine.LOCAL: strategy})
    router = EngineRouter(
        config=Config(),
        consent=None,
        lookup=lambda name: spec,  # type: ignore[arg-type,return-value]
    )
    original = EngineRouter.run

    def spy(self: EngineRouter, tool: str, *args: Any, **kwargs: Any) -> ToolResult:
        seen["requested"] = kwargs.get("requested")
        return original(self, tool, *args, **kwargs)

    monkeypatch.setattr(EngineRouter, "run", spy)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)

    a, b = sources
    runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf")])

    assert seen["requested"] is None


def test_the_cli_contains_no_engine_precedence_logic() -> None:
    """Structural: resolution words appear in the router, and nowhere in the CLI.

    A blunt check, but it fails loudly the day someone adds "if offline" or a
    consent test to a command — which is exactly when a second implementation
    of the precedence ladder starts to exist.
    """
    from docmax.cli import execution, main

    for module in (main, execution):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith(("#", '"', "|"))
        )
        assert "Engine.CLOUD" not in body, f"{module.__name__} is choosing an engine"
        assert ".has(" not in body, f"{module.__name__} is checking consent itself"
        assert "engine_for" not in body, f"{module.__name__} is reading engine preference"


# ---------------------------------------------------------------------------
# Errors — readable, non-zero, no traceback
# ---------------------------------------------------------------------------


def test_a_missing_input_is_reported_without_a_traceback(
    routed: RecordingStrategy, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["merge", str(tmp_path / "absent.pdf"), "-o", str(tmp_path / "o.pdf")]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "input.not_found" in text
    assert "Traceback" not in text
    assert "Check the path and try again." in text, "the remedy names the next step"


def test_an_existing_output_is_reported_without_a_traceback(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """Resolution happens inside the error boundary, not in the command body."""
    a, b = sources
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out)])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in text
    assert "Traceback" not in text
    assert "--force" in text


def test_an_output_that_is_an_input_is_refused(
    routed: RecordingStrategy, sources: tuple[Path, Path]
) -> None:
    """The most destructive v2 bug, reported rather than performed."""
    a, b = sources

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(a), "--force"])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "output.in_place_overwrite" in text
    assert "Traceback" not in text


def test_a_tool_failure_is_rendered_as_an_error(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = sources
    routed.raises = CorruptDocumentError("the file is damaged")

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf")])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "input.corrupt" in text
    assert "the file is damaged" in text
    assert "Traceback" not in text


def test_force_permits_an_existing_output(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = sources
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out), "--force"])

    assert result.exit_code == 0, shown(result)


# ---------------------------------------------------------------------------
# Progress and cancellation
# ---------------------------------------------------------------------------


def test_a_progress_sink_reaches_the_tool(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """The CLI supplies the terminal's implementation; core never learns of Rich."""
    from docmax.cli.progress import ConsoleProgress

    a, b = sources

    runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf")])

    assert isinstance(routed.calls[0]["progress"], ConsoleProgress)


def test_a_cancellation_token_reaches_the_tool(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    from docmax.core.cancellation import CancellationToken as Token

    a, b = sources

    runner.invoke(app, ["merge", str(a), str(b), "-o", str(tmp_path / "out.pdf")])

    assert isinstance(routed.calls[0]["cancellation"], Token)


def test_cancelling_exits_130_and_writes_nothing(
    routed: RecordingStrategy, sources: tuple[Path, Path], tmp_path: Path
) -> None:
    """130 is `128 + SIGINT`; scripts and CI already read it as "interrupted"."""
    from docmax.core.errors import CancelledError

    a, b = sources
    out = tmp_path / "out.pdf"
    routed.raises = CancelledError("merge was cancelled.")

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out)])

    assert result.exit_code == EXIT_CANCELLED
    assert "Nothing was written" in shown(result)
    assert not out.exists()


def test_an_interrupt_cancels_the_token_rather_than_raising() -> None:
    """Ctrl-C reaches the existing cancellation contract, not a bare KeyboardInterrupt.

    The handler is invoked directly rather than by raising a real signal — this
    has to pass on Windows, where SIGINT cannot be delivered to oneself, and a
    test that only ran on POSIX would be a test of the CI matrix.

    What matters is that the handler *cancels the token*: that is what lets the
    atomic writers discard their staged file and leave the destination
    untouched, instead of dropping a `KeyboardInterrupt` wherever the
    interpreter happened to be.
    """
    import signal

    from docmax.cli.execution import _interruptible
    from docmax.core.cancellation import CancellationToken as Token

    token = Token()
    before = signal.getsignal(signal.SIGINT)

    with _interruptible(token):
        during = signal.getsignal(signal.SIGINT)
        assert during is not before, "a handler was installed for the operation"
        assert callable(during)

        # Read the property into a local on *both* sides. Asserting on
        # `token.is_cancelled` directly narrows the property itself to
        # `Literal[False]` for the rest of the function, and mypy --strict then
        # calls every statement below it unreachable.
        before_interrupt = token.is_cancelled
        assert not before_interrupt

        during(signal.SIGINT, None)  # the interrupt

        after_interrupt = token.is_cancelled
        assert after_interrupt, "the token carries the request, not an exception"

    # Restored afterwards, or the next command in the same process inherits a
    # handler pointing at a finished operation's token.
    restored = signal.getsignal(signal.SIGINT)
    assert restored is before


def test_a_second_interrupt_restores_the_default_behaviour() -> None:
    """Someone who wants out *now* must not be held by a program being clever."""
    import signal

    from docmax.cli.execution import _interruptible
    from docmax.core.cancellation import CancellationToken as Token

    before = signal.getsignal(signal.SIGINT)

    with _interruptible(Token()):
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)

        assert signal.getsignal(signal.SIGINT) is before, "the first interrupt stood down"

    assert signal.getsignal(signal.SIGINT) is before


def test_errors_do_not_pollute_stdout(routed: RecordingStrategy, tmp_path: Path) -> None:
    """`docmax ... | jq` must not receive an error panel on stdout.

    Results go to stdout; diagnostics go to stderr. That split is the whole
    reason `render.py` keeps two consoles, and it is invisible until something
    pipes the output somewhere.
    """
    result = runner.invoke(
        app, ["merge", str(tmp_path / "absent.pdf"), "-o", str(tmp_path / "o.pdf")]
    )

    assert "input.not_found" in plain(result.stderr)
    assert "input.not_found" not in plain(result.stdout)


def test_the_progress_sink_never_raises() -> None:
    """A broken progress bar must not lose someone's merged document."""
    from rich.console import Console

    from docmax.cli.progress import ConsoleProgress

    sink = ConsoleProgress(Console(quiet=True))

    sink.finish()  # before start
    sink.advance()  # before start
    sink.start("work", total=3)
    sink.advance(2)
    sink.finish()
    sink.finish()  # twice


# ---------------------------------------------------------------------------
# Integration — the real registry, router and merge tool
# ---------------------------------------------------------------------------


def test_the_whole_path_produces_a_merged_pdf(real_router: None, tmp_path: Path) -> None:
    """CLI → router → registry → MergeLocal → a real file on disk."""
    from pypdf import PdfReader

    a = write_pdf(tmp_path / "a.pdf", 2)
    b = write_pdf(tmp_path / "b.pdf", 3)
    out = tmp_path / "merged.pdf"

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert out.is_file()
    assert len(PdfReader(str(out)).pages) == 5
    assert "local engine" in shown(result)


def test_a_dry_run_writes_nothing_and_explains(real_router: None, tmp_path: Path) -> None:
    a = write_pdf(tmp_path / "a.pdf", 1)
    b = write_pdf(tmp_path / "b.pdf", 1)
    out = tmp_path / "merged.pdf"

    result = runner.invoke(app, ["merge", str(a), str(b), "-o", str(out), "--dry-run"])

    text = shown(result)
    assert result.exit_code == 0, text
    assert "Dry run" in text
    assert "local" in text
    assert not out.exists()


def test_requesting_the_cloud_engine_for_merge_is_refused(
    real_router: None, tmp_path: Path
) -> None:
    """The router refuses it; the CLI merely reports. No cloud logic here."""
    a = write_pdf(tmp_path / "a.pdf", 1)

    result = runner.invoke(
        app, ["merge", str(a), "-o", str(tmp_path / "out.pdf"), "--engine", "cloud"]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "engine.not_supported" in text
    assert "Traceback" not in text
