"""Generating the fixtures, timing the runs, and recording what the machine was.

Small on purpose. There is no plugin system, no configuration file and no
comparison-against-last-time: this measures two tools and writes what it found,
and every line of it should be readable in one sitting.

## What is measured

Wall clock, in milliseconds, around one whole `EngineRouter.run` — so the number
includes DocMax's own overhead rather than only the binary's. That is the number
a user experiences, and separating them would flatter us.

Not CPU time, and not memory. Both tools shell out to a subprocess, so
attributing either across a process boundary would take more machinery than the
answer is worth, and a wrong attribution is worse than no attribution.

## Median and minimum, not mean

One warmup run, discarded — the first touch of a file pays for a cold page
cache, and that is not what anyone wants to know. Then five timed runs, reported
as **median** and **minimum**. A mean lets one scheduler hiccup move the
headline; the minimum says what the machine can do and the median says what it
usually does, and the gap between them is itself informative.
"""

from __future__ import annotations

import platform
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

#: Discarded. The first run pays for a cold cache and tells nobody anything.
WARMUP_RUNS = 1

#: Enough for a median to mean something, few enough that the whole suite is a
#: coffee rather than a lunch.
TIMED_RUNS = 5


@dataclass(frozen=True, slots=True)
class Fixture:
    """One input, described so the results file says what was measured."""

    name: str
    path: Path
    kind: str
    size_bytes: int


@dataclass(slots=True)
class Measurement:
    """One tool, one engine, one fixture — or the reason there is no number."""

    tool: str
    engine: str
    fixture: str
    runs: list[float] = field(default_factory=list)
    output_bytes: int | None = None
    #: Set when nothing was measured. The row still appears: a gap that says why
    #: is worth more than a missing row.
    skipped: str | None = None

    @property
    def median_ms(self) -> float | None:
        return round(statistics.median(self.runs), 2) if self.runs else None

    @property
    def min_ms(self) -> float | None:
        return round(min(self.runs), 2) if self.runs else None

    def to_payload(self, *, source_bytes: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": self.tool,
            "engine": self.engine,
            "fixture": self.fixture,
            "median_ms": self.median_ms,
            "min_ms": self.min_ms,
            "runs": TIMED_RUNS if self.runs else 0,
            "output_bytes": self.output_bytes,
        }
        if self.skipped:
            payload["skipped"] = self.skipped
        elif self.tool == "compress" and self.output_bytes and source_bytes:
            # Only compress has a ratio worth reporting; a conversion's output
            # size is a property of the target format, not of how well it went.
            payload["ratio"] = round(self.output_bytes / source_bytes, 4)
        return payload


def environment() -> dict[str, Any]:
    """What the machine was, so a number can be compared with another honestly.

    A benchmark without this is a number without units. Recorded from the
    running interpreter rather than from a config file, because the point is to
    describe the run that actually happened.
    """
    from docmax import __version__
    from docmax.tools import _binaries

    return {
        "docmax": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "binaries": {
            binary.name: _binaries.find(binary.name) for binary in _binaries.EXTERNAL_BINARIES
        },
    }


def build_fixtures(root: Path) -> list[Fixture]:
    """The four inputs, generated rather than committed.

    Generated for the reason the test suite generates its fixtures: a committed
    binary is a file somebody has to keep, and one that drifts from what it is
    supposed to represent is worse than no fixture at all.
    """
    from pypdf import PdfWriter

    root.mkdir(parents=True, exist_ok=True)
    fixtures: list[Fixture] = []

    for name, pages in (("small", 10), ("large", 100)):
        path = root / f"{name}.pdf"
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=595, height=842)
        with path.open("wb") as handle:
            writer.write(handle)
        fixtures.append(Fixture(name, path, f"pdf/{pages}pp", path.stat().st_size))

    fixtures.append(_image_heavy(root))

    markdown = root / "notes.md"
    body = "\n\n".join(f"## Section {n}\n\nSome prose for section {n}." for n in range(50))
    markdown.write_text(f"# Benchmark document\n\n{body}\n", encoding="utf-8")
    fixtures.append(Fixture("markdown", markdown, "markdown", markdown.stat().st_size))

    return fixtures


def _image_heavy(root: Path) -> Fixture:
    """A PDF whose weight is pixels, because that is what `compress` acts on.

    Compressing blank pages measures almost nothing — Ghostscript has no images
    to downsample, so the interesting half of the operation never runs.
    """
    import io

    import img2pdf
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    path = root / "images.pdf"
    writer = PdfWriter()
    for shade in range(4):
        frame = Image.effect_noise((900, 1200), 48).convert("RGB")
        if shade:
            frame = frame.rotate(90 * shade, expand=True)
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=92)
        page = img2pdf.convert(buffer.getvalue())
        writer.add_page(PdfReader(io.BytesIO(page)).pages[0])
    with path.open("wb") as handle:
        writer.write(handle)
    return Fixture("images", path, "pdf/image-heavy", path.stat().st_size)


def measure(
    tool: str,
    engine: str,
    fixture: Fixture,
    run: Callable[[Path], Path],
    destination: Path,
) -> Measurement:
    """One cell of the results table: warm up, time five, record."""
    timings: list[float] = []
    produced: Path | None = None

    for index in range(WARMUP_RUNS + TIMED_RUNS):
        target = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
        started = time.perf_counter()
        produced = run(target)
        elapsed = (time.perf_counter() - started) * 1000
        if index >= WARMUP_RUNS:
            timings.append(elapsed)

    return Measurement(
        tool=tool,
        engine=engine,
        fixture=fixture.name,
        runs=timings,
        output_bytes=produced.stat().st_size if produced and produced.exists() else None,
    )


def skipped(tool: str, engine: str, fixture: Fixture, reason: str) -> Measurement:
    """A row that says why there is no number, rather than no row at all."""
    return Measurement(tool=tool, engine=engine, fixture=fixture.name, skipped=reason)


def results_payload(
    measurements: list[Measurement],
    fixtures: list[Fixture],
) -> dict[str, Any]:
    sizes = {fixture.name: fixture.size_bytes for fixture in fixtures}
    return {
        "environment": environment(),
        "method": {
            "warmup_runs": WARMUP_RUNS,
            "timed_runs": TIMED_RUNS,
            "reported": ["median_ms", "min_ms"],
            "clock": "wall",
        },
        "fixtures": [asdict(fixture) | {"path": str(fixture.path)} for fixture in fixtures],
        "measurements": [
            measurement.to_payload(source_bytes=sizes.get(measurement.fixture, 0))
            for measurement in measurements
        ],
    }


__all__ = [
    "TIMED_RUNS",
    "WARMUP_RUNS",
    "Fixture",
    "Measurement",
    "build_fixtures",
    "environment",
    "measure",
    "results_payload",
    "skipped",
]
