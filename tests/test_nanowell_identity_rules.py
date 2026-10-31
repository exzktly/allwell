"""The identity check's verdict rules on hand-built tables (``pipeline/nanowell_identity.py``; review tracker rows M3,
M4, M6, L17, L18, L19).

``test_nanowell_identity.py`` renders montages and registers them, which costs seconds per pair; the rules these tests
pin sit after the registration, so here the registration is taken as found (``register_on_square`` and ``refine``
replaced) and each timepoint is the sites and tiles the run would have written for a hexagonal array whose physical
nanowells are known. Each fixture is built so that the wrong rule and the right one give different answers.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from pipeline import nanowell_identity as ni

P = 50.0
A1, A2 = np.array([P, 0.0]), np.array([P / 2, -P * math.sqrt(3) / 2])
ARRAY = [(q, r) for q in range(-8, 9) for r in range(-8, 9) if max(abs(q), abs(r), abs(q + r)) <= 7]


def _pos(q, r):
    return q * A1 + r * A2


def _blank(name):
    return ni.Timepoint(name, "ok", np.zeros((4, 4), np.uint8), 1.0, (0.0, 0.0), P, 0.0, "hex", False, (0.0, 0.0), "",
                        (0.0, 0.0))


@pytest.fixture
def registered(monkeypatch):
    """The registration taken as found on the square at the origin, unturned and unscaled."""
    monkeypatch.setattr(ni, "register_on_square",
                        lambda ref, other: ni.SquareMatch(True, "", (0.0, 0.0), 0.9, 0.3, 0.6, 0.0, 1.0))
    monkeypatch.setattr(ni, "refine", lambda *a: None)


# ── M3: a tile a lattice step off at the reference ───────────────────────────
BOUNDARY = 180.0


def _owner(x, y, boundary=BOUNDARY):
    return ("F2" if y < -150 else "F0") if x < boundary else "F1"


def _two_tiles(name, f1_shift):
    """Three tiles; F1's placement is *f1_shift* um along ``a1`` off the stage grid. A tile placed off moves its sites'
    positions and their addresses together: the physical nanowell at ``(q, r)`` is seen by the misplaced tile at
    ``pos(q, r) + shift`` and named there. ``physical`` maps each written address to the nanowell it really is."""
    t = _blank(name)
    t.tiles = {"F0": (-400.0, -400.0), "F1": (150.0 + f1_shift, -400.0), "F2": (-400.0, -800.0)}
    physical = {}
    dq = int(round(f1_shift / P))
    for q, r in ARRAY:
        x, y = _pos(q, r)
        owner = _owner(x, y, BOUNDARY + f1_shift / 2)
        if owner == "F1" and dq:
            name_, (x, y) = (q + dq, r), _pos(q + dq, r)
            if _owner(x, y, BOUNDARY + f1_shift / 2) != "F1":
                continue
            t.sites[name_], t.owners[name_], physical[name_] = (float(x), float(y)), "F1", (q, r)
        else:
            t.sites[(q, r)], t.owners[(q, r)], physical[(q, r)] = (float(x), float(y)), owner, (q, r)
    return t, physical


def test_a_reference_tile_a_step_off_leaves_out_every_address_it_names_wrongly(registered):
    """M3: the reference's F1 is placed a pitch to the left, the compared timepoint is right. The layout test sees F1
    moved, but only the compared timepoint's F1 addresses were left out; at F1's edge six addresses are F1's at the
    reference and another tile's at the other timepoint, so their rows name two different physical nanowells and were joined."""
    ref, ref_physical = _two_tiles("T0", -P)
    other, other_physical = _two_tiles("T1", 0.0)
    result = ni.compare(ref, other)
    assert result["verdict"] == ni.PARTIAL
    excluded = {(q, r) for q, r, _why in result["excluded"]}
    joined_wrongly = sorted(a for a in ref.sites if a in other.sites and a not in excluded
                            and ref_physical[a] != other_physical[a])
    assert joined_wrongly == []
    edge = sorted(a for a in ref.sites if ref.owners[a] == "F1" and other.owners.get(a) not in (None, "F1"))
    assert edge == [(1, 5), (2, 3), (3, 1), (4, -1), (5, -3), (6, -5)]
    assert {a: why for q, r, why in result["excluded"] if (a := (q, r)) in edge} == dict.fromkeys(
        edge, "misregistered_tile")


# ── L17: a tie between two references ────────────────────────────────────────
def _step(s):
    return {"verdict": ni.CONTRADICTED, "reason": "step", "diagnosis": {"kind": "step", "step": list(s)}}


