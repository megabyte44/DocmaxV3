"""``docmax mcp connect``: wiring a client's config file instead of a person.

Every test aims `known_clients()` at a machine with nothing installed by
faking `platformdirs.user_config_dir` and `Path.home` — never the real config
directory or the real `~/.cursor` — so this suite cannot see, let alone touch,
a real client's configuration. The one client that is always a target
(Claude Code's project-scoped `.mcp.json`) lives under `tmp_path`, exactly the
same way `test_cli_setup.py` never lets a real package manager run.

``--remote`` tests set `DOCMAX_API_KEY`/`DOCMAX_CLOUD_ENDPOINT` through the
environment (`test_cli_json.py::sentinel_key` does the same for `cloud
status`) rather than writing to the real `config.toml`, so no run of this
suite can read or write a real cloud credential.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli import mcp_group
from docmax.cli.main import app

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

EXIT_FAILURE = 1
EXIT_USAGE = 2


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    return plain(result.stdout) + plain(result.stderr)


def parsed(result: Any) -> dict[str, Any]:
    text = result.stdout
    assert text.strip(), f"nothing on stdout; stderr was: {result.stderr[:400]}"
    body = json.loads(text)
    assert isinstance(body, dict)
    return body


@pytest.fixture(autouse=True)
def isolated_json_state() -> Any:
    from docmax.cli import json_output

    json_output.set_enabled(False)
    yield
    json_output.set_enabled(False)


@pytest.fixture
def no_other_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither Claude Desktop nor Cursor is "installed" — only their absence.

    Points both at directories under `tmp_path` that are never created, so
    `known_clients()` reports them undetected. Claude Code's project file is
    unaffected: it is always offered, gated by the confirm/dry-run flow like
    everything else.
    """
    monkeypatch.setattr(
        mcp_group.platformdirs, "user_config_dir", lambda *a, **k: str(tmp_path / "no-claude")
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")


@pytest.fixture
def cloud_env(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """A cloud endpoint and key, set only through the environment.

    Never touches the real `config.toml` — the same isolation
    `test_cli_json.py::sentinel_key` relies on for `cloud status`.
    """
    endpoint = "https://mcp.example.invalid"
    key = "sentinel-remote-key"
    monkeypatch.setenv("DOCMAX_CLOUD_ENDPOINT", endpoint)
    monkeypatch.setenv("DOCMAX_API_KEY", key)
    return endpoint, key


# ---------------------------------------------------------------------------
# The default target: Claude Code's project file
# ---------------------------------------------------------------------------


def test_dry_run_shows_the_plan_and_writes_nothing(no_other_clients: None, tmp_path: Path) -> None:
    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0
    assert "claude-code" in shown(result)
    assert not (tmp_path / ".mcp.json").exists()


def test_yes_writes_the_project_file(no_other_clients: None, tmp_path: Path) -> None:
    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--yes"])

    assert result.exit_code == 0
    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert written["mcpServers"]["docmax"]["command"] == "docmax"
    assert written["mcpServers"]["docmax"]["args"] == ["mcp", "--root", str(tmp_path)]


def test_allow_cloud_is_carried_into_the_entry(no_other_clients: None, tmp_path: Path) -> None:
    runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--allow-cloud", "--yes"])

    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "--allow-cloud" in written["mcpServers"]["docmax"]["args"]


def test_no_project_falls_back_to_the_manual_snippet(
    no_other_clients: None, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["mcp", "connect", "--root", str(tmp_path), "--no-project", "--yes"]
    )

    assert result.exit_code == 0
    assert "mcpServers" in shown(result), "the manual snippet is the fallback with nothing to do"
    assert not (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------------------
# Merging: everything already in the file survives
# ---------------------------------------------------------------------------


def test_an_existing_unrelated_server_entry_survives(
    no_other_clients: None, tmp_path: Path
) -> None:
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {"other-tool": {"command": "othertool"}},
                "someOtherTopLevelKey": True,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--yes"])

    assert result.exit_code == 0
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["other-tool"] == {"command": "othertool"}
    assert written["someOtherTopLevelKey"] is True
    assert written["mcpServers"]["docmax"]["command"] == "docmax"


def test_an_existing_docmax_entry_is_reported_as_an_update_not_a_create(
    no_other_clients: None, tmp_path: Path
) -> None:
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"docmax": {"command": "stale"}}}), encoding="utf-8"
    )

    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--dry-run"])

    assert "update" in shown(result)
    assert "create" not in shown(result)


