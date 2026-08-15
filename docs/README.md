# DocMax documentation

Start here.

## Understanding the project

| | |
|---|---|
| [architecture/overview.md](architecture/overview.md) | What DocMax is and how it is put together — **read this first** |
| [architecture/layers.md](architecture/layers.md) | What each layer is for, and what it may not know about |
| [architecture/dependencies.md](architecture/dependencies.md) | The dependency rules, and which are enforced in CI |
| [adr/](adr/README.md) | Why things are the way they are |

## Where the project is going

| | |
|---|---|
| [planning/current-status.md](planning/current-status.md) | What is true right now |
| [planning/roadmap.md](planning/roadmap.md) | Milestones, in shipping order |
| [planning/phases.md](planning/phases.md) | The engineering order underneath the roadmap |
| [planning/backlog.md](planning/backlog.md) | Known gaps and unscheduled work |
| [planning/reconciliation.md](planning/reconciliation.md) | What happens to the `m1-foundations` branch *(temporary)* |

## Reference

| | |
|---|---|
| [cloud-api.md](cloud-api.md) | The Cloud Engine wire contract *(design stage — no server exists yet)* |
| [migrating-from-v2.md](migrating-from-v2.md) | What changed, and what will break |

## Not written yet

`docs/development/` (setup, testing, contributing) and `docs/implementation/`
are listed in [the backlog](planning/backlog.md#required) and do not exist. Until
they do, the architecture documents and the ADRs are the onboarding path.

## How these fit together

```
requirements  →  architecture  →  ADRs  →  phases  →  code  →  tests  →  docs
```

Each layer of that chain constrains the next. The practical rules:

- **`current-status.md` describes reality.** If it disagrees with the
  repository, the repository is right and the document is stale.
- **`dependencies.md` must match `.importlinter`.** It separates rules CI
  enforces from rules it does not, so an unenforced rule is visible rather than
  assumed.
- **ADRs are immutable.** A decision that no longer holds is superseded by a new
  ADR; both stay.
- **Documentation is part of done.** A change that alters a boundary and does not
  update the boundary's documentation is incomplete.
