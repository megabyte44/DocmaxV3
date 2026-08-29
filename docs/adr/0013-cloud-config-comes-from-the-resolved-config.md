# ADR 0013 — A cloud strategy takes its endpoint from the resolved configuration, not from the environment

**Status:** Accepted · 2026-08-27

## Context

`[cloud] endpoint` in `config.toml` is documented in `cloud-api.md`, parsed by
`core/config.py`, and **silently ignored by the only cloud strategy that
exists**. This is a live defect found while auditing M6, not a design question
invented for it.

The path it falls down:

```
core/config.py  load()          reads config.toml + DOCMAX_* env → Config
core/router.py  load_strategy() calls factory()          ← no arguments
tools/ocr/cloud.py  build()     → CloudClient() → CloudConfig.from_env()
```

`ToolSpec.load_strategy()` calls the module's `build()` with no arguments,
because the registry deliberately knows nothing about configuration —
`core/registry.py` holds metadata and a dotted path, and that is the whole of
ADR 0002. So the strategy has no way to receive the `Config` the router is
holding, and falls back to `CloudConfig.from_env()`, which reads
`DOCMAX_CLOUD_ENDPOINT` and `DOCMAX_API_KEY` and nothing else.

A user who sets `endpoint` in their config file — the documented way to point
DocMax at a self-hosted deployment — is ignored, and their document goes to the
default hosted endpoint instead. For a feature whose entire premise is "your
documents go where you say", that is the worst possible direction to fail in.

The intended design is already written down. `CloudConfig.from_env`'s own
docstring says:

> The config file is the other source and takes lower precedence; that merge
> belongs to `core/config.py` in M1, which will construct this.

The merge was written. Nothing was ever wired to it.

## Decision

**A cloud strategy resolves its configuration through `core.config.load()` and
maps the result onto a `CloudConfig`.** `cloud_client/config.py` gains one
classmethod for the mapping:

```python
CloudConfig.from_core(config: Config) -> CloudConfig
```

It reads `cloud_endpoint` and `api_key` from the already-resolved `Config` and
leaves timeouts and retry counts at the client's own defaults. It parses
nothing: `core/config.py` remains the single implementation of the precedence
chain (defaults → file → environment), and this is a projection of its output,
not a second reader of its inputs.

**Resolution happens once per strategy instance, lazily.** `is_available()` is
called on *every* routing decision, including the ones that choose the other
engine, so it must not read a file each time. The strategy resolves on first
use and memoises on the instance; a strategy instance lives for one run.

**`CloudConfig.from_env()` stays.** It is the right constructor for a library
caller embedding the client without DocMax's config file, and for the tests.
It simply stops being what a *strategy* uses.

**No change to Core, the Registry, or `ToolSpec`.** `load_strategy()` still
calls `build()` with no arguments. The layering already permits what is needed:
`tools` may import `core`, and `cloud_client` may import `core`.

## Alternatives considered

**Pass the `Config` through the registry** — `load_strategy(engine, config=...)`
reaching `build(config)`. Architecturally the tidiest: the router already holds
the resolved config and would hand it down explicitly, with no module reaching
sideways for global state. Rejected for M6 on two grounds. It changes
`ToolSpec.load_strategy`, `EngineRouter`, and the `build()` contract every one
of eighteen tools implements — a Core and Registry change in the same milestone
that adds cloud engines. And ADR 0002's argument is that a `ToolSpec` is
metadata plus a dotted path; giving it a configuration parameter starts it down
the road of being a dependency-injection container. **This remains the correct
long-term answer**, and the trigger for revisiting it is named below.

**Let `CloudClient()` default to reading `core.config.load()` itself.** One
change, fixes every caller at once. Rejected: it makes the client — documented
as *"deliberately thin"*, knowing the wire contract *"and nothing else"* — reach
into the user's home directory. A library caller constructing a `CloudClient`
would silently pick up a config file they never mentioned. The mapper is
explicit and the strategy opts in.

**Duplicate the precedence chain inside `cloud_client`.** Rejected immediately.
Two readers of one setting is the failure `core/config.py` exists to prevent,
and they would disagree about environment-over-file the first time someone
touched one of them.

## Consequences

**What it costs.**

- The strategy reaches for configuration rather than being handed it, which is
  the weaker of the two arrangements and is the reason the alternative above is
  still the right long-term answer.
- **The router's configuration and the strategy's are resolved separately, and
  could in principle disagree.** This is sharper than it first looked and is
  worth stating exactly. The router decides *whether* to upload — consent is
  scoped to `self.config.cloud_endpoint` — while the strategy decides *where*,
  from its own `load()`. If those two ever came from different places, consent
  could be checked against one endpoint and the document sent to another.

  In the product they cannot diverge: `cli/execution.build_router()` calls
  `load()` and so does the strategy, so both read one source.
  `test_the_router_and_the_strategy_read_one_configuration` pins that wiring,
  because it is the thing holding the invariant up.

  It *is* reachable by a library caller who constructs
  `EngineRouter(config=...)` by hand with settings that differ from the config
  file. That caller gets consent checked against their `Config` and an upload
  to the file's endpoint. Narrow, real, and the price of not changing the
  registry contract in this milestone.

- **Runtime overrides do not reach a cloud strategy** for the same reason.
  `Config.with_overrides(cloud_endpoint=..., api_key=...)` is the documented
  fourth precedence layer, and the strategy cannot see an override applied in
  memory. Today the CLI applies none, so nothing is lost. **The moment a flag
  like `--endpoint` or `--api-key` is added, both of these become wrong
  together** — and that is the trigger to take the rejected alternative and
  change the registry contract, with its own ADR.
- One extra config-file read per run, on the first `is_available()` that
  resolves. A small TOML parse, once.

**What it buys.** `[cloud] endpoint` means what the documentation says it
means, self-hosted deployments work as advertised, and there is still exactly
one implementation of configuration precedence.

## Enforcement

- A test sets `[cloud] endpoint` in a temporary config file and asserts the
  strategy's client targets it — the check that would have caught the original
  defect.
- A test asserts the same for `api_key`, and that the environment still wins
  over the file, by going through `core.config.load()` rather than re-testing
  precedence separately.
- `test_the_router_and_the_strategy_read_one_configuration` asserts the CLI
  wires both sides to the same `load()`, which is what keeps consent and upload
  pointed at one endpoint.
- `lint-imports` already forbids `core` importing `cloud_client` or `tools`, so
  the direction of this dependency cannot invert.
- Nothing enforces that a future `--endpoint` flag triggers the registry
  change. It is stated here and in `backlog.md` instead, which is a weaker
  guarantee and is named as such.
