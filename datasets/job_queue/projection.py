"""What a queued job is expected to produce, before it has run.

When the Job Queue opens a tool's page to set up the next step after a queued
job, the pages that build their controls from the input's contents — Subset's
well, timepoint and FOV pickers; Analyze's channel options — have nothing on
disk to read. This module answers from the queue instead: what the job will
write, worked out from its own spec and, recursively, from what its inputs
will hold.

Each answer is a :class:`Projection` whose fields are either known or
``None``. ``None`` means "not known until the job runs", never "empty": a
Subset of a converted plate cannot know which timepoints a CZI holds until
the converter has read every file, and offering an empty list would queue a
job that keeps no timepoints at all. The page says "all — decided when it
runs" instead, and the run-time check in the Subset controller refuses a job
whose explicit choices the real output turns out not to contain.

The projection mirrors what the page itself would read from the finished
output, and nothing more. A Subset page builds its timepoint and FOV pickers
from ``out/pipeline_info.json``, which only an analysed dataset has; a
converted plate has none, so a converter's projection states its wells and
leaves timepoints and FOVs unknown, exactly as the real folder would.

Qt-free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.safe import swallow
from datasets.core.dataset_paths import preview_wells
from services.job_kinds import CONVERTER_KINDS, input_targets, produced_layout
from services.job_queue import JobQueue, JobSpec, JobState
from util.timepoint import sort_key as tp_sort_key
from util.well_token import canonical_well_label


@dataclass(frozen=True)
class Projection:
    """What a dataset holds, or is expected to hold. ``None`` is unknown."""

    wells: tuple[str, ...] | None = None
    timepoints: tuple[str, ...] | None = None
    fovs: tuple[str, ...] | None = None
    #: The filename schema and channel roles a converter recorded, passed
    #: through any layout-preserving job in between (``produced_layout``).
    layout: dict | None = field(default=None, compare=False)

    def as_dict(self) -> dict:
        """The shape a page's ``set_pending_inputs(projection=…)`` takes."""
        return {
            "wells": list(self.wells) if self.wells is not None else None,
            "timepoints": list(self.timepoints) if self.timepoints is not None else None,
            "fovs": list(self.fovs) if self.fovs is not None else None,
            "layout": dict(self.layout) if self.layout else None,
        }


UNKNOWN = Projection()


def on_disk(root: str | Path) -> Projection:
    """What a dataset folder holds now, read the way the Subset page reads it.

    Wells from ``in/``, timepoints and FOVs from ``out/pipeline_info.json``
    when there is one. A folder that is not a dataset (yet) is unknown, not
    empty.
    """
    root = Path(root) if root else None
    if root is None or not root.is_dir():
        return UNKNOWN
    wells = tuple(preview_wells(root)) or None
    timepoints = fovs = None
    info_path = root / "out" / "pipeline_info.json"
    if info_path.is_file():
        with swallow("reading pipeline_info for a queue projection"):
            info = json.loads(info_path.read_text())
            tps = {str(t).strip() for t in info.get("available_timepoints") or [] if str(t).strip()}
            fv = {str(f).strip() for f in info.get("available_fovs") or [] if str(f).strip()}
            timepoints = tuple(sorted(tps, key=tp_sort_key)) or None
            fovs = tuple(sorted(fv)) or None
    return Projection(wells=wells, timepoints=timepoints, fovs=fovs)


def project(queue: JobQueue, job_id: str) -> Projection:
    """What *job_id* is expected to write, from the queue alone.

    A job that has already succeeded is read from the folder it reported.
    Anything else is worked out from its spec and its inputs' projections.
    """
    return _project(queue, job_id, set())


def _project(queue: JobQueue, job_id: str, seen: set[str]) -> Projection:
    job = queue.get(job_id)
    if job is None or job_id in seen:
        return UNKNOWN
    seen = seen | {job_id}
    if job.state == JobState.SUCCEEDED and job.dataset_root:
        return replace(on_disk(job.dataset_root), layout=job.layout)
    inputs = [_input(queue, job, target, seen) for target in input_targets(job.kind)]
    layout = produced_layout(job.kind, job.spec, [p.layout for p in inputs])
    kind, spec = job.kind, job.spec or {}
    if kind in CONVERTER_KINDS:
        wells = _wells(spec.get("planned_wells"))
        return Projection(wells=wells, layout=layout)
    if kind in ("validate", "analyze"):
        # Validate writes nothing; Analyze reads its input's wells and writes
        # an out/ beside them. Either way the dataset holds what the input did.
        return replace(inputs[0], layout=layout)
    if kind == "subset":
        return _subset(inputs[0], spec, layout)
    if kind == "concatenate":
        return _combine(inputs, layout, timepoints_known=False)
    if kind == "merge_plates":
        return _merge(inputs, spec, layout)
    return UNKNOWN


def _input(queue: JobQueue, job: JobSpec, target: str, seen: set[str]) -> Projection:
    link = job.link_for(target)
    if link is not None:
        return _project(queue, link.source, seen)
    value = _dotted(job.spec or {}, target)
    return on_disk(value) if value else UNKNOWN


def _dotted(spec: dict, dotted: str) -> str:
    cur: object = spec
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    return str(cur or "").strip()


def _wells(values) -> tuple[str, ...] | None:
    if not values:
        return None
    out = sorted({canonical_well_label(str(w)) or str(w) for w in values})
    return tuple(out) or None


def _keep(known: tuple[str, ...] | None, chosen) -> tuple[str, ...] | None:
    """A Subset dimension: what it chose, narrowed to what its input holds."""
    if not chosen:
        return known
    chosen = tuple(str(c) for c in chosen)
    if known is None:
        return chosen
    return tuple(c for c in chosen if c in set(known))


def _subset(src: Projection, spec: dict, layout: dict | None) -> Projection:
    return Projection(
        wells=_keep(src.wells, _wells(spec.get("wells"))),
        timepoints=_keep(src.timepoints, spec.get("timepoint_labels")),
        fovs=_keep(src.fovs, spec.get("fovs")),
        layout=layout,
    )


def _union(values: list[tuple[str, ...] | None]) -> tuple[str, ...] | None:
    if not values or any(v is None for v in values):
        return None
    return tuple(sorted({item for v in values for item in v}))


def _combine(inputs: list[Projection], layout: dict | None, *,
             timepoints_known: bool) -> Projection:
    """Concatenate: every well and FOV of either input. Timepoints are not
    projected, because dataset 2's are relabelled by a gap only the run
    settles (an untouched gap field means "work it out when it runs")."""
    return Projection(
        wells=_union([p.wells for p in inputs]),
        timepoints=_union([p.timepoints for p in inputs]) if timepoints_known else None,
        fovs=_union([p.fovs for p in inputs]),
        layout=layout,
    )


def _merge(inputs: list[Projection], spec: dict, layout: dict | None) -> Projection:
    """Merge plates: dataset 1's wells plus dataset 2's, moved as a block when
    the job places dataset 2 at another origin."""
    if len(inputs) == 2 and spec.get("remap_enabled") and inputs[1].wells is not None:
        from datasets.merge_plates.merge_plates_compute import (
            MergePlatesError,
            block_shift_well_map,
        )

        try:
            moved = tuple(block_shift_well_map(
                inputs[1].wells, target_origin=str(spec.get("remap_origin") or "")).values())
        except MergePlatesError:
            moved = None
        inputs = [inputs[0], replace(inputs[1], wells=moved)]
    return _combine(inputs, layout, timepoints_known=True)
