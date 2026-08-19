# Core

The foundation layer: the domain types, the boundary contracts, and the two
mechanisms — atomic writing and cancellation — that every operation depends on.

For where Core sits, see [layers.md](../architecture/layers.md#foundation--core).
For what it may import, see
[dependencies.md](../architecture/dependencies.md).

## What Core is responsible for

Expressing the domain, and nothing else. Every other layer speaks these types,
which is exactly why Core cannot depend on any of them — a type that four layers
share cannot be owned by one of them.

| Module | Responsibility |
|---|---|
| `branding.py` | Product identity. The only module with brand literals. |
| `errors.py` | The typed exception hierarchy. |
| `models.py` | `DocumentRef`, `OutputTarget`, `ToolResult`, `Engine`. |
| `protocols.py` | `ProgressSink`, `EngineStrategy`, `Validator`, `NullProgress`. |
| `atomic.py` | The only module permitted to write to a destination. |
| `cancellation.py` | `CancellationToken`, `NEVER_CANCELLED`. |
| `registry.py` | Lazy tool discovery — `ToolSpec`, `Param`, entry points. |
| `router.py` | Engine resolution, the consent gate, timing, the error boundary. |

**Core is complete.** Every module above is implemented and tested.

## What Core does not know

Each of these is a deliberate absence with a check behind it, not an oversight:

- **It cannot name a tool.** Discovery is metadata; `core` never imports
  `docmax.tools`.
- **It cannot tell whether a terminal is attached.** No `rich`, no `typer`.
- **It cannot make a network call.**
- **It cannot end the process.** Library code raises; only the CLI exits.
- **It cannot write to a destination**, except through `atomic.py`.

## Why protocols rather than base classes

Every contract in `protocols.py` is a `Protocol`, so satisfying one is
structural. A strategy in `tools/` does not import `core.protocols` in order to
*be* a strategy, and `core` does not import any strategy in order to *use* one.

This is the mechanism the whole layering rests on. Inheritance would have
inverted it: every tool importing the foundation, and the foundation importing
every tool to type its own registry. `test_protocols.py` asserts the absence of
inheritance directly by checking the MRO — `issubclass` cannot show it, because a
method-only runtime protocol answers `True` to it.

## Models

`DocumentRef` and `OutputTarget` both call `Path.resolve()` on construction, and
that is not incidental. It is what makes the in-place check survive a
case-insensitive filesystem: on Windows and default macOS, `-o DOC.PDF` against
an input of `doc.pdf` is the *same file*, and comparing the strings would miss
it. `resolve()` returns the name as the filesystem stores it, so both sides
normalise before they are compared.

`OutputTarget.resolve()` is where the destructive cases of v2 became
unreachable. Three refusals:

| Condition | Error |
|---|---|
| the destination is any input | `InPlaceOverwriteError` |
| it exists and `force` is not set | `OutputExistsError` |
| its parent directory does not exist | `OutputNotWritableError` |

The in-place check runs *after* the default destination is derived, because the
dangerous path is often the one the user never typed: `convert x.pdf --to pdf`
derives its own input.

Engine signatures accept an `OutputTarget`, never a `Path`. Passing a bare path
is a type error under strict mypy — that is the mechanism that stops the checks
being skipped by accident rather than by intent.

The models are frozen. They cross threads and layers, and a target mutated after
`resolve` had checked it would defeat the check entirely.

## Progress

`ProgressSink` has three methods and no state. It is why `core` can be forbidden
from importing `rich`: progress crosses the boundary as three calls rather than
as a `rich.progress.Progress` object, so the same engine reports into a terminal
bar, a Textual widget, a job row, or nothing.

**Who implements it:** each interface. **Who calls it:** engines. Engines never
construct one — they are handed one.

Implementations must tolerate being called from a worker thread and must not
raise. Failing to *report* progress is never a reason to fail an operation that
is otherwise succeeding.

`NullProgress` is not an optimisation; it deletes a branch. With it, no engine
contains `if progress is not None`, so there is one path through every operation
and it is the one exercised in tests.

## Cancellation

The full contract is in the module docstring. In summary:

- **Created by** the interface layer, at the top of a user-initiated operation.
  Tools never create their own — a tool that constructs a token has made itself
  uncancellable by its caller.
- **Observed by** whatever is doing the work, at points where stopping is safe:
  between pages, between files, around a subprocess.
- **Propagates** three ways: `raise_if_cancelled()` for code that can poll,
  `on_cancel()` for code that cannot (killing Ghostscript, closing a socket),
  and `child()` for sub-operations, whose deadlines accumulate down the chain so
  a child can never outlive its parent.
- **After cancellation, do nothing special.** `CancelledError` is
  `user_fixable = False` and means the user got what they asked for. Because
  every write goes through `atomic.py`, the destination is untouched — so "stop"
  is always safe and no operation needs a cleanup path of its own.

Two properties that constrain the design:

**No thread is ever started.** A deadline is observed when someone looks, not by
a timer firing in the background. A library that spawns a watchdog per operation
misbehaves inside somebody else's application — the same reasoning that forbids
`sys.exit` in library code.

**A deadline stops the next checkpoint, not the current instruction.** That
follows directly from having no timer, and it is why `remaining_seconds()` exists:
every subprocess call passes it as a timeout, so the operating system enforces
the deadline for the one thing that cannot be polled.

## Atomic writes

Three helpers, one shape: stage beside the destination, validate, `os.replace()`.

| Helper | For |
|---|---|
| `atomic_write` | output this process writes (yields a file handle) |
| `atomic_path` | output an external program writes (yields a path) |
| `atomic_dir` | many outputs delivered as a unit (`split`, `to-images`) |

Staging happens in the destination's own directory, not the system temp
directory, so the final step is a rename rather than a cross-device copy. The
cost is that a full volume fails at write or validate time — which is the trade
[ADR 0003](../adr/0003-atomic-writes.md) accepts.

Details that exist because of a specific failure:

- Cleanup catches `BaseException`, not `Exception`. `KeyboardInterrupt` does not
  inherit from `Exception`, and Ctrl-C is the case this exists for.
- `atomic_path` rejects an empty or absent staged file. Ghostscript can exit zero
  having written nothing, and replacing a real document with a valid-looking husk
  is worse than failing.
- `atomic_dir` moves an existing destination aside before the swap rather than
  deleting it, so a failure mid-swap leaves the old tree recoverable.
- A validator that raises something other than `OutputValidationError` is wrapped
  rather than propagated, so callers can rely on the documented failure type.

## The router

`EngineRouter` is the single path from "run this tool" to "here is the result".
Every interface calls it and nothing else, which is what stops each of them
growing its own orchestration.

It owns exactly the things that would otherwise be implemented once per tool or
once per interface: engine resolution, the consent gate, cancellation and
progress plumbing, timing, dry runs, and the boundary where an untyped exception
becomes an `InternalError` rather than a traceback in someone's terminal.

It owns nothing about documents. If this module ever imports `pypdf`, the design
has failed.

### Resolution

The ladder, highest first — an explicit argument, then `[tools.<name>] engine`,
then the global `engine`, then `auto`:

```
resolve(tool, requested=None)
    ↓  requested ?? config.engine_for(tool)      # config falls back to default_engine
    ↓  AUTO → local if available, else cloud
    ↓  cloud → offline? → consent? → allowed
```

Two rules sit above the ladder:

**`offline` beats everything**, including an explicit `--engine cloud`. It is
checked before consent, so a policy decision never surfaces to the user as a
question they could answer.

**Every route to the cloud passes the consent gate** — the explicit one and the
automatic fallback alike. The fallback is the branch that matters: it is where a
document would otherwise be uploaded because a local dependency happened to be
missing. A `None` consent store reads as *nothing consented*, so a caller who
forgot to supply one cannot thereby gain permission.

`Routing` carries the engine *and the reason*. The reason is what `--dry-run`
prints and what makes `NoEngineAvailableError` name both halves of the failure
rather than asserting that nothing worked.

### What the router guarantees to strategies

- `progress` and `cancellation` are always real objects — `NULL_PROGRESS` and
  `NEVER_CANCELLED` are substituted — so no engine needs a `None` check.
- `progress.finish()` runs in a `finally`, so a failure cannot leave a live
  progress region open.
- Cancellation is checked *before* resolution, so a cancelled batch stops
  without even loading the next tool's strategy module.
- `duration_ms` is filled from wall-clock time unless the strategy timed itself.

## Extension points

- **A new domain type** belongs here only if more than one layer speaks it.
  Otherwise it belongs to the layer that uses it.
- **A new protocol** belongs here only if it is a real boundary between layers.
  A protocol with one implementation and one caller is indirection, not
  architecture.
- **A new validator** is any callable taking a `Path`; nothing needs registering.

## Testing

`tests/unit/test_{models,protocols,atomic,cancellation}.py`, plus the hygiene
suite. Core has no third-party dependency, so its tests need no fixtures, no
network and no UI framework.

Two conventions worth keeping:

**Test the refusal, not the happy path.** "We write atomically now" is a claim
about what happens when something goes wrong; a successful write demonstrates
nothing. Most of `test_atomic.py` and `test_models.py` assert that an operation
*declined* and left the destination untouched.

**Probe the filesystem rather than the platform.** macOS is usually
case-insensitive but can be formatted either way. `test_models.py` asks the
filesystem and asserts accordingly, because assuming is wrong somewhere in a
three-platform matrix.

## Known limitations

- `atomic_dir`'s swap is two renames wide, not one. A hard kill inside that
  window leaves the old tree beside the destination under a dotted name rather
  than at it. Genuinely atomic directory replacement is not available on the
  platforms this targets.
- `_remove_tree` is hand-rolled rather than `shutil.rmtree`. It handles the trees
  this module creates and nothing more adversarial.
- A deadline cannot interrupt a single long-running call inside the process. Only
  subprocesses get hard enforcement, via `remaining_seconds()`.
