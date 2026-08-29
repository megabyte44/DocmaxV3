"""How M10 is installed, and why it differs from the server.

`docmax.server` is excluded from the wheel: it is deployed from a checkout
inside an image that also carries Ghostscript, Tesseract and Pandoc.
`docmax.mcp` is the opposite — a command a *user* runs on their own machine,
named in their MCP client's configuration — so the **code ships and only the
dependency is gated**. Getting that backwards would mean the documented command
does not exist after `pip install`, which is not a packaging preference but a
broken feature. See ADR 0027.

`test_wheel_excludes_server.py` holds the other half of the same line.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest

from tests.paths import REPO_ROOT

PACKAGE = "docmax.mcp"
EXTRA = "mcp"
DISTRIBUTION = "mcp"


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def extras(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = pyproject["project"]["optional-dependencies"]
    return found


# ---------------------------------------------------------------------------
# The extra
# ---------------------------------------------------------------------------


def test_the_mcp_extra_exists(pyproject: dict[str, Any]) -> None:
    assert EXTRA in extras(pyproject)


def test_the_sdk_is_not_a_base_dependency(pyproject: dict[str, Any]) -> None:
    """Non-negotiable #3: `pip install DocmaxV3` gets a terminal tool.

    The SDK brings jsonschema, sse-starlette, httpx2, opentelemetry and, on
    Windows, pywin32. None of that belongs in the install of someone who wants
    to merge two PDFs.
    """
    base = pyproject["project"]["dependencies"]

    assert not [item for item in base if item.split(">")[0].strip() == DISTRIBUTION]


def test_the_dependency_is_bounded_at_both_ends(pyproject: dict[str, Any]) -> None:
    """The 1.x to 2.x change renamed the server API this depends on.

    An unbounded requirement would let a future major release install and fail at
    runtime, in a user's MCP client, where no test runs.
    """
    (requirement,) = extras(pyproject)[EXTRA]

    assert requirement.startswith(DISTRIBUTION)
    assert ">=2.1" in requirement
    assert "<3" in requirement


def test_the_extra_is_not_in_all(pyproject: dict[str, Any]) -> None:
    """`all` is the document-processing extras; a protocol server is not one.

    `server` is out of `all` for the same reason, and installing every tool
    dependency should not quietly install an agent interface.
    """
    combined = " ".join(extras(pyproject)["all"])

    assert EXTRA not in combined.replace("DocmaxV3", "")


def test_the_installed_sdk_satisfies_the_floor() -> None:
    """The version the suite actually ran against, asserted rather than assumed."""
    from importlib.metadata import version

    major, minor = (int(part) for part in version(DISTRIBUTION).split(".")[:2])

    assert (major, minor) >= (2, 1)
    assert major < 3


# ---------------------------------------------------------------------------
# The wheel
# ---------------------------------------------------------------------------


def test_the_wheel_ships_the_mcp_package(pyproject: dict[str, Any]) -> None:
    """The mirror image of `test_wheel_excludes_server.py`."""
    excluded = pyproject["tool"]["setuptools"]["packages"]["find"].get("exclude", [])

    assert not [pattern for pattern in excluded if pattern.startswith(PACKAGE)], (
        f"{PACKAGE} must ship: `docmax mcp` is a command a user runs locally, "
        "unlike the server, which is deployed from a checkout. See ADR 0027."
    )


def test_the_server_is_still_excluded(pyproject: dict[str, Any]) -> None:
    """M10 must not have loosened the exclusion it sits beside."""
    excluded = pyproject["tool"]["setuptools"]["packages"]["find"].get("exclude", [])

    assert any(pattern.startswith("docmax.server") for pattern in excluded)


def test_the_package_directory_exists() -> None:
    from tests.paths import SRC

    assert (SRC / "mcp" / "__init__.py").is_file()


# ---------------------------------------------------------------------------
# The optional dependency is genuinely optional
# ---------------------------------------------------------------------------


def test_importing_the_package_root_needs_no_sdk() -> None:
    """`docmax mcp --help` must work on a machine without the extra.

    A subprocess, because once the SDK is in this process's `sys.modules` — and
    the rest of this suite puts it there — an in-process assertion proves nothing.
    """
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent(
        """
        import sys
        import docmax.mcp
        assert "mcp.server.lowlevel" not in sys.modules, "the SDK was imported eagerly"
        print(docmax.mcp.is_available())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_the_help_text_does_not_import_the_sdk() -> None:
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent(
        """
        import sys
        from typer.testing import CliRunner
        from docmax.cli.main import app
        result = CliRunner().invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0, result.output
        assert "mcp.server.lowlevel" not in sys.modules, "--help imported the SDK"
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_a_missing_extra_names_the_install_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure a user meets is a remedy, not an ImportError traceback."""
    import docmax.mcp as package
    from docmax.core.errors import LocalDependencyMissingError

    monkeypatch.setattr(package, "is_available", lambda: False)

    with pytest.raises(LocalDependencyMissingError) as caught:
        package.require_available()

    assert 'pip install "DocmaxV3[mcp]"' in (caught.value.remedy or "")
