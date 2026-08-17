# ADR 0006 — The reference server lives in this repository, and is open

**Status:** Accepted · 2026-08-15
**Supersedes:** the `cloud_server/` clause of [ADR 0004](0004-open-core-boundary.md). The rest of 0004 stands unchanged.

## Context

[ADR 0004](0004-open-core-boundary.md) drew the open-core line in August 2026 and
placed two things behind it:

> **Gated** — `src/docmax/pro/**` and the future `cloud_server/` repo.

The `pro/` half of that has held up. The `cloud_server/` half has not, because
the architecture around it changed after 0004 was written.

**What changed.** The HLD now treats the HTTP API as a first-class *interface* —
a peer of the CLI, the TUI and the MCP server, sitting in the interface layer and
driving the same tools through the same registry. See
[architecture/layers.md](../architecture/layers.md#interfaces--cli-tui-server-mcp).
When 0004 was written, "the cloud server" was an unexamined future thing that
could plausibly live anywhere. It is now a named layer with a defined position in
the dependency graph, and that position is inside this package.

**Why the old decision no longer works.** Three specific problems, none of which
were visible in August:

1. **A separate repository forks the tool layer.** The server's entire purpose is
   to run the *same* `LocalStrategy` a user would run locally — that is what makes
   a cloud result provably identical to a local one. A separate repository cannot
   import `docmax.tools` without either vendoring it or depending on the published
   wheel. Vendoring duplicates every tool. Depending on the wheel introduces
   version skew, so a self-hoster on an older release silently gets different
   output from the same document. There is no third option.

2. **It makes a documented promise unfulfillable.**
   [cloud-api.md](../cloud-api.md) tells self-hosters that "any server
   implementing this document — the hosted service, or a self-hosted one — works
   with an unmodified client", and that the configurable endpoint means the
   hosted service "competes on convenience rather than lock-in". If the only
   implementation is gated and in another repository, a self-hoster's real
   option is to write a server from a Markdown file. Endpoint configurability is
   then a gesture rather than a capability.

3. **It gates a capability, which 0004 itself forbids.** 0004's own principle is
   that candidates for gating are "team- and scale-shaped, not capability-shaped:
   if a feature makes a single user's document processing better, it is free."
   Running `ocr` without installing Tesseract makes a single user's document
   processing better. Under the old clause, the only way to get that was to use
   the hosted service — which is a capability paywall wearing a deployment
   costume.

## Decision

**The reference Cloud Engine server lives at `src/docmax/server/`, is MIT
licensed, and is open.**

The open-core line does not move. It runs exactly where 0004 drew it — this
decision only corrects which side of it the server was on:

| | |
|---|---|
| **Open (MIT)** | Everything under `src/docmax/` except `pro/`. All tools, both engines, the CLI, the TUI, the cloud client, **the reference API server**, and the MCP server. |
| **Gated** | `src/docmax/pro/**` — unchanged. Team- and scale-shaped features only: SSO, audit logging, centrally managed policy, org quotas, hosted pipeline orchestration. |

The hosted service at the default endpoint remains a product. It competes on
convenience — someone else operating the machine, keeping Ghostscript patched,
and absorbing the compute — rather than on being the only implementation.

## Alternatives considered

**Keep the separate gated repository (the status quo).** Rejected: it forks the
tool layer, which defeats the reason the server exists, and it leaves
cloud-api.md making a promise nothing can keep.

**Server in this repository, but under `pro/` and gated.** Rejected. The CI
`open-core` job deletes `pro/` and requires the full suite to pass, so nothing
outside `pro/` may depend on it — but the server's whole job is to depend on
`tools/` and `core/`. More fundamentally, gating an interface gates the
capabilities reached through it, which is the thing 0004 exists to prevent.

**A separate open repository depending on the published `docmax` wheel.**
Rejected for the version-skew reason above. It also splits the test suite across
two repositories, so "the server produces byte-identical output to the local
engine" stops being a test anyone can run.

**Vendor the tool layer into a server repository.** Rejected outright. This is
the duplication the layered architecture exists to prevent, and it would drift
within one release.

## Consequences

**Positive**

- One implementation of every operation. A cloud result cannot diverge from a
  local one, because there is nothing to diverge from — the server runs the same
  `LocalStrategy` on a machine that happens to have the dependencies installed.
- Self-hosting becomes real. `docker compose up` against this repository is a
  working endpoint, and the configurable endpoint in cloud-api.md means what it
  says.
- The parity claim becomes testable: run a fixture through `LocalStrategy`
  directly and through the HTTP API, and compare bytes.

**Negative — and accepted**

- **The commercial moat gets thinner.** Anyone can run the hosted service's exact
  server. 0004 already chose "revenue depends on convenience and organisational
  needs rather than on withholding functionality — a weaker moat, deliberately
  chosen"; this extends that choice rather than making a new one.
- **The repository now contains code most users will never run**, and a
  dependency set — a web framework, an ASGI server — that must be kept out of
  their install. That cost is real and is paid in packaging (below).
- **Two interfaces now exist in one package**, so the `layers` import-linter
  contract has to order them even though they are peers. An `independence`
  contract is needed to say what `layers` cannot.

**Packaging consequence.** `src/docmax/server/` must not ship in the wheel a user
installs. The server deploys from a checkout inside an image that also carries
Ghostscript, Tesseract and Pandoc — none of which pip can install — so it has no
reason to be in the distribution, and one reason to stay out: everything in the
wheel lands in the `site-packages` of someone who only wanted to merge two PDFs.
Its dependencies belong in a `server` extra, never in `[project.dependencies]`
and never in `all`.

## Enforcement

**All of this is now enforced.** `src/docmax/server/` exists on `main`, and it
arrived with every check listed below — the layers entry, the independence
contract, `server-is-not-a-client`, `fastapi` in `core-is-ui-free`, the wheel
exclusion and its hygiene test. The decision below was reached independently of
that implementation and matches it exactly.

*(Historical note: this section previously said none of it was enforced.)*
That is deliberate: the checks land in the *same change* as the layer, not as a
follow-up. Until then these are recorded in
[dependencies.md](../architecture/dependencies.md#not-yet-enforced) and
[backlog.md](../planning/backlog.md#required) as unenforced.

When the server lands (Phase 8), it must land with:

| Check | Enforces |
|---|---|
| `independence` contract between `docmax.cli` and `docmax.server` | interfaces are peers; the CLI never pulls in a web framework |
| `forbidden` contract: `docmax.server` may not import `docmax.cloud_client` | the two halves of the contract stay independent implementations |
| `exclude = ["docmax.server*"]` plus a hygiene test | the server does not ship to users |
| `server` added to `LIBRARY_PACKAGES` in `tests/paths.py` | a request handler that exits cannot take every in-flight request with it |

The existing `open-core` CI job is unaffected: it deletes `pro/`, which this
decision does not touch.

## Implementation impact

- **Code:** none yet. No source file changes as a result of this ADR.
- **Docs:** [ADR 0004](0004-open-core-boundary.md) gains a superseded-in-part
  note; the [ADR index](README.md), `architecture/dependencies.md`,
  `architecture/layers.md`, `planning/backlog.md` and
  `planning/current-status.md` are updated to match.
- **Phase:** the server remains Phase 8 / milestone M6. This ADR unblocks it; it
  does not advance it.
