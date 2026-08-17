"""``python -m docmax.server`` — run the reference server.

Kept separate from ``app.py`` so that importing the application never starts
one. This module is the only place a process is bound to a port, and the only
place uvicorn is imported at all.
"""

from __future__ import annotations

from docmax.server.app import create_app
from docmax.server.config import ServerSettings


def main() -> None:
    import uvicorn

    settings = ServerSettings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
