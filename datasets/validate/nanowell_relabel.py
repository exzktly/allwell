"""Find and repair nanowell wells whose timepoints were addressed in different lattice frames.

Each timepoint of a nanowell well is anchored on its own evidence, and a nanowell's address ``(q, r)`` is counted
from that origin. A timepoint whose origin landed a site off (an array centre a fitted circle left loose, with no
central square to check it against) names every nanowell after its neighbour, and Review's join by address then
pairs each nanowell with its neighbour from that timepoint on — fold change, trajectories and every per-nanowell
series. The run now settles that before it measures (``pipeline.nanowell_run._settle_steps``); a dataset analysed
before it did carries the wrong addresses in its tables.

This module repairs such a dataset from its tables alone, with no images and no re-run. The decision is the run's
own (:func:`pipeline.nanowell_frames.settle_frames`): the timepoints are grouped by the lattice step their detected
nanowells sit at against one another's, and the frame holding the best-ranked origin (an anchor set by hand, then
the central square, then the array's centre), then the most timepoints, is kept. The other timepoints' rows are
then relabelled: a row's measurements were taken at a physical nanowell, and only its name was wrong, so address
``(q, r)`` becomes ``(q + dq, r + dr)`` and every value stays as measured.

Only a timepoint the run flagged ``roster_mismatch`` is read for a step. A step shows in the tables only as a
crescent of empty rim sites on one side, and a bubble or glare over that side leaves the same crescent, written the
same way (``occluded``, nothing of the disk visible). The run itself could tell them apart, since it compared every
nanowell each timepoint detected, beyond the roster too, and it flags a timepoint whose nanowells sat a step from the
others'; a timepoint it left consistent is taken to be in the roster's frame. Its rim reading a step is still
reported (``nanowell_rim_unconfirmed``, an info finding, or a sentence of the well's finding), never acted on: a
dataset whose run kept a roster without that check would show a real step only that way.

What a relabel cannot give back is the rim. The wrong origin put that timepoint's roster disk a step off, so it
measured a crescent of sites beyond the array's rim and none of the crescent inside it on the far side. Rows that
land outside the well's roster are dropped; roster nanowells the timepoint never measured get a row that says so —
state ``clipped``, every measurement blank, never zero — and the grid record counts both. A cell whose nanowell left
the roster keeps its row in the cell table with its nanowell address blanked. Everything inside the rim is exact.

Two cases are reported and left alone: two frames that rank alike (nothing says which is right; an anchor set by
hand on one timepoint, then a re-run of the well, settles it), and a timepoint flagged ``roster_mismatch`` that no
step within reach explains (a re-run of the well is the repair). Such a timepoint keeps its flag even when the
repair relabels the well's other timepoints: only a timepoint the repair moves, or re-rosters, is marked
``consistent``, so Review goes on warning about the rest and the next run still re-analyses them. Only circular wells are considered: an inferred
array's timepoints are registered by the run itself and each may see a different array.

Qt-free. The originals are copied to ``out/nanowells/relabel_backup/<time>/`` before anything is written.
"""
from __future__ import annotations

import csv
import gzip
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from datasets.core.nanowell_rim import site_address as _address
from datasets.core.nanowell_rim import step_against_roster
from pipeline.nanowell_frames import SHIFT, TIE, choose_frame

NANOWELL_DIRNAME = "nanowells"
#: Where the originals of every relabelled well are copied before the rewrite, under a folder named for the time.
BACKUP_DIRNAME = "relabel_backup"
#: The state a roster nanowell is written with at a relabelled timepoint that never measured it: not imaged, as far
#: as this timepoint's rows can say, so no count is drawn from it. Its measurement columns are blank.
UNMEASURED_STATE = "clipped"
#: What a relabelled timepoint's grid block names as the repair, so the Array view and a reader of the record can
#: tell a relabel from a timepoint the run itself moved.
RELABELLED_BY = "Datasets → Validate"
_ADDRESS_COLUMNS = ("nw_q", "nw_r", "nw_ring", "nw_label")


#: The verdict of a well with nothing to repair whose consistent timepoints' rims read a step (``WellPlan.rim_only``).
RIM_ONLY = "rim_only"


