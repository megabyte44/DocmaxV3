"""The router: which engine runs, and what stands between a document and the wire.

Two clusters carry the weight.

**Resolution**, because it is the part of the system users most often need to
understand and can least easily observe — and because getting it wrong either
uploads something it should not, or refuses to run when it could.

**The consent gate**, because every route to the cloud passes through one
checkpoint and the tests are what make that claim true rather than merely
stated. `offline` beating an explicit `--engine cloud` is asserted directly.

Every strategy here is a fake. The router must never know how a document is
processed, so a router test that needed pypdf would be evidence of a design
failure rather than a thorough test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    ConsentRequiredError,
    CorruptDocumentError,
    EngineNotSupportedError,
    InternalError,
    NoEngineAvailableError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget, ToolResult
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import Param, ToolSpec
from docmax.core.router import EngineRouter, Routing

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.protocols import EngineStrategy, ProgressSink

ENDPOINT = "https://api.example.com"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeStrategy:
    """A strategy that records what it was handed. Satisfies EngineStrategy structurally."""

    engine: Engine = Engine.LOCAL
    available: bool = True
    reason: str | None = None
    raises: BaseException | None = None
    duration_ms: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.available

    def unavailable_reason(self) -> str | None:
        return None if self.available else (self.reason or "unavailable")

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
            engine_used=self.engine,
            duration_ms=self.duration_ms,
        )


@dataclass
class FakeSpec:
    """A ToolSpec stand-in that hands back fakes instead of importing modules."""

    name: str = "widget"
    strategies: dict[Engine, FakeStrategy] = field(default_factory=dict)
    default_suffix: str = ".pdf"
    produces_directory: bool = False
    loads: list[Engine] = field(default_factory=list)

    @property
    def supported_engines(self) -> frozenset[Engine]:
        return frozenset(self.strategies)

    def supports(self, engine: Engine) -> bool:
        return engine in self.strategies

    def load_strategy(self, engine: Engine) -> EngineStrategy:
        self.loads.append(engine)
        if engine not in self.strategies:
            raise EngineNotSupportedError(f"no {engine.value} engine", context={"tool": self.name})
        return self.strategies[engine]


class FakeConsent:
    """Stands in for ConsentStore. Only `has` is reached from the router."""

    def __init__(self, *granted: str) -> None:
        self.granted = set(granted)
        self.asked: list[str] = []

    def has(self, tool: str) -> bool:
        self.asked.append(tool)
        return tool in self.granted


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append("start")

    def advance(self, amount: int = 1) -> None:
        self.events.append("advance")

    def finish(self) -> None:
        self.events.append("finish")


def make_router(
    spec: FakeSpec,
    *,
    config: Config | None = None,
    consent: FakeConsent | None = None,
) -> EngineRouter:
    return EngineRouter(
        config=config or Config(cloud_endpoint=ENDPOINT),
        consent=consent,  # type: ignore[arg-type]
        lookup=lambda name: spec,  # type: ignore[arg-type,return-value]
    )


def local_only(**kwargs: Any) -> FakeSpec:
    return FakeSpec(strategies={Engine.LOCAL: FakeStrategy(Engine.LOCAL, **kwargs)})


def dual(*, local_available: bool = True, local_reason: str | None = None) -> FakeSpec:
    return FakeSpec(
        strategies={
            Engine.LOCAL: FakeStrategy(
                Engine.LOCAL, available=local_available, reason=local_reason
            ),
            Engine.CLOUD: FakeStrategy(Engine.CLOUD),
        }
    )


@pytest.fixture
def target(tmp_path: Path) -> OutputTarget:
    return OutputTarget(destination=tmp_path / "out.pdf", force=True)


@pytest.fixture
def document(tmp_path: Path) -> DocumentRef:
    path = tmp_path / "in.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return DocumentRef.from_path(path)


# ---------------------------------------------------------------------------
# Resolution — auto
# ---------------------------------------------------------------------------


def test_auto_prefers_local_when_it_can_run() -> None:
    """Local first is the privacy default, not a performance judgement."""
    routing = make_router(dual()).resolve("widget")

    assert routing.engine is Engine.LOCAL
    assert "local" in routing.reason


def test_auto_falls_back_to_cloud_when_local_cannot_run() -> None:
    spec = dual(local_available=False, local_reason="tesseract is not installed")
    router = make_router(spec, consent=FakeConsent("widget"))

    routing = router.resolve("widget")

    assert routing.engine is Engine.CLOUD
    assert "tesseract is not installed" in routing.reason


def test_auto_on_a_local_only_tool_names_both_halves(tmp_path: Path) -> None:
    """ "It didn't work" is not a diagnosis; the message says what and why."""
    spec = local_only(available=False, reason="pypdf is not installed")

    with pytest.raises(NoEngineAvailableError) as caught:
        make_router(spec).resolve("widget")

    message = str(caught.value)
    assert "pypdf is not installed" in message
    assert "no cloud engine" in message


def test_a_tool_with_no_local_engine_still_routes_to_cloud() -> None:
    spec = FakeSpec(strategies={Engine.CLOUD: FakeStrategy(Engine.CLOUD)})

    routing = make_router(spec, consent=FakeConsent("widget")).resolve("widget")

    assert routing.engine is Engine.CLOUD


# ---------------------------------------------------------------------------
# Resolution — precedence
# ---------------------------------------------------------------------------


def test_an_explicit_argument_beats_configuration() -> None:
    config = Config(cloud_endpoint=ENDPOINT, tool_engines={"widget": Engine.CLOUD})
    router = make_router(dual(), config=config)

    assert router.resolve("widget", requested=Engine.LOCAL).engine is Engine.LOCAL


def test_per_tool_configuration_beats_the_global_default() -> None:
    config = Config(
        cloud_endpoint=ENDPOINT,
        default_engine=Engine.LOCAL,
        tool_engines={"widget": Engine.CLOUD},
    )
    router = make_router(dual(), config=config, consent=FakeConsent("widget"))

    assert router.resolve("widget").engine is Engine.CLOUD


def test_the_global_default_beats_auto() -> None:
    """With local available, auto would pick local; the default says otherwise."""
    config = Config(cloud_endpoint=ENDPOINT, default_engine=Engine.CLOUD)
    router = make_router(dual(), config=config, consent=FakeConsent("widget"))

    assert router.resolve("widget").engine is Engine.CLOUD


def test_requesting_an_engine_the_tool_lacks_is_refused() -> None:
    with pytest.raises(EngineNotSupportedError) as caught:
        make_router(local_only()).resolve("widget", requested=Engine.CLOUD)

    assert "local" in (caught.value.remedy or "")


def test_requesting_local_when_it_cannot_run_is_refused() -> None:
    """An explicit choice is honoured as a choice, not silently re-routed."""
    spec = dual(local_available=False, local_reason="pypdf is not installed")

    with pytest.raises(NoEngineAvailableError) as caught:
        make_router(spec, consent=FakeConsent("widget")).resolve("widget", requested=Engine.LOCAL)

    assert "pypdf is not installed" in str(caught.value)


# ---------------------------------------------------------------------------
# The consent gate — the checkpoint before any upload
# ---------------------------------------------------------------------------


def test_cloud_without_consent_is_refused() -> None:
    with pytest.raises(ConsentRequiredError) as caught:
        make_router(dual()).resolve("widget", requested=Engine.CLOUD)

    assert caught.value.tool == "widget"
    assert ENDPOINT in str(caught.value), "the prompt must say where the document goes"


def test_cloud_with_consent_is_permitted() -> None:
    router = make_router(dual(), consent=FakeConsent("widget"))

    assert router.resolve("widget", requested=Engine.CLOUD).engine is Engine.CLOUD


def test_consent_is_per_tool() -> None:
    """Agreeing for one tool must not authorise another."""
    router = make_router(dual(), consent=FakeConsent("something-else"))

    with pytest.raises(ConsentRequiredError):
        router.resolve("widget", requested=Engine.CLOUD)


def test_no_consent_store_at_all_means_no_consent() -> None:
    """A caller that forgot to supply one must not thereby gain permission."""
    with pytest.raises(ConsentRequiredError):
        make_router(dual(), consent=None).resolve("widget", requested=Engine.CLOUD)


def test_the_automatic_fallback_also_requires_consent() -> None:
    """The branch that would otherwise upload quietly."""
    spec = dual(local_available=False, local_reason="not installed")

    with pytest.raises(ConsentRequiredError):
        make_router(spec, consent=None).resolve("widget")


def test_offline_beats_an_explicit_cloud_request() -> None:
    """The flag exists to be un-overridable; an argument that defeats it is decoration."""
    config = Config(cloud_endpoint=ENDPOINT, offline=True)
    router = make_router(dual(), config=config, consent=FakeConsent("widget"))

    with pytest.raises(NoEngineAvailableError) as caught:
        router.resolve("widget", requested=Engine.CLOUD)

    assert "offline" in str(caught.value)


def test_offline_is_checked_before_consent() -> None:
    """Offline is a policy, not a question — it must not surface as a prompt."""
    config = Config(cloud_endpoint=ENDPOINT, offline=True)

    with pytest.raises(NoEngineAvailableError):
        make_router(dual(), config=config, consent=None).resolve("widget", requested=Engine.CLOUD)


def test_offline_leaves_local_working() -> None:
    config = Config(cloud_endpoint=ENDPOINT, offline=True)

    assert make_router(dual(), config=config).resolve("widget").engine is Engine.LOCAL


def test_offline_with_no_local_engine_names_offline_as_the_reason() -> None:
    spec = dual(local_available=False, local_reason="not installed")
    config = Config(cloud_endpoint=ENDPOINT, offline=True)

    with pytest.raises(NoEngineAvailableError) as caught:
        make_router(spec, config=config, consent=FakeConsent("widget")).resolve("widget")

    assert "offline mode is on" in str(caught.value)


# ---------------------------------------------------------------------------
# Execution — pass-through
# ---------------------------------------------------------------------------


def test_run_passes_documents_and_target_through(
    target: OutputTarget, document: DocumentRef
) -> None:
    spec = dual()
    result = make_router(spec).run("widget", [document], target)

    call = spec.strategies[Engine.LOCAL].calls[0]
    assert call["docs"] == [document]
    assert call["target"] is target
    assert result.outputs == (target.destination,)
    assert result.engine_used is Engine.LOCAL


def test_run_passes_parameters_through(target: OutputTarget) -> None:
    spec = dual()
    make_router(spec).run("widget", [], target, outline=True, quality=90)

    assert spec.strategies[Engine.LOCAL].calls[0]["params"] == {"outline": True, "quality": 90}


def test_run_passes_the_progress_sink_through(target: OutputTarget) -> None:
    spec = dual()
    progress = RecordingProgress()

    make_router(spec).run("widget", [], target, progress=progress)

    assert spec.strategies[Engine.LOCAL].calls[0]["progress"] is progress


def test_run_supplies_the_null_sink_when_the_caller_has_none(target: OutputTarget) -> None:
    """So no engine ever needs `if progress is not None`."""
    spec = dual()
    make_router(spec).run("widget", [], target)

    assert spec.strategies[Engine.LOCAL].calls[0]["progress"] is NULL_PROGRESS


def test_progress_is_finished_even_when_the_strategy_fails(target: OutputTarget) -> None:
    """A live progress region must not be left open by a failure."""
    spec = dual()
    spec.strategies[Engine.LOCAL].raises = CorruptDocumentError("bad pdf")
    progress = RecordingProgress()

    with pytest.raises(CorruptDocumentError):
        make_router(spec).run("widget", [], target, progress=progress)

    assert progress.events == ["finish"]


def test_run_passes_the_cancellation_token_through(target: OutputTarget) -> None:
    spec = dual()
    token = CancellationToken()

    make_router(spec).run("widget", [], target, cancellation=token)

    assert spec.strategies[Engine.LOCAL].calls[0]["cancellation"] is token


def test_run_supplies_the_shared_token_when_the_caller_has_none(target: OutputTarget) -> None:
    spec = dual()
    make_router(spec).run("widget", [], target)

    assert spec.strategies[Engine.LOCAL].calls[0]["cancellation"] is NEVER_CANCELLED


def test_an_already_cancelled_run_never_reaches_the_strategy(target: OutputTarget) -> None:
    """Checked before resolution, so a cancelled batch stops without loading anything."""
    spec = dual()
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError) as caught:
        make_router(spec).run("widget", [], target, cancellation=token)

    assert "widget" in str(caught.value)
    assert spec.strategies[Engine.LOCAL].calls == []
    assert spec.loads == [], "not even the strategy module was loaded"


# ---------------------------------------------------------------------------
# Execution — results and errors
# ---------------------------------------------------------------------------


def test_the_router_times_the_operation(target: OutputTarget) -> None:
    result = make_router(dual()).run("widget", [], target)

    assert result.duration_ms >= 0


def test_a_strategys_own_timing_is_preserved(target: OutputTarget) -> None:
    """It measured itself more precisely than wall-clock around the call."""
    spec = dual()
    spec.strategies[Engine.LOCAL].duration_ms = 4321

    assert make_router(spec).run("widget", [], target).duration_ms == 4321


def test_a_typed_error_propagates_unchanged(target: OutputTarget) -> None:
    """It already carries a code and a remedy; wrapping it would lose both."""
    spec = dual()
    original = CorruptDocumentError("the file is damaged")
    spec.strategies[Engine.LOCAL].raises = original

    with pytest.raises(CorruptDocumentError) as caught:
        make_router(spec).run("widget", [], target)

    assert caught.value is original


def test_an_untyped_error_becomes_an_internal_error(target: OutputTarget) -> None:
    """The traceback boundary: a bug in a tool must not reach a user as a stack trace."""
    spec = dual()
    spec.strategies[Engine.LOCAL].raises = ValueError("an unanticipated bug")

    with pytest.raises(InternalError) as caught:
        make_router(spec).run("widget", [], target)

    assert "an unanticipated bug" in str(caught.value)
    assert caught.value.user_fixable is False
    assert isinstance(caught.value.__cause__, ValueError), "the original is kept for a bug report"


def test_a_keyboard_interrupt_is_not_swallowed(target: OutputTarget) -> None:
    """Ctrl-C is not a tool bug and must not be reported as one."""
    spec = dual()
    spec.strategies[Engine.LOCAL].raises = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        make_router(spec).run("widget", [], target)


# ---------------------------------------------------------------------------
# Dry runs and target resolution
# ---------------------------------------------------------------------------


def test_a_dry_run_resolves_without_running(target: OutputTarget) -> None:
    spec = dual()

    result = make_router(spec).run("widget", [], target, dry_run=True)

    assert result.details["dry_run"] is True
    assert result.engine_used is Engine.LOCAL
    assert result.outputs == ()
    assert spec.strategies[Engine.LOCAL].calls == []


def test_a_dry_run_explains_its_choice(target: OutputTarget) -> None:
    result = make_router(dual()).run("widget", [], target, dry_run=True)

    assert "local" in result.details["reason"]
    assert str(target.destination) in result.details["destination"]


def test_a_dry_run_still_refuses_what_it_could_not_do(target: OutputTarget) -> None:
    """Otherwise --dry-run would report success for a run that cannot happen."""
    with pytest.raises(ConsentRequiredError):
        make_router(dual()).run("widget", [], target, requested=Engine.CLOUD, dry_run=True)


def test_target_for_uses_the_tools_default_suffix(tmp_path: Path) -> None:
    """`.pdf` versus `.txt` is the tool's business, not the interface's."""
    source = tmp_path / "scan.tiff"
    source.write_bytes(b"x")
    spec = dual()
    spec.default_suffix = ".pdf"

    target = make_router(spec).target_for("widget", [DocumentRef.from_path(source)])

    assert target.destination == tmp_path / "scan.pdf"


def test_target_for_appends_the_default_suffix_to_an_extensionless_request(
    tmp_path: Path,
) -> None:
    """Requirement 5: generic through the router for any tool, not a `merge`
    special case — ``widget`` is the router test suite's own fake spec, with
    nothing merge-shaped about it."""
    source = tmp_path / "in.pdf"
    source.write_bytes(b"x")
    spec = dual()
    spec.default_suffix = ".pdf"

    target = make_router(spec).target_for(
        "widget", [DocumentRef.from_path(source)], requested=str(tmp_path / "out")
    )

    assert target.destination == tmp_path / "out.pdf"


def test_target_for_leaves_an_extensioned_request_alone(tmp_path: Path) -> None:
    """The other half: a request that already names an extension — right or
    wrong for the tool — passes through the router unchanged, same as it
    always has."""
    source = tmp_path / "in.pdf"
    source.write_bytes(b"x")
    spec = dual()
    spec.default_suffix = ".pdf"

    target = make_router(spec).target_for(
        "widget", [DocumentRef.from_path(source)], requested=str(tmp_path / "out.pdf")
    )

    assert target.destination == tmp_path / "out.pdf"


def test_target_for_leaves_a_directory_shaped_output_untouched(tmp_path: Path) -> None:
    """The router forwards `produces_directory` from the looked-up spec, the
    same way it already forwards `default_suffix` — `widget`, not `split` or
    `to-images` by name, since the router does not know either exists."""
    source = tmp_path / "in.pdf"
    source.write_bytes(b"x")
    spec = dual()
    spec.default_suffix = ".pdf"
    spec.produces_directory = True

    target = make_router(spec).target_for(
        "widget", [DocumentRef.from_path(source)], requested=str(tmp_path / "parts")
    )

    assert target.destination == tmp_path / "parts"


def test_target_for_still_refuses_an_in_place_overwrite(tmp_path: Path) -> None:
    """The router must not become a way around OutputTarget's checks."""
    from docmax.core.errors import InPlaceOverwriteError

    source = tmp_path / "doc.pdf"
    source.write_bytes(b"x")
    document = DocumentRef.from_path(source)

    with pytest.raises(InPlaceOverwriteError):
        make_router(dual()).target_for("widget", [document], requested=str(source))


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_the_router_holds_no_mutable_state() -> None:
    """One router serves a whole batch, or a whole server process."""
    from dataclasses import FrozenInstanceError

    router = EngineRouter()
    with pytest.raises(FrozenInstanceError):
        router.config = Config()  # type: ignore[misc]


def test_routing_reports_engine_and_reason() -> None:
    routing = Routing(Engine.LOCAL, "because")

    assert routing.engine is Engine.LOCAL
    assert routing.reason == "because"


def test_the_router_works_against_a_real_toolspec(tmp_path: Path) -> None:
    """The fakes above must not be hiding a mismatch with the actual registry type."""
    spec = ToolSpec(
        name="widget",
        summary="A real ToolSpec.",
        category="test",
        module="docmax.tools.nonexistent",
        supported_engines=frozenset({Engine.LOCAL}),
        params=(Param(name="outline", description="x", type_="bool"),),
        default_suffix=".out",
    )
    router = EngineRouter(config=Config(), lookup=lambda name: spec)

    source = tmp_path / "a.txt"
    source.write_bytes(b"x")
    target = router.target_for("widget", [DocumentRef.from_path(source)])

    assert target.destination == tmp_path / "a.out"
    # Resolution reaches load_strategy, which fails on the absent module —
    # proving the router uses the real ToolSpec API rather than a lookalike.
    with pytest.raises(InternalError):
        router.resolve("widget")
