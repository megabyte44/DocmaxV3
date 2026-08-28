# ADR 0024 — A pipeline is a TOML file, and only its last stage writes a destination

**Status:** Accepted · 2026-08-28

## Context

A pipeline runs several operations over one document: recognise a scan, then
compress it, then strip its metadata. The operations already exist. What M9 has
to decide is how a user says which ones, in what order, with what parameters —
and where the intermediate documents go.

Two constraints come from earlier decisions rather than from taste.

**Every destination is written once, atomically, and only if it validates.**
[ADR 0003](0003-atomic-writes.md). A three-stage pipeline that wrote three files
into the user's directory would leave two of them behind on success — v2's
`_preprocessed.png` defect wearing a different hat, and the one
[implementation/ocr.md](../implementation/ocr.md) says M8 made structurally
impossible for OCR alone.

**`OutputTarget.resolve` refuses a destination that is one of its inputs**, and
refuses one that already exists without `force`. A naive chain that fed stage
N's output back as stage N+1's input on the same path would be refused by the
core contract — correctly, and that refusal is the design pointing at the
answer.

There is also a piece of history worth naming. `docs/plans/03-stream-targets.md`
on `main` proposed making `DocumentRef.path` optional so that pipelines could be
shell pipelines — `docmax ocr - | docmax compress -`. That plan was never built;
`path` is still `Path`, not `Path | None`. Building it now would be a Core
change touching every engine signature, which M9 is explicitly not doing.

## Decision

**A pipeline is a TOML file, named with `--pipeline`.**

```toml
name = "scan-cleanup"

[[stage]]
tool = "ocr"
params = { lang = "eng", dpi = 300 }

[[stage]]
tool = "compress"
engine = "local"
params = { preset = "ebook" }
```

Top-level keys are `name` (optional) and `stage` (required, at least one). A
stage takes `tool` (required), `params` (optional table) and `engine`
(optional). **Any other key is an error**, at load time, naming the key — the
rule `core/config.py` already applies to `config.toml`, for the reason ADR 0008
gives: a misspelled key that silently does nothing is worse than a refusal.

Parameters sit in their own table rather than beside `tool` so that a tool with
a parameter called `tool` or `engine` is representable. That is not
hypothetical caution; it costs one line of nesting and removes a class of
collision permanently.

**Only the last stage writes the user's destination.** Every earlier stage
writes into a single `TemporaryDirectory` created for the run and removed when
it ends, under a name derived from the stage index and tool. The last stage is
handed the real `OutputTarget`, so the destination is written exactly once, by
`core/atomic.py`, after that stage's own validators have passed.

**A pipeline is validated before it runs.** Every stage's tool must exist in the
registry, every parameter name must be one the tool declares, and no
non-final stage may be a tool that produces a directory or produces nothing.
A pipeline that fails validation raises before any temporary directory is made.

**`--dry-run` resolves every stage's engine and writes nothing**, reporting the
chain it would run. It does not execute stages, because stage two of a dry run
would have no input.

## Alternatives considered

**Shell pipelines with `-`.** The `docs/plans/03` design. It is the better
long-term answer and it is a Core change: `DocumentRef.path` becomes optional, a
`materialize()` context manager appears, `atomic.py` grows a stdout sink, and
every one of nineteen engines is revisited. Out of scope for M9 by direction, and
this decision does not foreclose it — a `--pipeline` file and a shell pipe can
coexist, and the file is the better fit for a saved, re-runnable workflow anyway.

**A CLI chain syntax** — `docmax pipeline in.pdf --then ocr --then compress`.
Rejected because parameters have nowhere to live. `--then ocr --lang eng` cannot
say which stage `--lang` belongs to once two stages accept it, and the escape
from that is a quoting mini-language nobody wants to write or read.