@dataclass(frozen=True)
class WellPlan:
    """What Validate found about one well's timepoint frames.

    *moves* maps each timepoint whose rows are rewritten to the step its addresses move by (``(0, 0)`` for one that
    keeps its addresses but whose roster changes); *roster_offset* is the winning frame's own step, so the repaired
    roster is the recorded one moved by ``-roster_offset``. *rim_only* maps each timepoint the run left consistent
    whose rim alone reads a step to that step: it is reported and never moved (module docstring)."""
    well: str
    verdict: str                                   # SHIFT (repairable), TIE, "unexplained", or RIM_ONLY
    moves: dict = field(default_factory=dict)
    reference: str | None = None
    frames: tuple = ()
    unexplained: tuple = ()                        # roster_mismatch timepoints no step explains
    roster_offset: tuple = (0, 0)
    rim_only: dict = field(default_factory=dict)

    @property
    def shifts(self) -> dict:
        """The timepoints whose addresses move, and by how much."""
        return {tp: step for tp, step in self.moves.items() if step != (0, 0)}

    @property
    def repairable(self) -> bool:
        return self.verdict == SHIFT and bool(self.shifts)


def _read_rows(path: Path) -> tuple[list[str], list[dict]]:
    opener = gzip.open(path, "rt", newline="", encoding="utf-8") if path.suffix == ".gz" else \
        path.open(newline="", encoding="utf-8")
    with opener as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _part(path: Path) -> Path:
    """Where a table's rewrite is written before it replaces the table (:func:`relabel_well` commits them together)."""
    return path.with_name(path.name + ".part")


def _write_rows(path: Path, columns: list[str], rows: list[dict]) -> Path:
    """Write *rows* beside *path* (``<name>.part``), gzipped when *path* is; return that file. The caller replaces
    *path* with it."""
    tmp = _part(path)
    opener = gzip.open(tmp, "wt", newline="", encoding="utf-8") if path.suffix == ".gz" else \
        tmp.open("w", newline="", encoding="utf-8")
    with opener as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return tmp


def _is_circular(grid: dict) -> bool:
    return grid.get("array_radius", {}).get("radius_um") is not None


def plan_well(well: str, grid: dict, site_rows: list[dict]) -> WellPlan | None:
    """The plan for one well from its grid record and sites table; ``None`` when its timepoints already share one
    frame, or it is not a circular well with a kept roster (an inferred array's timepoints are registered by the run
    itself; a well analysed with the disk rule has a ring of roster sites beyond its rim at every timepoint, which
    hides the crescent this reads, and is re-run instead)."""
    if not _is_circular(grid):
        return None
    blocks = {tp: b for tp, b in grid.get("timepoints", {}).items() if isinstance(b, dict)}
    flagged = tuple(tp for tp, b in blocks.items() if b.get("consistency") == "roster_mismatch")
    roster = {tuple(map(int, a)) for a in grid.get("array_radius", {}).get("roster") or ()}
    if not roster:
        return WellPlan(well, "unexplained", unexplained=flagged) if flagged else None
    anchored = [tp for tp, b in blocks.items() if b.get("status") == "ok" and b.get("origin_method")]
    by_tp: dict = {tp: [] for tp in anchored}
    for row in site_rows:
        if row.get("timepoint") in by_tp:
            by_tp[row["timepoint"]].append(row)
    # Only a flagged timepoint is moved by what its rim reads. The rim cannot tell a step from a bubble or glare over
    # one side (the rendered run writes both crescents ``occluded`` with no visible disk), but the run that wrote the
    # tables could: it compared every nanowell each timepoint detected, beyond the roster too, and flagged a timepoint
    # whose nanowells sat a step from the others' (``nanowell_run.misanchored``). A timepoint it left consistent stays
    # in the roster's frame, and a step its rim reads is only reported (``rim_only``).
    read = {tp: step_against_roster(rows, roster) for tp, rows in by_tp.items() if rows}
    onto = {tp: step if tp in flagged else (0, 0) for tp, step in read.items()}
    rim_only = {tp: step for tp, step in read.items() if tp not in flagged and step != (0, 0)}
    decision = choose_frame(onto, {tp: blocks[tp].get("origin_method") for tp in onto}, list(onto))
    if decision.verdict == SHIFT:
        winner = onto[decision.reference]
        moves = {tp: (step[0] - winner[0], step[1] - winner[1]) for tp, step in onto.items()}
        if winner == (0, 0):
            moves = {tp: m for tp, m in moves.items() if m != (0, 0)}
        # A flagged timepoint the repair neither moves nor re-rosters keeps its flag and is reported: the repair has
        # not explained its mismatch, and the flag is what makes Review warn and the next run re-analyse it.
        left = tuple(tp for tp in flagged if tp not in moves)
        return WellPlan(well, SHIFT, moves, decision.reference, decision.frames, unexplained=left,
                        roster_offset=winner, rim_only=rim_only)
    if decision.verdict == TIE:
        return WellPlan(well, TIE, frames=decision.frames, rim_only=rim_only)
    if flagged:
        return WellPlan(well, "unexplained", unexplained=flagged, rim_only=rim_only)
    return WellPlan(well, RIM_ONLY, rim_only=rim_only) if rim_only else None


