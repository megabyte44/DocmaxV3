"""The benchmark harness, self-tested.

A benchmark suite that has never run is a benchmark suite that does not work,
and the first time anyone finds out is when they want a number. So the harness
is exercised here like any other code.

**No real binary is required, and no real timing is asserted.** A test that
depended on Ghostscript would not run on most machines, and a test that asserted
"this took under 50ms" would fail on a loaded CI box for reasons that have
nothing to do with DocMax. What is checked is the shape of the thing: that
fixtures generate, that the statistics are the ones the methodology promises,
that a missing binary produces a *row saying so* rather than silence, and that
nothing invents a number.

The measuring path itself is proven with a fake binary, following the pattern
`test_compress.py` established at M3 — a real subprocess, so the timing loop is
genuinely exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks import harness
from docmax.tools import _binaries


@pytest.fixture
def fixtures(tmp_path: Path) -> list[harness.Fixture]:
    return harness.build_fixtures(tmp_path / "fixtures")


# ---------------------------------------------------------------------------
# Fixtures are generated, not committed
# ---------------------------------------------------------------------------


def test_the_suite_generates_four_fixtures(fixtures: list[harness.Fixture]) -> None:
    """Three PDFs and one Markdown file — the approved methodology, exactly."""
    assert len(fixtures) == 4
    assert sum(1 for item in fixtures if item.kind.startswith("pdf")) == 3
    assert sum(1 for item in fixtures if item.kind == "markdown") == 1


def test_every_fixture_exists_and_is_not_empty(fixtures: list[harness.Fixture]) -> None:
    for item in fixtures:
        assert item.path.exists(), f"{item.name} was not written"
        assert item.size_bytes > 0
        assert item.path.stat().st_size == item.size_bytes


def test_the_pdf_fixtures_are_readable_pdfs(fixtures: list[harness.Fixture]) -> None:
    from pypdf import PdfReader

    for item in fixtures:
        if item.kind.startswith("pdf"):
            assert len(PdfReader(str(item.path)).pages) > 0


def test_the_image_heavy_fixture_is_actually_heavy(
    fixtures: list[harness.Fixture],
) -> None:
    """Compressing blank pages measures nothing — Ghostscript has no images to touch.

    So the fixture that exists to exercise compression has to contain pixels,
    and this is what stops it quietly becoming four blank pages again.
    """
    by_name = {item.name: item for item in fixtures}

    assert by_name["images"].size_bytes > 10 * by_name["large"].size_bytes


def test_fixtures_are_regenerated_rather_than_reused(tmp_path: Path) -> None:
    """Two runs into the same directory must not accumulate or diverge."""
    first = harness.build_fixtures(tmp_path / "f")
    second = harness.build_fixtures(tmp_path / "f")

    assert [item.name for item in first] == [item.name for item in second]


# ---------------------------------------------------------------------------
# The statistics are the ones the methodology promises
# ---------------------------------------------------------------------------


def test_a_measurement_reports_median_and_minimum() -> None:
    measurement = harness.Measurement(
        tool="compress", engine="local", fixture="small", runs=[10.0, 30.0, 20.0]
    )

    assert measurement.median_ms == 20.0
    assert measurement.min_ms == 10.0


def test_a_measurement_with_no_runs_reports_no_numbers() -> None:
    """The rule the whole package exists for: nothing is invented."""
    measurement = harness.Measurement(tool="compress", engine="cloud", fixture="small")

    assert measurement.median_ms is None
    assert measurement.min_ms is None


def test_the_methodology_is_one_warmup_and_five_timed_runs() -> None:
    assert harness.WARMUP_RUNS == 1
    assert harness.TIMED_RUNS == 5


def test_compress_reports_a_ratio_and_convert_does_not() -> None:
    """A conversion's output size is a property of the target format, not of quality."""
    compressed = harness.Measurement(
        tool="compress", engine="local", fixture="small", runs=[1.0], output_bytes=500
    ).to_payload(source_bytes=1000)
    converted = harness.Measurement(
        tool="convert", engine="local", fixture="markdown", runs=[1.0], output_bytes=500
    ).to_payload(source_bytes=1000)

    assert compressed["ratio"] == 0.5
    assert "ratio" not in converted


def test_a_skipped_measurement_says_why_and_carries_no_ratio() -> None:
    fixture = harness.Fixture("small", Path("x"), "pdf", 1)

    payload = harness.skipped("compress", "local", fixture, "gs is not installed").to_payload(
        source_bytes=1000
    )

    assert payload["skipped"] == "gs is not installed"
    assert payload["median_ms"] is None
    assert payload["runs"] == 0


