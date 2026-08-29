# ADR 0016 — Cloud jobs run in the server process, and the job store is in memory

**Status:** Accepted · 2026-08-27

**Closes** the "Execution model" item that
[backlog.md](../planning/backlog.md#decisions-owed-an-adr) has listed as owed
since Phase 1: *"Whether jobs are in-process or queued, and what that means for
cancellation crossing a process boundary."*

## Context

The reference server accepts a request, validates it, creates a job, and then
stops:

```python
def start(self, job: Job, payload: bytes, *, filename: str) -> Job:
    raise NotImplementedError(
        "This endpoint accepts and validates the request, but does not run the tool yet."
    )
```

M6 has to fill that in, and the shape it takes is the execution-model decision
the backlog has been holding. The backlog's own rule applies: *"Writing the code
first would make the ADR a justification rather than a decision."*

The forces are not balanced. A queue (Redis, Celery, RQ) is what a
production multi-tenant service needs — durable jobs, horizontal workers,
retries that survive a restart. But `server/execution.py` already states what
this server is *for*:

> the server is a machine that already has Ghostscript, Tesseract, and a LaTeX
> distribution installed, running exactly the code a user would run if they had
> installed them too.

And [ADR 0006](0006-reference-server-location.md) places it in-tree as the
*reference* implementation a self-hoster deploys — not as the hosted service's
production topology. A self-hoster running one container for their own
documents does not want a broker.

There is also a hard constraint from M6's own scope: the only way to verify a
cloud engine end to end is to run a client against this server. If the server
needs a broker to run at all, then testing M6 needs a broker, and the two
`needs_binary` tests become a `needs_docker` suite.

## Decision

**Jobs run in-process, synchronously, inside the request that submits them.**
`RegistryRunner.start()` stages the payload under a temp path, builds a
`DocumentRef` and an `OutputTarget` over it, calls
`spec.load_strategy(Engine.LOCAL).run(...)`, publishes the output, and discards
the input — on success and on failure alike, because the contract says documents
are deleted on completion, not on success.

**The wire contract does not change.** `cloud-api.md` already allows a
synchronous answer: the small-file path returns `200` with the output, and the
large-file path returns `202` with a job to poll. An in-process runner answers
`200` for both. The client handles it already — `CloudJob.from_payload` treats a
missing `status` with an output present as `succeeded`, and `wait()` returns
immediately for a terminal job. **So a future queued implementation can start
returning `202` without any client change**, which is the property that makes
this decision reversible.

**The job store stays in memory**, as `server/jobs.py` already implements it,
including the idempotency-key index. Jobs and their idempotency records do not
survive a restart.

**This is the reference server's model, not a claim about the hosted service.**
The hosted service may run anything behind the same contract. That is what
having a contract is for.

## Alternatives considered

**A task queue — Celery, RQ, or Dramatiq with Redis.** The right answer for a
service that must not lose a job across a deploy. Rejected for the reference
server: it adds a broker to `pip install -e ".[server]"`, makes local
development a `docker compose` problem, and buys durability for a deployment
that is one container serving its owner's documents. It also collides with
non-negotiable #3's spirit — the server extra is already the heaviest thing in
the repository.

**`asyncio.create_task` — in-process but asynchronous**, returning `202` and
polling against an in-memory job. Closer to the eventual shape and no new
dependency. Rejected for M6 on two counts: the tool engines are blocking
(Ghostscript subprocesses, pypdf), so a task would need a thread pool to avoid
stalling the event loop, and an in-memory job that outlives its request but not
a restart is the *worst* durability story — it looks asynchronous and is not.
Synchronous is honest about what it is.

**A thread pool with a bounded queue.** Same objection: it adds concurrency
control and a backpressure decision to a server whose scaling story is "run
another one".

**Persist jobs to SQLite.** Durability without a broker. Rejected as
speculative for M6 — nothing needs a job to survive a restart yet, and adding a
schema now means migrating it later for a requirement nobody has stated.

## Consequences

**What it costs, and these are real.**

- **A long job holds a connection open for its whole duration.** A five-minute
  OCR is a five-minute HTTP request, and any proxy in front of the server needs
  a matching timeout. The client's `read_timeout` defaults to 120s, which is
  *less* than `DEFAULT_JOB_TIMEOUT` — so a genuinely long synchronous job will
  hit the read timeout before the job timeout. M6 must reconcile those two
  numbers, and this is the first place that mismatch is written down.
- **Jobs and idempotency keys are lost on restart.** A client retrying across a
  server restart re-runs the work rather than getting the original result,
  which weakens the idempotency guarantee `cloud-api.md` makes. The guarantee
  holds within a process lifetime.
- **No concurrency control.** Two large simultaneous requests get two
  simultaneous Ghostscript processes. Acceptable for a single-user deployment;
  it is not a multi-tenant server and this ADR is where that stops being
  implied.
- The `202` path is specified, exercised by the client, and not produced by
  this server. It is contract surface that only a future implementation
  reaches.

**What it buys.** The server runs from a checkout with no infrastructure, which
is what makes end-to-end verification of M6's cloud engines possible at all. And
because the client already handles both answers, replacing this with a queue
later is a server-side change with no client, contract, or test churn.

## Enforcement

- A test runs a real tool through `RegistryRunner.start()` and asserts the job
  reaches `succeeded` with an output, within the request.
- A test asserts the staged input is deleted after a **failed** job, not only a
  successful one — the half of the data-handling rule that is easy to miss.
- A test asserts a repeated `Idempotency-Key` within one process returns the
  original job rather than re-running.
- A test asserts `RegistryRunner.resolve()` still refuses a tool with no cloud
  engine, so the in-process runner does not become a way to run anything.
- Nothing enforces durability, concurrency limits, or the proxy timeout. They
  are not implemented, and this section names them rather than leaving their
  absence to be discovered.