def test_invalid_json_is_refused_not_overwritten(no_other_clients: None, tmp_path: Path) -> None:
    config_path = tmp_path / ".mcp.json"
    config_path.write_text("not json {", encoding="utf-8")

    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--yes"])

    assert result.exit_code == EXIT_USAGE
    assert config_path.read_text(encoding="utf-8") == "not json {"


# ---------------------------------------------------------------------------
# Refusing to write without consent
# ---------------------------------------------------------------------------


def test_refuses_to_write_without_yes_when_not_interactive(
    no_other_clients: None, tmp_path: Path
) -> None:
    """`CliRunner` is never a tty; `connect` must not hang waiting for a prompt."""
    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path)])

    assert result.exit_code == EXIT_FAILURE
    assert not (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------


def test_dry_run_as_json(no_other_clients: None, tmp_path: Path) -> None:
    body = parsed(
        runner.invoke(app, ["--json", "mcp", "connect", "--root", str(tmp_path), "--dry-run"])
    )

    items = body["result"]["items"]
    assert len(items) == 1
    assert items[0]["client"] == "claude-code"
    assert body["result"]["wrote"] is False


def test_yes_as_json_reports_what_was_written(no_other_clients: None, tmp_path: Path) -> None:
    body = parsed(
        runner.invoke(app, ["--json", "mcp", "connect", "--root", str(tmp_path), "--yes"])
    )

    assert body["result"]["wrote"] is True
    assert body["result"]["items"][0]["path"] == str(tmp_path / ".mcp.json")


# ---------------------------------------------------------------------------
# Detection: a client only becomes a target if it looks installed
# ---------------------------------------------------------------------------


def test_claude_desktop_is_offered_only_when_its_config_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_dir = tmp_path / "Claude"
    claude_dir.mkdir()
    monkeypatch.setattr(mcp_group.platformdirs, "user_config_dir", lambda *a, **k: str(claude_dir))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")

    result = runner.invoke(
        app, ["mcp", "connect", "--root", str(tmp_path), "--no-project", "--dry-run"]
    )

    assert "claude-desktop" in shown(result)


def test_an_unknown_client_name_is_a_usage_error(no_other_clients: None, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["mcp", "connect", "--root", str(tmp_path), "--client", "not-a-real-client"]
    )

    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# --remote
# ---------------------------------------------------------------------------


def test_remote_without_a_configured_key_fails_with_the_login_remedy(
    no_other_clients: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCMAX_API_KEY", raising=False)

    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--remote", "--yes"])

    assert result.exit_code == EXIT_USAGE
    assert "cloud login" in shown(result)


def test_remote_writes_the_endpoint_and_key_into_claude_codes_config(
    no_other_clients: None, cloud_env: tuple[str, str], tmp_path: Path
) -> None:
    endpoint, key = cloud_env

    result = runner.invoke(app, ["mcp", "connect", "--root", str(tmp_path), "--remote", "--yes"])

    assert result.exit_code == 0
    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    entry = written["mcpServers"]["docmax"]
    assert entry["url"] == f"{endpoint}/v1/mcp"
    assert entry["headers"]["Authorization"] == f"Bearer {key}"
    assert key not in shown(result), "the key must never appear in the CLI's own output"


def test_remote_skips_clients_without_a_documented_remote_shape(
    tmp_path: Path, cloud_env: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_dir = tmp_path / "Claude"
    claude_dir.mkdir()
    monkeypatch.setattr(mcp_group.platformdirs, "user_config_dir", lambda *a, **k: str(claude_dir))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")

    result = runner.invoke(
        app, ["mcp", "connect", "--root", str(tmp_path), "--remote", "--dry-run"]
    )

    assert "claude-desktop" not in shown(result)
    assert "claude-code" in shown(result)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_merged_preserves_unrelated_servers_and_top_level_keys() -> None:
    document = {"mcpServers": {"other": {"command": "x"}}, "extra": 1}

    merged_doc, existed = mcp_group.merged(document, {"command": "docmax", "args": []})

    assert existed is False
    assert merged_doc["mcpServers"]["other"] == {"command": "x"}
    assert merged_doc["extra"] == 1
    assert merged_doc["mcpServers"]["docmax"] == {"command": "docmax", "args": []}


def test_merged_reports_an_existing_docmax_entry() -> None:
    document = {"mcpServers": {"docmax": {"command": "stale"}}}

    _, existed = mcp_group.merged(document, {"command": "docmax", "args": []})

    assert existed is True


def test_local_entry_has_one_root_flag_per_root() -> None:
    entry = mcp_group.local_entry([Path("/a"), Path("/b")], allow_cloud=False)

    assert entry["args"] == ["mcp", "--root", str(Path("/a")), "--root", str(Path("/b"))]
    assert "--allow-cloud" not in entry["args"]
