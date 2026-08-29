"""Pytest configuration and fixtures."""

from __future__ import annotations

import shutil

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "needs_binary(name): requires an external binary. Skipped when absent locally, REQUIRED in CI.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests that need binaries not present locally."""
    skip_binary = pytest.mark.skip(reason="binary not installed")

    for item in items:
        for marker in item.iter_markers("needs_binary"):
            binary_name = marker.args[0] if marker.args else None
            if binary_name and shutil.which(binary_name) is None:
                item.add_marker(skip_binary)
