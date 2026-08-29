"""``python -m benchmarks`` — run the suite and write a results file.

Local and cloud, for `compress` and `convert`, over four generated fixtures.
Cloud is measured against a **reference server started here**, on this machine,
so the number is honest about what it is: DocMax's own overhead plus loopback
HTTP plus the same local engine, and not a measurement of anyone's internet
connection. `METHODOLOGY.md` says so where the numbers are published.

A tool whose binary is absent produces a row saying so rather than no row. That
is the whole discipline: the results file describes the run that happened.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.harness import build_fixtures, measure, results_payload, skipped

if TYPE_CHECKING:
    from collections.abc import Iterator

    from benchmarks.harness import Fixture, Measurement

RESULTS_DIR = Path(__file__).parent / "results"

#: The port the reference server binds for the cloud half. Fixed rather than
#: ephemeral so a failed run leaves an obvious thing to check.
SERVER_PORT = 8973
SERVER_KEY = "benchmark-key"


def main(argv: list[str] | None = None) -> int:
    """Run everything that can run, and write what was measured."""
    argv = sys.argv[1:] if argv is None else argv
    include_cloud = "--no-cloud" not in argv

    with tempfile.TemporaryDirectory(prefix="docmax-bench-") as work:
        root = Path(work)
        fixtures = build_fixtures(root / "fixtures")
        measurements: list[Measurement] = []

        measurements.extend(_local(fixtures, root / "out"))
        if include_cloud:
            measurements.extend(_cloud(fixtures, root / "cloud-out"))
        else:
            for fixture in fixtures:
                measurements.append(skipped("compress", "cloud", fixture, "--no-cloud was passed"))

        payload = results_payload(measurements, fixtures)

    destination = _write(payload)
    _summarise(payload, destination)
    return 0


# ---------------------------------------------------------------------------
# Local
# ---------------------------------------------------------------------------


def _local(fixtures: list[Fixture], out: Path) -> list[Measurement]:
    from docmax.core.config import Config
    from docmax.core.router import EngineRouter
    from docmax.tools import _binaries

    out.mkdir(parents=True, exist_ok=True)
    router = EngineRouter(config=Config())
    results: list[Measurement] = []

    for fixture in fixtures:
        for tool, binary, params in _plan(fixture):
            if _binaries.find(binary) is None:
                results.append(skipped(tool, "local", fixture, f"{binary} is not installed"))
                continue
            results.append(
                measure(
                    tool,
                    "local",
                    fixture,
                    _runner(router, tool, fixture, params),
                    out / f"{tool}-{fixture.name}{_suffix(tool, params)}",
                )
            )
    return results


def _plan(fixture: Fixture) -> list[tuple[str, str, dict[str, Any]]]:
    """Which tools apply to this fixture, and with what parameters."""
    if fixture.kind == "markdown":
        return [("convert", "pandoc", {"to": "html"})]
    return [("compress", "gs", {"preset": "ebook"})]


def _suffix(tool: str, params: dict[str, Any]) -> str:
    return ".html" if tool == "convert" else ".pdf"


def _runner(router: Any, tool: str, fixture: Fixture, params: dict[str, Any]) -> Any:
    from docmax.core.cancellation import NEVER_CANCELLED
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import NULL_PROGRESS

    def run(target: Path) -> Path:
        router.run(
            tool,
            [DocumentRef.from_path(fixture.path)],
            OutputTarget(destination=target, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            **params,
        )
        return target

    return run


# ---------------------------------------------------------------------------
# Cloud, against a reference server started here
# ---------------------------------------------------------------------------


def _cloud(fixtures: list[Fixture], out: Path) -> list[Measurement]:
    out.mkdir(parents=True, exist_ok=True)
    results: list[Measurement] = []

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        for fixture in fixtures:
            for tool, _binary, _params in _plan(fixture):
                results.append(skipped(tool, "cloud", fixture, "the server extra is not installed"))
        return results

    from docmax.tools import _binaries

    with _reference_server():
        for fixture in fixtures:
            for tool, binary, params in _plan(fixture):
                if _binaries.find(binary) is None:
                    # The server runs the *local* engine, so a missing binary
                    # means the endpoint cannot do it either.
                    results.append(
                        skipped(tool, "cloud", fixture, f"{binary} is not installed on the server")
                    )
                    continue
                results.append(
                    measure(
                        tool,
                        "cloud",
                        fixture,
                        _cloud_runner(tool, fixture, params),
                        out / f"{tool}-{fixture.name}{_suffix(tool, params)}",
                    )
                )
    return results


def _cloud_runner(tool: str, fixture: Fixture, params: dict[str, Any]) -> Any:
    from docmax.cloud_client import CloudClient, CloudConfig
    from docmax.core.cancellation import NEVER_CANCELLED
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import NULL_PROGRESS
    from docmax.tools._cloud import CloudEngine

    engine = CloudEngine(
        tool,
        client=CloudClient(
            CloudConfig(endpoint=f"http://localhost:{SERVER_PORT}", api_key=SERVER_KEY)
        ),
    )

    def run(target: Path) -> Path:
        engine.run(
            [DocumentRef.from_path(fixture.path)],
            OutputTarget(destination=target, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            **params,
        )
        return target

    return run


@contextmanager
def _reference_server() -> Iterator[None]:
    """A real uvicorn on loopback, for the length of the cloud measurements.

    A real server rather than an in-process test client, because the number is
    supposed to include the HTTP round trip. It is still loopback, which is why
    `METHODOLOGY.md` says what the cloud column does and does not represent.
    """
    import uvicorn

    from docmax.server.app import create_app
    from docmax.server.config import ServerSettings

    config = uvicorn.Config(
        create_app(settings=ServerSettings(api_keys=frozenset({SERVER_KEY}))),
        host="127.0.0.1",
        port=SERVER_PORT,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - a wedged server
            raise RuntimeError("the reference server did not start")
        time.sleep(0.05)

    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _write(payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    system = payload["environment"]["platform"].split("-")[0].lower()
    destination = RESULTS_DIR / f"{stamp}-{system}.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def _summarise(payload: dict[str, Any], destination: Path) -> None:
    measured = [row for row in payload["measurements"] if row.get("median_ms") is not None]
    absent = [row for row in payload["measurements"] if row.get("skipped")]

    print(f"Wrote {destination}")
    for row in measured:
        ratio = f"  ratio {row['ratio']}" if "ratio" in row else ""
        print(
            f"  {row['tool']:9} {row['engine']:6} {row['fixture']:9} "
            f"median {row['median_ms']:>9.2f} ms   min {row['min_ms']:>9.2f} ms{ratio}"
        )
    for row in absent:
        print(
            f"  {row['tool']:9} {row['engine']:6} {row['fixture']:9} not measured — {row['skipped']}"
        )

    if not measured:
        print("\nNothing was measured. No numbers may be published from this run.")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