# ---------------------------------------------------------------------------
# The measuring path, with a fake binary
# ---------------------------------------------------------------------------


FAKE_PANDOC = """
out = args[args.index("--output") + 1]
open(out, "w", encoding="utf-8").write("<html>converted</html>\\n")
"""


def install_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    script = tmp_path / "fake_binary.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] in {'-v', '--version'}:\n"
        "    print('fake 1.0')\n"
        "    sys.exit(0)\n" + body,
        encoding="utf-8",
    )
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


def test_measure_runs_the_operation_the_promised_number_of_times(tmp_path: Path) -> None:
    """One warmup plus five timed, and only the five are recorded."""
    calls: list[Path] = []

    def run(target: Path) -> Path:
        calls.append(target)
        target.write_text("x", encoding="utf-8")
        return target

    fixture = harness.Fixture("small", tmp_path / "in.pdf", "pdf", 1)
    measurement = harness.measure("compress", "local", fixture, run, tmp_path / "out.pdf")

    assert len(calls) == harness.WARMUP_RUNS + harness.TIMED_RUNS
    assert len(measurement.runs) == harness.TIMED_RUNS


def test_each_run_writes_to_a_fresh_destination(tmp_path: Path) -> None:
    """Overwriting and creating are different amounts of work; mixing them skews run one."""
    seen: list[Path] = []

    def run(target: Path) -> Path:
        seen.append(target)
        target.write_text("x", encoding="utf-8")
        return target

    fixture = harness.Fixture("small", tmp_path / "in.pdf", "pdf", 1)
    harness.measure("compress", "local", fixture, run, tmp_path / "out.pdf")

    assert len(set(seen)) == len(seen)


def test_the_local_runner_measures_a_real_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end through the real router, with a fake Pandoc as the binary."""
    from benchmarks.__main__ import _local

    install_fake(monkeypatch, tmp_path, FAKE_PANDOC)
    fixtures = [item for item in harness.build_fixtures(tmp_path / "f") if item.kind == "markdown"]

    results = _local(fixtures, tmp_path / "out")

    assert len(results) == 1
    assert results[0].tool == "convert"
    assert results[0].skipped is None
    assert results[0].median_ms is not None
    assert results[0].median_ms > 0
    assert results[0].output_bytes


def test_a_missing_binary_produces_a_row_that_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gap that explains itself beats a missing row."""
    from benchmarks.__main__ import _local

    monkeypatch.setattr(_binaries, "find", lambda name: None)
    fixtures = [item for item in harness.build_fixtures(tmp_path / "f") if item.kind == "markdown"]

    results = _local(fixtures, tmp_path / "out")

    assert results[0].skipped is not None
    assert "pandoc" in results[0].skipped
    assert results[0].median_ms is None


# ---------------------------------------------------------------------------
# The results file
# ---------------------------------------------------------------------------


def test_the_results_payload_records_what_the_machine_was(
    fixtures: list[harness.Fixture],
) -> None:
    """A benchmark without this is a number without units."""
    payload = harness.results_payload([], fixtures)

    assert set(payload["environment"]) >= {"docmax", "python", "platform", "binaries"}
    assert payload["method"]["warmup_runs"] == harness.WARMUP_RUNS
    assert payload["method"]["timed_runs"] == harness.TIMED_RUNS


def test_the_results_payload_is_json_serialisable(
    fixtures: list[harness.Fixture],
) -> None:
    import json

    measurement = harness.Measurement(
        tool="compress", engine="local", fixture="small", runs=[1.0, 2.0], output_bytes=10
    )
    payload = harness.results_payload([measurement], fixtures)

    assert json.loads(json.dumps(payload))["measurements"][0]["tool"] == "compress"


def test_the_environment_records_which_binaries_were_present() -> None:
    """So a reader can tell a fast run from a run that measured nothing."""
    recorded = harness.environment()["binaries"]

    assert set(recorded) >= {"gs", "pandoc"}


def test_no_results_file_is_committed_with_unmeasured_numbers() -> None:
    """The roadmap's rule, enforced against whatever is actually in the tree.

    Every published row must either carry a median or say why it does not.
    A row with neither would be a number nobody measured.
    """
    import json

    results = Path(__file__).resolve().parents[2] / "benchmarks" / "results"
    if not results.is_dir():
        pytest.skip("no results have been published yet")

    for path in results.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload["measurements"]:
            has_number = row.get("median_ms") is not None
            explains = bool(row.get("skipped"))
            assert has_number or explains, f"{path.name}: {row} is neither measured nor explained"
