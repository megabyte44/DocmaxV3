"""The three M9 commands at the argv boundary, and their JSON envelopes.

ADR 0017's contract is the one most easily broken by a command that reports on
many things at once: **stdout carries one JSON object and nothing else.** A
batch has a progress bar, a per-document listing and a summary, and all three
have to go somewhere that is not stdout when `--json` is on.

The other thing under test here is the exit code. A batch where three of two
hundred documents failed is not a success, and a script needs to be able to tell
without parsing anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from docmax.cli.main import app
from docmax.core.errors import CorruptDocumentError
from tests.unit.m9_support import document, markers, router_for, tool
from tests.unit.test_m9_watch import Ticker, nothing

if TYPE_CHECKING:
    from docmax.core.router import EngineRouter

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CANCELLED = 130


@pytest.fixture(autouse=True)
def isolated_json_state() -> Any:
    """Reset the process-wide `--json` switch, as ``test_cli_json.py`` does."""
    from docmax.cli import json_output

    json_output.set_enabled(False)
    yield
    json_output.set_enabled(False)


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> EngineRouter:
    """Wire the CLI to fake tools, so no real engine or binary is involved."""
    router = router_for(tool("a"), tool("b"), tool("boom", raises=CorruptDocumentError("broken")))
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)
    return router


@pytest.fixture
def instant_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the watcher's loop without spending real seconds in it."""
    from docmax.runners import watch as watch_module

    real = watch_module.watch_folder

    def patched(*args: Any, **kwargs: Any) -> Any:
        token = kwargs["cancellation"]
        kwargs.setdefault("sleep", Ticker(token, [nothing]))
        return real(*args, **kwargs)

    monkeypatch.setattr(watch_module, "watch_folder", patched)


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    inputs = tmp_path / "in"
    outputs = tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("alpha", "beta"):
        document(inputs / f"{name}.pdf", text=name)
    return inputs, outputs


def chain_file(tmp_path: Path, *tools: str, name: str = "chain") -> Path:
    path = tmp_path / f"{name}.toml"
    body = f'name = "{name}"\n' + "".join(f'\n[[stage]]\ntool = "{item}"\n' for item in tools)
    path.write_text(body, encoding="utf-8")
    return path


def parsed(result: Any) -> dict[str, Any]:
    text = result.stdout
    assert text.strip(), f"nothing on stdout; stderr was: {result.stderr[:400]}"
    body = json.loads(text)
    assert isinstance(body, dict), f"stdout was not a JSON object: {text[:200]}"
    return body


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


def test_pipeline_runs_and_writes_the_destination(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "pipeline",
            str(inputs / "alpha.pdf"),
            "--pipeline",
            str(chain_file(tmp_path, "a", "b")),
            "-o",
            str(outputs / "done.pdf"),
        ],
    )

    assert result.exit_code == EXIT_OK, result.stdout + result.stderr
    assert markers(outputs / "done.pdf") == ["a", "b"]


def test_pipeline_json_is_one_object_on_stdout(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "pipeline",
            str(inputs / "alpha.pdf"),
            "--pipeline",
            str(chain_file(tmp_path, "a", "b")),
            "-o",
            str(outputs / "done.pdf"),
        ],
    )

    body = parsed(result)
    assert body["ok"] is True
    assert body["result"]["details"]["stages"] == ["a", "b"]
    assert body["result"]["outputs"] == [str(outputs / "done.pdf")]


def test_a_bad_pipeline_file_is_an_envelope_not_a_traceback(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    inputs, outputs = workspace
    broken = tmp_path / "broken.toml"
    broken.write_text('[[stage]]\ntool = "ghost"\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--json",
            "pipeline",
            str(inputs / "alpha.pdf"),
            "--pipeline",
            str(broken),
            "-o",
            str(outputs / "done.pdf"),
        ],
    )

    body = parsed(result)
    assert result.exit_code == EXIT_FAILURE
    assert body["ok"] is False
    assert body["error"]["code"] == "input.invalid_parameter"
    assert "Traceback" not in result.stdout + result.stderr


def test_pipeline_dry_run_writes_nothing(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "pipeline",
            str(inputs / "alpha.pdf"),
            "--pipeline",
            str(chain_file(tmp_path, "a", "b")),
            "-o",
            str(outputs / "done.pdf"),
            "--dry-run",
        ],
    )

    assert result.exit_code == EXIT_OK
    assert not (outputs / "done.pdf").exists()


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


def test_batch_processes_every_input(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "batch",
            *[str(p) for p in sorted(inputs.glob("*.pdf"))],
            "--output-dir",
            str(outputs),
            "--tool",
            "a",
        ],
    )

    assert result.exit_code == EXIT_OK, result.stdout + result.stderr
    assert sorted(p.name for p in outputs.iterdir()) == ["alpha.pdf", "beta.pdf"]


