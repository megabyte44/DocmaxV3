# ADR 0034 — `to-images` joins the cloud engines, and a directory output travels as a zip

**Status:** Accepted · 2026-08-31

## Context

[ADR 0012](0012-cloud-engines-are-compress-and-convert.md) drew the cloud
boundary at M6 as `compress` and `convert`, with `ocr` named explicitly as the
reference skeleton a later milestone would fill in — which M8 did. That ADR's
own justification for the boundary is worth restating exactly:

> Cloud exists to eliminate install pain and nothing else.

An audit of all nineteen registered tools against that rationale (tracked as
issue #37) found exactly one tool outside `{compress, convert, ocr}` with a
genuinely painful native dependency: `to-images`, which renders pages with
Poppler's `pdftoppm` — the *same binary* `ocr` already uses for rasterisation.
`tools/_binaries.py` has declared `used_by=("ocr", "to-images")` since M0. A
user who can already avoid installing Poppler by running `docmax ocr --engine
cloud` has no principled reason to be blocked installing it just to render
pages as images.

Two other candidates the same audit considered and rejected:

- `from-images` uses `img2pdf` and Pillow — pure Python wheels, no system
  binary. No install pain, no case for cloud.
- Every remaining tool is `pypdf`-only. Whether *any* pure-Python tool should
  get a cloud engine is a real product question — uploading a document to
  perform a millisecond-long local operation is worse than doing it where the
  document already is, which is exactly what `server/execution.py`'s
  `RegistryRunner.resolve` already says when it refuses a tool with no cloud
  engine. That question is explicitly **not** decided here; it goes against
  this ADR's and ADR 0012's shared rationale and would need its own ADR if the
  answer is ever yes.

The one obstacle `to-images` presents that `compress`, `convert` and `ocr`
never had to: its output is a **directory** of files
(`ToolSpec.produces_directory`, ADR 0031), not one file. The wire contract in
`docs/cloud-api.md` carries exactly one file per job — one URL, one size, one
content type — because every cloud tool until now wrote exactly one.

## Decision

**`to-images` gets a cloud engine, built the same way `ocr`'s is: a thin
`cloud.py` supplying a tool name and a validator factory to the shared
`CloudEngine` in `tools/_cloud.py`.** Nothing about the router, the CLI, the
TUI, or the MCP surface changes to make this true — `ToolSpec.supported_engines`
gaining `Engine.CLOUD` is the whole of the wiring, exactly as it was for the
first three.

**A directory-shaped job's output is a zip archive.** `CloudEngine` gains one
constructor flag, `produces_directory: bool = False`, mirroring
`ToolSpec.produces_directory` by name and by purpose. A tool's `cloud.py` sets
it — `to-images/cloud.py` is the only one that does, today — and the shared
flow branches on it:

- **File-shaped** (`compress`, `convert`, `ocr`): fetch bytes, write them with
  `atomic_write`, exactly as before.
- **Directory-shaped** (`to-images`): fetch bytes, unzip them into a directory
  staged by `atomic_dir`, validate the *directory*, swap it into place as a
  unit.

The zip/unzip shape lives in one new module, `tools/_archive.py`
(`zip_directory`, `unzip_into`), used by both halves of the contract: the
client-side unpack in `CloudEngine.run`, and the server-side zip in
`server/execution.py`'s `RegistryRunner.start`, which now branches on
`spec.produces_directory` the same generic way the router already does for
`OutputTarget.resolve` — a spec field read by both ends, never a name check.

**The validators are the same ones the local engine passes.** `to-images/
cloud.py`'s `validators_for` opens the source, resolves the page selection and
the image format — through `image_format_for`, moved out of `local.py` into
`validators.py` so both engines resolve `--format` identically rather than
maintaining two copies of "is this a real rasterisable format" — and hands the
result to `renders_images`, the exact validator the local engine already
passes to `atomic_dir`. A cloud render that lost a page, or wrote a file with
no PNG header, fails exactly where a local one would.

**The wire contract's shape is unchanged.** `docs/cloud-api.md` still
describes one `output` per job. What travels in it, for a directory-producing
tool, is `application/zip` instead of the document's own type — a detail of
*that job's* bytes, not a new field or a second URL.

## Alternatives considered

**A second output field on the wire contract — `outputs: [...]` instead of
`output: {...}`.** Rejected: it is a breaking change to every existing client
and server for the sake of the one tool that needs it today, when the existing
single-output shape already has a well-understood way to carry many files —
an archive — that every language's standard library already reads and writes.
If a future tool needs *streamed* multi-file output (large images, one at a
time) this would be revisited; nothing here forecloses it.

**Give `to-images` its own bespoke cloud strategy instead of extending
`CloudEngine`.** Rejected on the same grounds `ocr/cloud.py`'s history already
argues: a hand-rolled class per tool is a second implementation of "upload,
poll, fetch, validate, write", and it is exactly the shape that had to be
deleted once already when `ocr` moved onto the shared flow at M8. One flag on
the shared class, read the same way `ToolSpec.produces_directory` already is,
keeps there being one flow.

**Extend cloud to the `pypdf`-only tools while this ADR is open anyway.**
Rejected — this is the decision issue #37 explicitly asked not to be bundled
with item 1. It goes against ADR 0012's rationale as directly as `to-images`
satisfies it, and deserves its own ADR argued on its own terms rather than
riding in on this one.

## Consequences

**What it costs.**

- `tools/_cloud.py` is no longer "two things differ between tools" — it is
  three, with the third being a boolean every existing tool leaves at its
  default. `CLAUDE.md`'s rule against per-tool branching is unweakened: the
  branch is on a spec-shaped fact (`produces_directory`), not on a tool's name.
- `server/execution.py`'s `RegistryRunner.start`, previously written
  assuming exactly one output file, now carries a directory branch. It is
  generic — driven by `spec.produces_directory`, the same field the client
  reads — so the next directory-producing tool that joins cloud needs no
  server change at all.
- `image_format_for` moving from `to_images/local.py` (private) to
  `to_images/validators.py` (shared, exported) is a small internal
  reorganisation with no behavioural change to the local engine.

**What it buys.** A user who wants to rasterise a PDF into images without
installing Poppler now can, on the same terms `ocr` already offers — and the
next tool whose only obstacle is "its output is a directory" has both the
client-side unpack and the server-side zip already written, generically,
rather than needing its own copy of either.

## Enforcement

- `tests/unit/test_m6_cloud.py::CLOUD_TOOLS` gains `to-images`, and
  `test_only_the_intended_tools_have_a_working_cloud_engine` — the test ADR
  0012 wrote specifically to fail loudly "if a fourth appears" — is the test
  that had to be touched deliberately to let this fourth one in, which is
  exactly the point of it having existed.
- `test_a_pure_python_tool_has_no_cloud_engine` no longer parametrizes over
  `to-images`, since it is no longer one of the tools that statement is true
  of.
- A new end-to-end test drives `to_images/cloud.py` against a mocked endpoint
  returning a zip archive, asserting the unpacked directory matches what the
  local engine would have written and that a validator failure (a missing
  page, a corrupt image) leaves the destination untouched — the same shape
  `test_ocr_runs_in_the_cloud_end_to_end` and its failure-path siblings
  already hold for the file-shaped tools.
- `tests/unit/test_cloud_client.py` / server-side tests cover
  `RegistryRunner.start`'s directory branch: a directory-producing tool's job
  reports `output.content_type == "application/zip"`, and downloading and
  unzipping it reproduces the directory the local engine wrote.
