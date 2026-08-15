"""One module per endpoint group in ``docs/cloud-api.md``.

    capabilities.py   GET  /v1/capabilities
    tools.py          POST /v1/tools/{tool_name}
    uploads.py        POST /v1/uploads, PUT /v1/uploads/{file_id}
    jobs.py           GET  /v1/jobs/{job_id}

Routers declare the API-key dependency once, at construction, so no individual
endpoint can be added without it by forgetting a decorator.
"""

from __future__ import annotations
