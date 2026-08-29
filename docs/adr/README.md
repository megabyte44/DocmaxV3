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
| [0007](0007-m1-foundations-reconciliation.md) | `m1-foundations` is a source branch, absorbed phase by phase | **Superseded by 0009** |
| [0008](0008-consent-record.md) | Consent is machine-local state, scoped to an endpoint and a terms version | Accepted |
| [0009](0009-main-is-the-base.md) | `main` is the base; ADR 0007 was overtaken by events | Accepted |
| [0010](0010-format-vocabulary.md) | Formats are declared once, and `docmax formats` reads the declaration | Accepted |
| [0011](0011-convert-is-pandoc-only.md) | `convert` is Pandoc-only at M5: no PDF in, no PDF out, no derived output path | Accepted |
| [0012](0012-cloud-engines-are-compress-and-convert.md) | M6 ships two cloud engines, and a cloud run never falls back to local | Accepted |
| [0013](0013-cloud-config-comes-from-the-resolved-config.md) | A cloud strategy takes its endpoint from the resolved configuration | Accepted |
| [0014](0014-api-key-storage.md) | The API key lives in the config file in plaintext, and DocMax says so | Accepted |
| [0015](0015-cancellation-crosses-the-network.md) | Cancellation reaches a cloud job, and a cloud job has a deadline | Accepted |
| [0016](0016-jobs-run-in-process.md) | Cloud jobs run in the server process, and the job store is in memory | Accepted |
| [0017](0017-json-output-contract.md) | `--json` is a global option and stdout carries only the envelope | Accepted |
| [0018](0018-capabilities-mean-runnable.md) | `/v1/capabilities` lists what this deployment can run, not what this build knows about | Accepted |
| [0019](0019-picker-package-and-rendering.md) | Pickers are their own package below the interfaces, and render with the browser's own PDF viewer | Accepted |
| [0020](0020-tui-entry-point.md) | The CLI launches the TUI; a bare `docmax` opens it only where there is someone to use it | Accepted |
| [0021](0021-the-tui-is-generated-from-the-registry.md) | The TUI is generated from `ToolSpec` and names no tool | Accepted |
| [0022](0022-ocr-runs-tesseract-directly-and-skips-pages-that-have-text.md) | OCR shells out to Tesseract directly, and leaves pages that already have text alone | Accepted |
| [0023](0023-runners-are-a-package-below-the-interfaces.md) | Pipelines, batch and watch live in one package below the interfaces | Accepted |
| [0024](0024-a-pipeline-is-a-toml-file.md) | A pipeline is a TOML file, and only its last stage writes a destination | Accepted |
| [0025](0025-batch-mirrors-names-into-an-output-directory.md) | Batch mirrors input names into an output directory it may not share with its inputs | Accepted |
| [0026](0026-the-watcher-polls-and-never-watches-its-own-output.md) | The watcher polls, waits for a file to settle, and may not write where it watches | Accepted |
| [0027](0027-mcp-is-an-optional-interface-behind-an-extra.md) | MCP is an optional interface, behind the `mcp` extra, started by the CLI | Accepted |
| [0028](0028-the-mcp-tool-surface-is-the-registry.md) | The MCP tool surface is the registry, and the schema says only what `ToolSpec` can | Accepted |
| [0029](0029-the-mcp-policy-boundary.md) | MCP runs inside a policy boundary: roots, offline by default, and consent it may not grant | Accepted |
| [0030](0030-mcp-cancellation-maps-onto-the-cancellation-token.md) | A cancelled MCP request cancels the DocMax run, through the existing token | Accepted |
| [0031](0031-toolspec-says-when-output-is-a-directory.md) | `ToolSpec` says when output is a directory, so `OutputTarget` can safely fill in a missing extension | Accepted |
| [0032](0032-param-gains-component-labels-for-composite-values.md) | `Param` gains `component_labels`, so a composite value gets one labelled field per part | Accepted |

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
