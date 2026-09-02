"""The remote MCP route — ownership, session binding, and the transport policy.

ADR 0035 is explicit that remote MCP reuses `docmax.server`'s existing
upload/job/auth machinery rather than building a second one, and that the
policy boundary this needs is per-key storage ownership rather than a port of
ADR 0029's local roots. These tests hold that: the security assertions are the
ones to read first, the same way `test_m10_mcp.py` reads for the local server.

Two things do the heavy lifting:

* the `client` fixture drives plain REST — ownership on `Storage`/`JobStore` is
  asserted through it, because the same checks protect every caller regardless
  of transport, and REST needs no ASGI lifespan.
* `mcp_session` speaks MCP-over-HTTP through an in-memory ASGI transport, the
  HTTP analogue of `mcp.shared.memory.create_client_server_memory_streams` the
  local server's tests use — there is no memory-stream helper for the
  streamable-HTTP transport, so an ASGI transport is the equivalent. Every
  scenario that reaches it is wrapped in `_LifespanManager`, driven by hand in
  the caller's own event loop: `StreamableHTTPSessionManager.run()` is only
  entered by the app's own `lifespan`, and neither a bare `TestClient(app)`
  (never entered) nor `TestClient`'s own portal (a *different* event loop than
  a plain `anyio.run` scenario) works here — a request to `/v1/mcp` with
  neither raises "Task group is not initialized" rather than a clean HTTP
  error, a real trap the first version of this file fell into.
"""

from __future__ import annotations

import ast
import contextlib
import sys
from typing import TYPE_CHECKING, Any

import anyio
import httpx2
import pytest

pytest.importorskip("fastapi", reason="the server extra is not installed")

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from docmax.core.models import Engine
from docmax.core.registry import iter_tools
from docmax.server.app import create_app
from docmax.server.config import ServerSettings
from docmax.server.identity import SqliteIdentityStore
from tests.paths import SRC, relative

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
pytest.importorskip("mcp", reason="the mcp extra is not installed")

MCP_ROUTE_SOURCES = (
    SRC / "server" / "routes" / "mcp.py",
    SRC / "server" / "mcpauth.py",
)

KEY_A = "key-a"
KEY_B = "key-b"
AUTH_A = {"Authorization": f"Bearer {KEY_A}"}
AUTH_B = {"Authorization": f"Bearer {KEY_B}"}

CLOUD_TOOL_NAMES = {spec.name for spec in iter_tools() if spec.supports(Engine.CLOUD)}

PANDOC_FAKE = """
out = args[args.index("--output") + 1]
open(out, "w", encoding="utf-8").write("<html>converted</html>\\n")
"""


