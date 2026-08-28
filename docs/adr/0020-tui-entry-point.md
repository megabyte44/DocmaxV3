# ADR 0020 — The CLI launches the TUI, and a bare `docmax` opens it only where there is someone to use it

**Status:** Accepted · 2026-08-28

## Context

Two questions arrived together at M7, and they have the same answer underneath.

**Something has to start the TUI.** `interfaces-are-independent` has forbidden
`cli` and `server` from importing each other since M0, and M7 adds `tui` to that
contract. But `docmax tui` is the documented way in, and turning argv into a call
is the CLI's entire job. Adding the layer made the contract fail immediately:

```
docmax.cli is not allowed to import docmax.tui:
- docmax.cli.main -> docmax.tui (l.115, l.121, l.140)
```

**A bare `docmax` was already promised to the TUI.** `tests/unit/test_cli.py` has
carried this since M0:

> Today it prints help. In M7 it will launch the TUI instead — at which point
> this test changes to assert the app starts, and the exit code becomes 0.

Acting on that literally breaks three things at once: `docmax | less` gets a
screenful of escape sequences, `docmax` in a CI step draws into a log and may
never exit, and `docmax --json` with no subcommand violates
[ADR 0017](0017-json-output-contract.md)'s rule that stdout carries one JSON
object and nothing else.

## Decision

**The independence contract keeps one ignored import, spelled as one module
pair:**

```ini
ignore_imports =
    docmax.cli.main -> docmax.tui
```

`docmax.tui`'s package root exposes exactly three things — `is_available`,
`require_available`, `launch` — and none of them is document logic. That is a
*process entry point*, not a dependency between interfaces.

The narrowness is the decision. `docmax.cli.main -> docmax.tui` is allowed;
anything importing `docmax.tui.app`, `.runner`, `.forms` or `.catalog` is the CLI
reaching into the TUI's internals, stays broken, and is separately asserted by
`test_the_cli_reaches_only_the_tui_entry_point`. **The reverse direction is not
ignored at all**: the TUI may never import the CLI, which is the direction that
would actually rot — it is where `cli/execution.py`'s four lines of router
construction sit, and copying them is correct where importing them is not.

**A bare `docmax` launches the TUI only when all three guards pass:**

| Guard | The failure it prevents |
|---|---|
| `--json` was not given | escape sequences in the stream ADR 0017 reserves for one JSON object |
| stdin **and** stdout are TTYs | `docmax \| less`, a CI step, a cron job, an SSH session with no terminal |
| `textual` is installed | a missing optional dependency met at the worst possible moment |

Otherwise it prints help, exactly as before, and exits `0` — help is not a
failure. `docmax tui` asked for explicitly gets a *typed error* with a remedy
instead of silent help, because there the user said what they wanted.

Both streams are checked, not just one: `stdout` alone would let
`docmax | tee log` open a TUI and write into a pipe; `stdin` alone would miss
output redirection, which is the commoner case.

## Alternatives considered

**A separate `docmax-tui` console script**, leaving the contract untouched.
Rejected: it invents a second command name for one product, `docmax tui` is what
the documentation says, and the entry-point dependency would exist either way —
`pyproject.toml` would simply be where it hid, out of reach of import-linter.

**A package-wide allowance (`docmax.cli -> docmax.tui`).** One line shorter and
much weaker: it would let any CLI module import any TUI module, which is
precisely the traffic the contract exists to forbid. The ignored pair is written
narrowly so that the interesting violation still fails.

**Drop the contract for `tui`.** Rejected outright. The direction that matters —
`tui -> cli` — is the one that would let the TUI grow a dependency on Typer,
Rich and `typer.Exit`, and it stays forbidden.

**Bare `docmax` always launches the TUI**, per the letter of the M0 test.
Rejected: it breaks piping, CI and `--json`, and the M0 comment was written
before `--json` existed.

**Bare `docmax` never launches it.** Safest, and it discards a decision the
project made deliberately and wrote a test about. The guards give both.

## Consequences

- `no_args_is_help=True` is gone from the Typer app; `_no_command` owns the
  choice, because Typer's flag cannot express "unless there is a person here".
- One ignored import in `.importlinter`, which is one more than the project had.
  It is documented in place, in this ADR, and in a test.
- A user on a machine without the `tui` extra sees no behaviour change at all
  from a bare `docmax`.
- `docmax tui` in a script fails fast with `input.invalid_parameter` and a
  remedy, rather than hanging — which is the outcome a script author wants and
  the one `cli/execution.py` already chose for the consent prompt.

## Enforcement

- `.importlinter`'s `interfaces-are-independent` contract, which now includes
  `docmax.tui` and ignores exactly one pair.
- `tests/unit/test_tui.py::test_the_cli_reaches_only_the_tui_entry_point` — an
  AST scan asserting no `docmax.tui.*` import anywhere in `cli/`.
- `tests/unit/test_tui.py::test_the_tui_imports_no_other_interface` — the same,
  in the direction that is not ignored.
- `tests/unit/test_cli.py` covers all four bare-invocation paths (terminal,
  `--json`, non-TTY, and `docmax tui` refusing both).
