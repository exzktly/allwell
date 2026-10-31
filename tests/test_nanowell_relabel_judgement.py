"""Validate's nanowell relabel clears only the flags it explains, and judges only the timepoints the run flagged.

The tables are built here rather than rendered, in the shape the run writes them: one row per roster site at every
timepoint, and a site where no nanowell was detected is ``occluded`` with none of its disk visible — whether a
bubble hid it or the array does not reach it. That is what the rendered run (``tests.test_nanowell_relabel``'s
``_old_tables``) wrote for the crescent of a timepoint anchored a step off, measured before these tests were
written, and it is why the crescent alone cannot tell a step from a bubble or glare over one side of the rim.
"""
from __future__ import annotations

import csv
import json

from datasets.core.nanowell_rim import step_against_roster
from pipeline.nanowell_run import hex_ring
from pipeline.nanowell_task import SITE_COLUMNS, _has_a_step_off_timepoint

#: A 127-site hexagonal array; its +q rim holds 7 sites.
ROSTER = sorted((q, r) for q in range(-7, 8) for r in range(-7, 8) if hex_ring(q, r) <= 6)
R = set(ROSTER)


def _rows(tp, *, step=(0, 0), hidden=(), consistency="consistent") -> list[dict]:
    """*tp*'s sites table rows. *step* is how far its origin sat off: the array then lies at ``roster - step`` in
    its frame, so a nanowell is at address ``a`` only when ``a + step`` is in the roster. *hidden* are sites a
    bubble or glare covered."""
    rows = []
    for q, r in ROSTER:
        there = (q + step[0], r + step[1]) in R and (q, r) not in hidden
        row = dict.fromkeys(SITE_COLUMNS, "")
        row.update(timepoint=tp, nw_q=q, nw_r=r, nw_ring=hex_ring(q, r), well_x_um=f"{q * 100.0 + r * 50.0:.1f}",
                   well_y_um=f"{r * 86.6:.1f}", radius_um="20.0", geometry="circle", detected=str(there),
                   fused="False", imaged_fraction="1.0", visible_fraction="1.0" if there else "0.0",
                   state="measured" if there else "occluded", n_cells="2" if there else "", consistency=consistency)
        rows.append(row)
    return rows


