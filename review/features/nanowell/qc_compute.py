"""The QC view's model: every well and timepoint of a nanowell plate, each with the one state that says what needs
doing about it — read from the run's own records for the whole plate, never from the page's selection.

A timepoint's state comes from two records. The grid record (``nanowells/<well>_grid.json``) says whether the run
could number the timepoint's nanowells at all; the identity check's sidecar (``pipeline.nanowell_identity``) says
whether the numbers it gave name the same nanowells as at the well's reference timepoint. The grid status wins: a
timepoint with no addresses has nothing for the identity check to confirm, and reading it as *inconclusive* would
hide the remedy, which is an anchor. So, in order:

1. ``unaddressed`` with an anchor set that no run has used yet → **anchor waiting** (a re-run is the remedy);
2. ``unaddressed`` otherwise → **not addressed** (an anchor is the remedy; :attr:`QcCell.refused` when the last run
   tried the anchor set here and still could not address it);
3. any other status but ``ok`` → **not located** (no lattice, a refused orientation, …: an anchor cannot change it);
4. ``ok`` → the identity check's verdict: contradicted, inconclusive, partial, not checked, reference, confirmed.

Which timepoints are *not addressed* and which have an *anchor waiting* is decided by
``anchor_queue_compute.waiting_items`` — the very function the *Anchor queue* walks — so the matrix and the queue
cannot disagree about what is waiting.

Reading it costs a hash of each well's sites table (the identity check's own staleness test), so
:func:`read_plate` runs on a worker; :func:`signature` is the cheap part that says when to read again.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from review.features.nanowell import anchor_queue_compute as aqc

NOT_ADDRESSED, ANCHOR_WAITING, NOT_LOCATED = "not_addressed", "anchor_waiting", "not_located"
CONTRADICTED, INCONCLUSIVE, PARTIAL = "contradicted", "inconclusive", "partial"
NOT_CHECKED, REFERENCE, CONFIRMED = "not_checked", "reference", "confirmed"
#: Every state a cell can hold, worst first: the legend's order.
QC_STATES = (NOT_ADDRESSED, ANCHOR_WAITING, NOT_LOCATED, CONTRADICTED, INCONCLUSIVE, PARTIAL, NOT_CHECKED,
             REFERENCE, CONFIRMED)
#: The states that need a look: a well holding any of them gets a row under *Wells: Needing a look*.
NEEDS_A_LOOK = QC_STATES[:7]
#: The states a click opens on the Array view (an anchor's business); the rest open on the Identity view.
ARRAY_STATES = (NOT_ADDRESSED, ANCHOR_WAITING, NOT_LOCATED)
#: How each state is named on the legend and in the readout.
WORDS = {NOT_ADDRESSED: "not addressed", ANCHOR_WAITING: "anchor waiting", NOT_LOCATED: "not located",
         CONTRADICTED: "contradicted", INCONCLUSIVE: "inconclusive", PARTIAL: "partial", NOT_CHECKED: "not checked",
         REFERENCE: "reference", CONFIRMED: "confirmed"}
#: The *Wells* control: only the wells needing a look, or every well with a grid record.
WELL_FILTERS = ("needing", "all")
_OK = "ok"


@dataclass(frozen=True)
class QcCell:
    well: str
    timepoint: str
    state: str
    why: str = ""
    left_out: int | None = None       # partial: how many nanowells the timepoint leaves out of joins over time
    refused: bool = False             # not addressed: the last run tried the anchor set here and could not use it
    grid_status: str = ""


@dataclass
class QcMatrix:
    """The plate's cells, ``(well, timepoint) -> QcCell``; a pair the well's grid record does not name has none."""

    wells: list
    timepoints: list
    cells: dict
    #: Why the anchors file could not be read, or ``""``: then no anchor can be told to be waiting.
    anchors_problem: str = ""
    #: Whether any well has an identity sidecar at all; without one every addressed timepoint reads *not checked*.
    checked_any: bool = False
    counts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = {}
            for cell in self.cells.values():
                self.counts[cell.state] = self.counts.get(cell.state, 0) + 1

    def needing(self) -> list[str]:
        """The wells with any cell needing a look, in plate order."""
        bad = {cell.well for cell in self.cells.values() if cell.state in NEEDS_A_LOOK}
        return [well for well in self.wells if well in bad]

    def needing_count(self) -> int:
        return sum(n for state, n in self.counts.items() if state in NEEDS_A_LOOK)

    def shown_wells(self, which: str) -> list[str]:
        return list(self.wells) if which == "all" else self.needing()


@dataclass(frozen=True)
class PlateRecords:
    """What :func:`read_plate` reads from disk, before any state is decided."""

    grids: dict                       # well -> {timepoint -> grid block} (``anchor_queue_compute.grid_statuses``)
    verdicts: dict                    # well -> {timepoint -> (verdict, why)} (``pipeline.nanowell_identity``)
    excluded: dict                    # well -> {timepoint -> addresses} a partial timepoint leaves out
    anchors: dict                     # (well, timepoint) -> Anchor
    anchors_problem: str = ""
    checked_any: bool = False
    #: ``timepoint -> hours`` from the acquisition record (or the labels), as the page's tables get them.
    times_h: dict = field(default_factory=dict)


def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (path.name, None)
    return (path.name, st.st_mtime_ns, st.st_size)


def signature(data_dir, anchors_path) -> tuple:
    """Every file :func:`read_plate` depends on, by name, time and size: a different signature means read again.
    Only ``stat`` calls, cheap enough for the GUI thread. A run, a re-run, the identity check and a new anchor each
    change it."""
    from review.data.nanowell_focus_gate import nanowell_dir

    folder = nanowell_dir(data_dir)
    files = []
    if folder is not None and Path(folder).is_dir():
        files = [p for p in sorted(Path(folder).iterdir())
                 if p.name.endswith(("_grid.json", "_identity.json", ".csv"))]
        files += sorted(Path(folder).parent.glob("*_out.zip"))
    anchors = [Path(anchors_path)] if anchors_path is not None else []
    return (str(data_dir or ""), tuple(_stat(p) for p in files + anchors))


def read_plate(data_dir, anchors_path) -> PlateRecords:
    """Read every well's grid record, identity verdicts and exclusions, and the anchors file. For a worker: the
    identity check's verdicts hash each well's sites table to tell a current result from a stale one."""
    from pipeline.nanowell_identity import exclusions, sidecar_path, verdicts
    from review.data.nanowell_focus_gate import nanowell_dir
    from review.features.nanowell.collect import timepoint_hours
    from util.nanowell_anchors import AnchorsFileError, read_anchors

    folder = nanowell_dir(data_dir)
    grids = aqc.grid_statuses(folder)
    out_dir = Path(folder).parent if folder is not None else None
    found, left_out, checked_any = {}, {}, False
    for well, blocks in grids.items():
        found[well] = verdicts(out_dir, well, list(blocks))
        left_out[well] = exclusions(out_dir, well)
        checked_any = checked_any or sidecar_path(out_dir, well).is_file()
    anchors, problem = {}, ""
    if anchors_path is not None:
        try:
            anchors = read_anchors(anchors_path)
        except AnchorsFileError as exc:
            problem = str(exc)
    tps = {tp for blocks in grids.values() for tp in blocks}
    times = timepoint_hours(data_dir, tps) if tps else {}
    return PlateRecords(grids, found, left_out, anchors, problem, checked_any, times)


def _verdict_state(verdict: str) -> str:
    return verdict if verdict in (CONTRADICTED, INCONCLUSIVE, PARTIAL, REFERENCE, CONFIRMED) else NOT_CHECKED


def time_order(times_h: dict):
    """Sort timepoint labels as ``NanowellData.order`` does: by hours, those without a time last, ties by label."""
    return lambda tps: sorted(tps, key=lambda tp: (tp not in times_h, times_h.get(tp, 0.0), _natural(tp)))


def plate_qc(records: PlateRecords, order=None) -> QcMatrix:
    """The :class:`QcMatrix` of *records*: one cell per timepoint each grid record names, by the precedence in the
    module docstring. *order* sorts timepoint labels; ``None`` sorts them by the records' own hours
    (:func:`time_order`), which is the page's order whether or not any table has been read."""
    order = order if order is not None else time_order(records.times_h)
    waiting, pending = aqc.waiting_items(records.grids, records.anchors, order=order)
    waiting = {(item.well, item.timepoint): item for item in waiting}
    cells = {}
    for well, blocks in records.grids.items():
        for tp, block in blocks.items():
            status = str(block.get("status") or "")
            reason = str(block.get("reason") or "").strip()
            key = (well, tp)
            if key in pending:
                cells[key] = QcCell(well, tp, ANCHOR_WAITING, reason, grid_status=status)
            elif key in waiting:
                cells[key] = QcCell(well, tp, NOT_ADDRESSED, reason, refused=waiting[key].refused,
                                    grid_status=status)
            elif status != _OK:
                cells[key] = QcCell(well, tp, NOT_LOCATED, reason, grid_status=status)
            else:
                verdict, why = (records.verdicts.get(well) or {}).get(tp, (NOT_CHECKED, ""))
                state = _verdict_state(verdict)
                n = len((records.excluded.get(well) or {}).get(tp) or ()) if state == PARTIAL else None
                cells[key] = QcCell(well, tp, state, str(why or ""), left_out=n, grid_status=status)
    tps = {tp for blocks in records.grids.values() for tp in blocks}
    timepoints = list(order(list(tps)))
    return QcMatrix(wells=sorted(records.grids), timepoints=timepoints, cells=cells,
                    anchors_problem=records.anchors_problem, checked_any=records.checked_any)


