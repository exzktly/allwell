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

**The writer drains on its own thread while this cuts the chunks.** A batch
job is already inside a run that blocks, one group at a time, writing into a
staging directory that is committed or discarded as a unit, so this call still
returns only when the file is written — the thread is joined before it does.
What the thread buys is the bound: the queue holds at most two chunks, and each
well's rows are read only when its chunk is cut, so the export holds one well
and two slices at a time, the same bound the tab's export keeps, rather than a
sliced copy of every well in the scope. The rows are read twice — once for the
column lists the header needs, once for the chunk — which costs a re-read from
the Parquet cache for a well the row cache has since evicted. A well that reads
the first time and fails the second is logged and contributes no rows.

A writer on a thread is a writer whose failure can vanish, so two things are
carried across: a write that fails (the temp file unwritable, the disk full,
the replace refused) is re-raised here exactly as it was raised inline — never
a hang on a queue nobody drains, never "0 rows" — and a chunk that fails to cut
cancels the writer, which then discards its temp file instead of committing a
partial one, before that failure is re-raised.
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
from review.features.export import cell_scope_compute as csc
from review.features.export.service import (
    _gating_busy, _scope_block, figure_names_map,
)
from review.features.export.worker import DONE, CellExportWriter

#: How often a blocked ``put`` re-checks that the writer thread is still draining.
_PUT_POLL_S = 0.1

_logger = logging.getLogger("review")


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
    from review.data.data_loading import get_rows

    if scope is None:
        return -1
    # ``export/service``'s own predicate, imported rather than restated, so the
    # GUI refusal and this one cannot come to disagree about what "busy" means.
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

    # Each well's columns, not its frame — see ``export.service.export_cell_data``
    # for why; the rows are read again, one well at a time, as its chunk is cut.
    columns_by_well: dict[str, list] = {}
    unreadable: list[str] = []
    for _group, well in wells:
        try:
            columns_by_well[well] = list(get_rows(ctx, well).columns)
        except Exception:
            # Named, not dropped. A well whose rows would not load is otherwise
            # indistinguishable from one that had no passing cells — both
            # simply contribute nothing — and the scope block carries the
            # distinction into the file.
            _logger.exception("Batch cell export: rows for %r", well)
            unreadable.append(well)
    frames_columns = list(columns_by_well.values())
    if not frames_columns:
        return -2 if unreadable else -1

    header = csc.header_for(frames_columns, scope)
    scope_lines = _scope_block(
        ctx, scope, wells=wells,
        missing_verdicts=csc.missing_verdict_layers(frames_columns),
        header_len=len(header), unreadable=unreadable)

    labels = figure_names_map(ctx)
    # Bounded, with the writer draining it on its own thread while the chunks
    # are cut here: an unbounded queue filled before the writer started held a
    # sliced copy of every well of the scope at once.
    chunks: queue.Queue = queue.Queue(maxsize=2)

    cancel = threading.Event()
    writer = CellExportWriter(
        out_path=out_path, header=header, scope_lines=scope_lines,
        chunks=chunks,
        # Set only when cutting a chunk fails, so the writer discards its temp
        # file rather than committing a partial one; that failure is then
        # re-raised here, so the batch records the shortfall and never sees a
        # file. There is no user to cancel a job already inside a blocking run.
        cancel=cancel,
        total_wells=len(wells))
    outcome: dict = {}

    def _drain() -> None:
        # The writer re-raises a write failure; on a thread that would vanish,
        # so it is carried back and raised in the caller, as it was inline.
        try:
            outcome["result"] = writer() or {}
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            outcome["error"] = exc

    def _put(item) -> bool:
        """Queue *item* unless the writer has stopped; never block on a dead one."""
        while thread.is_alive():
            try:
                chunks.put(item, timeout=_PUT_POLL_S)
                return True
            except queue.Full:
                continue
        return False

    thread = threading.Thread(target=_drain, name="batch-cell-export-writer", daemon=True)
    thread.start()
    try:
        for group, well in wells:
            if well not in columns_by_well:
                continue
            try:
                frame = get_rows(ctx, well)
            except Exception:
                _logger.exception("Batch cell export: rows for %r", well)
                continue
            chunk = csc.build_chunk(frame, scope, group=group, well_label=well,
                                    well_name=labels.get(well, well))
            if not _put(chunk):
                break
    except BaseException:
        cancel.set()
        thread.join()
        raise
    _put(DONE)
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return int(outcome.get("result", {}).get("rows", 0))