def test_a_tie_contradicts_a_timepoint_confirmed_only_against_one_of_the_two(monkeypatch):
    """L17: T0 and T1 each make the other a step off, and as many timepoints agree with each (T0 with T3, T1 with T2),
    so which is right cannot be told. T3 is confirmed against T0 and inconclusive against T1: if T1 is right, T3's
    agreement with T0 puts it a step off too. The tie merged the two sides' verdicts with T1's overriding, read T3 as
    inconclusive there, and left it ``confirmed`` against a reference the check had just declined."""
    order = ["T0", "T1", "T2", "T3"]
    table = {("T0", "T1"): _step((1, 0)), ("T0", "T2"): _step((1, 0)), ("T0", "T3"): {"verdict": ni.CONFIRMED},
             ("T1", "T0"): _step((-1, 0)), ("T1", "T2"): {"verdict": ni.CONFIRMED},
             ("T1", "T3"): {"verdict": ni.INCONCLUSIVE, "reason": "margin"}}
    monkeypatch.setattr(ni, "compare", lambda ref, other: dict(table[(ref.name, other.name)]))
    monkeypatch.setattr(ni, "reference_candidates", lambda timepoints, order: list(order))
    monkeypatch.setattr(ni, "square_names_the_centre", lambda t: True)
    out = ni.check_well({"timepoints": {t: {"status": "ok"} for t in order}}, [], {}, order)
    assert out["reference"] is None
    assert {tp: v["verdict"] for tp, v in out["timepoints"].items()} == dict.fromkeys(order, ni.CONTRADICTED)
    assert "cannot be told" in out["timepoints"]["T3"]["reason"]


# ── L18: too few addresses to accuse ─────────────────────────────────────────
def _small(name, addresses, relabel=lambda a: a):
    t = _blank(name)
    t.centre_um = None
    for q, r in addresses:
        t.sites[relabel((q, r))] = tuple(float(v) for v in _pos(q, r))
        t.owners[relabel((q, r))] = "F0"
    return t


SMALL = [(q, r) for q in range(-5, 6) for r in range(-5, 6) if max(abs(q), abs(r), abs(q + r)) <= 4]


@pytest.mark.parametrize("kept, verdict", [(2, ni.INCONCLUSIVE), (3, ni.CONTRADICTED)])
def test_a_timepoint_is_not_contradicted_on_fewer_addresses_than_the_check_needs(registered, kept, verdict):
    """L18: the compared timepoint holds only *kept* sites, every one a lattice step off. Two give four compared
    addresses, fewer than ``MIN_COMPARED`` (6): the documented verdict is inconclusive, and the step was accused
    first. Three give six, enough to decide, and the step is contradicted as before."""
    result = ni.compare(_small("T0", SMALL), _small("T1", SMALL[:kept], lambda a: (a[0] + 1, a[1])))
    assert (result["compared"], result["verdict"]) == (2 * kept, verdict)
    if verdict == ni.INCONCLUSIVE:
        assert result["reason"] == f"only {2 * kept} address(es) in common with the reference, {ni.MIN_COMPARED} needed"


# ── M6: the audit of one well keeps the plate's stage verdict ────────────────
PITCH_UM = 172.0
SIX_WELLS = {"A01": (0.0, 0.0), "A02": (9000.0, 0.0), "B01": (0.0, 9000.0), "B02": (9000.0, 9000.0),
             "C01": (0.0, 18000.0), "C02": (9000.0, 18000.0)}


def _stage_plate(out, off="B02"):
    """Six wells' grid records with stage origins, the plate moved and turned between T0 and T1 and *off*'s origin a
    pitch off that motion at T1. No montages: every image verdict is inconclusive, so only the stage block can make
    *off* contradicted."""
    folder = out / "nanowells"
    folder.mkdir(parents=True)
    turn = ni._screen_rotation(0.5)
    for well, origin in SIX_WELLS.items():
        moved = turn @ np.asarray(origin) + (120.0, -80.0) + ((PITCH_UM, 0.0) if well == off else 0.0)
        blocks = {tp: {"status": "ok", "pitch_um": PITCH_UM, "origin_method": "fiducial",
                       "origin_stage_um": [float(v) for v in point]}
                  for tp, point in (("T0", origin), ("T1", moved))}
        (folder / f"{well}_grid.json").write_text(json.dumps({"well": well, "timepoints": blocks}))
        (folder / f"{well}.csv").write_text("timepoint,nw_q,nw_r,well_x_um,well_y_um,owner\n")


@pytest.mark.parametrize("named", [["B02"], ["A01"], ["A01", "A02", "B01", "B02", "C01"]])
def test_auditing_named_wells_keeps_the_plate_s_stage_verdict(tmp_path, named, capsys):
    """M6: the stage cross-check is a fit over the plate's wells, and ``--well`` handed it only the named ones. One
    well is fewer than the five the fit needs, so B02's flag was replaced by an empty block and its contradiction
    became the image check's inconclusive; five refitted the plate on a subset. The named wells are still the only
    sidecars written, but the fit is the plate's."""
    _stage_plate(tmp_path)
    assert ni.main([str(tmp_path)]) == 1
    full = {w: ni.read_sidecar(tmp_path, w)["stage"] for w in SIX_WELLS}
    assert ni.verdicts(tmp_path, "B02", ["T1"])["T1"][0] == ni.CONTRADICTED
    untouched = {w: (tmp_path / "nanowells" / f"{w}_identity.json").read_bytes() for w in SIX_WELLS if w not in named}

    code = ni.main([str(tmp_path), *(arg for w in named for arg in ("--well", w))])

    assert {w: ni.read_sidecar(tmp_path, w)["stage"] for w in SIX_WELLS} == full
    verdict, reason = ni.verdicts(tmp_path, "B02", ["T1"])["T1"]
    assert verdict == ni.CONTRADICTED and "rigid motion" in reason
    assert code == (1 if "B02" in named else 2)
    assert {w: (tmp_path / "nanowells" / f"{w}_identity.json").read_bytes() for w in untouched} == untouched
