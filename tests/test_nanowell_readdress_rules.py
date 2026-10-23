"""The re-address step's refusals that a rendered plate does not reach, on rows built from an ideal lattice (H3).

A well with 0h addressed and 24h unaddressed: 24h's rows sit on the same lattice in another frame. Addressing 24h
from them is accepted when its evidence leaves the well's rim as it was, and refused when its nanowells add a rim site
the kept roster lacks, or sit a lattice step from 0h's.
"""

from __future__ import annotations

import numpy as np
import pytest

import pipeline.nanowell_readdress as rd
from pipeline import nanowell_run as nr

PITCH, RADIUS = 172.0, 640.0
BASIS = np.column_stack((PITCH * np.array([1.0, 0.0]), PITCH * np.array([0.5, -np.sqrt(3) / 2])))
SHIFT_24H = np.array([3000.0, 2000.0])          # 24h's origin, in its own (common) frame


def _fit():
    from pipeline.nanowell_lattice import LatticeFit

    return LatticeFit(True, "", tuple(BASIS[:, 0]), tuple(BASIS[:, 1]), PITCH, 0.0, "hex", (0.0, 0.0))


def _disk():
    """Every site a nanowell was detected at: all those within the radius. The evidence rule's roster for them."""
    frame = nr.RimFrame.of(_fit(), (0.0, 0.0), RADIUS)
    detected = {a for a in frame.candidates if frame.distance[a] <= RADIUS}
    return frame, frame.supported(detected, set(frame.candidates)), detected


def _row(tp, position, address=None, detected=True):
    q, r = ("", "") if address is None else map(str, address)
    return {"timepoint": tp, "nw_q": q, "nw_r": r, "nw_ring": "", "well_x_um": str(position[0]),
            "well_y_um": str(position[1]), "detected": str(detected), "imaged_fraction": "1.0", "n_cells": "0",
            "consistency": "consistent"}


def _files(tmp_path, sites_24h, centre=(0, 0)):
    """*centre* is the site 24h's outline centre is nearest, the one its rows were laid out from."""
    _, kept, _ = _disk()
    block = {"pitch_um": PITCH, "angle_deg": 0.0, "lattice_kind": "hex", "mirrored": False,
             "basis_um": [list(BASIS[:, 0]), list(BASIS[:, 1])], "stage_offset_um": [0.0, 0.0]}
    grid = {"settings": {}, "array_radius": {"radius_um": RADIUS, "roster": [list(a) for a in sorted(kept)],
                                             "roster_rule": nr.ROSTER_RULE_EVIDENCE},
            "timepoints": {"0h": {**block, "status": nr.OK, "origin_method": "fiducial"},
                           "24h": {**block, "status": nr.UNADDRESSED,
                                   "array_boundary": {"centre_um": list(SHIFT_24H + BASIS @ np.asarray(centre, dtype=float) + 5.0),
                                                      "radius_um": RADIUS}}}}
    rows = [_row("0h", BASIS @ np.asarray(a, dtype=float), a) for a in sorted(kept)]
    rows += [_row("24h", SHIFT_24H + BASIS @ np.asarray(a, dtype=float)) for a in sorted(sites_24h)]
    table = rd._Table(tmp_path / "B02.csv", list(rows[0]), rows)
    return rd.WellFiles("B02", tmp_path / "B02_grid.json", grid, table, None)


def _origin():
    return rd.Origin(rd.MANUAL, tuple(SHIFT_24H + (6.0, -4.0)))


def test_the_same_nanowells_at_24h_are_addressed(tmp_path):
    _, kept, _ = _disk()
    plan = rd.plan_timepoint(_files(tmp_path, kept), "24h", _origin())
    assert plan.roster == kept and sorted(plan.addresses.values()) == sorted(kept)


def test_a_rim_nanowell_only_24h_found_is_refused(tmp_path):
    frame, kept, _ = _disk()
    beyond = min((a for a in frame.candidates if a not in kept and frame.distance[a] <= RADIUS + PITCH),
                 key=lambda a: frame.distance[a])
    with pytest.raises(rd.Refused, match=r"change the well's rim \(1 added, 0 removed\)"):
        rd.plan_timepoint(_files(tmp_path, kept | {beyond}), "24h", _origin())


def test_nanowells_a_step_from_the_other_timepoint_s_are_refused(tmp_path):
    """24h laid out and anchored one site east of where its nanowells match 0h's: every address is a step off."""
    _, kept, _ = _disk()
    files = _files(tmp_path, kept, centre=(1, 0))
    with pytest.raises(rd.Refused, match="a lattice step apart"):
        rd.plan_timepoint(files, "24h", rd.Origin(rd.MANUAL, tuple(SHIFT_24H + BASIS[:, 0])))


def test_a_timepoint_that_is_not_unaddressed_is_not_addressed_again(tmp_path):
    _, kept, _ = _disk()
    with pytest.raises(rd.Refused, match="it is ok, not unaddressed"):
        rd.plan_timepoint(_files(tmp_path, kept), "0h", _origin())
