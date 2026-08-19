"""The one path from "run this tool" to "here is the result".

Every interface calls this and nothing else: the CLI turns argv into a call
here, the server turns an HTTP request into the same call, and the TUI and the
MCP server will do likewise. That is the point — everything cross-cutting lives
here once, so no tool implements it twice and no interface implements it at all.

What the router owns:

* **Engine resolution.** Which of a tool's up-to-two strategies runs, and why.
* **Consent.** No document reaches a remote endpoint without a recorded
  agreement. This is the single checkpoint that makes that claim testable.
* **Cancellation and progress.** Passed down as the framework-independent
  contracts, substituted with the shared do-nothing constants when a caller has
  neither.
* **Timing**, so a `ToolResult` reports how long the whole operation took rather
  than how long one strategy thinks it took.
* **Dry runs**, answered without touching a strategy.
* **The traceback boundary.** Anything escaping a tool that is not a
  ``DocMaxError`` is wrapped in :class:`InternalError`, so no interface ever
  renders a stack trace for a condition we anticipated.

What the router does not own, and must never learn: how any operation works. It
cannot merge, compress, or OCR anything. It selects a strategy and calls it. If
this module ever imports ``pypdf``, something has gone badly wrong.

The resolution ladder, highest precedence first, is
``docs/architecture/overview.md``'s and is implemented in :meth:`EngineRouter.resolve`:

1. an explicit argument — ``--engine local``
2. per-tool configuration — ``[tools.ocr] engine = "cloud"``
3. the global default — ``engine = "local"``
4. ``auto``

with one rule above all of them: ``offline = true`` makes cloud unreachable
*regardless of flags*, because it exists for the person whose policy says
documents do not leave the building.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.config import Config
from docmax.core.errors import (
    ConsentRequiredError,
    DocMaxError,
    EngineNotSupportedError,
    InternalError,
    NoEngineAvailableError,
)
from docmax.core.models import Engine, OutputTarget, ToolResult
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.consent import ConsentStore
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import EngineStrategy, ProgressSink
    from docmax.core.registry import ToolSpec


@dataclass(frozen=True, slots=True)
class Routing:
    """Which engine will run, and the reasoning that chose it.

    The ``reason`` is not decoration. It is what a `--dry-run` prints, what a
    future `explain` command would show, and what turns "no engine available"
    from an assertion into an explanation. Resolution is the part of this system
    users most often need to understand and can least easily observe.
    """

    engine: Engine
    reason: str


@dataclass(frozen=True, slots=True)
class EngineRouter:
    """Resolves an engine for a tool and runs it.

    Holds no mutable state, so one router can serve a whole batch — or a whole
    server process — without accumulating anything between calls.
    """

    config: Config = field(default_factory=Config)
    #: Where recorded agreements live. ``None`` means *nothing is consented*,
    #: which is the safe reading: a caller that forgot to supply a store must
    #: not thereby gain permission to upload.
    consent: ConsentStore | None = None
    #: Injectable so tests need no real tool packages on disk. Production
    #: callers leave it alone and get the real registry.
    lookup: Callable[[str], ToolSpec] = get_tool

    # -- resolution ---------------------------------------------------------

    def resolve(self, tool_name: str, *, requested: Engine | None = None) -> Routing:
        """Decide which engine runs, or raise explaining why none can.

        Raises :class:`EngineNotSupportedError` if the tool has no such engine,
        :class:`ConsentRequiredError` if cloud is the answer but nobody agreed,
        and :class:`NoEngineAvailableError` when neither half can run — with both
        reasons in the message, because "it didn't work" is not a diagnosis.
        """
        spec = self.lookup(tool_name)
        wanted = requested if requested is not None else self.config.engine_for(tool_name)

        if wanted is Engine.AUTO:
            return self._resolve_auto(spec)

        if not spec.supports(wanted):
            raise EngineNotSupportedError(
                f"The {spec.name!r} tool has no {wanted.value} engine.",
                remedy=self._supported_engines_remedy(spec),
                context={"tool": spec.name, "engine": wanted.value},
            )

        if wanted is Engine.CLOUD:
            self._require_cloud_is_permitted(spec, explicit=True)
            return Routing(Engine.CLOUD, "the cloud engine was requested explicitly")

        strategy = spec.load_strategy(Engine.LOCAL)
        if not strategy.is_available():
            raise NoEngineAvailableError(
                f"The local engine for {spec.name!r} cannot run: "
                f"{strategy.unavailable_reason() or 'it is unavailable'}",
                remedy=self._local_unavailable_remedy(spec),
                context={"tool": spec.name, "engine": "local"},
            )
        return Routing(Engine.LOCAL, "the local engine was requested explicitly")

    def _resolve_auto(self, spec: ToolSpec) -> Routing:
        """``auto``: prefer local, fall back to cloud only where that is allowed.

        Local first is not a performance judgement — it is the privacy default.
        A tool that can run here runs here, and the cloud is reached for only
        when the local half genuinely cannot run.
        """
        local_reason: str | None = None
        if spec.supports(Engine.LOCAL):
            strategy = spec.load_strategy(Engine.LOCAL)
            if strategy.is_available():
                return Routing(Engine.LOCAL, "the local engine is available")
            local_reason = strategy.unavailable_reason() or "the local engine is unavailable"
        else:
            local_reason = f"{spec.name!r} has no local engine"

        if not spec.supports(Engine.CLOUD):
            raise NoEngineAvailableError(
                f"Cannot run {spec.name!r}: {local_reason}, and it has no cloud engine.",
                remedy=self._local_unavailable_remedy(spec),
                context={"tool": spec.name, "local_reason": local_reason},
            )

        cloud_reason = self._cloud_unavailable_reason(spec)
        if cloud_reason is not None:
            raise NoEngineAvailableError(
                f"Cannot run {spec.name!r}: {local_reason}, and {cloud_reason}.",
                remedy=self._local_unavailable_remedy(spec),
                context={
                    "tool": spec.name,
                    "local_reason": local_reason,
                    "cloud_reason": cloud_reason,
                },
            )

        # Falling back to cloud means uploading. Consent is not optional here,
        # and this is the branch that would otherwise do it quietly.
        self._require_cloud_is_permitted(spec, explicit=False)
        return Routing(Engine.CLOUD, f"{local_reason}, so the cloud engine was chosen")

    def _cloud_unavailable_reason(self, spec: ToolSpec) -> str | None:
        """Why the cloud engine cannot be used, ignoring consent. ``None`` if it can."""
        if self.config.offline:
            return "offline mode is on"
        strategy = spec.load_strategy(Engine.CLOUD)
        if not strategy.is_available():
            return strategy.unavailable_reason() or "the cloud engine is unavailable"
        return None

    def _require_cloud_is_permitted(self, spec: ToolSpec, *, explicit: bool) -> None:
        """Gate every route to the cloud. The one checkpoint before an upload.

        ``offline`` is checked first and beats an explicit request, which is the
        whole reason the flag exists. Consent is checked second, and its absence
        is a question for the user rather than a failure — the interface turns
        :class:`ConsentRequiredError` into a prompt.
        """
        if self.config.offline:
            raise NoEngineAvailableError(
                f"Cannot run {spec.name!r} in the cloud: offline mode is on.",
                remedy="Set offline = false in your config, or install the local engine.",
                context={"tool": spec.name, "offline": True},
            )

        if explicit:
            strategy = spec.load_strategy(Engine.CLOUD)
            if not strategy.is_available():
                raise NoEngineAvailableError(
                    f"The cloud engine for {spec.name!r} cannot run: "
                    f"{strategy.unavailable_reason() or 'it is unavailable'}",
                    context={"tool": spec.name, "engine": "cloud"},
                )

        if self.consent is None or not self.consent.has(spec.name):
            raise ConsentRequiredError(
                f"Running {spec.name!r} in the cloud uploads your document to "
                f"{self.config.cloud_endpoint}.",
                tool=spec.name,
                remedy="Agree once and it is remembered, or run locally instead.",
                context={"endpoint": self.config.cloud_endpoint},
            )

    @staticmethod
    def _supported_engines_remedy(spec: ToolSpec) -> str:
        available = ", ".join(sorted(engine.value for engine in spec.supported_engines))
        return f"This tool supports: {available}."

    @staticmethod
    def _local_unavailable_remedy(spec: ToolSpec) -> str:
        if spec.supports(Engine.CLOUD):
            return "Install the local engine's dependencies, or use the cloud engine."
        return "Install the local engine's dependencies."

    # -- targets ------------------------------------------------------------

    def target_for(
        self,
        tool_name: str,
        docs: Sequence[DocumentRef],
        *,
        requested: str | None = None,
        force: bool = False,
    ) -> OutputTarget:
        """Resolve a destination using the tool's own default extension.

        Here rather than in each interface so that the in-place and
        already-exists checks cannot be skipped by a caller who did not know
        about them — and so ``.pdf`` versus ``.txt`` is the tool's business
        rather than the CLI's.
        """
        spec = self.lookup(tool_name)
        return OutputTarget.resolve(
            inputs=docs,
            requested=requested,
            default_suffix=spec.default_suffix,
            force=force,
        )

    # -- execution ----------------------------------------------------------

    def run(
        self,
        tool_name: str,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        requested: Engine | None = None,
        progress: ProgressSink = NULL_PROGRESS,
        cancellation: CancellationToken = NEVER_CANCELLED,
        dry_run: bool = False,
        **params: Any,
    ) -> ToolResult:
        """Resolve an engine and run it. The single entry point every interface uses.

        ``progress`` and ``cancellation`` default to the shared do-nothing
        constants, so a caller with neither still hands the strategy a real
        object and no engine needs a ``None`` check.
        """
        cancellation.raise_if_cancelled(operation=tool_name)
        routing = self.resolve(tool_name, requested=requested)

        if dry_run:
            return ToolResult(
                outputs=(),
                engine_used=routing.engine,
                details={
                    "dry_run": True,
                    "tool": tool_name,
                    "reason": routing.reason,
                    "destination": str(target.destination),
                },
            )

        spec = self.lookup(tool_name)
        strategy: EngineStrategy = spec.load_strategy(routing.engine)

        started = time.monotonic()
        try:
            result = strategy.run(
                docs,
                target,
                progress=progress,
                cancellation=cancellation,
                **params,
            )
        except DocMaxError:
            # Already typed, already carries a remedy. Nothing to add.
            raise
        except Exception as exc:
            # Anything else escaping a tool is a bug in that tool. Wrapping it
            # here is what keeps the promise that no interface renders a
            # traceback for an anticipated condition — and an unanticipated one
            # becomes a bug report rather than a wall of text.
            raise InternalError(
                f"The {routing.engine.value} engine for {tool_name!r} failed unexpectedly: {exc}",
                context={"tool": tool_name, "engine": routing.engine.value},
            ) from exc
        finally:
            progress.finish()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if result.duration_ms:
            # The strategy timed itself more precisely than we can; leave it.
            return result
        return replace(result, duration_ms=elapsed_ms)


__all__ = ["EngineRouter", "Routing"]