def install_fake_pandoc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A real subprocess standing in for Pandoc, the M3 pattern `test_m6_cloud.py` also uses."""
    from docmax.tools import _binaries

    script = tmp_path / "fake_pandoc.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] in {'-v', '--version'}:\n"
        "    print('fake 1.0')\n"
        "    sys.exit(0)\n" + PANDOC_FAKE,
        encoding="utf-8",
    )
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


@pytest.fixture
def app() -> Any:
    return create_app(settings=ServerSettings(api_keys=frozenset({KEY_A, KEY_B})))


@pytest.fixture
def client(app: Any) -> Any:
    """Plain REST, no lifespan needed."""
    from fastapi.testclient import TestClient

    return TestClient(app)


class _LifespanManager:
    """Drives the ASGI lifespan protocol by hand, in the caller's own event loop.

    `TestClient` manages lifespan through its own background portal, which runs
    on a *different* event loop than a plain `httpx2.AsyncClient` driven from
    `anyio.run` — mixing the two breaks anyio's cross-loop assertions. This
    stays in the caller's loop, which is what lets it share that loop with the
    `mcp` client session in the same scenario.
    """

    def __init__(self, asgi_app: Any) -> None:
        self._app = asgi_app

    async def __aenter__(self) -> _LifespanManager:
        self._to_app: anyio.streams.memory.MemoryObjectSendStream[dict[str, Any]]
        self._to_app_recv: anyio.streams.memory.MemoryObjectReceiveStream[dict[str, Any]]
        self._from_app_send: anyio.streams.memory.MemoryObjectSendStream[dict[str, Any]]
        self._from_app: anyio.streams.memory.MemoryObjectReceiveStream[dict[str, Any]]
        self._to_app, self._to_app_recv = anyio.create_memory_object_stream(4)
        self._from_app_send, self._from_app = anyio.create_memory_object_stream(4)
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()

        async def run() -> None:
            await self._app(
                {"type": "lifespan"}, self._to_app_recv.receive, self._from_app_send.send
            )

        self._tg.start_soon(run)
        await self._to_app.send({"type": "lifespan.startup"})
        message = await self._from_app.receive()
        assert message["type"] == "lifespan.startup.complete", message
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._to_app.send({"type": "lifespan.shutdown"})
        message = await self._from_app.receive()
        assert message["type"] == "lifespan.shutdown.complete", message
        await self._tg.__aexit__(*exc_info)  # type: ignore[arg-type]


@contextlib.asynccontextmanager
async def mcp_session(
    app: Any, *, key: str, client_host: str = "203.0.113.5", scheme: str = "https"
) -> AsyncIterator[tuple[ClientSession, dict[str, str]]]:
    """An initialised MCP client session over the real ASGI app, plus a place
    the caller's `Mcp-Session-Id` lands once the handshake completes.
    """
    transport = httpx2.ASGITransport(app=app, client=(client_host, 51000))
    captured: dict[str, str] = {}

    class _Capturing(httpx2.AsyncClient):
        async def send(self, request: Any, **kw: Any) -> Any:
            response = await super().send(request, **kw)
            if "mcp-session-id" in response.headers:
                captured["session_id"] = response.headers["mcp-session-id"]
            return response

    async with (
        _Capturing(
            transport=transport,
            base_url=f"{scheme}://example.test",
            headers={"Authorization": f"Bearer {key}"},
        ) as http_client,
        streamable_http_client(f"{scheme}://example.test/v1/mcp", http_client=http_client) as (
            read,
            write,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session, captured


# ---------------------------------------------------------------------------
# Ownership — jobs, uploads, and idempotency (P0: "possible cross-user data
# exposure" / "possible cross-user output access")
# ---------------------------------------------------------------------------


def test_a_caller_cannot_read_a_file_id_it_did_not_upload(client: Any) -> None:
    reserved = client.post(
        "/v1/uploads", json={"filename": "a.pdf", "size_bytes": 5}, headers=AUTH_A
    ).json()
    client.put(reserved["upload_url"].split("testserver", 1)[-1], content=b"%PDF-", headers=AUTH_A)

    response = client.post(
        "/v1/tools/compress", json={"file_id": reserved["file_id"], "params": {}}, headers=AUTH_B
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "input.not_found"


def test_a_caller_cannot_fill_in_bytes_for_a_file_id_it_did_not_reserve(client: Any) -> None:
    """The upload-hijack case: reserving is authenticated, so is PUT-ing bytes into it."""
    reserved = client.post(
        "/v1/uploads", json={"filename": "a.pdf", "size_bytes": 5}, headers=AUTH_A
    ).json()

    response = client.put(
        reserved["upload_url"].split("testserver", 1)[-1], content=b"%PDF-", headers=AUTH_B
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "input.not_found"


def test_a_caller_cannot_poll_a_job_it_did_not_create(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake_pandoc(monkeypatch, tmp_path)
    submitted = client.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# hi\n", "application/octet-stream")},
        data={"params": "{}"},
        headers=AUTH_A,
    ).json()

    response = client.get(f"/v1/jobs/{submitted['job_id']}", headers=AUTH_B)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "input.not_found"


def test_an_idempotency_key_collision_does_not_hand_over_someone_elses_job(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sharpest form of the ownership gap: a client-chosen retry key that
    collides across callers must not leak one caller's job (and its output's
    file_id) to the other.
    """
    install_fake_pandoc(monkeypatch, tmp_path)
    key_header = {"Idempotency-Key": "sha256:shared-value-both-callers-happen-to-send"}

    a_job = client.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# a\n", "application/octet-stream")},
        data={"params": "{}"},
        headers={**AUTH_A, **key_header},
    ).json()
    b_job = client.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# b\n", "application/octet-stream")},
        data={"params": "{}"},
        headers={**AUTH_B, **key_header},
    ).json()

    assert a_job["job_id"] != b_job["job_id"]

    # And a repeat from the *same* caller still gets the retry-safety property.
    a_again = client.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# a\n", "application/octet-stream")},
        data={"params": "{}"},
        headers={**AUTH_A, **key_header},
    ).json()
    assert a_again["job_id"] == a_job["job_id"]


