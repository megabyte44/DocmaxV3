# ADR 0018 — `/v1/capabilities` lists what this deployment can run, not what this build knows about

**Status:** Accepted · 2026-08-27

## Context

`GET /v1/capabilities` answers from the registry:

```python
"tools": sorted(spec.name for spec in iter_tools(engine=Engine.CLOUD)),
```

Run against the reference server today, that returns:

```json
{"tools": ["ocr"], "max_sync_bytes": 10485760, "api_version": "1"}
```

Every word of which is wrong in practice. `ocr` is the only tool declaring
`Engine.CLOUD`, and it is an M8 skeleton whose `run()` raises
`NotImplementedError`. The endpoint advertises the one tool it cannot perform,
on a machine with no Tesseract installed, and would answer a submission with a
500.

The client believes it. `cloud-api.md` says the point of this endpoint is that
*"a self-hosted server offering a subset of tools degrades cleanly rather than
failing per-call"* — and `Capabilities.supports()` exists so the router can rule
a tool out before a document is uploaded. A capabilities list that overstates
what the server can do converts that clean degradation into a failed upload.

The defect is that the route asks a question about the *build* — which tools
were compiled in — and presents the answer as a fact about the *deployment*.
Those differ precisely when a tool's dependencies are missing, which is the
normal condition for a document server.

The mechanism to ask the right question already exists and the route does not
use it. `EngineStrategy.is_available()` is defined in `core/protocols.py` as
*"Can this strategy run right now, on this machine?"*, is required to be cheap
and side-effect free, and is what the router consults on every routing decision.

## Decision

**A tool is advertised when it declares `Engine.CLOUD` *and* its local strategy
reports itself available on this machine.**

The server runs the *local* strategy — `server/execution.py` is explicit that
the cloud engine is *"a machine that already has Ghostscript, Tesseract, and a
LaTeX distribution installed, running exactly the code a user would run"*. So
"can this endpoint offer `compress`?" is exactly "is `CompressLocal` available
here?", and that is now the question asked.

**No tool declaration changes.** `ocr` keeps `Engine.CLOUD` in its `ToolSpec`,
because M8 will implement it and removing the declaration would delete the
skeleton M8 fills in. Nothing in `core` or `registry` changes. This is a change
to one route, using a protocol method that has existed since M1.

**The endpoint's meaning is narrowed, not widened**, which is the safe
direction: it can now list fewer tools than before and never more. A client
that previously got an over-long list gets a correct one; no client gets a tool
it did not get before.

## Alternatives considered

**Remove `Engine.CLOUD` from `ocr`'s spec until M8.** The most direct fix, and
it makes the list correct on every machine rather than most. Rejected: it
deletes the declaration M8 needs, it would make `ocr/cloud.py` unreachable and
therefore dead code, and it treats a reporting bug by editing an unrelated
tool's contract.

**Hard-code the M6 list — `{"compress", "convert"}` — in the route.** Rejected
outright: `cloud-api.md` says the server *"answers it from the tool registry
rather than from a list of its own, so it cannot advertise a tool it does not
have"*, and a hand-maintained list is the exact failure that sentence forbids.
It is also the mistake ADR 0010 was written about, one layer over.

**Have the route call the strategy's `run()` in a probe.** Rejected as absurd
on cost, and `is_available()` exists to avoid it.

**Add a `implemented: bool` flag to `ToolSpec`.** Rejected: a Registry contract
change, for a condition that is temporary by construction — `ocr` is
unimplemented until M8 and then never again. Encoding "we have not written this
yet" into a published metadata type outlives the situation it describes.

## Consequences

**What it costs, and it is a real residual.**

- **On a machine where OCR's dependencies *are* installed, `ocr` is still
  advertised and still cannot run.** `OcrLocal.is_available()` returns true when
  Tesseract, `pytesseract`, `pdf2image` and OpenCV are present, and its `run()`
  raises anyway — so its `is_available()` overstates itself by the protocol's own
  definition. That is a pre-existing defect in a skeleton this ADR deliberately
  does not touch, and it is resolved when M8 implements the method. Until then a
  fully-provisioned server can advertise a tool that answers 500. The reference
  server as tested has no Tesseract, so the list is correct there.
- Capabilities now vary by deployment, so two servers on the same version can
  answer differently. That is the intended behaviour and is worth stating,
  because it means the answer cannot be cached across endpoints — the client
  already caches per client instance, which is per endpoint.
- The route now constructs strategies to ask them a question. `is_available()`
  is contractually cheap — a `shutil.which` or a `find_spec` — so this is a
  handful of path lookups per request, and the client fetches it once.

**What it buys.** The endpoint tells the truth about the machine answering it, a
client can rule a tool out before uploading a document, and M6's two real cloud
engines appear in the list exactly when they can actually run.

## Enforcement

- A test asserts the advertised list on a machine without Ghostscript, Pandoc or
  Tesseract excludes all three of `compress`, `convert` and `ocr` — i.e. that
  availability, not declaration, decides.
- A test asserts a tool whose local strategy reports available *is* advertised,
  by faking availability rather than installing a binary.
- A test asserts nothing lacking `Engine.CLOUD` can ever appear, so the
  narrowing did not accidentally widen the set.
- Nothing enforces the `ocr`-with-Tesseract residual above. It cannot be
  enforced without changing `ocr`, which is M8's work, and it is named here
  rather than left to be discovered.