def _natural(label: str) -> tuple:
    import re

    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(label)))


def _hours(hours) -> str:
    return f" ({hours:g} h)" if isinstance(hours, (int, float)) and hours == hours else ""


def short_text(cell: QcCell) -> str:
    """The canvas readout's lead for *cell*: ``B04 · T03 · contradicted``."""
    word = WORDS[cell.state] + (f" ({cell.left_out:,} left out)" if cell.left_out else "")
    return f"{cell.well} · {cell.timepoint} · {word}"


def hover_text(cell: QcCell, hours=None) -> str:
    """The full sentence for *cell*'s tooltip: what its state means and what a click does."""
    head = f"{cell.well} · {cell.timepoint}{_hours(hours)} — "
    why = cell.why.rstrip(".")
    identity = " Click to open it on Identity."
    if cell.state == CONFIRMED:
        body = "confirmed: its addresses name the same nanowells as at the reference timepoint." + identity
    elif cell.state == REFERENCE:
        body = "reference: the timepoint this well's others are checked against." + identity
    elif cell.state == PARTIAL:
        body = (f"partial: {cell.left_out or 0:,} nanowell(s) left out of comparisons over time; the rest are used."
                + (f" {why}." if why else "") + identity)
    elif cell.state == INCONCLUSIVE:
        body = (f"inconclusive: the identity check could not decide{f' — {why}' if why else ''}. Still used in "
                "comparisons over time; look before relying on it." + identity)
    elif cell.state == CONTRADICTED:
        body = (f"contradicted{f': {why}' if why else ''}. Left out of every comparison over time." + identity)
    elif cell.state == NOT_CHECKED:
        body = f"not checked{f': {why}' if why else ''}." + identity
    elif cell.state == NOT_ADDRESSED and cell.refused:
        body = ("not addressed: the last run could not address it from the anchor set here. Click to set it again "
                "on Array.")
    elif cell.state == NOT_ADDRESSED:
        body = (f"not addressed: {why or 'the run could not find the central square'}. Click to set its anchor on "
                "Array.")
    elif cell.state == ANCHOR_WAITING:
        body = ("anchor waiting: an anchor is set here and no run has used it yet. Re-run anchored wells… addresses "
                "it. Click to see it on Array.")
    else:
        status = cell.grid_status.replace("_", " ") or "no status"
        body = (f"not located ({status}){f': {why}' if why else ''}. An anchor set by hand cannot change this. "
                "Click to see it on Array.")
    return head + body


