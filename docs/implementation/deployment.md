# Deploying the server

How to run `src/docmax/server/`, and why deployment looks like this rather than
`pip install` or a checked-in container image.

The decisions are in [ADR 0006](../adr/0006-reference-server-location.md) (why
the server lives here, is open, and is excluded from the wheel) and
[ADR 0016](../adr/0016-jobs-run-in-process.md) (why jobs run in-process against
an in-memory store, and what that costs). This file describes what exists and
how to run it; it does not repeat their reasoning beyond what a deployer needs.

---

## Why this deploys from a checkout, not a package

`pip install DocmaxV3` must stay a terminal tool with no web framework pulled
in — that is non-negotiable #3 in `CLAUDE.md`. So `src/docmax/server/` is
excluded from the wheel on purpose
(`tests/hygiene/test_wheel_excludes_server.py`, `pyproject.toml`'s
`exclude = ["docmax.server*"]`), and the `server` extra installs only what the
server *needs* — FastAPI, uvicorn, `python-multipart` — never the server's own
code.

That means there is no `pip install docmax-server`. Deployment is: **clone the
repo, install the extra into that checkout, run the module.** This is
deliberate, not a gap — see ADR 0006's rationale for keeping the server in the
same repository as the tools it runs, so a self-hosted result can never diverge
from a local one.

## What "deploy" means today

**There is no Dockerfile or docker-compose file checked into this repository
yet.** ADR 0006 describes the target ("an image that also carries Ghostscript,
Tesseract and Pandoc") but building that image is unscheduled work — check
[planning/backlog.md](../planning/backlog.md) before assuming it exists. Until
it lands, a self-hoster builds their own image or runs the checkout directly on
a host they control. If you add one, update this section rather than leaving it
stale.

## Steps

**1. Clone and install the `server` extra:**

```bash
git clone https://github.com/<org>/docmax.git
cd docmax
pip install -e ".[server]"
```

The `-e`/checkout requirement is not a convenience — the server's code has no
other way to reach an installed environment (see above).

**2. Install the system binaries the tools need.** The server runs the exact
same `LocalStrategy` a local user would (`server/execution.py`), so it needs
whatever that user would need: Ghostscript, Tesseract, Pandoc. Use
`docmax setup` or your OS package manager, same as a local install.

**3. Configure via environment variables.** `ServerSettings.from_env()`
(`src/docmax/server/config.py`) is the only configuration source — no config
file, because that is what every deployment target agrees on.

| Variable | Default | Notes |
|---|---|---|
| `DOCMAX_SERVER_HOST` | `127.0.0.1` | Binds to localhost until set otherwise, on purpose — exposing a service is a decision someone should make explicitly. |
| `DOCMAX_SERVER_PORT` | `8000` | |
| `DOCMAX_SERVER_API_KEYS` | *(empty)* | Comma-separated bearer tokens. Empty means **every request is rejected** — an open-by-default document endpoint is a mistake you make once. |
| `DOCMAX_SERVER_LOG_LEVEL` | `info` | Passed straight to uvicorn. |

`max_sync_bytes`, `max_upload_bytes`, and `retention_seconds` are also on
`ServerSettings` but have no env var wired to them yet — they take their
dataclass defaults (10 MB, 512 MB, 3600s) until one is added.

**4. Run it:**

```bash
DOCMAX_SERVER_HOST=0.0.0.0 \
DOCMAX_SERVER_API_KEYS=dmx_live_yourkey \
python -m docmax.server
```

`server/__main__.py` is the only place a process is bound to a port and the
only place uvicorn is imported — importing `server.app` never starts a server,
which is what lets tests build one without binding anything.

**5. Point a client at it.** Any server implementing
[cloud-api.md](../cloud-api.md) works with an unmodified client — set
`[cloud] endpoint` in config, or `DOCMAX_CLOUD_ENDPOINT`.

## What the reference backends do *not* give you

`create_app()` defaults to `InMemoryStorage` and `InMemoryJobStore`
(`server/app.py`) unless a caller passes real ones in — which nothing in this
repository does yet. Know this before deploying:

- **Nothing survives a restart.** Uploaded bytes, running jobs, and the
  idempotency-key index all live in a process dict. A deploy, a crash, or a
  scale-to-zero event loses every in-flight job.
- **It does not work across replicas.** Two processes behind a load balancer
  each have their own storage and job store; a request can land on the wrong
  one. Run exactly one process per endpoint until this changes.
- **Jobs run synchronously, in the request.** A five-minute OCR job is a
  five-minute HTTP request. Any reverse proxy or load balancer in front of the
  server needs a read timeout longer than the job can take — ADR 0016 flags
  this as unreconciled with the client's own `read_timeout` default (120s).
- **No concurrency limit.** Two large simultaneous requests are two
  simultaneous Ghostscript processes. Fine for one owner's documents; not a
  multi-tenant posture.

None of this is a bug to route around locally — `Storage` and `JobStore` are
both `Protocol`s precisely so a real deployment can swap in object storage and
a shared job store without `create_app()`'s callers changing. That swap is
unscheduled work, not a broken promise: nothing has needed it yet.

## Health and observability

`GET /healthz` is liveness only — it does not check Ghostscript, Tesseract, or
storage, and it takes no auth. That is deliberate (`server/app.py`): a health
check that reports on dependencies gets used as a monitoring endpoint and then
answers slowly exactly when it needs to answer fast. `GET /v1/capabilities` is
where a client (or an operator) learns what the deployment actually
supports — API version, size limits, which cloud engines are live.

## Keeping this document current

Update this file in the same change that:

- Adds a Dockerfile, compose file, or any other packaged deployment artifact —
  replace "What 'deploy' means today" rather than adding a second path.
- Wires an env var for `max_sync_bytes`, `max_upload_bytes`, or
  `retention_seconds`.
- Replaces `InMemoryStorage` or `InMemoryJobStore` with a persistent backend,
  or adds one as an option.
- Changes anything in `ServerSettings` — the table above must match
  `server/config.py` exactly, not approximately.
- Revisits the in-process job model from ADR 0016 (a queue, async execution,
  etc.) — that changes several claims in this file at once.

If this file disagrees with `src/docmax/server/`, the code is right and this
file is stale — fix it, the way `current-status.md` asks for the rest of the
project.
