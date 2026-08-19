# Plan 02 — One contract suite every tool inherits

**Goal:** the guarantees in the README are verified for *every* tool by a suite
written once, parametrized over the registry. Tool #45 enters it on the day it
registers, without anybody writing a test.

Two problems this solves, and they are different problems:

1. **Authoring time.** Writing "does it leave temp litter / does it corrupt the
   input / is the destination untouched on failure" per tool is ~15 tests × 45
   tools. Almost all of it is the same test with a different `ToolSpec`.
2. **Wall-clock time.** The suite runs on 9 CI legs. If it is naive it becomes
   the slowest thing in the project. Section 6 keeps it fast.

The existing hygiene tests check *structure* (AST walks). This checks
*behaviour*. Same trick, one level up.

---

## 1. Current state

Revised after the M1–M3 stack (PR #10).

- **867 tests pass.** The stack brought real coverage: `test_router.py`,
  `test_merge.py`, `test_m2_tools.py`, `test_compress.py`, `test_cli_*.py`,
  `test_pagespec.py`, `test_binaries.py`.
- Ten tools are implemented — merge, split, rotate, pages, reorder, sanitize,
  metadata, get-info, compress, and ocr (cloud only).
- Still no `tests/contract/`, no `conftest.py`, no shared corpus.
- `hypothesis` is in `[dev]` and still unused.
- The markers `golden`, `property`, `slow`, and `needs_binary` are declared in
  `pyproject.toml` and used by nothing.

This changes the argument for this plan, in both directions.

**Against:** the guarantees are no longer untested. Several of them are checked
inside the per-tool files.

**For, more strongly:** they are checked *per tool*, which is exactly the
duplication this plan predicted. Ten tools have produced ten sets of overlapping
assertions, and the interesting question is no longer "will they diverge" but
"have they already". The first job of this plan is to find out — build the
suite, run it across all ten, and see which tools quietly fail an invariant
their own test file never checked.

That is a much better return than the original framing. A suite written before
any tool exists proves nothing on day one. A suite written across ten
independently-authored tools is a bug hunt.

**Dependency:** needs a generic way to invoke a tool. `EngineRouter.run()`
exists and is enough — the suite does not have to wait for
[plan 01](01-spec-driven-surfaces.md). Do them in either order; 01 first is
slightly better, because the contract suite then also guards its refactor.

## 2. Layout

```
tests/
  conftest.py              # session corpus fixture, fs-snapshot fixture
  contract/
    __init__.py
    corpus.py              # generates fixture documents; nothing binary in git
    harness.py             # snapshot / hash / litter helpers
    samples.py             # per-tool required-param values + the coverage ledger
    test_output_contract.py
    test_failure_contract.py
    test_input_contract.py
```

No binary fixtures are committed. The corpus is **generated** at session scope
from pypdf, which keeps the repo small, keeps the files byte-identical across
platforms, and means a fixture is described by the code that makes it rather
than by a filename someone chose in 2026.

## 3. The corpus

```python
# tests/contract/corpus.py
CASES = {
    "single_page":    "one page, minimal, valid",
    "three_page":     "the ordinary case",
    "many_page":      "500 pages — marked slow",
    "unicode_name":   "ĥéllo wörld.pdf — Windows/macOS filename handling",
    "spaced_name":    "'has spaces.pdf' — argv quoting",
    "empty_file":     "0 bytes with a .pdf extension",
    "truncated":      "valid header, body cut mid-object",
    "not_a_pdf":      "PNG bytes named .pdf",
    "encrypted":      "user password set",
    "no_extension":   "valid PDF, no suffix at all",
}
```

Built once per session into `tmp_path_factory.mktemp("corpus")`, then
**copied** into each test's `tmp_path` — never used in place, so a tool that
mutates its input cannot poison the rest of the session.

`many_page` sits behind `@pytest.mark.slow`.

## 4. The invariants

Every one of these is a claim the README or an ADR already makes. Some are
checked for some tools, in that tool's own file. None is checked for *all*
tools, and no tool's file checks all of them — which is the gap.

Before writing the suite, grep the existing per-tool tests for each invariant
and record which tools already cover it. That inventory is the shortest route
to knowing which of the ten are actually at risk.

| # | Invariant | Source |
|---|---|---|
| I1 | Inputs are byte-identical after any run — success, failure, or cancel | README "your input is never the output" |
| I2 | On success, `ToolResult.outputs` all exist and are non-empty | — |
| I3 | On success, no `.docmax-*` staging file survives anywhere under the destination's parent | ADR 0003 |
| I4 | On failure, the destination is absent (or unchanged if it pre-existed) | ADR 0003 |
| I5 | On failure, no staging file survives | ADR 0003 |
| I6 | On cancellation, `CancelledError` is raised and I4 + I5 hold | `cancellation.py` |
| I7 | Every raised exception is a `DocMaxError` — never a bare `Exception`, never a traceback | README "no tracebacks" |
| I8 | Every raised error has a non-empty `remedy` when `user_fixable` | `errors.py` |
| I9 | Destination equal to an input raises `InPlaceOverwriteError` before any work | ADR 0003 |
| I10 | Existing destination without `--force` raises `OutputExistsError` before any work | ADR 0003 |
| I11 | `ToolResult.engine_used` is `LOCAL` or `CLOUD`, never `AUTO` | `models.py` |
| I12 | A local-engine run opens no sockets | README "stay on your machine" |
| I13 | Corrupt / truncated / not-a-PDF input raises `CorruptDocumentError`, not an engine's native exception | `errors.py` |
| I14 | Encrypted input raises `EncryptedDocumentError` | `errors.py` |
| I15 | Both `is_available()` and `unavailable_reason()` are cheap and side-effect free — no heavy import, no network | `protocols.py` |

I12 is worth the small effort: patch `socket.socket` to raise for the duration
of a local run. "Local means local" is the product's central promise, and it is
the kind of thing that breaks silently when someone adds a version check.

I1 is the cheapest and catches the worst class of bug — hash every input before
and after, always, in a fixture that wraps every contract test.

## 5. The two mechanisms that make it self-maintaining

### The sample ledger

The suite needs valid arguments for a tool it knows nothing about. Optional
params use their declared defaults; **required** params need a value, and only
a human knows a sensible one.

```python
# tests/contract/samples.py
SAMPLES: dict[str, Sample] = {
    "merge": Sample(inputs=("single_page", "three_page"), params={}),
    "ocr":   Sample(inputs=("single_page",), params={"lang": "eng"}),
}
```

```python
def test_every_registered_tool_has_a_contract_sample() -> None:
    """A new tool cannot quietly opt out of the contract suite."""
    missing = {s.name for s in all_specs()} - set(SAMPLES)
    assert not missing, (
        "These tools registered without a contract sample, so nothing is "
        f"checking their guarantees: {sorted(missing)}. Add one to "
        "tests/contract/samples.py."
    )
```

That single test is what makes the suite inheritable rather than opt-in.

### The unimplemented ledger

Smaller than originally planned — ten tools now work — but not redundant.
`ocr` has no local engine, several M4–M8 tools are declared before they are
built, and every future tool passes through this state.

```python
NOT_YET_IMPLEMENTED = frozenset({"ocr"})   # must only ever shrink

def test_unimplemented_ledger_is_accurate() -> None:
    """Fails when a listed tool starts working, forcing the list down."""
    for name in NOT_YET_IMPLEMENTED:
        assert not _is_implemented(name), (
            f"{name} is implemented now. Remove it from NOT_YET_IMPLEMENTED "
            "so the contract suite actually runs against it."
        )
```

Contract tests `pytest.skip` on tools in that set. Without the ledger test, a
tool ships and the skip silently persists forever — which is the failure mode
of every "we'll add tests later" plan.

Tools needing an absent binary (`compress` without Ghostscript) use the
existing `needs_binary` marker instead: skipped locally, **required in CI**.
That policy is already declared in `pyproject.toml` and has never been used.

## 6. Keeping it fast

The suite runs on every push across 3 OSes × 3 Pythons. The existing 867 tests
take **34s** locally on one core; the contract suite adds to that. Budget:
**under 90s per leg** for everything, and it should stay there through M5.

| Lever | Effect |
|---|---|
| `pytest-xdist`, `-n auto` in `addopts` | biggest single win; the suite is embarrassingly parallel. Add to `[dev]`. |
| Session-scoped corpus, copied per test | generation happens once, not 300 times |
| `-m "not slow"` in default `addopts` | keeps `many_page` out of the inner loop |
| One slow leg | run `-m slow` only on ubuntu/3.12, as a separate job |
| `needs_binary` skips locally, required in CI | already the declared policy; now actually used |
| Trim the PR matrix | see below |

The matrix in `ci.yml` is a full 3×3. On pull requests, the cross product buys
little: run **ubuntu/3.11, ubuntu/3.13, macos/3.12, windows/3.12** — every OS
and both ends of the supported Python range, 4 legs instead of 9. Keep the full
3×3 on push to `main` and in `release.yml`, which already re-runs the whole
gate before publishing. Roughly halves PR latency with no real loss of signal.

Add a budget test so this does not rot:

```python
@pytest.mark.slow
def test_contract_suite_stays_under_budget() -> None:
    """A tool that makes the suite 3x slower should have to justify it."""
```

## 7. Fault injection

I4/I5 need a failure *mid-write*, not a failure at validation.

- **Validator failure** — pass a validator that always raises
  `OutputValidationError`. Exercises the real `atomic.py` reject path.
- **Mid-run failure** — monkeypatch the strategy's `run` to write some bytes
  into the staged path and then raise. Exercises the partial-file path.
- **Cancellation** — a `ProgressSink` whose `advance()` cancels the token on
  first call, so cancellation lands *during* work rather than before it. This
  also verifies that engines actually check the token, which is otherwise easy
  to forget and impossible to notice.

The third is the one that will find real bugs in every engine written from M2
onward.

## 8. Acceptance

- [ ] `pytest tests/contract` passes green with every tool skipped via the ledger
- [ ] adding a `ToolSpec` without a sample fails `test_every_registered_tool_has_a_contract_sample`
- [ ] implementing `merge` and forgetting to update the ledger fails `test_unimplemented_ledger_is_accurate`
- [ ] deliberately writing straight to the destination in `merge` fails I3/I4/I5
- [ ] deliberately opening a socket in `merge/local.py` fails I12
- [ ] deliberately mutating an input fails I1
- [ ] `pytest -m "not slow" -n auto` runs under 60s on ubuntu/3.12
- [ ] `pytest-xdist` added to `[dev]`; `addopts` carries `-n auto` and `-m "not slow"`
- [ ] PR matrix trimmed to 4 legs; full 3×3 retained on `main` and in `release.yml`
