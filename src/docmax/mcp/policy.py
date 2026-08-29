"""The boundary an MCP call crosses before anything runs.

Every other interface is driven by a person. An MCP client is a program acting
on someone's behalf, and its instructions may have come from a document it was
asked to read. That is a different threat model, and `docs/plans/05` named the
consequence plainly:

    Without this, a prompt-injected agent can ask DocMax to read anything the
    user can read.

So this module answers three questions before `EngineRouter` is reached: **may
this path be touched, may this run go to the cloud, and who is allowed to say
yes to that.** See ``docs/adr/0029-the-mcp-policy-boundary.md``.

**Nothing here imports the MCP SDK**, for the reason ``schema.py`` gives — the
security half of this interface is testable without a protocol session — and
nothing here writes, prints or exits. Core is unchanged by all of it: the root
rule is a property of *this interface's* callers, not of destinations in
general, and ADR 0029 records why it was deliberately not pushed down into
``OutputTarget``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from docmax.core.branding import CLI_NAME
from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from docmax.core.config import Config


@dataclass(frozen=True, slots=True)
class Policy:
    """What this server is allowed to do, decided once at startup.

    Immutable on purpose: a policy that could be widened by a request is not a
    policy. Everything a client sends is checked against this, never merged into
    it.
    """

    #: Directories the client may read from and write to. Never empty — the
    #: constructor below defaults it to the working directory rather than to
    #: "everywhere", because a default that is safe only once configured is not
    #: a safe default.
    roots: tuple[Path, ...]
    #: False unless the operator passed ``--allow-cloud``. Note this only
    #: *declines to force* offline; it cannot clear a configured one.
    allow_cloud: bool = False

    @classmethod
    def build(
        cls,
        roots: Sequence[Path] | None = None,
        *,
        allow_cloud: bool = False,
    ) -> Policy:
        """Resolve the roots, or fall back to the working directory."""
        chosen = list(roots) if roots else [Path.cwd()]
        resolved: list[Path] = []

        for root in chosen:
            candidate = Path(root).expanduser().resolve()
            if not candidate.is_dir():
                raise InvalidParameterError(
                    f"Not a directory, so it cannot be a root: {candidate}",
                    remedy="Pass --root with a directory that exists.",
                    context={"path": str(candidate)},
                )
            resolved.append(candidate)

        return cls(roots=tuple(resolved), allow_cloud=allow_cloud)

    def contains(self, path: Path) -> bool:
        """True when ``path`` resolves to somewhere inside a root.

        Resolution before comparison is the entire check: it is what collapses
        ``../../etc/passwd`` and a symlink pointing out of the tree into the same
        case. The M9 watcher's containment rule works the same way, for the same
        reason — see ADR 0026.
        """
        resolved = Path(path).expanduser().resolve()
        return any(resolved == root or resolved.is_relative_to(root) for root in self.roots)

    def check(self, path: Path, *, field: str) -> Path:
        """Return the resolved path, or refuse it naming the field it came from.

        The message names the offending path — which the caller already sent —
        and the roots, which the caller's own operator configured. Neither
        discloses anything the other end does not have.
        """
        resolved = Path(path).expanduser().resolve()
        if not self.contains(resolved):
            raise InvalidParameterError(
                f"{field!r} is outside every allowed root: {resolved}",
                remedy=(
                    "This server may only read and write inside: "
                    + ", ".join(str(root) for root in self.roots)
                    + f". Start {CLI_NAME} mcp with --root to widen it."
                ),
                context={"path": str(resolved), "field": field},
            )
        return resolved

    def check_all(self, paths: Iterable[Path], *, field: str) -> list[Path]:
        return [self.check(path, field=field) for path in paths]

    def configure(self, config: Config) -> Config:
        """Apply the cloud half of the policy to a loaded configuration.

        ``offline`` is one-way by design — Phase 3 made it so *"because the flag
        exists to be un-overridable by the command line"* — and that is exactly
        the property wanted here. Without ``--allow-cloud`` this forces offline
        on; **with** it, this forces nothing, so a user whose ``config.toml``
        says ``offline = true`` stays offline. A flag on a protocol server
        cannot defeat a policy the user wrote down.
        """
        if self.allow_cloud:
            return config
        return config.with_overrides(offline=True)

    def describe(self) -> str:
        """One line for the server's own instructions, so a client can see the rule."""
        where = ", ".join(str(root) for root in self.roots)
        cloud = "enabled" if self.allow_cloud else "disabled"
        return f"Reads and writes are restricted to: {where}. Cloud engines: {cloud}."


__all__ = ["Policy"]
