"""The other end of the Cloud Engine contract.

``docs/cloud-api.md`` is written as a contract precisely so that it has two
independent implementations: the hosted service, and anyone who would rather
run their own. This package is the reference implementation of that document —
the one a self-hoster deploys, and the one the client's tests can be pointed at.

It is optional in every direction. Nothing else in the package imports it, it is
not installed by default (``pip install docmax[server]``), and the terminal tool
neither needs it nor knows it exists. A user who never touches cloud never
touches a line of this code.

    config.py      host, port, keys, limits
    app.py         the application factory, and where the routers are mounted
    routes/        one module per endpoint group in the contract
    errors.py      typed exception -> HTTP status + error envelope
    security.py    bearer token check
    storage.py     where uploaded bytes live until the job is done
    jobs.py        job records and their lifecycle
    execution.py   the bridge back to the registry, so the server runs the
                   same engines the CLI does

The layering rule that governs the rest of the project applies here too: this
package may use ``core`` and ``tools``, and nothing may use it.
"""

from __future__ import annotations
