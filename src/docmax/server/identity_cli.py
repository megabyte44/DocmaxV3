"""``python -m docmax.server.identity_cli`` — issue and revoke tokens for this deployment.

[ADR 0037](../../../docs/adr/0037-server-token-identity.md) originally sketched
this as a `docmax server identity` subcommand of the base CLI. Implementation
found the reason that shape does not work: `docmax.server` is deliberately
excluded from the wheel ([ADR 0006](../../../docs/adr/0006-reference-server-location.md))
and `docmax.cli` — the package the base install ships — may never import it
(`tests/hygiene/test_wheel_excludes_server.py`). So this lives here instead,
invoked the same way the server itself is: `python -m docmax.server...`, from
a checkout, by whoever already has the `server` extra installed and shell
access to the deployment — which is exactly who ADR 0037 §4 wanted running it.

Kept in its own module rather than folded into `__main__.py`, for the same
reason `__main__.py` gives for staying separate from `app.py`: importing this
module never binds a port, and running the server never parses `identity`
subcommands it does not need.

**No `sys.exit` here.** `tests/hygiene/test_no_sys_exit.py` scans every file
in this package, this one included — a request handler is not the only thing
that must never decide to kill a process by itself; the rule is simpler to
keep by applying it uniformly. A failure surfaces as an uncaught
`DocMaxError`: its message and remedy are printed first, then it is
re-raised, so Python's own handling of an uncaught exception supplies the
non-zero exit code this script never asks for directly.
"""

from __future__ import annotations

import argparse
import sys

from docmax.core.branding import CLI_NAME
from docmax.core.errors import DocMaxError, InvalidParameterError
from docmax.server.config import IDENTITY_DB_ENV, ServerSettings
from docmax.server.identity import SqliteIdentityStore


def _open_store() -> SqliteIdentityStore:
    settings = ServerSettings.from_env()
    if settings.identity_db_path is None:
        raise InvalidParameterError(
            "No identity store is configured for this deployment.",
            remedy=f"Set {IDENTITY_DB_ENV} to a file path and re-run.",
        )
    return SqliteIdentityStore(settings.identity_db_path)


def _create_user(args: argparse.Namespace) -> None:
    store = _open_store()
    user_id = store.create_user(label=args.label)
    suffix = f" ({args.label})" if args.label else ""
    print(f"Created user {user_id}{suffix}")


def _create_token(args: argparse.Namespace) -> None:
    store = _open_store()
    token = store.create_token(user_id=args.user, label=args.label)
    print(f"Issued a token for {args.user}")
    print(f"  {token}")
    print("  Shown once, and not stored anywhere recoverable. Save it now.")


def _revoke(args: argparse.Namespace) -> None:
    store = _open_store()
    store.revoke(args.token_id)
    print(f"Revoked. {args.token_id} may no longer authenticate.")


def _list(args: argparse.Namespace) -> None:
    store = _open_store()
    if args.user is None:
        for row in store.list_users():
            suffix = f" ({row.label})" if row.label else ""
            print(f"{row.user_id}{suffix}")
        return

    for info in store.list_tokens(args.user):
        status = "revoked" if info.revoked_at else "active"
        suffix = f" ({info.label})" if info.label else ""
        print(f"{info.token_id}{suffix} -- {status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python -m {CLI_NAME}.server.identity_cli",
        description=f"Issue and revoke bearer tokens for this {CLI_NAME}.server deployment.",
    )
    subparsers = parser.add_subparsers(required=True, dest="command")

    create_user = subparsers.add_parser("create-user", help="Create a user to hold tokens.")
    create_user.add_argument("--label", default=None, help="A note for your own bookkeeping.")
    create_user.set_defaults(func=_create_user)

    create_token = subparsers.add_parser(
        "create-token", help="Issue a token for a user. Prints the raw value exactly once."
    )
    create_token.add_argument("--user", required=True, help="The user id to issue a token for.")
    create_token.add_argument("--label", default=None, help="A note for this token.")
    create_token.set_defaults(func=_create_token)

    revoke = subparsers.add_parser("revoke", help="Revoke a token.")
    revoke.add_argument("token_id", help="The token id to revoke (see `list --user <id>`).")
    revoke.set_defaults(func=_revoke)

    list_cmd = subparsers.add_parser("list", help="List users, or one user's tokens.")
    list_cmd.add_argument(
        "--user", default=None, help="Only this user's tokens. Omit to list every user."
    )
    list_cmd.set_defaults(func=_list)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except DocMaxError as exc:
        print(exc.message, file=sys.stderr)
        if exc.remedy:
            print(exc.remedy, file=sys.stderr)
        raise


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["build_parser", "main"]
