"""The MCP interface, driven through a real protocol session.

These tests speak the protocol rather than calling Python functions: a client
session is connected to the server over an in-memory transport, performs the
handshake, lists tools and calls them. Testing the handlers directly would prove
the handlers work and say nothing about whether a client can reach them, which
is the half that actually ships.

The security assertions are the ones to read first. An MCP client is a program
acting on someone's behalf and its instructions may have come from a document it
was asked to summarise, so "a path outside the roots is refused" is not a
usability nicety — it is the milestone's one genuinely new piece of design.
See ADR 0029.
"""

from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from docmax.core.config import Config
from docmax.core.errors import CorruptDocumentError, EncryptedDocumentError
from docmax.core.registry import iter_tools
from docmax.mcp import policy as policy_module
from docmax.mcp.policy import Policy
from docmax.mcp.server import build_server
from tests.paths import SRC, relative
from tests.unit.m9_support import document, markers, router_for, strategy_of, tool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from docmax.core.router import EngineRouter

MCP_SOURCE = SRC / "mcp"

#: The sentinel `test_cli_json.py` uses: a key nobody would type, so finding it
#: anywhere is unambiguous.
SENTINEL_KEY = "dmx_sentinel_MUST_NEVER_APPEAR_9f3a"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def fake_router(*names: str, **kwargs: Any) -> EngineRouter:
    specs = [tool(name, **kwargs) for name in names] if names else [tool("a")]
    return router_for(*specs)


