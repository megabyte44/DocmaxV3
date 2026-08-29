# ADR 0014 — The API key lives in the config file in plaintext, and DocMax says so

**Status:** Accepted · 2026-08-27

## Context

The Cloud Engine needs a bearer token. `cloud-api.md` fixes where it comes
from — `[cloud] api_key` or `DOCMAX_API_KEY` — and `core/config.py` already
parses both. What has never been decided is where `docmax cloud login` should
*put* one, and what DocMax should claim about protecting it.

The pressure is that a plaintext credential in a config file is the answer
every security checklist marks wrong, and the temptation is to reach for the
system keyring so the checklist passes. That would add a compiled dependency
(`keyring` plus a platform backend), a new failure mode on headless Linux where
no backend exists, and a second place a key can live — while doing very little,
because the threat model that matters here is *an attacker who can read the
user's files*, and such an attacker can generally also read the process memory
that the keyring hands the key into.

The stronger requirement, and the one worth designing around, is not where the
key is stored but that it never leaks from where it is stored. `ToolResult`
travels into `--json` and into logs. `DocMaxError.context` is rendered to the
terminal and serialised into the error envelope. A key that reaches either is
a key in someone's CI log.

## Decision

**The API key is stored in `config.toml`, in plaintext, under `[cloud]`, and
`docmax cloud login` writes it there through `core/atomic.py`.** No keyring, no
encryption, no separate credential file.

**`login` is honest about what that means.** It prints the absolute path it
wrote to and states that the value is not encrypted, on success, every time —
not in a manual nobody reads.

**On a POSIX filesystem, `config.toml` is created `0600`.** This is not
protection against a determined attacker on the same machine; it is protection
against the ordinary case of a world-readable home directory, and it costs one
`chmod`. On Windows the file inherits the user profile's ACL and DocMax does
not attempt to tighten it, which is stated rather than papered over.

**The key never appears in output. This is the load-bearing half of this ADR.**
Four rules, each with a test behind it:

- never in `ToolResult.details`
- never in `DocMaxError.context` or any error message
- never on stdout, including `--json`
- never on stderr, including progress and diagnostics

`docmax cloud status` reports whether a key is configured and, at most, a
masked suffix — never the value. The existing `CloudClient` already puts the key
only in an `Authorization` header, and nothing logs headers.

**Environment beats file**, which `core/config.py` already implements, so CI
uses `DOCMAX_API_KEY` and never writes a file at all.

## Alternatives considered

**The system keyring, via `keyring`.** The obviously "correct" answer.
Rejected: a compiled dependency and a platform backend for the base install,
which non-negotiable #3 forbids; no backend on headless Linux, which is exactly
where a self-hoster runs this; a second storage location to reconcile with the
config file and the environment variable; and a real risk of *overclaiming* —
"stored securely in your keyring" invites a user to relax about a token that is
still handed to a process, still sent over the network, and still readable by
anything running as them.

**A separate `credentials.toml`, app-owned like `consent.json`.** Consent is
app-owned for a specific reason ADR 0008 gives: it must not travel between
machines in a dotfile sync. A key is different — a user syncing dotfiles
*wants* their key to follow them, and `cloud-api.md` already documents
`[cloud] api_key` as the place. Splitting it would contradict a published
contract to gain nothing.

**Refuse to store it at all; require `DOCMAX_API_KEY`.** Safest, and hostile.
It makes the first-run experience "export this variable in every shell forever"
and pushes users into `.bashrc`, which is a plaintext file with worse
permissions than the one this ADR chose.

**Encrypt it with a passphrase.** Turns every invocation into a prompt, or
stores the passphrase next to the ciphertext. Security theatre in both
directions.

## Consequences

**What it costs, stated plainly: anything that can read the user's home
directory can read their API key.** That includes another program running as
the same user, a backup that ends up somewhere unintended, and a dotfile
repository pushed to a public host. DocMax does not defend against any of them,
and `login` says so at the moment the file is written.

Also:

- `0600` is POSIX-only. Windows users get whatever their profile ACL gives,
  which is usually adequate and is not verified.
- A user who commits `config.toml` to a public repository has leaked a live
  key. The mitigation is the printed warning and nothing else.
- The four "never in output" rules are only as good as their tests, and those
  tests can only cover paths that exist. A future command that renders a
  `Config` wholesale would break the rule without failing them, which is why
  the enforcement below tests the *rendering functions* rather than only the
  current commands.

**What it buys.** No new dependency, no platform-specific failure mode, one
documented location that matches the published contract, and a claim about
security that is true.

## Enforcement

- A test asserts `Config` and `CloudConfig` never appear whole in any rendered
  output: `render_result`, `render_error` and the `--json` envelopes are driven
  with a config carrying a sentinel key, and the sentinel must not appear on
  either stream.
- A test asserts `DocMaxError.to_dict()` output for every cloud error raised
  through a failing transport contains no sentinel.
- A test asserts `docmax cloud status` with a key configured prints neither the
  key nor more than a masked suffix.
- A test asserts `login` creates the file with mode `0600` on POSIX, and is
  skipped on Windows with that stated as the reason rather than silently
  passing.
- Nothing enforces that a *future* renderer respects these rules. The
  sentinel-based tests above are written against the render functions rather
  than against individual commands so that a new command using them inherits
  the check; a new command that formats output by hand would not.
