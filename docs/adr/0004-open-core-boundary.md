# ADR 0004 — Open-core boundary

**Status:** Accepted · 2026-08-13

## Context

DocMax needs a sustainable model without becoming the kind of open-core project
that ships a deliberately crippled free tier. Getting this line wrong is how
projects lose goodwill permanently, and it is much harder to move a boundary
inward later than outward.

Stirling PDF is the closest comparable and a useful precedent: it keeps every
document operation free and gates SSO, audit logging, and user management. That
line has held across 30M+ downloads without a community revolt, which is
stronger evidence than reasoning from first principles.

## Decision

**Every document operation is free and permissively licensed, forever.**

| | |
|---|---|
| **Open (MIT)** | Everything under `src/docmax/` except `pro/`. All tools, both engines, the CLI, the TUI, the cloud *client*, the MCP server. |
| **Gated** | `src/docmax/pro/**` and the future `cloud_server/` repo. |

Gated means a licence key or installation fingerprint check — **never code
obfuscation**. The source stays readable; the check is honest and visible.

Candidates for `pro/` are team- and scale-shaped, not capability-shaped: SSO,
audit logging, centrally managed policy, hosted pipeline orchestration. If a
feature makes a single user's document processing better, it is free.

The Cloud Engine deserves a specific note. It is not a paywall on a capability:
every cloud-backed tool has a fully functional local engine. Cloud exists so a
user can skip installing Tesseract or a LaTeX distribution. The *client* is
open and the endpoint is user-configurable, so the hosted service competes on
convenience rather than lock-in.

## Enforcement

CI runs the full test suite with `src/docmax/pro/` deleted. The open half can
never quietly grow a dependency on the gated half — if it does, the `open-core`
job goes red on that PR rather than being discovered at packaging time.

## Consequences

- Anyone can fork, delete `pro/`, and ship a fully functional document toolkit.
  That is intended, and it is the constraint that keeps the boundary honest.
- Modules crossing the boundary are marked in a header comment as well as by
  location, so the line is visible while reading, not only while browsing.
- Revenue depends on convenience and organisational needs rather than on
  withholding functionality — a weaker moat, deliberately chosen.