def plan_dataset(out_dir, progress: Callable[[str], None] | None = None) -> dict[str, WellPlan]:
    """``well -> plan`` for every well of the dataset whose nanowell timepoints need attention. *progress*, when
    given, is told each well as it is read (``"  A01 (3/96): …"``), since a plate's sites tables take a while."""
    folder = Path(out_dir) / NANOWELL_DIRNAME
    plans: dict[str, WellPlan] = {}
    grids = sorted(folder.glob("*_grid.json")) if folder.is_dir() else []
    for k, grid_path in enumerate(grids, start=1):
        well = grid_path.name[: -len("_grid.json")]
        sites_path = folder / f"{well}.csv"
        try:
            grid = json.loads(grid_path.read_text(encoding="utf-8"))
            _columns, rows = _read_rows(sites_path) if sites_path.is_file() else ([], [])
        except (OSError, ValueError, csv.Error):
            continue                     # unreadable tables are nanowell_findings' to report, not this module's
        plan = plan_well(well, grid, rows)
        if plan is not None:
            plans[well] = plan
        if progress is not None:
            verdict = ("timepoints share one frame" if plan is None else
                       "to relabel: " + ", ".join(f"{tp} by {st}" for tp, st in plan.shifts.items())
                       + (f"; {', '.join(plan.unexplained)} stay flagged" if plan.unexplained else "")
                       if plan.repairable else f"left alone ({plan.verdict})")
            progress(f"  {well} ({k}/{len(grids)}): {verdict}")
    return plans


def _rim_only_sentence(plan: WellPlan) -> str:
    """What *plan* says about the timepoints it reports and never moves (``WellPlan.rim_only``)."""
    read = ", ".join(f"{tp} (as {step} would)" for tp, step in plan.rim_only.items())
    return (f"the rim of timepoint(s) {read} is empty on one side as a lattice step would leave it, but the run "
            "found their nanowells in the other timepoints' frame (they are not flagged roster_mismatch), so they are "
            "left as they are: a bubble or glare over that side of the rim reads the same way in the tables. If the "
            "Array view shows their addresses a nanowell off, re-run the well")


def finding_of(plan: WellPlan) -> tuple[str, str, str]:
    """``(severity, code, message)`` Validate lists for *plan*."""
    if plan.verdict == RIM_ONLY:
        return ("info", "nanowell_rim_unconfirmed", f"{plan.well}: {_rim_only_sentence(plan)}")
    severity, code, message = _finding_of(plan)
    return severity, code, message + (f". Also, {_rim_only_sentence(plan)}" if plan.rim_only else "")