@asynccontextmanager
async def session_for(policy: Policy, router: EngineRouter) -> AsyncIterator[ClientSession]:
    """A client session connected to a real server over in-memory streams.

    The server runs in its own task, so what these tests exercise is the whole
    path a client takes: handshake, `tools/list`, `tools/call`, and the JSON that
    comes back — not the handler functions in isolation.
    """
    server = build_server(policy, router)
    async with (
        create_client_server_memory_streams() as ((cr, cw), (sr, sw)),
        anyio.create_task_group() as group,
    ):

        async def _serve() -> None:
            await server.run(sr, sw, server.create_initialization_options())

        group.start_soon(_serve)
        async with ClientSession(cr, cw) as session:
            yield session
        group.cancel_scope.cancel()


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A root the server may touch, and a directory outside it that it may not."""
    inside = tmp_path / "allowed"
    outside = tmp_path / "secret"
    inside.mkdir()
    outside.mkdir()
    document(inside / "report.pdf", text="report")
    document(outside / "private.pdf", text="private")
    return inside, outside


def structured(result: types.CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None, "no structured content on the result"
    return dict(result.structured_content)


# ---------------------------------------------------------------------------
# Handshake and discovery
# ---------------------------------------------------------------------------


def test_the_handshake_completes_and_names_the_server(tmp_path: Path) -> None:
    async def scenario() -> types.InitializeResult:
        async with session_for(Policy.build([tmp_path]), fake_router()) as session:
            return await session.initialize()

    result = anyio.run(scenario)

    assert result.server_info.name == "docmax"
    assert result.capabilities.tools is not None, "the server must advertise tools"


def test_the_instructions_state_the_policy(tmp_path: Path) -> None:
    """A client should be able to see the boundary without hitting it."""

    async def scenario() -> types.InitializeResult:
        async with session_for(Policy.build([tmp_path]), fake_router()) as session:
            return await session.initialize()

    instructions = anyio.run(scenario).instructions or ""

    assert str(tmp_path.resolve()) in instructions
    assert "Cloud engines: disabled" in instructions


def test_tool_discovery_returns_every_registered_tool(tmp_path: Path) -> None:
    """`list_tools` is the registry rendered, so a new tool appears for free."""

    async def scenario() -> list[types.Tool]:
        policy = Policy.build([tmp_path])
        async with session_for(policy, fake_router("a")) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    offered = {item.name for item in anyio.run(scenario)}

    assert offered == {spec.name for spec in iter_tools()}


def test_every_offered_tool_carries_a_schema(tmp_path: Path) -> None:
    async def scenario() -> list[types.Tool]:
        async with session_for(Policy.build([tmp_path]), fake_router("a")) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    for item in anyio.run(scenario):
        assert item.input_schema["type"] == "object"
        assert "inputs" in item.input_schema["properties"]
        assert item.description, f"{item.name} has no description"


def test_the_runners_are_not_exposed(tmp_path: Path) -> None:
    """ADR 0028's finding, held rather than left to memory.

    `pipeline`, `batch` and `watch` are not registered tools — they are
    `docmax.runners`, composition over the registry. Offering them would mean a
    hand-written list, which ADR 0021 and CLAUDE.md rule 1 forbid. Plan 05
    proposed one; this is where that contradiction is recorded in code.
    """

    async def scenario() -> list[types.Tool]:
        async with session_for(Policy.build([tmp_path]), fake_router("a")) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    offered = {item.name for item in anyio.run(scenario)}

    assert not offered & {"pipeline", "batch", "watch"}


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def test_a_tool_call_writes_its_destination(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace
    router = fake_router("a")

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")},
            )

    result = anyio.run(scenario)

    assert result.is_error is not True, result.content
    assert (inside / "out.pdf").exists()
    assert markers(inside / "out.pdf") == ["a"]
    assert structured(result)["ok"] is True


def test_the_success_envelope_is_the_m6_shape(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace
    router = fake_router("a")

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")},
            )

    body = structured(anyio.run(scenario))

    assert body["ok"] is True
    assert body["result"]["tool"] == "a"
    assert body["result"]["engine"] == "local"
    assert body["result"]["outputs"] == [str(inside / "out.pdf")]
    assert "details" in body["result"]


def test_parameters_reach_the_tool(workspace: tuple[Path, Path]) -> None:
    from tests.unit.m9_support import ANGLE

    inside, _ = workspace
    spec = tool("rotate", params=(ANGLE,))
    router = router_for(spec)

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "rotate",
                {
                    "inputs": [str(inside / "report.pdf")],
                    "output": str(inside / "out.pdf"),
                    "by": 180,
                },
            )

    anyio.run(scenario)

    assert strategy_of(spec).calls[0]["params"] == {"by": 180}


def test_a_tool_that_reports_needs_no_output(workspace: tuple[Path, Path]) -> None:
    """`get-info` and friends are callable without inventing a destination.

    `ToolSpec` cannot say "I produce no output" — the first of the three seams —
    so the server finds out by running into a temporary directory. See ADR 0028.
    """
    from docmax.core.models import Engine, ToolResult

    inside, _ = workspace

    class Reporter:
        def is_available(self) -> bool:
            return True

        def unavailable_reason(self) -> str | None:
            return None

        def run(self, docs: Any, target: Any, **kwargs: Any) -> ToolResult:
            return ToolResult(outputs=(), engine_used=Engine.LOCAL, details={"pages": 3})

    from tests.unit.m9_support import FakeSpec

    spec = FakeSpec(name="get-facts", strategies={Engine.LOCAL: Reporter()})  # type: ignore[dict-item]
    router = router_for(spec)

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool("get-facts", {"inputs": [str(inside / "report.pdf")]})

    result = anyio.run(scenario)

    assert result.is_error is not True, result.content
    body = structured(result)
    assert body["result"]["outputs"] == []
    assert body["result"]["details"]["pages"] == 3


def test_a_tool_that_writes_and_gets_no_output_is_refused_having_written_nothing(
    workspace: tuple[Path, Path],
) -> None:
    inside, _ = workspace
    router = fake_router("a")

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool("a", {"inputs": [str(inside / "report.pdf")]})

    result = anyio.run(scenario)

    assert result.is_error is True
    assert structured(result)["error"]["code"] == "input.invalid_parameter"
    assert sorted(p.name for p in inside.iterdir()) == ["report.pdf"], "something was left behind"


# ---------------------------------------------------------------------------
# Errors — structured, never a traceback
# ---------------------------------------------------------------------------


def test_a_docmax_error_becomes_a_structured_result(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace
    router = router_for(tool("a", raises=CorruptDocumentError("this file is broken")))

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")},
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    error = structured(result)["error"]
    assert error["code"] == "input.corrupt"
    assert error["message"] == "this file is broken"


def test_an_error_carries_its_remedy(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace
    router = router_for(
        tool("a", raises=EncryptedDocumentError("locked", remedy="Unlock it with a password."))
    )

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")},
            )

    result = anyio.run(scenario)

    assert structured(result)["error"]["remedy"] == "Unlock it with a password."


def test_no_traceback_reaches_the_client(workspace: tuple[Path, Path]) -> None:
    """An unexpected failure is wrapped by the router; the stack never travels."""
    inside, _ = workspace
    router = router_for(tool("a", raises=RuntimeError("secret internal detail at line 42")))

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")},
            )

    result = anyio.run(scenario)
    payload = json.dumps(result.model_dump(mode="json"))

    assert result.is_error is True
    assert "Traceback" not in payload
    assert 'File "' not in payload
    assert structured(result)["error"]["code"] == "internal"


def test_an_unknown_tool_is_a_protocol_error(workspace: tuple[Path, Path]) -> None:
    """A tool `tools/list` never offered is a protocol fault, not a document one."""
    from mcp.shared.exceptions import MCPError

    inside, _ = workspace

    async def scenario() -> str:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            try:
                await session.call_tool("nonesuch", {"inputs": [str(inside / "report.pdf")]})
            except MCPError as exc:
                return f"{exc.error.code}:{exc.error.message}"
            return "no error raised"

    answer = anyio.run(scenario)

    assert answer.startswith(str(types.METHOD_NOT_FOUND))
    assert "nonesuch" in answer


def test_an_unknown_argument_is_refused(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {
                    "inputs": [str(inside / "report.pdf")],
                    "output": str(inside / "out.pdf"),
                    "nonsense": 1,
                },
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert "nonsense" in structured(result)["error"]["message"]


def test_missing_inputs_is_refused(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool("a", {"output": str(inside / "out.pdf")})

    result = anyio.run(scenario)

    assert result.is_error is True
    assert "inputs" in structured(result)["error"]["message"]


# ---------------------------------------------------------------------------
# Filesystem roots — the boundary
# ---------------------------------------------------------------------------


def test_an_input_outside_the_roots_is_refused(workspace: tuple[Path, Path]) -> None:
    inside, outside = workspace
    router = fake_router("a")

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(outside / "private.pdf")], "output": str(inside / "out.pdf")},
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert "outside every allowed root" in structured(result)["error"]["message"]
    assert strategy_of(router.lookup("a")).calls == [], "the tool ran anyway"  # type: ignore[arg-type]


def test_an_output_outside_the_roots_is_refused(workspace: tuple[Path, Path]) -> None:
    inside, outside = workspace

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(outside / "escaped.pdf")},
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert not (outside / "escaped.pdf").exists()


def test_path_traversal_is_refused(workspace: tuple[Path, Path]) -> None:
    """`..` and the roots are compared after resolution, which is the whole check."""
    inside, _ = workspace
    traversal = inside / ".." / "secret" / "private.pdf"

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool(
                "a", {"inputs": [str(traversal)], "output": str(inside / "out.pdf")}
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert "outside every allowed root" in structured(result)["error"]["message"]


def test_an_existing_output_is_never_overwritten(workspace: tuple[Path, Path]) -> None:
    """`force` is not a parameter, so an agent cannot destroy a file it did not create."""
    inside, _ = workspace
    document(inside / "out.pdf", text="precious")

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool(
                "a", {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")}
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert structured(result)["error"]["code"] == "output.exists"
    assert (inside / "out.pdf").read_text(encoding="utf-8") == "precious"


def test_an_output_that_is_an_input_is_refused(workspace: tuple[Path, Path]) -> None:
    inside, _ = workspace

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), fake_router("a")) as session:
            await session.initialize()
            return await session.call_tool(
                "a",
                {"inputs": [str(inside / "report.pdf")], "output": str(inside / "report.pdf")},
            )

    result = anyio.run(scenario)

    assert result.is_error is True
    assert structured(result)["error"]["code"] in {"output.in_place_overwrite", "output.exists"}
    assert (inside / "report.pdf").read_text(encoding="utf-8") == "report"


def test_no_force_parameter_is_offered(tmp_path: Path) -> None:
    async def scenario() -> list[types.Tool]:
        async with session_for(Policy.build([tmp_path]), fake_router("a")) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    for item in anyio.run(scenario):
        assert "force" not in item.input_schema["properties"], item.name


# ---------------------------------------------------------------------------
# Cloud, consent and credentials
# ---------------------------------------------------------------------------


def test_cloud_is_off_by_default() -> None:
    """The server forces offline unless the operator opted in."""
    configured = Policy.build([Path.cwd()]).configure(Config())

    assert configured.offline is True


def test_allow_cloud_declines_to_force_offline() -> None:
    configured = Policy.build([Path.cwd()], allow_cloud=True).configure(Config())

    assert configured.offline is False


def test_allow_cloud_cannot_defeat_a_configured_offline() -> None:
    """`offline` is one-way by design; a protocol flag must not clear a policy."""
    configured = Policy.build([Path.cwd()], allow_cloud=True).configure(Config(offline=True))

    assert configured.offline is True


def test_the_server_never_records_consent() -> None:
    """An agent may not agree to an upload on the user's behalf. ADR 0008/0029."""
    offences: list[str] = []

    for source in sorted(MCP_SOURCE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "record":
                offences.append(f"{relative(source)}:{node.lineno}")

    assert not offences, f"consent must never be granted by the MCP server: {offences}"


def test_no_credential_reaches_the_client(workspace: tuple[Path, Path]) -> None:
    """The `test_cli_json.py` sentinel, applied to an MCP payload."""
    from dataclasses import replace

    inside, _ = workspace
    base = router_for(tool("a"))
    router = replace(base, config=replace(base.config, api_key=SENTINEL_KEY))

    async def scenario() -> types.CallToolResult:
        async with session_for(Policy.build([inside]), router) as session:
            await session.initialize()
            return await session.call_tool(
                "a", {"inputs": [str(inside / "report.pdf")], "output": str(inside / "out.pdf")}
            )

    result = anyio.run(scenario)

    assert SENTINEL_KEY not in json.dumps(result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------


def test_the_mcp_package_names_no_tool() -> None:
    """ADR 0028: the tool surface is the registry, never a list in this package."""
    tool_names = {spec.name for spec in iter_tools()}
    offenders: list[str] = []

    for source in sorted(MCP_SOURCE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        offenders += [
            f"{relative(source)}:{node.lineno} — {node.value!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in tool_names
        ]

    assert not offenders, "the MCP server must not name a tool: " + "; ".join(offenders)


def test_mcp_imports_no_other_interface() -> None:
    forbidden = ("docmax.cli", "docmax.tui", "docmax.server")
    offenders: list[str] = []

    for source in sorted(MCP_SOURCE.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        offenders += [f"{source.name}: {name}" for name in forbidden if f"import {name}" in text]

    assert not offenders, offenders


def test_the_cli_reaches_only_the_mcp_entry_point() -> None:
    """The narrow half of ADR 0027, mirroring ADR 0020's for the TUI."""
    text = (SRC / "cli" / "main.py").read_text(encoding="utf-8")

    assert "from docmax.mcp import" in text
    for internal in ("docmax.mcp.server", "docmax.mcp.policy", "docmax.mcp.schema"):
        assert internal not in text, f"the CLI reaches into {internal}"


def test_there_is_one_router_call_site() -> None:
    """ADR 0029's boundary is only a property if nothing else can reach a tool."""
    calls: list[str] = []

    for source in sorted(MCP_SOURCE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_router"
            ):
                calls.append(f"{relative(source)}:{node.lineno}")

    assert len(calls) == 2, (
        "expected exactly the two `self._router.run(...)` calls in `_run` "
        f"(the given-output and staged-output branches), found: {calls}"
    )


def test_the_policy_module_imports_no_sdk() -> None:
    """The security half must be testable without the optional dependency."""
    text = Path(policy_module.__file__).read_text(encoding="utf-8")

    assert "import mcp" not in text
    assert "from mcp" not in text


def test_progress_is_the_null_sink() -> None:
    """ADR 0030's deliberate omission, held so it stays a decision."""
    text = (MCP_SOURCE / "server.py").read_text(encoding="utf-8")

    assert "NULL_PROGRESS" in text
    assert text.count("progress=NULL_PROGRESS") == 2


def test_the_mcp_package_is_covered_by_the_hygiene_suite() -> None:
    from tests.paths import LIBRARY_PACKAGES

    assert "mcp" in LIBRARY_PACKAGES
