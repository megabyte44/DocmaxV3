"""Composition over the tools: pipelines, batch, and folder watch.

M9's three runners. None of them is a tool and none belongs to one — they
compose operations the registry already knows, through the router every
interface already uses. See
``docs/adr/0023-runners-are-a-package-below-the-interfaces.md`` for why they live
here rather than in ``core`` or in ``cli``.

This is **library code**. Nothing here prints, exits, or writes a destination:
progress arrives as a ``ProgressSink``, stopping arrives as a
``CancellationToken``, outcomes are returned, and every byte that reaches a
user's directory goes through ``core/atomic.py`` by way of a tool's own engine.
``tests/hygiene/`` enforces all three.

The composition is deliberately one-way: a batch runs a pipeline over many
inputs, and a watch runs that batch shape forever. There is one execution path
through :func:`~docmax.runners.pipeline.run_pipeline`, and a bare tool is a
one-stage pipeline rather than a second one.
"""

from __future__ import annotations

from docmax.runners.batch import BatchReport, ItemOutcome, plan_batch, run_batch
from docmax.runners.pipeline import (
    Pipeline,
    Stage,
    load_pipeline,
    pipeline_from_mapping,
    run_pipeline,
    single_stage,
    validate,
)
from docmax.runners.watch import WatchOptions, watch_folder

__all__ = [
    "BatchReport",
    "ItemOutcome",
    "Pipeline",
    "Stage",
    "WatchOptions",
    "load_pipeline",
    "pipeline_from_mapping",
    "plan_batch",
    "run_batch",
    "run_pipeline",
    "single_stage",
    "validate",
    "watch_folder",
]
