# Cloud Engine API contract

**Status:** implemented, for two tools. The client in
`src/docmax/cloud_client/` speaks the whole contract; the reference server in
`src/docmax/server/` implements every endpoint below, including running the
tool. `compress` and `convert` have cloud engines as of M6 —
[ADR 0012](adr/0012-cloud-engines-are-compress-and-convert.md) records why those
two and not the five this document once listed.

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

Two tools have it today. OCR — the case this section used to lead with — is M8,
and `convert` still does not produce PDF on either engine
([ADR 0011](adr/0011-convert-is-pandoc-only.md)).

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