def _dataset(tmp_path, timepoints: dict) -> object:
    """``out`` holding one circular well A01; *timepoints* maps each to ``(origin_method, consistency, rows)``."""
    out = tmp_path / "out"
    (out / "nanowells").mkdir(parents=True)
    rows = [row for _method, _consistency, these in timepoints.values() for row in these]
    with (out / "nanowells" / "A01.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(SITE_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    blocks = {tp: {"status": "ok", "origin_method": method, "consistency": consistency,
                   "consistency_detail": "27 addresses differ from the reference roster"
                   if consistency == "roster_mismatch" else "",
                   "lattice_kind": "hex", "origin_stage_um": [0.0, 0.0]}
              for tp, (method, consistency, _rows_) in timepoints.items()}
    grid = {"array_radius": {"radius_um": 650.0, "roster": [list(a) for a in ROSTER], "roster_rule": "evidence"},
            "timepoints": blocks}
    (out / "nanowells" / "A01_grid.json").write_text(json.dumps(grid))
    with (out / "m_A01.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["well", "timepoint", "nw_q", "nw_r", "nw_ring", "nw_label"])
        writer.writerows([["A01", tp, 0, 0, 0, "0_0"] for tp in timepoints])
    return out


def _consistency(out) -> tuple[dict, dict]:
    grid = json.loads((out / "nanowells" / "A01_grid.json").read_text())
    rows = list(csv.DictReader((out / "nanowells" / "A01.csv").open()))
    by_tp: dict = {}
    for row in rows:
        by_tp.setdefault(row["timepoint"], set()).add(row["consistency"])
    return {tp: b["consistency"] for tp, b in grid["timepoints"].items()}, by_tp


def test_apply_keeps_the_flag_of_a_timepoint_it_neither_moved_nor_explained(tmp_path):
    """Tracker M12. 48h is a step off and is relabelled; 24h is flagged too, but only two rim sites differ (a bubble,
    no step explains it). Apply cleared every flagged block and row of the well, so Review's warning for 24h went away
    and the next run no longer re-analysed it. 24h must stay flagged, and Validate must say so."""
    from datasets.validate import nanowell_relabel as rl

    out = _dataset(tmp_path, {
        "0h": ("fiducial", "consistent", _rows("0h")),
        "24h": ("fiducial", "roster_mismatch", _rows("24h", hidden={(6, 0), (6, -1)}, consistency="roster_mismatch")),
        "48h": ("array_centre", "roster_mismatch", _rows("48h", step=(1, 0), consistency="roster_mismatch"))})
    plan = rl.plan_dataset(out)["A01"]
    assert plan.shifts == {"48h": (1, 0)}
    assert rl.relabel_dataset(out, {"A01": plan}, ["m"]) == ["A01: relabelled 48h by (1, 0) onto 0h's nanowells"]

    blocks, rows = _consistency(out)
    assert blocks == {"0h": "consistent", "24h": "roster_mismatch", "48h": "consistent"}
    assert rows == {"0h": {"consistent"}, "24h": {"roster_mismatch"}, "48h": {"consistent"}}
    assert _has_a_step_off_timepoint(json.loads((out / "nanowells" / "A01_grid.json").read_text()))

    assert plan.unexplained == ("24h",)
    severity, code, message = rl.finding_of(plan)
    assert code == "nanowell_misaddressed"
    assert message.endswith("Timepoint(s) 24h are also flagged roster_mismatch and no lattice step explains it: the "
                            "repair leaves them flagged, and a re-run of the well is their repair")
    again = rl.plan_dataset(out)["A01"]
    assert (again.verdict, again.moves, again.unexplained) == ("unexplained", {}, ("24h",))


def test_a_repaired_well_with_nothing_else_flagged_says_nothing_more(tmp_path):
    """The control for the message: no other flag, no extra sentence, and the replan finds nothing."""
    from datasets.validate import nanowell_relabel as rl

    out = _dataset(tmp_path, {
        "0h": ("fiducial", "consistent", _rows("0h")),
        "48h": ("array_centre", "roster_mismatch", _rows("48h", step=(1, 0), consistency="roster_mismatch"))})
    plan = rl.plan_dataset(out)["A01"]
    assert (plan.shifts, plan.unexplained) == ({"48h": (1, 0)}, ())
    assert rl.finding_of(plan)[2].endswith("Apply repairs relabels them onto its addresses (the originals are backed up)")
    rl.relabel_dataset(out, {"A01": plan}, ["m"])
    assert _consistency(out)[0] == {"0h": "consistent", "48h": "consistent"}
    assert rl.plan_dataset(out) == {}


#: The +q side of the rim: the sites a step of (1, 0) leaves without a nanowell, and the ones glare over that side of
#: the well hides.
PLUS_Q_RIM = {a for a in ROSTER if (a[0] + 1, a[1]) not in R}


def test_glare_over_one_side_of_the_rim_at_a_timepoint_the_run_left_consistent_is_not_a_step(tmp_path):
    """Tracker M13. 48h's addresses are right and its run left it consistent, but glare hid the whole +q side of its
    rim: the same crescent a step of (1, 0) leaves, written the same way. Validate read it as a step and renamed every
    nanowell of 48h. Nothing is flagged, so nothing is moved; what the rim reads is reported, as information."""
    from datasets.validate import nanowell_relabel as rl

    out = _dataset(tmp_path, {
        "0h": ("fiducial", "consistent", _rows("0h")),
        "24h": ("fiducial", "consistent", _rows("24h")),
        "48h": ("array_centre", "consistent", _rows("48h", hidden=PLUS_Q_RIM))})
    assert step_against_roster(_rows("48h", hidden=PLUS_Q_RIM), R) == (1, 0)          # the rim alone says a step
    before = {p.name: p.read_bytes() for p in (out / "nanowells").iterdir()}
    plans = rl.plan_dataset(out)
    assert {w: (p.verdict, p.moves, p.rim_only, p.repairable) for w, p in plans.items()} == {
        "A01": (rl.RIM_ONLY, {}, {"48h": (1, 0)}, False)}
    assert rl.finding_of(plans["A01"]) == (
        "info", "nanowell_rim_unconfirmed",
        "A01: the rim of timepoint(s) 48h (as (1, 0) would) is empty on one side as a lattice step would leave it, "
        "but the run found their nanowells in the other timepoints' frame (they are not flagged roster_mismatch), so "
        "they are left as they are: a bubble or glare over that side of the rim reads the same way in the tables. If "
        "the Array view shows their addresses a nanowell off, re-run the well")
    assert rl.relabel_dataset(out, plans, ["m"]) == []
    assert {p.name: p.read_bytes() for p in (out / "nanowells").iterdir()} == before


def test_a_step_off_timepoint_is_relabelled_alone_beside_a_glared_one_the_run_left_consistent(tmp_path):
    """48h is a step off and flagged; 24h, square-anchored and consistent, has glare over the same side of its rim.
    Read from the rim alone, 24h joined 48h's frame, the two outvoted 0h, and 0h was the one renamed."""
    from datasets.validate import nanowell_relabel as rl

    out = _dataset(tmp_path, {
        "0h": ("fiducial", "consistent", _rows("0h")),
        "24h": ("fiducial", "consistent", _rows("24h", hidden=PLUS_Q_RIM)),
        "48h": ("array_centre", "roster_mismatch", _rows("48h", step=(1, 0), consistency="roster_mismatch"))})
    plan = rl.plan_dataset(out)["A01"]
    assert (plan.reference, plan.shifts, plan.roster_offset, plan.unexplained) == ("0h", {"48h": (1, 0)}, (0, 0), ())
    assert plan.rim_only == {"24h": (1, 0)}
    assert ". Also, the rim of timepoint(s) 24h (as (1, 0) would) is empty on one side" in rl.finding_of(plan)[2]
    rl.relabel_dataset(out, {"A01": plan}, ["m"])
    rows = list(csv.DictReader((out / "nanowells" / "A01.csv").open()))
    assert {(r["nw_q"], r["nw_r"]) for r in rows if r["timepoint"] == "24h" and r["detected"] == "False"} == {
        (str(q), str(r)) for q, r in PLUS_Q_RIM}                                    # 24h's addresses untouched
