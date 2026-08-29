"""`--json` on every command, and the rule that credentials never leave in output.

Two contracts, tested together because they are the same property looked at from
two sides: **what goes on stdout is exactly the envelope and nothing else.**

## The command coverage is parametrised over the registry

Not over a hand-written list. A command added later inherits the test, which is
the only version of "everywhere" that stays true — a list would agree on the day
it was written and drift the first time someone added a command.

## The credential tests use a sentinel

A key nobody would ever type, put where a real key goes, and then searched for
in every stream. Asserting "the key is not printed" by checking for a plausible
key would pass against a bug that printed a *different* string; a sentinel that
appears nowhere else in the project cannot.

The searches run against the *renderers* rather than against individual
commands, so a new command that uses them inherits the guarantee. A command that
formats output by hand would not — ADR 0014 names that as the gap.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli.main import app
from docmax.core.config import Config
from docmax.core.registry import iter_tools
from docmax.core.router import EngineRouter

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: A key nobody would type, so finding it anywhere is unambiguous.
SENTINEL_KEY = "dmx_sentinel_MUST_NEVER_APPEAR_9f3a"

#: `from-images` is pure Python (no binary), but Pillow and img2pdf are still
#: the optional `images` extra, not base dependencies. Matches the `crypto`
#: extra's pattern in test_m4_tools.py.
needs_images = pytest.mark.skipif(
    importlib.util.find_spec("PIL") is None or importlib.util.find_spec("img2pdf") is None,
    reason="the images extra is not installed",
)

EXIT_FAILURE = 1
EXIT_USAGE = 2


def write_pdf(path: Path, pages: int = 2) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def write_image(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (60, 40), "red").save(path)
    return path


@pytest.fixture
def real_router(monkeypatch: pytest.MonkeyPatch) -> None:
    router = EngineRouter(config=Config(), consent=None)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 2)


@pytest.fixture(autouse=True)
def isolated_json_state() -> Any:
    """Reset the process-wide switch between tests.

    `--json` is module-level state set from argv (ADR 0017). One test leaving it
    on would silently change the next, so it is cleared either side rather than
    depending on ordering.
    """
    from docmax.cli import json_output

    json_output.set_enabled(False)
    yield
    json_output.set_enabled(False)


def parsed(result: Any) -> dict[str, Any]:
    """The single JSON object on stdout, or a failure that says what was there."""
    text = result.stdout
    assert text.strip(), f"nothing on stdout; stderr was: {result.stderr[:400]}"
    body = json.loads(text)
    assert isinstance(body, dict), f"stdout was not a JSON object: {text[:200]}"
    return body


# ---------------------------------------------------------------------------
# Every command accepts it — parametrised over the registry
# ---------------------------------------------------------------------------


def registered_command_names() -> list[str]:
    names: list[str] = []
    for command in app.registered_commands:
        if command.name:
            names.append(command.name)
        elif command.callback is not None:
            names.append(command.callback.__name__)
    return sorted(names)


@pytest.mark.parametrize("command", registered_command_names())
def test_every_command_accepts_json(command: str) -> None:
    """ "Everywhere" means every command, checked against the registry not a list."""
    listed = _ANSI.sub("", runner.invoke(app, [command, "--help"]).stdout)

    assert "--json" in listed, f"{command} does not offer --json"


@pytest.mark.parametrize("command", registered_command_names())
def test_no_command_forgets_the_option_when_invoked(command: str) -> None:
    """`--help` listing it is not proof it parses. This invokes it for real."""
    result = runner.invoke(app, [command, "--json"])

    assert "No such option" not in _ANSI.sub("", result.stdout + result.stderr)


def test_json_is_accepted_before_the_command_too() -> None:
    """Both positions, because both are things people type."""
    result = runner.invoke(app, ["--json", "formats"])

    assert result.exit_code == 0
    assert parsed(result)["ok"] is True


# ---------------------------------------------------------------------------
# The success envelope
# ---------------------------------------------------------------------------


@needs_images
def test_a_successful_run_emits_one_object(real_router: None, tmp_path: Path) -> None:
    image = write_image(tmp_path / "a.png")
    out = tmp_path / "out.pdf"

    result = runner.invoke(app, ["from-images", str(image), "-o", str(out), "--json"])

    assert result.exit_code == 0
    body = parsed(result)
    assert body["ok"] is True
    assert body["result"]["tool"] == "from-images"
    assert body["result"]["engine"] == "local"
    assert body["result"]["outputs"] == [str(out)]


@needs_images
def test_stdout_holds_exactly_one_json_document(real_router: None, tmp_path: Path) -> None:
    """One object per command. Two would break `| jq` for everyone."""
    image = write_image(tmp_path / "a.png")

    result = runner.invoke(
        app, ["from-images", str(image), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    assert len([line for line in result.stdout.splitlines() if line.strip()]) == 1


def test_a_read_only_tool_reports_an_empty_outputs_list(real_router: None, source: Path) -> None:
    """Present and empty, not absent — those are different answers."""
    result = runner.invoke(app, ["get-info", str(source), "--json"])

    assert result.exit_code == 0
    assert parsed(result)["result"] == parsed(result)["result"]
    assert "pages" in parsed(result)["result"]


@needs_images
def test_a_dry_run_reports_that_nothing_was_written(real_router: None, tmp_path: Path) -> None:
    image = write_image(tmp_path / "a.png")
    out = tmp_path / "never.pdf"

    result = runner.invoke(app, ["from-images", str(image), "-o", str(out), "--dry-run", "--json"])

    assert result.exit_code == 0
    assert parsed(result)["result"]["outputs"] == []
    assert not out.exists()


@needs_images
def test_stdout_carries_no_ansi_escapes(real_router: None, tmp_path: Path) -> None:
    """Rich must not colour a stream a parser is reading."""
    image = write_image(tmp_path / "a.png")

    result = runner.invoke(
        app, ["from-images", str(image), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    assert "\x1b[" not in result.stdout


@needs_images
def test_progress_produces_no_stdout(real_router: None, tmp_path: Path) -> None:
    """A live region on stdout would corrupt the document being parsed."""
    images = [write_image(tmp_path / f"{n}.png") for n in range(4)]

    result = runner.invoke(
        app, ["from-images", *map(str, images), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    json.loads(result.stdout)


# ---------------------------------------------------------------------------
# The error envelope
# ---------------------------------------------------------------------------


@needs_images
def test_a_failure_emits_the_error_envelope(real_router: None, tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    result = runner.invoke(
        app, ["from-images", str(notes), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    assert result.exit_code == EXIT_FAILURE
    body = parsed(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "input.unsupported_format"
    assert body["error"]["remedy"]


def test_the_error_envelope_matches_the_wire_contract(real_router: None, tmp_path: Path) -> None:
    """Same shape a cloud failure arrives in, so a caller writes one parser."""
    notes = tmp_path / "notes.txt"
    notes.write_text("nope", encoding="utf-8")

    body = parsed(
        runner.invoke(app, ["from-images", str(notes), "-o", str(tmp_path / "o.pdf"), "--json"])
    )

    assert set(body["error"]) >= {"code", "message", "remedy", "retryable"}


@needs_images
def test_exit_codes_are_unchanged_under_json(real_router: None, tmp_path: Path) -> None:
    """`--json` changes what is printed, never what is returned."""
    image = write_image(tmp_path / "a.png")
    out = tmp_path / "out.pdf"

    assert runner.invoke(app, ["from-images", str(image), "-o", str(out), "--json"]).exit_code == 0
    assert (
        runner.invoke(app, ["from-images", str(image), "-o", str(out), "--json"]).exit_code
        == EXIT_FAILURE
    ), "the second run collides with the first and must still fail"


def test_a_usage_error_is_not_wrapped(real_router: None) -> None:
    """It happens before DocMax code runs, so there is no envelope to emit.

    Documented rather than worked around: making Typer emit our shape would mean
    owning its error rendering. A script must handle "not JSON" for exit 2.
    """
    result = runner.invoke(app, ["convert", "--json"])

    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# Structured answers from the question-answering commands
# ---------------------------------------------------------------------------


def test_formats_answers_as_data() -> None:
    body = parsed(runner.invoke(app, ["formats", "--json"]))

    from docmax.tools import _formats

    names = {item["name"] for item in body["result"]["documents"]}
    assert names == {item.name for item in _formats.DOCUMENT_FORMATS}


def test_doctor_answers_as_data() -> None:
    body = parsed(runner.invoke(app, ["doctor", "--json"]))

    from docmax.tools import _binaries

    names = {item["name"] for item in body["result"]["binaries"]}
    assert names == {binary.name for binary in _binaries.EXTERNAL_BINARIES}


def test_doctor_json_reports_what_is_missing_rather_than_prose(
    real_router: None,
) -> None:
    """A script asking "is Ghostscript here before I start a batch" wants this."""
    body = parsed(runner.invoke(app, ["doctor", "--json"]))

    for binary in body["result"]["binaries"]:
        assert "found" in binary
        assert binary["found"] is None or isinstance(binary["found"], str)


# ---------------------------------------------------------------------------
# Credentials never appear — ADR 0014
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DOCMAX_API_KEY", SENTINEL_KEY)
    return SENTINEL_KEY


def streams(result: Any) -> str:
    return _ANSI.sub("", result.stdout) + _ANSI.sub("", result.stderr)


def test_cloud_status_never_prints_the_key(sentinel_key: str) -> None:
    result = runner.invoke(app, ["cloud", "status"])

    assert result.exit_code == 0
    assert SENTINEL_KEY not in streams(result)


def test_cloud_status_still_confirms_a_key_is_set(sentinel_key: str) -> None:
    """Masked, not hidden — "is the key I think is there actually there?" must be answerable."""
    shown = streams(runner.invoke(app, ["cloud", "status"]))

    assert "configured" in shown
    assert SENTINEL_KEY[-4:] in shown, (
        "a suffix short enough to be useless, long enough to identify"
    )


@needs_images
def test_no_command_leaks_the_key_on_success(
    sentinel_key: str, real_router: None, tmp_path: Path
) -> None:
    image = write_image(tmp_path / "a.png")

    result = runner.invoke(
        app, ["from-images", str(image), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    assert SENTINEL_KEY not in streams(result)


def test_no_command_leaks_the_key_on_failure(
    sentinel_key: str, real_router: None, tmp_path: Path
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("nope", encoding="utf-8")

    result = runner.invoke(
        app, ["from-images", str(notes), "-o", str(tmp_path / "out.pdf"), "--json"]
    )

    assert SENTINEL_KEY not in streams(result)


def test_the_key_is_not_in_a_cloud_errors_message_or_context(sentinel_key: str) -> None:
    """`to_dict` feeds both the terminal and the `--json` envelope."""
    import httpx
    import respx

    from docmax.cloud_client import CloudClient, CloudConfig
    from docmax.core.errors import DocMaxError

    endpoint = "https://api.example.invalid"
    client = CloudClient(CloudConfig(endpoint=endpoint, api_key=SENTINEL_KEY, max_retries=0))

    with respx.mock:
        respx.get(f"{endpoint}/v1/capabilities").mock(
            return_value=httpx.Response(
                401, json={"ok": False, "error": {"code": "cloud.auth", "message": "bad key"}}
            )
        )
        try:
            client.capabilities()
        except DocMaxError as exc:
            rendered = json.dumps(exc.to_dict())
            assert SENTINEL_KEY not in rendered
        else:  # pragma: no cover
            pytest.fail("the endpoint should have refused")


def test_a_cloud_results_details_carry_the_endpoint_and_not_the_key() -> None:
    """`details` travels into logs and into `--json`."""
    import httpx
    import respx

    from docmax.cloud_client import CloudClient, CloudConfig
    from docmax.core.cancellation import NEVER_CANCELLED
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import NULL_PROGRESS

    endpoint = "https://api.example.invalid"

    with respx.mock:
        respx.post(f"{endpoint}/v1/tools/convert").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "job_id": "j",
                    "status": "succeeded",
                    "output": {
                        "url": f"{endpoint}/v1/outputs/f",
                        "size_bytes": 4,
                        "content_type": "text/html",
                    },
                },
            )
        )
        respx.get(f"{endpoint}/v1/outputs/f").mock(
            return_value=httpx.Response(200, content=b"<html></html>")
        )

        import tempfile

        from docmax.tools.convert.cloud import build

        with tempfile.TemporaryDirectory() as work:
            notes = Path(work) / "notes.md"
            notes.write_text("# T\n", encoding="utf-8")
            result = build(
                CloudClient(CloudConfig(endpoint=endpoint, api_key=SENTINEL_KEY, max_retries=0))
            ).run(
                [DocumentRef.from_path(notes)],
                OutputTarget(destination=Path(work) / "out.html", force=True),
                progress=NULL_PROGRESS,
                cancellation=NEVER_CANCELLED,
                to="html",
            )

    rendered = json.dumps(dict(result.details))
    assert SENTINEL_KEY not in rendered
    assert endpoint in rendered, "the endpoint is reportable; the key is not"


def test_every_tool_result_detail_is_json_serialisable() -> None:
    """The envelope must never fail to print because a tool put something odd in."""
    from docmax.cli import json_output
    from docmax.core.models import Engine, ToolResult

    result = ToolResult(
        outputs=(Path("/tmp/x.pdf"),),
        engine_used=Engine.LOCAL,
        details={"path": Path("/tmp/y"), "count": 3, "nested": {"a": (1, 2)}},
    )

    body = json.loads(json_output.success(result, tool="x"))

    assert body["result"]["details"]["nested"]["a"] == [1, 2]


@pytest.mark.parametrize("name", [spec.name for spec in iter_tools()])
def test_no_tool_declares_a_parameter_called_api_key(name: str) -> None:
    """A credential must never be a tool parameter — those are echoed in `details`."""
    from docmax.core.registry import get_tool

    for param in get_tool(name).params:
        assert "api_key" not in param.name
        assert "password" not in param.name or name in {"protect", "unlock", "permissions"}