def test_batch_json_reports_every_item(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "batch",
            *[str(p) for p in sorted(inputs.glob("*.pdf"))],
            "--output-dir",
            str(outputs),
            "--tool",
            "a",
        ],
    )

    body = parsed(result)
    assert body["ok"] is True
    assert body["result"]["summary"] == {"total": 2, "succeeded": 2, "failed": 0}
    assert [item["source"] for item in body["result"]["items"]] == [
        str(inputs / "alpha.pdf"),
        str(inputs / "beta.pdf"),
    ]
    assert all(item["ok"] for item in body["result"]["items"])


def test_a_failed_document_makes_the_batch_exit_nonzero(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        ["batch", str(inputs / "alpha.pdf"), "--output-dir", str(outputs), "--tool", "boom"],
    )

    assert result.exit_code == EXIT_FAILURE


def test_a_failed_document_appears_in_the_json_with_its_error_envelope(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "batch",
            str(inputs / "alpha.pdf"),
            "--output-dir",
            str(outputs),
            "--tool",
            "boom",
        ],
    )

    body = parsed(result)
    assert body["ok"] is False
    item = body["result"]["items"][0]
    assert item["ok"] is False
    assert item["error"]["code"] == "input.corrupt"
    assert body["result"]["summary"]["failed"] == 1


def test_batch_stdout_carries_only_the_envelope(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    """The per-document listing and the summary must not reach stdout."""
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "batch",
            *[str(p) for p in sorted(inputs.glob("*.pdf"))],
            "--output-dir",
            str(outputs),
            "--tool",
            "a",
        ],
    )

    assert result.stdout.count("\n") == 1, f"more than one line on stdout: {result.stdout!r}"
    assert "Wrote" not in result.stdout
    assert _ANSI.search(result.stdout) is None


def test_a_contaminating_output_directory_is_refused_at_the_cli(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, _ = workspace
    result = runner.invoke(
        app,
        ["--json", "batch", str(inputs / "alpha.pdf"), "--output-dir", str(inputs), "--tool", "a"],
    )

    body = parsed(result)
    assert result.exit_code == EXIT_FAILURE
    assert body["ok"] is False
    assert body["error"]["code"] == "input.invalid_parameter"


def test_batch_needs_a_tool_or_a_pipeline(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app, ["--json", "batch", str(inputs / "alpha.pdf"), "--output-dir", str(outputs)]
    )

    assert parsed(result)["error"]["code"] == "input.invalid_parameter"


def test_batch_refuses_both_a_tool_and_a_pipeline(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "batch",
            str(inputs / "alpha.pdf"),
            "--output-dir",
            str(outputs),
            "--tool",
            "a",
            "--pipeline",
            str(chain_file(tmp_path, "a")),
        ],
    )

    assert "not both" in parsed(result)["error"]["message"]


def test_engine_may_not_be_combined_with_a_pipeline_file(
    fake_tools: EngineRouter, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    """A pipeline file states each stage's engine; a flag overriding all of them silently would lie."""
    inputs, outputs = workspace
    result = runner.invoke(
        app,
        [
            "--json",
            "batch",
            str(inputs / "alpha.pdf"),
            "--output-dir",
            str(outputs),
            "--pipeline",
            str(chain_file(tmp_path, "a")),
            "--engine",
            "local",
        ],
    )

    assert "--engine cannot be combined" in parsed(result)["error"]["message"]


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


def test_watch_processes_what_is_already_there_and_stops(
    fake_tools: EngineRouter, instant_watch: None, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(app, ["watch", str(inputs), "--output-dir", str(outputs), "--tool", "a"])

    assert result.exit_code == EXIT_CANCELLED
    assert sorted(p.name for p in outputs.iterdir()) == ["alpha.pdf", "beta.pdf"]


def test_watch_json_is_one_object_written_at_shutdown(
    fake_tools: EngineRouter, instant_watch: None, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(
        app, ["--json", "watch", str(inputs), "--output-dir", str(outputs), "--tool", "a"]
    )

    body = parsed(result)
    assert result.stdout.count("\n") == 1, "one object, not one per document"
    assert body["ok"] is True
    assert body["result"]["command"] == "watch"
    assert body["result"]["cancelled"] is True
    assert body["result"]["summary"]["succeeded"] == 2


def test_watch_refuses_an_output_directory_inside_the_watched_tree(
    fake_tools: EngineRouter, workspace: tuple[Path, Path]
) -> None:
    inputs, _ = workspace
    inside = inputs / "done"
    inside.mkdir()

    result = runner.invoke(
        app, ["--json", "watch", str(inputs), "--output-dir", str(inside), "--tool", "a"]
    )

    body = parsed(result)
    assert result.exit_code == EXIT_FAILURE
    assert "overlaps the watched folder" in body["error"]["message"]


def test_watch_announces_each_document_when_not_in_json_mode(
    fake_tools: EngineRouter, instant_watch: None, workspace: tuple[Path, Path]
) -> None:
    inputs, outputs = workspace
    result = runner.invoke(app, ["watch", str(inputs), "--output-dir", str(outputs), "--tool", "a"])

    listed = _ANSI.sub("", result.stdout)
    assert listed.count("Wrote") == 2
