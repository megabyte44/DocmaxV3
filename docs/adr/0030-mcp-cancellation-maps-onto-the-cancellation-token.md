# ADR 0030 — A cancelled MCP request cancels the DocMax run, through the existing token

**Status:** Accepted · 2026-08-29

## Context

MCP is asynchronous and its server SDK is `anyio`-based. DocMax tools are
**synchronous and blocking** — `core/cancellation.py` says so in its opening
lines and explains the consequence:

> `CancellationToken` … is not an `asyncio.Event`, because tools are synchronous
> and a batch runner is threads.

So an MCP `tools/call` that runs `ocr` over a 500-page scan would block the
event loop for minutes, and the protocol's own cancellation would have nothing
to act on.

How cancellation actually arrives was **read from the installed SDK (`mcp`
2.1.1)** rather than assumed:

- `notifications/cancelled` is applied by the dispatcher. `PeerCancelMode`
  defaults to `"interrupt"`, which *"cancels the handler's scope"*; the
  alternative `"signal"` only sets a flag.
- `ServerRequestContext` — what a low-level handler is actually given — is a
  plain dataclass with `session, lifespan_context, protocol_version, method,
  params, request_id, meta, request` and the two SSE callbacks. It has **no**
  `cancel_requested`. That property lives on `BaseContext`, which
  `ServerRunner` does not hand to low-level handlers.

So the handler cannot poll for cancellation. What it gets is its task being
cancelled, which surfaces as `anyio.get_cancelled_exc_class()`.

## Decision

**The blocking call runs in a worker thread, and task cancellation is
translated into `CancellationToken.cancel()`.**

```python
token = CancellationToken()
try:
    return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
except anyio.get_cancelled_exc_class():
    token.cancel()  # the worker unwinds cooperatively
    raise
```

Three properties, each deliberate:

**`abandon_on_cancel=True`** lets the async side return at once, so the protocol
is answered promptly rather than waiting for a page of OCR to finish. The worker
thread is not killed — nothing kills a Python thread — it observes the token at
its next cooperative check and unwinds.

**The token is the existing one.** `EngineRouter.run` already threads it to every
strategy, `tools/_binaries.py` already wires it to a subprocess kill switch, and
`core/atomic.py` already discards a staged file on the way out. **A cancelled MCP
call therefore leaves no partial output, and that is inherited rather than
built.** There is no second cancellation mechanism, which is the requirement.

**Cancellation is re-raised**, not swallowed. The dispatcher drops the response
for a cancelled request — *"the handler's eventual result or error is dropped,
not written"* — and a handler that returned normally would be lying to its own
runtime.

**Progress is `NULL_PROGRESS`.** MCP has a progress notification, and using it
would need the request's progress token plumbed from `meta` and a `ProgressSink`
that emits async notifications from a worker thread. Not built at M10: it is
additive, it is the one place this interface would need care about thread-safety
across the async boundary, and nothing in the milestone requires it.

## Alternatives considered

**Run tools directly in the event loop.** Simplest, and wrong: one `ocr` call
blocks the whole server, including the `notifications/cancelled` that would stop
it. The deadlock is not hypothetical — the dispatcher reads inbound messages on
that same loop.

**`abandon_on_cancel=False`.** The async side would wait for the worker to
finish, which for a long OCR run means the client's cancellation appears to do
nothing for minutes. Rejected: it converts a prompt cancellation into a hang.

**Poll `ctx.cancel_requested` in the handler.** What the 1.x-era shape suggests,
and it does not exist on the context low-level handlers receive in 2.1.1. This
is exactly the assumption that reading the installed SDK caught.

**Set `PeerCancelMode="signal"` and poll the flag.** Would require threading the
dispatch context into the handler and gives no benefit over translating the
interrupt — the token still has to be cancelled the same way.

**A process per call**, as a stronger isolation boundary. Rejected as out of
scope and against [ADR 0016](0016-jobs-run-in-process.md), which settled the
in-process execution model for the reference server on the same reasoning.

## Consequences

- **Cancellation is cooperative and therefore not instant.** A tool between two
  checks finishes that step first. This is the contract `core/cancellation.py`
  has always had; MCP inherits it rather than weakening it.
- **The abandoned worker outlives the response.** It holds a thread until it
  unwinds, and the staged file is removed by the atomic writer when it does. A
  client that cancels a hundred long runs could accumulate threads; the default
  `anyio` capacity limiter bounds the pool, so the effect is queueing rather
  than exhaustion. Named because it is the real cost of `abandon_on_cancel`.
- **Calls are serialised in practice** by that same limiter's default. M10 makes
  no concurrency claim, consistent with M9's serial batch
  ([ADR 0025](0025-batch-mirrors-names-into-an-output-directory.md)).
- **No progress reaches the client**, so a long call is silent until it returns.
  The most likely first complaint, and the most clearly additive fix.
- **`KeyboardInterrupt` at the server is not the tool's business.** Ctrl-C stops
  the process; the atomic writers still guarantee no partial destination, which
  is the same property the CLI relies on.

## Enforcement

- `tests/unit/test_m10_cancellation.py::test_a_cancelled_call_cancels_the_token`
  — a fake strategy blocks; the surrounding scope is cancelled; the token is
  observed cancelled from inside the worker.
- `::test_a_cancelled_call_leaves_no_destination` — the inherited guarantee,
  asserted rather than assumed, including that no staged file survives.
- `::test_the_mapping_is_not_a_race` — repeated, because a concurrency test
  that passes once has proved little.
- `::test_cancellation_is_re_raised_not_swallowed`.
- `::test_the_tool_runs_off_the_event_loop` — a handler whose strategy blocks
  does not prevent the loop from making progress.
- `tests/unit/test_m10_mcp.py::test_progress_is_the_null_sink` — holds the
  deliberate omission, so it is a decision rather than an oversight.
