# ADR 0008 — Consent is machine-local state, scoped to an endpoint and a terms version

**Status:** Accepted · 2026-08-16

## Context

The Cloud Engine uploads a user's document to someone else's computer. The
architecture makes four promises about that, and the first is the one this ADR
implements:

> Consent is **per tool** and recorded. No record → the operation stops.

Phase 3 owns configuration, and consent is the part of it that is not a
preference. Three questions had to be answered before any code:

1. **Where does the record live?**
2. **What invalidates it?**
3. **How is it versioned?**

Getting this wrong is not a normal bug. A consent record that is too sticky
uploads documents the user did not agree to upload; one that is too fragile
re-prompts until the user stops reading the prompt, which is worse than not
asking.

## Decision

### 1. A separate, app-owned file beside the config

```
<config dir>/config.toml     user-owned   — DocMax reads it, never writes it
<config dir>/consent.json    app-owned    — DocMax writes it, no one hand-edits
```

One directory, two files, one owner each. The split matters for two reasons:

**Writing to `config.toml` would damage it.** It is hand-authored: comments,
ordering, formatting. Recording consent by rewriting it would destroy all three,
and Python has no round-tripping TOML writer in the standard library.

**Consent is not portable, and config is.** People keep dotfiles in git and sync
them between machines. A preference — `offline = true`, `engine = "cloud"` —
*should* travel. A statement that this person, on this machine, agreed to upload
documents from it should not. Putting consent in the synced file would silently
grant it on a laptop where nobody was ever asked.

**Why JSON for the consent file.** Python's standard library reads TOML
(`tomllib`) but cannot write it. Adding a dependency to serialise four fields is
not worth it, and the format difference is a useful signal: TOML is the file you
edit, JSON is the file we maintain. It stays human-readable and deletable, which
is what matters — deleting it revokes everything.

Both files live under the one configuration directory from
[`branding.CONFIG_DIR_NAME`](../../src/docmax/core/branding.py). v2 shipped two
config directories, one created as an import side effect and never read; there is
exactly one now, and nothing creates it until something is written.

### 2. Consent is scoped to `(tool, endpoint)`

A grant records the endpoint it was given for. Consent means "send *this kind of
document* to *this service*" — both halves are load-bearing. The endpoint is
user-configurable precisely so people can point DocMax at a server they trust,
and agreement to a self-hosted box on the LAN plainly is not agreement to a
hosted service on the internet.

So a grant is invalidated by:

| Cause | Effect |
|---|---|
| explicit revocation, or deleting the file | the grant is gone |
| the endpoint changing | grants for the old endpoint no longer apply |
| the terms version increasing | every older grant stops counting |

And deliberately **not** invalidated by:

| Non-cause | Why |
|---|---|
| time | An expiry that fires on a Tuesday for no reason teaches people to click through prompts. Nothing about the risk changed since Monday. |
| `offline = true` | Offline makes cloud *unreachable*, which is a different thing from *unconsented*. The record survives; turning offline back off does not re-prompt. |
| a new version of DocMax | Upgrading is not a change in what is sent or where. |

The grant timestamp is recorded, but only so a user can see when they agreed. It
does not expire anything.

### 3. Terms version: one integer, bumped deliberately

`CONSENT_TERMS_VERSION` is a constant in `core/consent.py`. Every grant stores
the value current when it was given; a grant below the current value is treated
as absent, and the user is asked again.

It is bumped when the **data-handling terms in
[cloud-api.md](../cloud-api.md#data-handling) change materially** — what is
retained, what is logged, what a document may be used for. Not for wording,
typos, or new tools.

**Why an integer rather than a hash of the terms text.** A hash re-prompts every
user for a corrected comma, which trains them to dismiss the prompt without
reading — the precise failure the mechanism exists to prevent. An integer makes
re-prompting a deliberate act by a maintainer who has decided the change is worth
interrupting people for.

The file also carries a schema `version`, separate from the terms version, so the
record format can change without implying the terms did.

### Unreadable or unrecognised records fail closed

A corrupt file, an unreadable one, or a schema version from the future is treated
as **no consent**, never as consent. The cost of being wrong in that direction is
one prompt; the cost in the other direction is an upload nobody agreed to.

## Alternatives considered

**Consent inside `config.toml`.** Rejected: it makes DocMax a writer of a
hand-authored file, and it syncs consent between machines through dotfiles.

**A single global "cloud is fine" flag.** Rejected — the architecture requires
per-tool consent, and it is right to. Agreeing to send a scanned receipt to OCR
is not agreeing to send every contract through `convert`.

**Consent per tool *and per document*.** Rejected as unusable: a prompt for every
file is one people learn to dismiss without reading, and a batch of 200 files
would be unusable.

**Expiring consent after N days.** Rejected, as above.

**A hash of the rendered terms text.** Rejected, as above.

## Consequences

**Positive**

- Copying dotfiles to a new machine carries preferences and not permissions.
- Re-pointing the endpoint re-asks, which is the correct behaviour and is
  automatic rather than something a user must remember.
- Revoking is `rm consent.json`, which needs no command and no documentation.
- The record is inspectable: a user can read exactly what they agreed to, when,
  and for which server.

**Negative — and accepted**

- **Two files instead of one**, and two formats. The ownership split justifies
  it, but it is more surface to explain.
- **Bumping the terms version is a judgement call** with no test behind it. A
  maintainer who changes the terms and forgets to bump leaves users consented to
  something they never saw. That risk is inherent to any versioned-terms scheme;
  the mitigation is that `cloud-api.md`'s data-handling section names the
  constant, so the two are read together.
- **Endpoint scoping can surprise.** Someone who switches endpoints back and
  forth is asked each time they visit a new one. This is intended, but it will
  read as a bug to somebody.
- Consent does not sync between a user's own machines, by design. Someone with
  five machines answers five prompts.

## Enforcement

- `core/consent.py` is the only module that reads or writes the consent file.
- Writes go through `core/atomic.py` like every other write, so a crash while
  recording consent cannot leave a truncated record that later fails closed for
  the wrong reason. `tests/hygiene/test_no_direct_writes.py` already enforces
  this for all of `core`.
- Tests cover: the fail-closed paths (corrupt, unreadable, future schema), the
  endpoint change, the terms bump, and that `offline` does **not** clear a grant.
- The architecture's stronger claim — that *no path reaches `cloud_client`
  without passing a consent check* — belongs to the router, and is enforced at
  Phase 5. This ADR provides the mechanism; it does not yet prove the mechanism
  is always called.

## Implementation impact

- **Code:** `core/consent.py` (new); `core/config.py` owns the paths.
- **Docs:** `implementation/config.md`; `cloud-api.md` gains a pointer from its
  data-handling section to the terms constant.
- **Phase:** Phase 3. The router consumes this at Phase 5; the CLI renders the
  prompt at Phase 6.
