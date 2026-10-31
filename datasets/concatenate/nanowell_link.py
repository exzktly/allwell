"""Concatenate two analysed nanowell datasets: join dataset 2's timepoints onto dataset 1's nanowells.

Every timepoint of a nanowell analysis is anchored on its own, on the array's central square (or a fiducial, an
anchor set by hand, or the rest of the plate), so an address ``(q, r)`` names the same physical nanowell in two
analyses of one plate. What two analyses do *not* share is what one run fixes across all of a well's timepoints:
the roster (which sites the well has), the check that no timepoint is anchored a lattice step off the others, and
the earliest empty-nanowell template a sparse timepoint falls back to. The supported analysed + raw route gets all
three from an incremental Analyze run onto dataset 1's tables. This module decides, well by well, where dataset 2's
own rows already agree with that run and can be joined as they are, and sets aside the rest for that run:

- **A well whose two rosters differ** has every dataset 2 timepoint set aside: dataset 2's rows cover other sites
  than dataset 1's, and an incremental run decides from the evidence of both what the roster is (re-analysing the
  whole well when the new evidence changes it).
- **A dataset 2 timepoint that may sit a lattice step from dataset 1's nanowells** is set aside: its rows may name
  each nanowell's neighbour. It may when dataset 2's own run flagged it ``roster_mismatch``, when its detected
  addresses sit a step from dataset 1's (:func:`pipeline.nanowell_run.misanchored`, as an incremental run judges a
  new timepoint), or when one side of its rim is empty as a step leaves it (``datasets.core.nanowell_rim``), the one
  way a step shows in tables that have rows only at the roster's addresses (:func:`_stepped_off`).
- **A dataset 2 timepoint measured on dataset 2's earliest template** (too few empty nanowells for its own) is set
  aside: one run would have fallen back to dataset 1's earliest template instead.

A set-aside timepoint has no grid block, no site rows, no cell rows and no montage in the destination, so it reads as
not yet analysed everywhere (Validate's ``nanowell_timepoint_missing``, Home's completeness) until an incremental
Analyze measures it onto dataset 1's nanowells. A well only dataset 2 analysed is carried whole, renamed.

What joining does not equalise, and the summary says so: each half keeps the nanowell diameter its own run settled
on (a median over that run's timepoints) and the radius it fitted. Qt-free.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from datasets.core.nanowell_tables import TEMPLATE_SUFFIX, carry_nanowell_tables, nanowell_dir, sites_table
from domain.nanowell_roster import roster_rule
from util.timepoint import parse_hours
from util.well_token import canonical_well_label

#: The end of a nanowell montage's name in ``<well>_out.zip``: ``<well>_<timepoint>_montage.png``, as
#: ``pipeline.nanowell_task._write_montages`` writes it.
MONTAGE_SUFFIX = "_montage.png"


@dataclass(frozen=True)
class NanowellLink:
    """What :func:`plan_link` decided, by destination timepoint label (dataset 2's already renamed)."""

    #: Wells both datasets analysed, dataset 2's timepoints joined onto dataset 1's tables (any not set aside).
    joined: tuple[str, ...] = ()
    #: Wells only dataset 2 analysed, carried whole with their timepoints renamed.
    ds2_only: tuple[str, ...] = ()
    #: ``well -> {timepoint: why}``: dataset 2's timepoints left for an incremental Analyze to measure.
    set_aside: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def sets_aside(self, well: str, timepoint: str) -> bool:
        """Whether dataset 2's *timepoint* (renamed) of *well* is left out of the destination for Analyze."""
        return timepoint in self.set_aside.get(canonical_well_label(well) or well, {})

    def summary(self) -> list[str]:
        """The lines the run log reports, one per decision that left something for Analyze."""
        lines = []
        if self.joined:
            lines.append(f"Joined dataset 2's nanowell results onto dataset 1's nanowells in {len(self.joined)} "
                         "well(s), by address; each half keeps the nanowell diameter and array radius its own "
                         "analysis measured.")
        if self.ds2_only:
            lines.append(f"Carried dataset 2's nanowell results for {len(self.ds2_only)} well(s) only it analysed.")
        for well, timepoints in sorted(self.set_aside.items()):
            reasons = sorted(set(timepoints.values()))
            lines.append(f"{well}: dataset 2's {', '.join(_by_time(timepoints))} left for an incremental Analyze "
                         f"(Fill in what's missing) to measure onto dataset 1's nanowells — " + "; ".join(reasons))
        return lines


def _by_time(timepoints) -> list[str]:
    def key(tp):
        hours = parse_hours(tp)
        return (0, hours, tp) if hours is not None else (1, 0.0, tp)

    return sorted(timepoints, key=key)


def _grids(out_dir) -> dict[str, Path]:
    """``canonical well -> grid record path`` of a dataset's nanowell tables."""
    folder = nanowell_dir(out_dir)
    found = {}
    for path in sorted(folder.glob("*_grid.json")) if folder.is_dir() else ():
        stem = path.name[: -len("_grid.json")]
        found[canonical_well_label(stem) or stem] = path
    return found


def _read_table(grid_path: Path) -> tuple[dict, list[dict], list[str]]:
    """``(grid, site rows, header)`` of one well; callers have refused unusable tables already."""
    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    table = sites_table(grid_path.parent, grid_path.name[: -len("_grid.json")])
    if table is None:
        raise ValueError(f"{grid_path.name} has no sites table beside it")
    with table.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return grid, list(reader), list(reader.fieldnames or [])


def _address(row: dict) -> tuple[int, int] | None:
    q, r = str(row.get("nw_q") or "").strip(), str(row.get("nw_r") or "").strip()
    return (int(float(q)), int(float(r))) if q and r else None


def _ok(grid: dict) -> set[str]:
    from pipeline.nanowell_run import OK

    return {tp for tp, block in (grid.get("timepoints") or {}).items() if block.get("status") == OK}


def roster_of(grid: dict, rows: list[dict]) -> frozenset:
    """The well's roster: the set its run recorded, else (a well analysed under the disk rule, which records a
    radius and no set) the addresses its addressed timepoints have rows for — the same sites at each."""
    recorded = (grid.get("array_radius") or {}).get("roster")
    if recorded is not None:
        return frozenset(tuple(map(int, a)) for a in recorded)
    ok = _ok(grid)
    return frozenset(a for a in (_address(r) for r in rows if r.get("timepoint") in ok) if a is not None)


def detected_by_timepoint(grid: dict, rows: list[dict]) -> dict[str, set]:
    """``timepoint -> addresses where a nanowell was detected`` over the addressed timepoints. A timepoint its run
    already flagged a roster mismatch is skipped, as ``nanowell_task._kept_evidence`` skips it: its addresses may
    name its neighbours' sites."""
    ok = _ok(grid)
    found: dict[str, set] = {tp: set() for tp in ok}
    for row in rows:
        tp = row.get("timepoint")
        if tp not in ok or row.get("consistency") == "roster_mismatch":
            continue
        address = _address(row)
        if address is not None and str(row.get("detected")).strip().lower() == "true":
            found[tp].add(address)
    return {tp: sites for tp, sites in found.items() if sites}


def _roster_reason(rule1, rule2, roster1: frozenset, roster2: frozenset) -> str | None:
    if rule1 != rule2:
        return (f"the two analyses decided the well's nanowells by different rules ({rule1 or 'none'} and "
                f"{rule2 or 'none'})")
    if roster1 != roster2:
        return (f"the two analyses found different nanowells in the well ({len(roster1 - roster2)} only in dataset "
                f"1's, {len(roster2 - roster1)} only in dataset 2's)")
    return None


def flagged_timepoints(grid: dict, rows: list[dict]) -> set[str]:
    """The addressed timepoints the well's own run flagged ``roster_mismatch``, in its grid record or its rows (the
    run writes both)."""
    ok = _ok(grid)
    flagged = {tp for tp, block in (grid.get("timepoints") or {}).items()
               if isinstance(block, dict) and block.get("consistency") == "roster_mismatch"}
    flagged |= {row.get("timepoint") for row in rows if row.get("consistency") == "roster_mismatch"}
    return flagged & ok


def _stepped_off(grid1: dict, rows1: list[dict], grid2: dict, rows2: list[dict]) -> dict[str, str]:
    """``dataset 2 timepoint -> why`` for each one that may sit a lattice step off dataset 1's nanowells, on a well
    whose two rosters agree. Three ways to know, each covering what the others cannot see:

    - dataset 2's own run flagged it ``roster_mismatch``: that run compared every nanowell it detected, beyond the
      roster too, and found it a step from its other timepoints';
    - its detected addresses sit a step from dataset 1's (:func:`pipeline.nanowell_run.misanchored`, as an
      incremental run judges a new timepoint), which shows where the rows reach beyond the array (a disk-rule well's
      ring of sites past its rim);
    - one side of its rim is empty, as a step leaves it (``datasets.core.nanowell_rim``), which is how a step shows
      in a well whose run kept a roster: every timepoint has rows only at the roster's addresses, so detected
      addresses agree at every step.

    The rim cannot tell a step from a bubble or glare over one side, but a set-aside timepoint is measured again by
    an incremental Analyze, from the images, so a needless one costs that run and nothing else."""
    from datasets.core.nanowell_rim import step_against_roster
    from pipeline.nanowell_run import misanchored

    aside = {tp: ("dataset 2's own analysis flagged it roster_mismatch (its nanowells sat a lattice step from its "
                  "other timepoints'), so its addresses may name neighbouring nanowells")
             for tp in sorted(flagged_timepoints(grid2, rows2))}
    kept = set().union(*detected_by_timepoint(grid1, rows1).values())
    for tp, step in misanchored(detected_by_timepoint(grid2, rows2), kept).items():
        aside.setdefault(tp, f"its nanowells sit {tuple(step)} lattice steps from dataset 1's, so its addresses may "
                             "name neighbouring nanowells")
    if (grid2.get("array_radius") or {}).get("roster") is None:
        return aside
    roster = set(roster_of(grid2, rows2))
    by_tp: dict[str, list[dict]] = {}
    for row in rows2:
        by_tp.setdefault(row.get("timepoint"), []).append(row)
    for tp in sorted(_ok(grid2) - set(aside)):
        step = step_against_roster(by_tp.get(tp, ()), roster)
        if step != (0, 0):
            aside[tp] = (f"one side of its rim is empty, as a lattice step of {step} off dataset 1's nanowells leaves "
                         "it (or a bubble or glare over that side), so its addresses may name neighbouring nanowells")
    return aside


def plan_link(ds1_out, ds2_out, relabel: Callable[[str], str]) -> NanowellLink:
    """Decide which of dataset 2's nanowell timepoints join dataset 1's as they are (module docstring).

    *relabel* renames a dataset 2 timepoint as Concatenate renames its images. Reads only; the tables have been
    checked by ``refuse_unusable_tables`` already.
    """
    from pipeline.nanowell_measure import SOURCE_EARLIEST

    first, second = _grids(ds1_out), _grids(ds2_out)
    joined, ds2_only, set_aside = [], [], {}
    for well, path2 in sorted(second.items()):
        if well not in first:
            ds2_only.append(well)
            continue
        grid1, rows1, _ = _read_table(first[well])
        grid2, rows2, _ = _read_table(path2)
        blocks2 = grid2.get("timepoints") or {}
        reason = _roster_reason(roster_rule(grid1.get("array_radius")), roster_rule(grid2.get("array_radius")),
                                roster_of(grid1, rows1), roster_of(grid2, rows2))
        aside: dict[str, str] = {}
        if reason:
            aside = {relabel(tp): reason for tp in blocks2}
        else:
            aside = {relabel(tp): why for tp, why in _stepped_off(grid1, rows1, grid2, rows2).items()}
            for tp, block in blocks2.items():
                if (block.get("template") or {}).get("source") == SOURCE_EARLIEST and relabel(tp) not in aside:
                    aside[relabel(tp)] = ("it was measured on dataset 2's earliest empty-nanowell template, where one "
                                          "run would use dataset 1's")
        if aside:
            set_aside[well] = aside
        if len(aside) < len(blocks2):
            joined.append(well)
    return NanowellLink(tuple(joined), tuple(ds2_only), set_aside)


def counted_cells(ds1_out, ds2_out, link: NanowellLink | None, relabel: Callable[[str], str]) -> dict[str, int]:
    """``canonical well -> cells`` the nanowell sites tables count at the timepoints a merge keeps: every one of
    dataset 1's, and dataset 2's that *link* does not set aside (none of dataset 2's when *ds2_out* is ``None``, a
    raw dataset 2). A well with no sites table is absent. A blank ``n_cells`` (a site not measured) counts none."""
    counts: dict[str, int] = {}
    sides = [(ds1_out, None)] + ([(ds2_out, link)] if ds2_out is not None else [])
    for out_dir, side_link in sides:
        for well, grid_path in _grids(out_dir).items():
            table = sites_table(grid_path.parent, grid_path.name[: -len("_grid.json")])
            if table is None:
                continue
            with table.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            kept = [r for r in rows if side_link is None or not side_link.sets_aside(well, relabel(r["timepoint"]))]
            counts[well] = counts.get(well, 0) + sum(_cells(r.get("n_cells")) for r in kept)
    return counts


def _cells(value) -> int:
    try:
        return int(float(str(value).strip() or 0))
    except ValueError:
        return 0


def _write_table(folder: Path, well: str, grid: dict, rows: list[dict], header: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    table, record = folder / f"{well}.csv", folder / f"{well}_grid.json"
    tmp = table.with_name(table.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, table)
    tmp = record.with_name(record.name + ".tmp")
    tmp.write_text(json.dumps(grid, indent=1), encoding="utf-8")
    os.replace(tmp, record)


def _carry_ds2_template(src: Path, dest: Path, relabel, kept: set) -> None:
    """Dataset 2's earliest template, renamed, when the destination has none and its timepoint is carried."""
    from pipeline import nanowell_measure as nm

    if dest.is_file() or not src.is_file():
        return
    loaded = nm.load_template(src)
    if loaded is not None and relabel(loaded[1]) in kept:
        nm.save_template(dest, loaded[0], relabel(loaded[1]))


def carry_linked_tables(link: NanowellLink, ds1_out, ds2_out, dest_out, relabel: Callable[[str], str]) -> dict:
    """Write the destination's nanowell tables as *link* decided; ``{well: site rows written}``.

    Dataset 1's tables are carried as they are (``carry_nanowell_tables``, its template with them). Dataset 2's
    are added under their renamed timepoints, less those set aside: onto dataset 1's well where both have one, its
    roster kept and every timepoint's template compared again with the well's earliest; on their own where only
    dataset 2 analysed the well.
    """
    from pipeline.nanowell_run import flag_template_drift

    first = _grids(ds1_out)
    written = carry_nanowell_tables(ds1_out, dest_out, {well: well for well in first})
    dest = nanowell_dir(dest_out)
    for well, path2 in sorted(_grids(ds2_out).items()):
        grid2, rows2, header2 = _read_table(path2)
        blocks = {relabel(tp): block for tp, block in (grid2.get("timepoints") or {}).items()}
        kept = {tp for tp in blocks if not link.sets_aside(well, tp)}
        rows = [dict(row, timepoint=relabel(row["timepoint"])) for row in rows2]
        rows = [row for row in rows if row["timepoint"] in kept]
        if not kept:
            continue
        # Dataset 1's well may have written nothing (a grid record naming no timepoint): dataset 2's then stand alone.
        if (dest / f"{well}_grid.json").is_file():
            grid, rows1, header = _read_table(dest / f"{well}_grid.json")
            merged = {**grid.get("timepoints", {}), **{tp: blocks[tp] for tp in kept}}
            order = _by_time(merged)
            grid = dict(grid, timepoints={tp: merged[tp] for tp in order})
            rank = {tp: k for k, tp in enumerate(order)}
            rows = sorted(rows1 + rows, key=lambda r: rank.get(r.get("timepoint"), len(rank)))
            header = header + [c for c in header2 if c not in header]
        else:
            grid = dict(grid2, well=well, timepoints={tp: blocks[tp] for tp in _by_time(kept)})
            header = header2
        flag_template_drift(grid["timepoints"], list(grid["timepoints"]))
        _write_table(dest, well, grid, rows, header)
        _carry_ds2_template(path2.with_name(path2.name[: -len("_grid.json")] + TEMPLATE_SUFFIX),
                            dest / f"{well}{TEMPLATE_SUFFIX}", relabel, kept)
        written[well] = len(rows)
    return written