def test_outputs_stay_reachable_with_no_key_by_design(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`routes/outputs.py`'s deliberate no-auth design (ADR: unguessable file_id
    is the access control there) must survive adding ownership everywhere else.
    """
    install_fake_pandoc(monkeypatch, tmp_path)
    submitted = client.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# hi\n", "application/octet-stream")},
        data={"params": '{"to": "html"}'},
        headers=AUTH_A,
    ).json()

    path = submitted["output"]["url"].split("testserver", 1)[1]
    downloaded = client.get(path)

    assert downloaded.status_code == 200


# ---------------------------------------------------------------------------
# ADR 0037 — a durable, issued token authenticates identically to a static
# key, over REST and over MCP, and two tokens for one user share ownership.
# ---------------------------------------------------------------------------


@pytest.fixture
def identity_store(tmp_path: Path) -> SqliteIdentityStore:
    return SqliteIdentityStore(tmp_path / "identity.db")


@pytest.fixture
def identity_app(identity_store: SqliteIdentityStore) -> Any:
    """A deployment with no static keys at all -- issued tokens are the whole
    identity model here, proving the identity backend works standalone and
    not merely as a fallback behind `api_keys`.
    """
    return create_app(settings=ServerSettings(), identity=identity_store)


@pytest.fixture
def identity_client(identity_app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(identity_app)


def test_an_issued_token_authenticates_over_rest(
    identity_client: Any, identity_store: SqliteIdentityStore
) -> None:
    user_id = identity_store.create_user()
    token = identity_store.create_token(user_id=user_id)

    response = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_a_never_issued_token_is_refused_over_rest(identity_client: Any) -> None:
    response = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": "Bearer dmx_live_" + "0" * 64},
    )

    assert response.status_code == 401


def test_a_revoked_token_is_refused_identically_to_an_unknown_one(
    identity_client: Any, identity_store: SqliteIdentityStore
) -> None:
    user_id = identity_store.create_user()
    token = identity_store.create_token(user_id=user_id)
    token_id = identity_store.list_tokens(user_id)[0].token_id
    identity_store.revoke(token_id)

    revoked = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    unknown = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": "Bearer dmx_live_" + "1" * 64},
    )

    assert revoked.status_code == unknown.status_code == 401
    assert revoked.json() == unknown.json()


def test_two_tokens_for_the_same_user_share_upload_ownership(
    identity_client: Any, identity_store: SqliteIdentityStore
) -> None:
    """The concrete new capability ADR 0037 promised: "who is calling" can now
    be one person across more than one credential, where a raw-token-as-owner
    model could only ever see two unrelated callers.
    """
    user_id = identity_store.create_user()
    laptop = identity_store.create_token(user_id=user_id, label="laptop")
    phone = identity_store.create_token(user_id=user_id, label="phone")

    reserved = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": f"Bearer {laptop}"},
    ).json()

    # PUT-ing the bytes and then reading the upload back, both with the
    # *other* token for the same user -- refused for two different tokens of
    # two different users in `test_a_caller_cannot_fill_in_bytes_for_a_file_id_it_did_not_reserve`
    # above; allowed here because both tokens resolve to one owner.
    put_response = identity_client.put(
        reserved["upload_url"].split("testserver", 1)[-1],
        content=b"%PDF-",
        headers={"Authorization": f"Bearer {phone}"},
    )

    assert put_response.status_code == 200


def test_two_tokens_for_different_users_stay_isolated(
    identity_client: Any, identity_store: SqliteIdentityStore
) -> None:
    alice = identity_store.create_token(user_id=identity_store.create_user())
    bob = identity_store.create_token(user_id=identity_store.create_user())

    reserved = identity_client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": f"Bearer {alice}"},
    ).json()

    put_response = identity_client.put(
        reserved["upload_url"].split("testserver", 1)[-1],
        content=b"%PDF-",
        headers={"Authorization": f"Bearer {bob}"},
    )

    assert put_response.status_code == 404
    assert put_response.json()["error"]["code"] == "input.not_found"


def test_a_static_key_and_an_issued_token_authenticate_side_by_side(
    tmp_path: Path, identity_store: SqliteIdentityStore
) -> None:
    """ADR 0037 §5: the env-var allowlist is additive, not replaced."""
    app = create_app(settings=ServerSettings(api_keys=frozenset({KEY_A})), identity=identity_store)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    user_id = identity_store.create_user()
    issued = identity_store.create_token(user_id=user_id)

    static_response = client.post(
        "/v1/uploads", json={"filename": "a.pdf", "size_bytes": 5}, headers=AUTH_A
    )
    issued_response = client.post(
        "/v1/uploads",
        json={"filename": "a.pdf", "size_bytes": 5},
        headers={"Authorization": f"Bearer {issued}"},
    )

    assert static_response.status_code == issued_response.status_code == 200


def test_an_issued_token_authenticates_over_mcp(
    identity_app: Any, identity_store: SqliteIdentityStore
) -> None:
    user_id = identity_store.create_user()
    token = identity_store.create_token(user_id=user_id)

    async def scenario() -> set[str]:
        async with (
            _LifespanManager(identity_app),
            mcp_session(identity_app, key=token) as (
                session,
                _captured,
            ),
        ):
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

    assert anyio.run(scenario) == CLOUD_TOOL_NAMES


# ---------------------------------------------------------------------------
# Tool advertisement — only cloud-capable tools, over MCP (P1: "tool
# advertisement vs authorization")
# ---------------------------------------------------------------------------


def test_mcp_tool_list_matches_the_cloud_capable_registry(app: Any) -> None:
    async def scenario() -> set[str]:
        async with _LifespanManager(app), mcp_session(app, key=KEY_A) as (session, _captured):
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

    assert anyio.run(scenario) == CLOUD_TOOL_NAMES


def test_no_mcp_tool_offers_force(app: Any) -> None:
    """ADR 0028/0035: an agent may not overwrite a file it did not create."""

    async def scenario() -> list[Any]:
        async with _LifespanManager(app), mcp_session(app, key=KEY_A) as (session, _captured):
            result = await session.list_tools()
            return list(result.tools)

    for tool in anyio.run(scenario):
        assert "force" not in tool.input_schema["properties"], tool.name


# ---------------------------------------------------------------------------
# Session/auth binding (P0) and the HTTPS requirement (P0)
# ---------------------------------------------------------------------------


def test_a_session_cannot_be_reused_by_a_different_key(app: Any) -> None:
    """The SDK's own `StreamableHTTPSessionManager` binding, exercised here."""

    async def scenario() -> tuple[dict[str, str], Any]:
        async with _LifespanManager(app):
            async with mcp_session(app, key=KEY_A) as (_session, captured):
                session_id = captured["session_id"]

            transport = httpx2.ASGITransport(app=app, client=("203.0.113.5", 51000))
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="https://example.test",
                headers={"Authorization": f"Bearer {KEY_B}", "mcp-session-id": session_id},
            ) as reused:
                response = await reused.post(
                    "/v1/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                )
        return captured, response

    _captured, response = anyio.run(scenario)
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Session not found"


def test_plaintext_from_a_non_loopback_address_is_refused(app: Any) -> None:
    async def scenario() -> Any:
        transport = httpx2.ASGITransport(app=app, client=("203.0.113.5", 51000))
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://example.test"
        ) as plaintext:
            return await plaintext.post(
                "/v1/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={
                    "authorization": f"Bearer {KEY_A}",
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    response = anyio.run(scenario)
    assert response.status_code == 400
    assert response.json()["error"] == "https_required"


def test_plaintext_from_loopback_is_allowed_through_to_authentication(app: Any) -> None:
    """The exemption is for loopback, not a blanket bypass: a request from
    127.0.0.1 with no credential still hits `RequireAuthMiddleware`, not the
    HTTPS gate.
    """

    async def scenario() -> Any:
        transport = httpx2.ASGITransport(app=app, client=("127.0.0.1", 51000))
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://example.test"
        ) as loopback:
            return await loopback.post(
                "/v1/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    response = anyio.run(scenario)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_mcp_route_requires_a_valid_key(app: Any) -> None:
    async def scenario() -> Any:
        transport = httpx2.ASGITransport(app=app, client=("203.0.113.5", 51000))
        async with httpx2.AsyncClient(transport=transport, base_url="https://example.test") as anon:
            return await anon.post(
                "/v1/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    response = anyio.run(scenario)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# A full call, end to end, and no consent machinery anywhere in reach
# ---------------------------------------------------------------------------


def test_a_tool_call_over_mcp_runs_through_the_same_registry_runner(
    app: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake_pandoc(monkeypatch, tmp_path)

    async def scenario() -> tuple[Any, Any]:
        async with _LifespanManager(app), mcp_session(app, key=KEY_A) as (session, _captured):
            transport = httpx2.ASGITransport(app=app, client=("203.0.113.5", 51000))
            async with httpx2.AsyncClient(
                transport=transport, base_url="https://example.test", headers=AUTH_A
            ) as http_client:
                uploaded = await http_client.post(
                    "/v1/uploads", json={"filename": "notes.md", "size_bytes": 5}
                )
                file_id = uploaded.json()["file_id"]
                await http_client.put(
                    uploaded.json()["upload_url"].split("example.test", 1)[-1], content=b"# hi\n"
                )
            result = await session.call_tool("convert", {"inputs": [file_id], "to": "html"})
            return result, file_id

    result, _file_id = anyio.run(scenario)
    assert result.is_error is False
    assert result.structured_content["status"] == "succeeded"
    assert result.structured_content["output"]["size_bytes"] > 0


def test_the_mcp_route_never_references_consentstore() -> None:
    """A remote caller cannot grant cloud consent on the operator's behalf —
    there is no consent machinery here to grant it through in the first place.
    See ADR 0035 §5.
    """
    offences: list[str] = []
    for source in MCP_ROUTE_SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ConsentStore":
                offences.append(f"{relative(source)}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in {"record", "has"}:
                offences.append(f"{relative(source)}:{node.lineno}: .{node.attr}(...)")

    assert not offences, offences
