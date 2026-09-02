"""The rows behind a batch figure, written beside it.

Every batch mode already writes a summary CSV per group — the numbers its
figure draws. This is the other file the plot tabs have always offered and
batch export never did: ``Save cell data…``, one row per cell the figure was
computed from, with the scope block naming what produced it and the marker
columns saying which cells the figure counted.

**Nothing here re-implements that export.** The scope comes from the mode's
source tab's own ``cell_scope_compute.cell_scope``, the rows from
``export/cell_scope_compute.build_chunk``, the header and the ``#`` block from
``export/service``, and the file is written by ``export/worker.CellExportWriter``
— the same serialiser, so the batch file's columns, quoting, blank-versus-zero
rule and atomic replace are the tab's and cannot drift from them. A second
writer beside that one would be a second answer to "what does a cell export
look like", and the two would disagree the first time either was fixed.

**It runs synchronously, where the tab's runs on a thread.** The tab is
exporting one plot while the user waits and the GUI has to stay alive; a batch
job is already inside a run that blocks, one group at a time, writing into a
staging directory that is committed or discarded as a unit. So the queue the
writer drains is filled and closed before it is called, and it is called
inline. That is the writer's own contract — it drains until ``DONE`` — used
without a thread rather than in spite of one.

The cost of filling it first is real and slightly worse than the tab's. The tab
reads every well's frame into a dict too, but it bounds the queue at two chunks
and slices one well per event-loop tick; this holds the frames dict **plus a
sliced copy of every well's population** at once. A batch group is a handful of
wells rather than a plate, so the bound is fine — but it is not the same bound,
and a future reader deciding whether it is safe at forty wells should have the
real number rather than a reassuring comparison.
"""

from __future__ import annotations

import logging
import queue
import threading

# Module scope, not function-local. ``features/export/`` is a **support lib**,
# not a removable feature — ARCHITECTURE §3 names it beside ``crop`` and ``qc``
# — so nothing here is working around a removability contract, and a deferred
# import that is not breaking a cycle is one the import budget counts for
# nothing. Several sibling features already import it this way.
from well_viewer.features.export import cell_scope_compute as csc
from well_viewer.features.export.service import _scope_block, _well_labels_map
from well_viewer.features.export.worker import DONE, CellExportWriter

_logger = logging.getLogger("well_viewer")


def _gating_busy(ctx) -> bool:
    """Whether the cell-gating worker is mid-recompute.

    Read through the same runtime flag ``export/service`` consults, so the two
    refusals cannot come to disagree about what "busy" means.
    """
    from well_viewer.core.safe import swallowed

    try:
        from well_viewer.features.export.service import _gating_busy as _busy
        return bool(_busy(ctx))
    except Exception:
        swallowed("batch cell export: reading the gating-busy flag")
        return False


def write_cell_rows(ctx, scope, out_path) -> int:
    """Write every cell row behind *scope* to *out_path*. Returns the row count.

    Two negatives, because they are two different problems with two different
    fixes. ``-1`` means the scope named nothing this dataset has loaded — check
    the selection. ``-2`` means it named loaded wells and **none of their rows
    would read** — the cache is bad, and telling that user to check their
    selection sends them to fix something that is already right.

    Both are distinguished from ``0``, which is a real answer: a scope whose
    wells are loaded and whose population is empty gets a real file with its
    header, so a reader can see which columns the export would have carried and
    that the count was genuinely none.
    """
    from well_viewer.data.data_loading import get_rows

    if scope is None:
        return -1
    if _gating_busy(ctx):
        # The same refusal the GUI path makes, for the same reason: the gating
        # worker rewrites ``Included`` / ``Gate_Pass`` / ``Gate_On`` **in place
        # on the cached frames**, and ``_run_batch_jobs`` pumps the event loop
        # between jobs — which is exactly where that worker's callbacks land.
        # A run started mid-recompute would give the early groups the old
        # gating and the later ones the new, in one output directory under one
        # manifest, with nothing in any file saying so.
        return -3
    loaded = ctx.state.dataset.well_paths or {}
    wells = [(group, well) for group, well in scope.wells if well in loaded]
    if not wells:
        return -1

    frames: dict = {}
    unreadable: list[str] = []
    for _group, well in wells:
        try:
            frames[well] = get_rows(ctx, well)
        except Exception:
            # Named, not dropped. A well whose rows would not load is otherwise
            # indistinguishable from one that had no passing cells — both
            # simply contribute nothing — and the scope block carries the
            # distinction into the file.
            _logger.exception("Batch cell export: rows for %r", well)
            unreadable.append(well)
    frames_columns = [list(f.columns) for f in frames.values() if f is not None]
    if not frames_columns:
        return -2 if unreadable else -1

    header = csc.header_for(frames_columns, scope)
    scope_lines = _scope_block(
        ctx, scope, wells=wells,
        missing_verdicts=csc.missing_verdict_layers(frames_columns),
        header_len=len(header), unreadable=unreadable)

    labels = _well_labels_map(ctx)
    chunks: queue.Queue = queue.Queue()
    for group, well in wells:
        frame = frames.get(well)
        if frame is None:
            continue
        chunks.put(csc.build_chunk(
            frame, scope, group=group, well_label=well,
            well_name=labels.get(well, well)))
    chunks.put(DONE)

    writer = CellExportWriter(
        out_path=out_path, header=header, scope_lines=scope_lines,
        chunks=chunks,
        # Never set: there is no user to cancel a job that is already inside a
        # blocking run, and a cancel event that could fire would let the writer
        # return its "cancelled" result into a batch that would then commit the
        # partial file it had left behind.
        cancel=threading.Event(),
        total_wells=len(wells))
    result = writer()
    return int(result.get("rows", 0))