def summary(matrix: QcMatrix) -> str:
    """The figure's title: how many timepoints need a look, in how many wells."""
    total = len(matrix.cells)
    if not total:
        return ""
    bad, wells = matrix.needing_count(), len(matrix.needing())
    if not bad:
        return f"All {total:,} timepoints of {len(matrix.wells)} wells are addressed and confirmed."
    return f"{bad:,} of {total:,} timepoints need a look, in {wells} of {len(matrix.wells)} wells."


def notes(matrix: QcMatrix, which: str, out_dir) -> list[str]:
    """The muted lines under the title: wells hidden by *Wells*, no identity check run, an unreadable anchors file."""
    out = []
    hidden = len(matrix.wells) - len(matrix.shown_wells(which))
    if hidden and matrix.needing():
        out.append(f"{hidden} well(s) with every timepoint confirmed are not shown — Wells: All shows them.")
    elif hidden:
        out.append("Wells: All shows them.")
    if not matrix.checked_any and any(c.state == NOT_CHECKED for c in matrix.cells.values()):
        from review.features.nanowell.identity_compute import audit_command

        out.append("The identity check has not been run on these tables, so no addressed timepoint is confirmed. "
                   f"Run it from a terminal: {audit_command(out_dir)}")
    if matrix.anchors_problem:
        out.append(f"The anchors file cannot be used: {matrix.anchors_problem}. Anchors waiting for a re-run cannot "
                   "be told apart.")
    return out


def legend_items(matrix: QcMatrix) -> list[tuple[str, int]]:
    """``(state, count)`` for each state on the plate, worst first, and *confirmed* always (its count may be 0)."""
    return [(s, matrix.counts.get(s, 0)) for s in QC_STATES if matrix.counts.get(s) or s == CONFIRMED]


def queue_tip(matrix: QcMatrix | None) -> str:
    """Why *Anchor queue* is greyed, or ``""`` when it has something to walk."""
    if matrix is None:
        return "The plate's records are still being read."
    if matrix.counts.get(NOT_ADDRESSED):
        return ""
    tip = "No timepoint is waiting for an anchor."
    waiting = matrix.counts.get(ANCHOR_WAITING, 0)
    if waiting:
        tip += f" {waiting} have an anchor waiting for a re-run: Re-run anchored wells… addresses them."
    return tip


def hours_of(times_h: dict, tp: str):
    hours = (times_h or {}).get(tp)
    return hours if isinstance(hours, (int, float)) and hours == hours else None

