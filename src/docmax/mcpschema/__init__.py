"""``ToolSpec`` rendered as JSON Schema — shared by every MCP transport.

A library package below every interface, on the same terms as
``docmax.pickers`` and ``docmax.runners``: it never prints, never exits, and
imports ``docmax.core`` only. See ``docs/adr/0035-remote-mcp-is-a-transport-
bridge-over-the-cloud-server.md`` for why this moved out of ``docmax.mcp`` at
M11, and ``schema.py`` for the mapping itself.
"""

from __future__ import annotations

from docmax.mcpschema.schema import INPUTS, OUTPUT, described, input_schema

__all__ = ["INPUTS", "OUTPUT", "described", "input_schema"]
