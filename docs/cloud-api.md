# Cloud Engine API contract

**Status:** implemented, for four tools. The client in
`src/docmax/cloud_client/` speaks the whole contract; the reference server in
`src/docmax/server/` implements every endpoint below, including running the
tool. `compress` and `convert` have cloud engines as of M6 —
[ADR 0012](adr/0012-cloud-engines-are-compress-and-convert.md) records why those
two and not the five this document once listed. `ocr` joined at M8, and
`to-images` joined after it — [ADR 0034](adr/0034-to-images-joins-the-cloud-engines.md)
records why that one and not the pure-Python remainder.

This document is the contract, and it has two independent implementations on
purpose — a client that borrowed the server's parsing (or the reverse) would
agree with itself about its own bugs. `tests/unit/test_cloud_client.py` asserts
the client honours it with `respx`. Any server implementing this document — the
hosted service, or a self-hosted one — works with an unmodified client.

> Until M6 this section claimed those `respx` tests existed. They did not. The
> client shipped untested through five milestones, and the claim is left here in
> corrected form rather than quietly deleted.

## Why this exists

Cloud is not a paid tier and not a capability gate. Every cloud-backed tool has
a fully functional local engine. Cloud exists so a user can compress a PDF
without installing Ghostscript, or convert a document without installing Pandoc.

Four tools have it: `compress`, `convert`, `ocr` — the case this section always
led with, and the one whose local install is genuinely painful — and
`to-images`, which shares `ocr`'s Poppler dependency and joined for the same
reason. `convert` still does not produce PDF on either engine
([ADR 0011](adr/0011-convert-is-pandoc-only.md)).

## Directory-shaped output

Every field above assumes one file. `to-images` writes many — a directory of
images, one per rendered page — and the wire contract has no second shape for
that: `output` is still exactly one URL, one size, one content type. For a
tool whose `ToolSpec` says `produces_directory` (ADR 0031), that one file is a
**zip archive** of everything the local engine would have written, with
`content_type: "application/zip"`. The client unpacks it into the destination
directory before running the tool's own validators against it — see ADR 0034
and `src/docmax/tools/_archive.py`, used by both the client's unpack and the
reference server's zip.

## Endpoint configuration

```toml
[cloud]
endpoint = "https://api.docmax.dev"   # default
```

Overridable via config or `DOCMAX_CLOUD_ENDPOINT`. The hosted service is the
convenient default, not the only option — self-hosters point this elsewhere and
everything else is identical.

## Auth

Bearer token, from `[cloud] api_key` or `DOCMAX_API_KEY`.

```http
Authorization: Bearer dmx_live_...
User-Agent: docmax/3.0.0 (python/3.12; linux)
```

An API key is required from day one. Anonymous access would make cost and abuse
unbounded for a single-maintainer service.

**A self-hosted deployment accepts a token from either of two backends** — see
[ADR 0037](adr/0037-server-token-identity.md) and
[implementation/identity.md](implementation/identity.md). One or both may be
configured; a token is checked against the static set first, then the durable
store if one is configured, so adding the store never requires retiring the
env var.

- **`DOCMAX_SERVER_API_KEYS`** — a flat, comma-separated allowlist, unchanged
  since M6. A key from this set resolves to a caller identity equal to the key
  itself. Zero setup; still the whole story for a single-operator deployment
  that never needs to revoke one caller without affecting the others.
- **A durable, per-user token store** — set `DOCMAX_SERVER_IDENTITY_DB` to a
  file path to enable it. A token is `dmx_live_` followed by random hex —
  literally the format this section has always shown as an example — issued
  and revoked with `python -m docmax.server.identity_cli`, and belongs to a
  user rather than standing alone: a user may hold more than one token (a
  laptop, a CI job), and every token for one user shares that user's identity
  for upload and job ownership. Revoking one token does not touch the user's
  others. Only a token's hash is ever stored; the raw value is shown once, at
  creation, and cannot be recovered afterward.

Both backends resolve to the identical `owner` value threaded through
`Storage` and `JobStore` — one identity model, for REST and for `/v1/mcp`
alike, described further below.

## Small files — synchronous

For payloads under 10 MB:

```http
POST /v1/tools/{tool_name}
Content-Type: multipart/form-data
Idempotency-Key: sha256:<digest of input bytes + canonicalised params>

  file:    <bytes>
  params:  {"preset": "ebook"}      # JSON, validated against the tool's ParamSpec
```

**200 OK**

```json
{
  "ok": true,
  "job_id": "job_01H...",
  "output": { "url": "https://...", "size_bytes": 182344, "content_type": "application/pdf" },
  "duration_ms": 1840,
  "engine_version": "gs/10.03.0"
}
```

The `Idempotency-Key` is derived from the input digest and parameters, so a
retried request after a network drop returns the original result rather than
re-running and re-billing.

## Large files — presigned upload, then poll

Over 10 MB, three steps:

