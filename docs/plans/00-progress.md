# Progress — where to pick this up

Written 2026-08-20. Delete when plans 01–06 have landed.

## Branch state, resolved

Fifteen branches existed and no pull request was open. They fell into four
groups.

**The stack — six branches, one line.** `feat/core-reconciliation` →
`feat/engine-router` → `feat/merge-tool` → `feat/cli-integration` →
`feat/m2-pypdf-tools` → `feat/m3-compress`, each a linear ancestor of the next,
all zero commits behind `main`. Merging the tip merges all six. Opened as
**PR #10**.

Verified locally against the tip before opening it:

```
ruff check          All checks passed
ruff format         133 files already formatted
mypy                no issues in 108 source files   (see caveat below)
lint-imports        5 contracts kept, 0 broken
pytest              867 passed, 2 skipped in 34s
```

**Already merged — four branches.** `chore/bump-version-3.0.0a7`,
`chore/rename-pypi-distribution`, `fix/ci-color-and-golden-exit-code`,
`m1-foundations`. Contained in `main`; safe to delete. Three others
(`chore/pypi-release-prep`, `fix/ci-lint-and-type-errors`,
`fix/ci-workflow-call`) were already gone from the remote and pruned locally.

**Superseded — two branches.** `architecture` and `feat/core-foundation`, both
23 commits behind `main`, with `architecture` fully contained in
`feat/core-foundation`. Every file in both exists at the stack tip. ADR 0009
settles it: `origin/main` is the authoritative base and the phase line was
reconciled onto it by `feat/core-reconciliation`, so these two are the
pre-reconciliation copies. Deleted, but tagged first —
`archive/architecture` and `archive/core-foundation` — so nothing is
unrecoverable.

**New — one branch.** `docs/build-leverage-plans`, carrying this directory,
ADRs 0010 and 0011, and `CLAUDE.md`.

## Open item

**PR #10 is open and not merged.** The merge was blocked by a permission
prompt, not by a failure — the branch is green and the PR is ready. Merge it,
then delete the six stack branches and the four merged ones.

## What changed in the plans because of the stack

These plans were drafted against `main` at `3.0.0a7`, where `merge` and `ocr`
were stubs. The stack changed the ground under two of them, both in the
project's favour.

- **Plan 01** — `core/router.py` and `cli/execution.py` now exist, and are
  what the plan asked for. What is left is the generation layer: nine
  hand-written commands in `cli/commands.py`, and the `ToolSpec` fields
  (`produces_output`, `produces_directory`, `requires_binaries`, and the `Param`
  additions) that would let them be generated. `execute_read_only()` exists only
  because a tool cannot declare that it writes nothing.
- **Plan 06** — mostly built. `tools/_binaries.py` has per-platform install
  commands, `install_hint()`, and a candidate-list lookup that finds
  Ghostscript's console build (`gswin64c`) on Windows. Four items remain.
- **Plan 02** — unchanged in substance, stronger in motivation. A contract
  suite written before any tool exists proves nothing on day one. Written
  across ten independently authored tools, it is a bug hunt.
- **Plans 03, 04, 05** — unchanged. `DocumentRef.path` is still `Path`,
  `--json` still does not exist, `src/docmax/mcp/` still does not exist. Plan 03
  now costs ten engines to migrate instead of one, and gets more expensive every
  milestone it waits.

Two places where the stack's design beat the plan are recorded in
[README.md](README.md) rather than quietly overwritten.

ADRs were renumbered **0006 → 0010** and **0007 → 0011**: the stack ships ADRs
0006–0009, so the original numbers collided.

## Start here

1. Merge PR #10, delete the ten dead branches.
2. Plan 01 — the generation layer. Do it at ten commands, not forty. The 867
   tests make the refactor cheap; migrate one command at a time.
3. Plan 02 — the contract suite, as a bug hunt across the ten existing tools.
4. Plan 03 next if M9 pipelines still matter, since it only gets dearer.

## Two loose threads, neither blocking

**`mypy` fails locally when the `ocr` extra is installed.** numpy's bundled
stubs use `type` statements that need `python_version >= 3.12`, and
`[tool.mypy]` pins `3.11`. CI never sees it because `[dev]` does not install
numpy — so this hits contributors and not the pipeline, which is the worst way
round. `mypy --python-version=3.12` is the workaround.

**`docs/plans/` and `docs/planning/` sit side by side** and mean different
things: this directory is what to build next, `docs/planning/` is where the
project is. Worth renaming one of them.
