"""Tool implementations — one subpackage per operation.

Each tool directory contains:

    tool.py        ToolSpec: name, description, params, supported engines
    local.py       LocalStrategy — heavy deps, offline, private
    cloud.py       CloudStrategy — thin API client (omitted when cloud makes no sense)
    validators.py  output checks run against the temp file before the atomic swap

Tools self-register. Adding one is ``mkdir tools/<name>/`` plus three files;
no central file is edited, ever. See ``core/registry.py`` and
``docs/adr/0002-registry-mechanism.md``.
"""

from __future__ import annotations
