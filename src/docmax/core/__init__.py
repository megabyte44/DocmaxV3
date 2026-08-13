"""Engine-agnostic core.

``core`` knows nothing about specific tools and nothing about user interfaces.
It may not import from ``docmax.tools``, ``docmax.cli``, or ``docmax.tui``, and
it may not import ``rich`` or ``textual`` — progress crosses that boundary as
the ``ProgressSink`` protocol instead.

The rule is enforced by import-linter contracts in ``.importlinter``, checked in
CI, so the layering cannot quietly erode the way v2's did.
"""

from __future__ import annotations
