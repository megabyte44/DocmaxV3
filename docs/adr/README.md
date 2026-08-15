# Architecture decision records

Each ADR records one decision that constrains the code: what was decided, why,
what else was considered, and what it costs. They are the answer to "why is this
like this?" for anyone — including a future maintainer — who did not attend the
conversation.

ADRs are **immutable once accepted**. A decision that no longer holds is not
edited; a new ADR supersedes it, and both stay in the record. The reasoning that
turned out to be wrong is as useful as the reasoning that held.

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-python-311.md) | Python 3.11 is the minimum | Accepted |
| [0002](0002-registry-mechanism.md) | Lazy self-registering tool registry | Accepted |
| [0003](0003-atomic-writes.md) | All output is written atomically; destinations are types | Accepted |
| [0004](0004-open-core-boundary.md) | Open-core boundary — every document operation is free | Accepted · superseded in part by 0006 |
| [0005](0005-gui-pickers.md) | GUI pickers over localhost | Accepted |
| [0006](0006-reference-server-location.md) | The reference server lives in this repository, and is open | Accepted |

## When to write one

Write an ADR when a decision affects any of:

- the architecture or the direction of a dependency
- a public API or wire contract
- the execution model, storage strategy, or plugin model
- cloud integration, authentication, or the open-core line
- packaging, or a major technology choice

Do **not** write one for an implementation detail. "We used a `frozenset` here"
is a code comment. "Tools are discovered lazily rather than imported eagerly" is
an ADR, because someone will otherwise 'simplify' it back.

A useful test: if reverting the decision would require changing several files
across more than one layer, it is an ADR.

## Format

```markdown
# ADR NNNN — Title

**Status:** Accepted · YYYY-MM-DD

## Context
What forced a decision. Include the constraint or failure that made the status quo untenable.

## Decision
What was decided, stated so it can be checked against the code.

## Alternatives considered
What else was on the table, and why it lost. An ADR with no alternatives is a description.

## Consequences
What this costs as well as what it buys. Name the cost explicitly.

## Enforcement
The test, lint contract, or CI job that keeps this true. If nothing enforces it, say so.
```

The **Enforcement** section is not optional. This project's convention is that
architectural rules are enforced by tests rather than by review — see the
structural guarantees in
[architecture/overview.md](../architecture/overview.md#the-structural-guarantees).
An ADR with no enforcement is a rule that will erode, and saying "nothing
enforces this yet" is a legitimate answer that makes the gap visible.

## Conventions

- **Location:** this directory. The numbering scheme `NNNN-kebab-title.md` is
  already referenced from the README, the changelog, source comments and CI, so
  it stays as it is rather than being renamed to match any external template.
- **Numbering:** four digits, allocated in order, never reused.
- **Updating the index:** adding an ADR without adding its row here is
  incomplete work. The table above is how anyone finds them.
- **Superseding:** mark the old ADR's status `Superseded by NNNN` and link both
  ways.
