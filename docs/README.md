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
| [implementation/config.md](implementation/config.md) | Configuration precedence and the consent record |
| [implementation/core.md](implementation/core.md) | The core contracts, and the failure each prevents |
| [implementation/json.md](implementation/json.md) | What `--json` produces, and which fields are stable |
| [cloud-api.md](cloud-api.md) | The Cloud Engine wire contract *(implemented, for `compress` and `convert`)* |
| [../benchmarks/METHODOLOGY.md](../benchmarks/METHODOLOGY.md) | How performance is measured, and what is not measured |
| [migrating-from-v2.md](migrating-from-v2.md) | What changed, and what will break |

## Not written yet

`docs/development/` (setup, testing, contributing) is listed in
[the backlog](planning/backlog.md#required) and does not exist. Until it does,
the architecture documents and the ADRs are the onboarding path.

`docs/implementation/` was listed here as missing while three of its documents
already existed. It is now indexed above.

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
