"""One module per endpoint group in ``docs/cloud-api.md``.

    capabilities.py   GET  /v1/capabilities
    tools.py          POST /v1/tools/{tool_name}
    uploads.py        POST /v1/uploads, PUT /v1/uploads/{file_id}
    jobs.py           GET  /v1/jobs/{job_id}
    outputs.py        GET  /v1/outputs/{file_id}

Routers declare the API-key dependency once, at construction, so no individual
endpoint can be added without it by forgetting a decorator.

``outputs.py`` is the one exception and says so in its own docstring: a
conforming client downloads a finished document through an *unauthenticated*
client, because the contract's output URL is a presigned link to storage and a
bearer token has no business being sent there.
"""

from __future__ import annotations
