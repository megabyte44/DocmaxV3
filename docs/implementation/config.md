# Configuration and consent

Two modules, one directory, two files with different owners.

| Module | Owns |
|---|---|
| `core/config.py` | the precedence chain, validation, and where both files live |
| `core/consent.py` | the record of what a user agreed to upload, and where |

They are separate because they are different kinds of thing. Configuration is a
*preference* a user states; consent is a *decision* a user made, once, on a
machine. Mixing them would mean DocMax writing to a file it should only read —
and syncing permissions through dotfiles.

## The precedence chain

```
defaults  →  config file (TOML)  →  environment  →  runtime override
```

Later layers win. `--engine cloud` beats `DOCMAX_TOOL_OCR_ENGINE`, which beats
`[tools.ocr] engine`, which beats the built-in default.

`load()` applies the first three. The fourth is
`Config.with_overrides()`, applied by the caller, because only the interface
knows what the user typed.

**Nothing else in DocMax reads the environment.** Scattered `os.environ` lookups
are how one setting comes to mean two things in two code paths, and how "why is
it using the cloud?" stops having an answer. There is one reader.

Both the file path and the environment are injectable, so tests never touch a
real home directory and a caller can load a config that is not the user's —
which the server will need.

## The file

```toml
offline = false

[cloud]
endpoint = "https://api.example.com"
api_key  = "dmx_live_..."

[tools.ocr]
engine = "cloud"
```

A missing file is not an error; first run has none.

### Unknown keys are an error

`offlien = true` is refused, not ignored. This is the one place the design
deliberately chooses strictness over forward compatibility, and it is because of
a specific v2 failure: the settings screen wrote config keys that nothing ever
read, and reported "settings saved". A typo that silently does nothing is the
same bug.

The cost is real — a config written for a newer DocMax fails on an older one —
but it fails in the better direction. It says so, rather than quietly ignoring
the setting the user cared about.

### Validation happens at load, not at use

An endpoint is checked when it is *configured*, not on the first upload. TLS is
required and plaintext is refused except for `localhost`, `127.0.0.1` and `::1`,
so self-hosted development works without a certificate. Trailing slashes are
stripped so a joined path cannot produce `//`.

Environment booleans accept a closed set (`1/true/yes/on` and their opposites).
An unrecognised value **raises** rather than reading as false: `DOCMAX_OFFLINE=maybe`
silently meaning "go online" would leak documents.

### `offline` is one-way

`offline = true` makes cloud unreachable *regardless of flags*, including an
explicit `--engine cloud`. `with_overrides(offline=False)` cannot turn it off.

Someone sets this because policy says documents do not leave the building. An
argument that defeats it makes it decoration.

## Consent

Full reasoning in [ADR 0008](../adr/0008-consent-record.md). The mechanism:

A grant is `(tool, endpoint, terms_version, granted_at)`. `ConsentStore` is
constructed with the endpoint it answers for, because every question it is asked
is really "may we upload to *this* server?" — a store that had to be told the
endpoint per call is one a caller can forget to tell, and that failure mode
fails open.

**Invalidated by** revocation, a change of endpoint, or a bump of
`CONSENT_TERMS_VERSION`.

**Not invalidated by** time, or by `offline`. Offline makes cloud *unreachable*,
which is different from *unconsented*; turning it back off must not re-prompt.

### Failing closed

Corrupt, unreadable, or a schema version from the future all mean "no consent" —
never "consent". Being wrong that way costs one prompt. Being wrong the other way
uploads a document nobody agreed to send.

A single malformed *entry* is dropped rather than failing the whole file, so one
bad record costs its own tool a prompt instead of discarding every other grant.

### Versioning

`CONSENT_TERMS_VERSION` is one integer, bumped by hand when the data-handling
terms in [cloud-api.md](../cloud-api.md#data-handling) materially change — what
is retained, what is logged, what a document may be used for. Not for wording.

A hash of the terms text would re-prompt everyone for a corrected comma, which
teaches people to dismiss the prompt unread — the exact failure the prompt exists
to prevent. An integer makes re-prompting a deliberate act.

A *newer* stored version than the running code's is honoured, not rejected: it
means an older DocMax is reading a record made by a newer one, and the user
agreed to terms at least as current.

### Writes go through `core/atomic.py`

Like every other write in the project. A crash mid-record must not leave a
truncated file, which would then fail closed for the wrong reason and discard
every other grant. `test_no_direct_writes.py` already enforces this for all of
`core`.

## What this layer does not do

- **It does not prompt.** `core` may not import a UI framework. This module
  records a decision and answers questions about it; asking belongs to the
  interface.
- **It does not decide which engine runs.** That is the router, Phase 5. Config
  supplies the preference and the `offline` flag; the router applies precedence
  against availability and consent.
- **It does not prove consent is checked.** The architecture's stronger claim —
  that no path reaches `cloud_client` without a consent check — is the router's
  to enforce, at Phase 5. This is the mechanism, not yet the guarantee.

## Testing

`tests/unit/test_config.py`, `tests/unit/test_consent.py` — 77 tests, no
third-party dependency, no network, no real home directory.

The consent tests are mostly attempts to break it: corrupt files, a directory
where a file belongs, a schema from the future, malformed entries beside valid
ones, and an endpoint that changes and changes back.

## Known limitations

- **Bumping the terms version is a judgement call with no test behind it.** A
  maintainer who changes the terms and forgets to bump leaves users consented to
  something they never saw. Inherent to any versioned-terms scheme; mitigated
  only by `cloud-api.md` naming the constant so the two are read together.
- **Consent does not sync between a user's own machines**, by design. Five
  machines, five prompts.
- **Endpoint scoping will occasionally surprise.** Switching endpoints back and
  forth asks each time a new one is visited. Intended, but it will read as a bug
  to somebody.
- **The API key is stored in plaintext** in a config file the user owns. Standard
  for CLI tools, and the file is theirs; a keyring would be a dependency and a
  platform matrix. Revisit if it ever holds more than one credential.
