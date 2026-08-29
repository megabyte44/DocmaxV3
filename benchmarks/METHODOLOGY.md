# Benchmark methodology

The rule this exists to serve is [roadmap.md](../docs/planning/roadmap.md)'s:

> **No numbers appear in the README or anywhere else until they are measured** —
> a performance claim without a method behind it is marketing.

So this document comes before any number, and every number that is ever
published points back at it.

## Run it

```bash
pip install -e ".[dev,all,server]"
python -m benchmarks                 # local and cloud
python -m benchmarks --no-cloud      # local only, no server started
```

Results land in `benchmarks/results/<date>-<platform>.json`.

## Scope: two tools, four fixtures

Deliberately small. `compress` and `convert` are the two tools with cloud
engines ([ADR 0012](../docs/adr/0012-cloud-engines-are-compress-and-convert.md)),
so they are the two where "local versus cloud" is a real question rather than a
hypothetical one. Everything else in DocMax is pure pypdf and runs in
milliseconds; benchmarking it would produce numbers nobody needs.

| Fixture | What it is | Why |
|---|---|---|
| `small` | 10-page PDF | The common case |
| `large` | 100-page PDF | Where per-page cost shows |
| `images` | 4-page PDF of JPEG noise, ~3 MB | **The only one that exercises compression properly** — Ghostscript has no images to downsample in a blank page, so a benchmark over blank pages measures the wrong half of the operation |
| `markdown` | ~50-section Markdown document | `convert`'s input |

Fixtures are **generated, not committed**, for the reason the test suite
generates its own: a committed binary is a file somebody has to maintain, and
one that drifts from what it is supposed to represent is worse than none.

## What is measured

**Wall clock, in milliseconds, around one whole `EngineRouter.run`.** That
includes DocMax's own overhead — argument validation, atomic staging, output
validators — and not just the subprocess. It is the number a user experiences.
Timing only the binary would flatter us.

**Output size in bytes**, and for `compress` only, the **ratio** of output to
input. A conversion's output size is a property of the target format rather
than of how well the conversion went, so reporting a ratio there would invite a
meaningless comparison.

### What is not measured, and why

- **CPU time and memory.** Both tools shell out. Attributing either across a
  process boundary takes more machinery than the answer is worth, and a wrong
  attribution is worse than no attribution.
- **Anyone else's hardware.** A results file describes the machine it ran on,
  which is recorded in it.
- **Comparisons against other tools.** Not what this is for.

## Statistics: one warmup, five runs, median and minimum

The warmup run is discarded. The first touch of a file pays for a cold page
cache, and that is not what anyone wants to know.

Five timed runs follow, reported as **median** and **minimum** — never a mean.
A mean lets one scheduler hiccup move the headline. The minimum says what the
machine can do; the median says what it usually does; the gap between them is
itself informative, which is why both are published.

Each run writes to a fresh destination, because overwriting an existing file and
creating a new one are different amounts of work, and mixing them would make the
first timed run quietly different from the rest.

## The cloud column is loopback, and says so

`python -m benchmarks` starts a **real uvicorn reference server on
127.0.0.1** and measures against it — a real HTTP round trip, not an in-process
test client.

**It is not a measurement of the hosted service, and it is not a measurement of
your internet connection.** It is DocMax's own overhead plus loopback HTTP plus
the same local engine, because [ADR 0016](../docs/adr/0016-jobs-run-in-process.md)
has the reference server run the local strategy in-process. Read the cloud
column as *the cost of the round trip*, which is the part that is stable enough
to be worth measuring. Network latency to a real endpoint is a property of a
network, and publishing it as a property of DocMax would be dishonest.

## A tool that could not run gets a row saying so

If Ghostscript or Pandoc is absent, the results file contains the row with
`"skipped": "gs is not installed"` and no timing — rather than omitting it. A
gap that explains itself is worth more than a missing row, and it makes "this
run measured nothing" impossible to mistake for "this run was fast".

`tests/unit/test_benchmarks.py` asserts that every row in every committed
results file either carries a median or says why it does not.

## CI does not publish numbers

CI runners are shared, virtualised and noisy, and a number from one is not a
number about DocMax. The suite's *harness* is tested in CI —
`tests/unit/test_benchmarks.py` runs on every commit, with a fake binary
standing in for a real one — but no results file is generated or published
there.

## What is published, and where

Committed results files under `results/`. Nothing else — no numbers in the
README, no numbers in the roadmap, no numbers in a release note, until they are
measured and a file here backs them.

### Current state

`results/` currently contains one file in which **every row is `skipped`**: the
development machine has neither Ghostscript nor Pandoc installed, so nothing was
measured. It is committed as evidence of the format and of the rule, not as a
performance claim. There are no published DocMax performance numbers yet.
