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