def _finding_of(plan: WellPlan) -> tuple[str, str, str]:
    if plan.repairable:
        moved = ", ".join(f"{tp} by {step}" for tp, step in plan.shifts.items())
        left = (f". Timepoint(s) {', '.join(plan.unexplained)} are also flagged roster_mismatch and no lattice step "
                "explains it: the repair leaves them flagged, and a re-run of the well is their repair"
                if plan.unexplained else "")
        return ("warning", "nanowell_misaddressed",
                f"{plan.well}: timepoint(s) addressed a lattice step off {plan.reference}'s nanowells ({moved}), so "
                "their rows name neighbouring nanowells and Review pairs each nanowell with its neighbour over time. "
                f"Apply repairs relabels them onto its addresses (the originals are backed up){left}")
    if plan.verdict == TIE:
        frames = " vs ".join("/".join(frame) for frame in plan.frames)
        return ("warning", "nanowell_frames_ambiguous",
                f"{plan.well}: timepoints anchored equally well disagree by a lattice step ({frames}), so which "
                "addresses are right cannot be told from the tables. Set an anchor by hand on the central square at "
                "one of them (Nanowells → Set anchor…) and re-run the well")
    return ("warning", "nanowell_frames_unexplained",
            f"{plan.well}: timepoint(s) {', '.join(plan.unexplained)} are flagged roster_mismatch and no lattice "
            "step within reach explains it, so they cannot be relabelled. Re-run the well")


def _cells_tables(out_dir: Path, well: str, csv_prefixes) -> list[Path]:
    from util.csv_gz import existing_csv

    found = []
    for prefix in csv_prefixes:
        path = existing_csv(out_dir / f"{prefix}_{well}.csv")
        if path is not None:
            found.append(path)
    return found


def _ring(q: int, r: int, kind) -> int | None:
    from pipeline.nanowell_run import lattice_ring

    return lattice_ring(q, r, kind)


def _relabel_sites(rows, shifts: dict, roster: set, kinds: dict, columns: list[str]) -> tuple[list, dict]:
    """The sites table with each shifted timepoint's rows renamed, and per timepoint ``(dropped, unmeasured)``. Only
    the timepoints in *shifts* (the plan's moves) lose a ``roster_mismatch`` flag; another timepoint's rows keep it."""
    out, counts, seen = [], {}, {tp: set() for tp in shifts}
    for row in rows:
        tp = row.get("timepoint")
        repaired = tp in shifts and row.get("consistency") == "roster_mismatch"
        row = dict(row, consistency="consistent") if repaired else dict(row)
        address = _address(row)
        if tp in shifts and address is not None:
            dq, dr = shifts[tp]
            q, r = address[0] + dq, address[1] + dr
            if (q, r) not in roster:
                counts.setdefault(tp, [0, 0])[0] += 1
                continue
            row.update(nw_q=q, nw_r=r, nw_ring=_ring(q, r, kinds.get(tp)), consistency="consistent")
            seen[tp].add((q, r))
        out.append(row)
    for tp in shifts:
        for q, r in sorted(roster - seen[tp]):
            counts.setdefault(tp, [0, 0])[1] += 1
            blank = {c: "" for c in columns}
            blank.update(timepoint=tp, nw_q=q, nw_r=r, nw_ring=_ring(q, r, kinds.get(tp)), detected="False",
                         state=UNMEASURED_STATE, consistency="consistent")
            out.append(blank)
    return out, {tp: tuple(counts.get(tp, (0, 0))) for tp in shifts}


#: How often, in rows, relabelling a cell table says how far it has got: a plate's table can hold millions of cells.
PROGRESS_EVERY_ROWS = 250_000