**YAML.** Rejected on dependencies. TOML parses with `tomllib` from the standard
library — one of the four things [ADR 0001](0001-python-311.md) raised the
Python floor to get — and the project already speaks TOML for configuration. A
second configuration language would need PyYAML and would make the answer to
"which format does DocMax use" two formats.

**Intermediates beside the input, cleaned up afterwards.** Rejected outright.
This is v2's defect: files appearing next to a user's documents, and a crash
leaving them there forever. A `TemporaryDirectory` is removed even on the
failure path, and its contents cannot be mistaken for the user's own.

**Each stage writing the real destination in turn.** Rejected: it makes the
destination the input of the next stage, which `OutputTarget` refuses, and it
would publish a half-finished document to any reader watching that path — the
guarantee ADR 0003 exists to make.

## Consequences

- **The whole pipeline is atomic from the user's side.** A failure or a Ctrl-C
  at stage three leaves the destination untouched and the temporary directory
  removed. There is no partial state to clean up and none to explain.
- **Disk cost is the sum of the intermediates**, all live at once inside one
  temporary directory, on the temp filesystem rather than beside the output.
  For a large scan through a three-stage pipeline that is three copies. Accepted:
  the alternative is streaming, which is the Core change above.
- **The temporary directory is not on the destination's filesystem.** The last
  stage's atomic write still stages beside the destination, so ADR 0003's
  cross-device argument is unaffected; what crosses a device is the *input* of
  the final stage, which is a read.
- **A pipeline cannot contain `split`, `to-images`, `get-info` or `permissions`
  anywhere but the end**, because the next stage needs a single readable file.
  This is validated, but the list is maintained in `runners/pipeline.py` rather
  than read from `ToolSpec` — see the seam below.
- **Consent is not prompted mid-pipeline.** A stage routed to cloud without a
  recorded grant fails with `ConsentRequiredError` and its remedy, rather than
  stopping to ask. Prompting inside a composed run — and, at M9, inside a batch
  of two hundred — is the interaction ADR 0008 warns about.

### The `ToolSpec` seam, for the fifth time

`ToolSpec` cannot say "I produce a directory" or "I produce nothing", so the
non-final-stage check reads a frozenset declared in `runners/pipeline.py`. This
is the same seam
[current-status.md](../planning/current-status.md#architecture-violations-and-gaps)
has recorded three times and that M7 met a fourth time in `tui/catalog.py` —
and it is answered the same way [ADR 0021](0021-the-tui-is-generated-from-the-registry.md)
answered it: name the exception in the consumer, hold it with a test, and do not
change Core for it alone. It strengthens rather than weakens the argument that
the three seams should be decided together.

## Enforcement

- `tests/unit/test_m9_pipeline.py` — an unknown top-level key, an unknown stage
  key, an unknown tool, an unknown parameter and an empty `stage` array each
  raise `InvalidParameterError` before anything runs.
- `test_a_failed_middle_stage_leaves_no_destination`,
  `test_a_failed_last_stage_leaves_no_destination` and
  `test_cancellation_between_stages_leaves_no_destination` — the atomicity claim.
- `test_intermediate_stages_write_inside_a_temporary_directory` and
  `test_the_temporary_directory_is_removed_after_a_failure` — the intermediates
  claim, on the success path and the failure path.
- `test_only_the_last_stage_writes_the_destination_directory` — asserts the
  user's directory holds exactly one new file after a three-stage run.
- `test_directory_producing_tools_are_refused_before_the_final_stage` holds the
  frozenset above, one entry at a time, and
  `test_m9_runners.py::test_every_named_tool_is_a_real_tool` fails if one of the
  names stops being a registered tool.
- **The reverse direction is not enforced, and cannot be.** A new tool that
  produces a directory will not be added to the frozenset by any test, because
  `ToolSpec` cannot say that it produces one — which is the seam itself. Such a
  tool would be accepted as a middle stage and fail at run time with
  `InputNotFoundError` on the next stage. Named here rather than left implied.
