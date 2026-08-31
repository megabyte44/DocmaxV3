"""Re-exports ``docmax.mcpschema`` for callers that imported this module before M11.

The registry-to-JSON-Schema mapping used to live here. It moved to
``docmax.mcpschema`` at M11 so ``docmax.server``'s remote MCP route could reach
it too — ``interfaces-are-independent`` forbids ``docmax.server`` from
importing ``docmax.mcp``, so the shared logic had to live below both, not
inside either. See
``docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md``.

Every name below still means exactly what it meant before this file became a
re-export: ``input_schema(spec)`` with no keywords still renders the local,
stdio-shaped wording this package has always used.
"""

from __future__ import annotations

from docmax.mcpschema.schema import INPUTS, OUTPUT, described, input_schema

__all__ = ["INPUTS", "OUTPUT", "described", "input_schema"]
