"""Fakes shared by the three M9 suites.

Every M9 test drives a router built from these rather than from the real
registry, for the reason ``test_router.py`` gives about itself: a runner must
never know how a document is processed, so a pipeline test that needed pypdf
would be evidence of a design failure rather than a thorough test.

One thing here is not a fake, deliberately. :class:`FakeStrategy` writes its
output through ``core/atomic.py``, exactly as a real strategy does. That is what
lets a pipeline chain — stage two reads what stage one produced — and it is what
makes the atomicity assertions mean something: when a stage raises, the staged
file is discarded by the same code that discards it in production.

Not a ``conftest.py``. ``backlog.md`` wants one and it would be a change across
every existing suite; this module is scoped to M9 and imported explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from docmax.core.atomic import atomic_write
from docmax.core.config import Config
from docmax.core.errors import EngineNotSupportedError
from docmax.core.models import Engine, ToolResult
from docmax.core.registry import Param
from docmax.core.router import EngineRouter

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink


@dataclass
class FakeStrategy:
    """Appends a marker to its input and writes the result atomically.

    The marker chain is how a test asserts that stages ran in order: a document
    through ``a`` then ``b`` contains ``|a|b`` and nothing else can produce that.
    """

    marker: str = "x"
    engine: Engine = Engine.LOCAL
    available: bool = True
    raises: BaseException | None = None
    #: Called before the write, with the target. Used to cancel mid-run, or to
    #: assert what a stage was handed.
    hook: Callable[[OutputTarget], None] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.available

    def unavailable_reason(self) -> str | None:
        return None if self.available else "the fake says no"

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        self.calls.append({"docs": list(docs), "target": target, "params": params})
        cancellation.raise_if_cancelled(operation=self.marker)
        progress.start(f"faking {self.marker}", total=1)

        if self.hook is not None:
            self.hook(target)
        if self.raises is not None:
            raise self.raises

        body = docs[0].path.read_bytes() + f"|{self.marker}".encode()
        with atomic_write(target) as handle:
            handle.write(body)

        progress.advance()
        return ToolResult(
            outputs=(target.destination,),
            engine_used=self.engine,
            details={"marker": self.marker},
        )


@dataclass
class FakeSpec:
    """A ``ToolSpec`` stand-in that hands back fakes instead of importing modules."""

    name: str = "widget"
    strategies: dict[Engine, FakeStrategy] = field(default_factory=dict)
    params: tuple[Param, ...] = ()
    default_suffix: str = ".pdf"
    accepts_multiple_inputs: bool = False
    produces_directory: bool = False
    produces_output: bool = True

    @property
    def supported_engines(self) -> frozenset[Engine]:
        return frozenset(self.strategies)

    def supports(self, engine: Engine) -> bool:
        return engine in self.strategies

    def load_strategy(self, engine: Engine) -> EngineStrategy:
        if engine not in self.strategies:
            raise EngineNotSupportedError(f"no {engine.value} engine", context={"tool": self.name})
        return self.strategies[engine]


def tool(
    name: str,
    *,
    marker: str | None = None,
    params: tuple[Param, ...] = (),
    raises: BaseException | None = None,
    hook: Callable[[OutputTarget], None] | None = None,
    suffix: str = ".pdf",
) -> FakeSpec:
    """One local-only fake tool, ready to be looked up by a router."""
    strategy = FakeStrategy(marker=marker or name, raises=raises, hook=hook)
    return FakeSpec(
        name=name,
        strategies={Engine.LOCAL: strategy},
        params=params,
        default_suffix=suffix,
    )


def router_for(*specs: FakeSpec) -> EngineRouter:
    """A real :class:`EngineRouter` whose registry is the fakes given.

    ``lookup`` is injectable precisely so this is possible — the router under
    test is the production one, and only what it resolves is substituted.
    """
    table = {spec.name: spec for spec in specs}

    def lookup(name: str) -> Any:
        from docmax.core.errors import InvalidParameterError

        try:
            return table[name]
        except KeyError as exc:
            raise InvalidParameterError(
                f"Unknown tool: {name!r}",
                remedy="Run `tools` to see everything available.",
                context={"tool": name},
            ) from exc

    return EngineRouter(config=Config(), lookup=lookup)


def strategy_of(spec: FakeSpec) -> FakeStrategy:
    return spec.strategies[Engine.LOCAL]


def document(path: Path, text: str = "seed") -> Path:
    """A source file with known content, so the marker chain is checkable."""
    with atomic_write_to(path) as handle:
        handle.write(text.encode())
    return path


def atomic_write_to(path: Path) -> Any:
    """``atomic_write`` against a bare path, for building fixtures."""
    from docmax.core.models import OutputTarget

    return atomic_write(OutputTarget(destination=path, force=True))


def markers(path: Path) -> list[str]:
    """The stage markers a produced document accumulated, in order."""
    return path.read_text(encoding="utf-8").split("|")[1:]


class RecordingProgress:
    """A ``ProgressSink`` that remembers every description it was given."""

    def __init__(self) -> None:
        self.descriptions: list[str] = []
        self.advances = 0
        self.finishes = 0

    def start(self, description: str, *, total: int | None = None) -> None:
        self.descriptions.append(description)

    def advance(self, amount: int = 1) -> None:
        self.advances += amount

    def finish(self) -> None:
        self.finishes += 1


ANGLE = Param(name="by", description="degrees", type_="int", default=90)
REQUIRED_TO = Param(name="to", description="target format", type_="str", required=True)
CHOICE = Param(
    name="preset",
    description="quality",
    type_="str",
    default="ebook",
    choices=("screen", "ebook", "printer"),
)

__all__ = [
    "ANGLE",
    "CHOICE",
    "REQUIRED_TO",
    "FakeSpec",
    "FakeStrategy",
    "RecordingProgress",
    "document",
    "markers",
    "router_for",
    "strategy_of",
    "tool",
]
