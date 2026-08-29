# ADR 0015 — Cancellation reaches a cloud job, and a cloud job has a deadline

**Status:** Accepted · 2026-08-27

## Context

`CancellationToken` is a required argument on every `EngineStrategy.run`.
`core/protocols.py` explains why it is required rather than optional: an
optional would put `if cancellation is not None` at the top of every engine, and
the branch that runs in tests would be the one users never hit.

Every local engine honours it. `_binaries.run` takes its subprocess timeout from
`cancellation.remaining_seconds()` and registers `process.kill` with
`on_cancel`, so Ctrl-C during a two-minute Ghostscript run stops it.

The cloud client honours none of it:

```python
def wait(self, job: CloudJob, *, timeout: float = DEFAULT_JOB_TIMEOUT) -> CloudJob:
    deadline = time.monotonic() + timeout
    while not current.is_terminal:
        ...
        time.sleep(current.poll_after_ms / 1000)
```

`time.sleep` is uninterruptible by a cooperative token, and `wait()` has no
parameter to pass one through. So Ctrl-C during a cloud OCR of a long document
does nothing until the poll returns — up to `poll_after_ms`, and then the loop
continues, because nothing checks. The user's only exit is a second interrupt
that drops a `KeyboardInterrupt` wherever the interpreter happens to be, which
is precisely the outcome `cli/execution.py`'s interrupt handling exists to
prevent.

This is the same class of defect v2 shipped — *"No subprocess had a timeout; a
hung `xelatex` hung DocMax indefinitely"* — arriving through a different door.
The client's own `DEFAULT_JOB_TIMEOUT` of 900 seconds means the current failure
mode is "unresponsive for up to fifteen minutes" rather than "forever", which is
better and is not good.

## Decision

**`CloudClient.wait()` and `CloudClient.run()` accept a `CancellationToken`,
and honour it.** Three behaviours:

- **The sleep is interruptible.** The wait between polls is broken into short
  intervals with a cancellation check between them, so a cancel takes effect
  within a bounded time rather than within `poll_after_ms`. The server's
  requested interval is still respected in aggregate — this changes how DocMax
  waits, not how often it asks.
- **The deadline comes from the token when the token has one.**
  `cancellation.remaining_seconds()` is what `_binaries.run` already uses, so a
  deadline set once at the top of an operation applies to a subprocess and to a
  cloud job identically. `DEFAULT_JOB_TIMEOUT` remains the fallback when the
  token is unbounded.
- **A cancelled wait raises `CancelledError`**, the same typed error a local
  engine raises, so `cli/execution.py`'s existing handling — the yellow line,
  exit 130, "nothing was written" — applies unchanged.

**`NEVER_CANCELLED` is the default**, matching `EngineRouter.run`. A library
caller that passes nothing gets today's behaviour and no `None` check appears
anywhere.

**Cancelling does not attempt to cancel the job server-side.** `cloud-api.md`
has no endpoint for it, and inventing one is a wire-contract change M6 is not
making. DocMax stops waiting; the endpoint finishes or times out on its own
terms, and its data-handling rules say the document is deleted on completion or
failure either way.

**Uploads and downloads are not made interruptible.** They are single `httpx`
calls with a connect and read timeout already, so they are bounded; making the
byte transfer itself cancellable means streaming it, which is a larger change
than M6 needs.

## Alternatives considered

**Leave `wait()` alone and have the strategy poll `submit`/`poll` itself.** No
client change, and the strategy already has the token. Rejected: it moves the
polling loop — including the `poll_after_ms` contract, the deadline, and the
backoff — into the tools layer, and then into *each* cloud strategy. The client
owns server-controlled polling because it is a contract requirement, and it
should own it once.

**Thread a `threading.Event` instead of the token.** Rejected: `core` already
has the cancellation contract, `CancellationToken.on_cancel` already exists for
exactly this, and a second cancellation primitive in the client would be a
parallel mechanism to keep in sync.

**Add a job-cancel endpoint to the wire contract.** The complete answer, and
genuinely better — a user who cancels probably wants the billing to stop.
Rejected for M6 because it changes `cloud-api.md`, needs a server endpoint, and
would need a decision about whether a cancelled job is billed. Worth doing;
worth doing deliberately, with the server.

**Make the sleep interruptible with a background timer thread.**
`core/cancellation.py` says explicitly that *"nothing here starts a thread"* and
that a deadline is observed when someone looks. Honouring that is why the sleep
is chunked rather than event-driven.

## Consequences

**What it costs.**

- The poll loop wakes more often than it polls. Negligible, and it is
  bookkeeping rather than network traffic.
- **A cancelled cloud job keeps running on the server, and may be billed.**
  DocMax stops waiting and says nothing was written locally, which is true; it
  cannot say nothing happened remotely. This is the honest cost of not having a
  cancel endpoint, and `docmax cloud` help says so.
- Cancelling during the upload or the download waits for that transfer to
  finish or time out. Bounded, but not immediate.
- `CloudClient.wait()`'s signature changes. The client has no external users —
  no test imports it today — so the blast radius is this repository.

**What it buys.** Ctrl-C means the same thing whichever engine ran, one
deadline covers a whole operation whether it spends its time in Ghostscript or
on the network, and the interrupt path that `cli/execution.py` documents
actually works for cloud.

## Enforcement

- A test cancels a token mid-poll against a `respx` transport that never
  reaches a terminal status, and asserts `CancelledError` within a bounded time
  rather than after `poll_after_ms`.
- A test asserts a token constructed with a timeout stops `wait()` at roughly
  that deadline rather than at `DEFAULT_JOB_TIMEOUT`. It surfaces as
  `CancelledError`, not `CloudTimeoutError`: `core/cancellation.py` defines a
  lapsed deadline *as* cancellation, and the client observing that consistently
  is the point. `CloudTimeoutError` remains what the client's own `timeout=`
  budget produces, and has its own test.
- A test drives a cloud strategy through the router with an already-cancelled
  token and asserts no HTTP request is made at all — the router's
  `raise_if_cancelled` runs first.
- A CLI test asserts a cancelled cloud run exits 130 and writes nothing.
- Nothing enforces that the *server* stops working. It cannot be enforced from
  this side, and this ADR says so rather than implying otherwise.