```http
POST /v1/uploads          → { "upload_url": "...", "file_id": "f_...", "expires_in": 900 }
PUT  <upload_url>         (direct to storage; never proxied through the API)
POST /v1/tools/{name}     { "file_id": "f_...", "params": {...} }
                          → 202 Accepted { "job_id": "job_...", "poll_after_ms": 2000 }
GET  /v1/jobs/{job_id}    → { "status": "running" | "succeeded" | "failed", ... }
```

The client honours `poll_after_ms` rather than choosing its own interval, so the
server controls its own load.

## Errors

Every failure returns the same envelope, and `code` maps 1:1 onto the client's
typed exception hierarchy:

```json
{
  "ok": false,
  "error": {
    "code": "cloud.quota_exceeded",
    "message": "Monthly OCR quota exhausted.",
    "remedy": "Run locally with --engine local, or wait for the reset on 2026-09-01.",
    "retryable": false
  }
}
```

| HTTP | `code` | Client raises |
|---|---|---|
| 401 / 403 | `cloud.auth` | `CloudAuthError` |
| 413 | `cloud.payload_too_large` | `CloudPayloadTooLargeError` |
| 422 | `input.*` | the matching `InputError` subclass |
| 429 | `cloud.quota_exceeded` | `CloudQuotaExceededError` |
| 5xx | `cloud.server_error` | `CloudServerError` |
| timeout | — | `CloudTimeoutError` |
| unparseable | — | `CloudProtocolError` |

`CloudProtocolError` matters more than it looks: the endpoint is
user-configurable, so version skew between a client and a self-hosted server is
a real and expected condition, not a corrupt-response edge case.

Retries use exponential backoff with jitter, and only for errors whose
`retryable` is true. Retrying a bad API key or an exhausted quota just wastes
the user's time.

## Data handling

These are contract terms, not aspirations. A server that does not honour them
is not implementing this API.

- Documents are deleted immediately on job completion or failure.
- Document **contents** are never logged. Metadata (size, page count, duration)
  may be.
- No document is retained for training, analytics, or any secondary purpose.
- TLS required. The client refuses plaintext `http://` endpoints except
  `localhost`, so self-hosted development still works.

## Discovery

```http
GET /v1/capabilities
→ { "tools": ["compress", "convert"],
    "max_sync_bytes": 10485760,
    "api_version": "1" }
```

The client calls this once and caches it, so a self-hosted server offering a
subset of tools degrades cleanly rather than failing per-call.

**The list is what this deployment can run, not what its build knows about.**
The server answers from the tool registry rather than from a list of its own,
*and* asks each tool's local strategy whether it is available on this machine —
so a server without Ghostscript does not advertise `compress`. See
[ADR 0018](adr/0018-capabilities-mean-runnable.md); before it, an endpoint with
nothing installed advertised the one tool it could not perform.

The example above is a machine with Ghostscript and Pandoc. A different
deployment answers differently, which is the point.

## MCP, for a client that speaks it instead of this contract directly

`POST /v1/mcp` (M11, [ADR 0035](adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md))
is the same tools this document describes, reached through the Model Context
Protocol's streamable-HTTP transport instead of the endpoints above — for a
client (ChatGPT's connectors, or any MCP-over-HTTP client) that speaks MCP
rather than this REST contract. It is not a second implementation: `tools/call`
resolves and runs a tool through the same code every REST call in this
document runs, so `tools/list` offers exactly the tools `GET /v1/capabilities`
does, `inputs` is a `file_id` from `POST /v1/uploads` exactly as it is for the
JSON path of `POST /v1/tools/{name}`, and a result is that call's job payload,
unmodified.

Three things differ from the REST endpoints above because this route is the
first one built for a caller the operator does not necessarily control:

- **TLS is required, except from loopback.** Every other endpoint here relies
  on `CloudClient` refusing plaintext on itself; a third-party MCP client has
  no reason to have made the same choice, so this route enforces it instead of
  assuming it.
- **A session belongs to the key that opened it.** The `Mcp-Session-Id` a
  handshake returns cannot be reused by a request presenting a different
  bearer token — a mismatch gets the same "not found" a made-up session id
  would.
- **One document per call.** Every tool's job model here carries one `file_id`
  per job; a tool whose schema allows several inputs still takes exactly one
  over this route.

Everything else in this document — the API key, the error envelope, the
"documents are deleted on completion or failure" rule — applies identically.

## Running the reference server

```bash
pip install -e ".[server]"        # the code is not in the wheel; run it from a checkout
DOCMAX_SERVER_API_KEYS=dev-key python -m docmax.server
```

It binds to localhost and accepts no keys until you name some. Point a client at
it with `DOCMAX_CLOUD_ENDPOINT=http://localhost:8000` — plaintext is permitted
for a local endpoint precisely so that this works, and refused everywhere else.

Jobs run **in the request that submits them**, in this process, with the job
store in memory. That is the reference server's model and not a claim about the
hosted service; [ADR 0016](adr/0016-jobs-run-in-process.md) records what it
costs — no durability across a restart, and a long job holds a connection open
for its duration.
