"""Command-line interface.

The only layer permitted to write to stdout, and the only layer permitted to
terminate the process. Library code raises typed errors; this layer decides how
to render them and what exit code to use.
"""

from __future__ import annotations