def _relabel_cells_file(path: Path, shifts: dict, roster: set, kinds: dict,
                        progress: Callable[[str], None] | None = None) -> tuple[dict, int, Path]:
    """Rewrite one cell table with each shifted timepoint's nanowell addresses renamed, streaming row by row (a
    plate's table can hold millions of cells, so it is never held whole). A cell whose nanowell left the roster keeps
    its row with the address blank. Returns per timepoint how many were unplaced, the rows read, and the rewrite
    (``<name>.part``), which the caller puts in the table's place."""
    unplaced: dict = {}
    tmp = _part(path)
    gz = path.suffix == ".gz"
    source = gzip.open(path, "rt", newline="", encoding="utf-8") if gz else path.open(newline="", encoding="utf-8")
    target = gzip.open(tmp, "wt", newline="", encoding="utf-8") if gz else tmp.open("w", newline="", encoding="utf-8")
    n = 0
    with source as fh_in, target as fh_out:
        reader = csv.DictReader(fh_in)
        writer = csv.DictWriter(fh_out, fieldnames=list(reader.fieldnames or []), extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            n += 1
            tp = row.get("timepoint")
            address = _address(row) if tp in shifts else None
            if address is not None:
                dq, dr = shifts[tp]
                q, r = address[0] + dq, address[1] + dr
                if (q, r) in roster:
                    row.update(nw_q=q, nw_r=r, nw_ring=_ring(q, r, kinds.get(tp)), nw_label=f"{q}_{r}")
                else:
                    row.update({c: "" for c in _ADDRESS_COLUMNS})
                    unplaced[tp] = unplaced.get(tp, 0) + 1
            writer.writerow(row)
            if progress is not None and n % PROGRESS_EVERY_ROWS == 0:
                progress(f"    {path.name}: {n:,} cells so far…")
    return unplaced, n, tmp


def _moved_origin(block: dict, rows, tp: str, shift) -> list | None:
    """The timepoint's origin in stage coordinates once its addresses move by *shift*: the old origin plus the
    measured offset between the nanowells that were ``(0, 0)`` and ``(-dq, -dr)`` (the new origin) at this
    timepoint. Both centres are in one frame, so the stage offset cancels. ``None`` when either is missing."""
    old = block.get("origin_stage_um")
    centres = {}
    for row in rows:
        if row.get("timepoint") == tp and _address(row) in ((0, 0), (-shift[0], -shift[1])):
            try:
                centres[_address(row)] = (float(row["well_x_um"]), float(row["well_y_um"]))
            except (KeyError, TypeError, ValueError):
                continue
    if not old or len(centres) < 2:
        return None
    new, zero = centres[(-shift[0], -shift[1])], centres[(0, 0)]
    return [float(old[0]) + new[0] - zero[0], float(old[1]) + new[1] - zero[1]]


def relabel_well(out_dir, plan: WellPlan, csv_prefixes, backup_root: Path,
                 progress: Callable[[str], None] | None = None) -> str:
    """Apply *plan* to one well's grid record, sites table and cell tables; return one line saying what changed.
    The originals are copied under *backup_root* first. *progress*, when given, is told each step as it ends.

    All or nothing: every table is rewritten beside itself (``<name>.part``) and the rewrites replace the tables
    only once all of them are written, so a failure part way (a full disk is the likely one) leaves the well as it
    was. The plan is made from the sites table and the grid record, so a well whose cells had been moved and whose
    sites had not would be planned again unchanged, and Apply again would move its cells a second lattice step."""
    say = progress or (lambda _text: None)
    out_dir = Path(out_dir)
    folder = out_dir / NANOWELL_DIRNAME
    grid_path, sites_path = folder / f"{plan.well}_grid.json", folder / f"{plan.well}.csv"
    cells_paths = _cells_tables(out_dir, plan.well, csv_prefixes)
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in [grid_path, sites_path, *cells_paths]:
        shutil.copy2(path, backup_root / path.name)
    say(f"    backed up {2 + len(cells_paths)} file(s) to {backup_root}")

    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    columns, rows = _read_rows(sites_path)
    blocks = grid["timepoints"]
    kinds = {tp: blocks[tp].get("lattice_kind") for tp in blocks if isinstance(blocks[tp], dict)}
    dq0, dr0 = plan.roster_offset
    roster = {(int(a[0]) - dq0, int(a[1]) - dr0) for a in grid["array_radius"]["roster"]}
    origins = {tp: _moved_origin(blocks[tp], rows, tp, shift) for tp, shift in plan.shifts.items()}
    rows, counts = _relabel_sites(rows, plan.moves, roster, kinds, columns)
    targets = [*cells_paths, sites_path, grid_path]
    try:
        _rewrite_well(plan, grid, grid_path, sites_path, cells_paths, columns, rows, counts, roster, kinds, origins,
                      progress, say)
    except BaseException:
        for path in targets:                   # nothing has replaced a table yet: the well is as it was
            _part(path).unlink(missing_ok=True)
        raise
    for path in targets:                       # every rewrite is on disk; renames on one volume need no space
        _part(path).replace(path)
    moved = ", ".join(f"{tp} by {shift}" for tp, shift in plan.shifts.items())
    return f"{plan.well}: relabelled {moved} onto {plan.reference}'s nanowells"


def _rewrite_well(plan: WellPlan, grid: dict, grid_path: Path, sites_path: Path, cells_paths, columns, rows, counts,
                  roster: set, kinds: dict, origins: dict, progress, say) -> None:
    """:func:`relabel_well`'s rewrites, each written beside its table as ``<name>.part`` and none put in place."""
    from util.nanowell_anchors import origin_phrase

    blocks = grid["timepoints"]
    unplaced: dict = {}
    for path in cells_paths:
        these, n_cells, _part_path = _relabel_cells_file(path, plan.moves, roster, kinds, progress)
        for tp, n in these.items():
            unplaced[tp] = unplaced.get(tp, 0) + n
        say(f"    {path.name}: {n_cells:,} cell(s) read, {sum(these.values()):,} left without a nanowell address")
    _write_rows(sites_path, columns, rows)
    say(f"    {sites_path.name}: {len(rows):,} site row(s) written — "
        + "; ".join(f"{tp}: {d} beyond the rim dropped, {u} never measured" for tp, (d, u) in counts.items()))

    stamp = datetime.now().isoformat(timespec="seconds")
    for tp in plan.moves:                      # only what the repair explains: plan.unexplained stays flagged
        if blocks[tp].get("consistency") == "roster_mismatch":
            blocks[tp].update(consistency="consistent", consistency_detail="")
    if plan.roster_offset != (0, 0):
        grid["array_radius"]["roster"] = [list(a) for a in sorted(roster)]
    for tp, shift in plan.moves.items():
        block = blocks[tp]
        dropped, unmeasured = counts[tp]
        block.update(roster_size=len(roster),
                     relabelled={"by": RELABELLED_BY, "at": stamp, "dropped_rows": dropped,
                                 "unmeasured_sites": unmeasured, "cells_unplaced": unplaced.get(tp, 0)})
        tail = (f"{dropped} row(s) measured beyond the array's rim were dropped, {unmeasured} roster nanowell(s) it "
                f"never measured are written {UNMEASURED_STATE} with no values, and {unplaced.get(tp, 0)} cell(s) "
                "outside the roster keep no nanowell address")
        if shift == (0, 0):
            block.setdefault("notes", []).append(
                f"Its addresses were right, but the well's roster had been decided in another timepoint's frame, a "
                f"lattice step off; {RELABELLED_BY} moved the roster onto this timepoint's on {stamp}. {tail}")
            continue
        block.update(origin_shift=[int(shift[0]), int(shift[1])], origin_registered_to=plan.reference,
                     origin_stage_um=origins[tp])
        block.setdefault("notes", []).append(
            f"Addresses relabelled by ({int(shift[0])}, {int(shift[1])}) lattice steps onto {plan.reference}'s by "
            f"{RELABELLED_BY} on {stamp}: its own origin ({origin_phrase(block.get('origin_method'))}) had landed "
            f"a site off. {tail}")
    _part(grid_path).write_text(json.dumps(grid, indent=2), encoding="utf-8")


def relabel_dataset(out_dir, plans: dict, csv_prefixes, progress: Callable[[str], None] | None = None) -> list[str]:
    """Relabel every repairable well of *plans*; one line per well. The originals go under one backup folder.
    *progress*, when given, is told each well and each table as it is done, for the Validate log."""
    repairable = [p for p in plans.values() if p.repairable]
    if not repairable:
        return []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(out_dir) / NANOWELL_DIRNAME / BACKUP_DIRNAME / stamp
    done = []
    for k, plan in enumerate(repairable, start=1):
        if progress is not None:
            moved = ", ".join(f"{tp} by {step}" for tp, step in plan.shifts.items())
            progress(f"  Relabelling {plan.well} ({k}/{len(repairable)}): {moved} onto {plan.reference}'s nanowells")
        done.append(relabel_well(out_dir, plan, csv_prefixes, backup, progress))
    return done
