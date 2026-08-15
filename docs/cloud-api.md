# Cloud Engine API contract

**Status:** design. Both halves exist in skeleton: the client in
`src/docmax/cloud_client/` speaks the whole contract, and the reference server
in `src/docmax/server/` implements every endpoint below except the part that
runs the tool, which lands with the cloud engines in M6.

This document is the contract, and it has two independent implementations on
purpose — a client that borrowed the server's parsing (or the reverse) would
agree with itself about its own bugs. `respx`-based tests assert the client
honours it. Any server implementing this document — the hosted service, or a
self-hosted one — works with an unmodified client.

## Why this exists

Cloud is not a paid tier and not a capability gate. Every cloud-backed tool has
a fully functional local engine. Cloud exists so a user can run OCR without
installing Tesseract, or convert to PDF without installing a LaTeX
distribution.

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
→ { "tools": ["ocr", "compress", "convert", "pdfa", "remove-bg"],
    "max_sync_bytes": 10485760,
    "api_version": "1" }
```

The client calls this once and caches it, so a self-hosted server offering a
subset of tools degrades cleanly rather than failing per-call. The server
answers it from the tool registry rather than from a list of its own, so it
cannot advertise a tool it does not have.

## Running the reference server

```bash
pip install -e ".[server]"        # the code is not in the wheel; run it from a checkout
DOCMAX_SERVER_API_KEYS=dev-key python -m docmax.server
```

It binds to localhost and accepts no keys until you name some. Point a client at
it with `DOCMAX_CLOUD_ENDPOINT=http://localhost:8000` — plaintext is permitted
for a local endpoint precisely so that this works, and refused everywhere else.
