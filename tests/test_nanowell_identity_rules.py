"""The identity check's verdict rules on hand-built tables (``pipeline/nanowell_identity.py``; review tracker rows M3,
M4, M6, L17, L18, L19).

``test_nanowell_identity.py`` renders montages and registers them, which costs seconds per pair; the rules these tests
pin sit after the registration, so here the registration is taken as found (``register_on_square`` and ``refine``
replaced) and each timepoint is the sites and tiles the run would have written for a hexagonal array whose physical
nanowells are known. Each fixture is built so that the wrong rule and the right one give different answers.
"""
from __future__ import annotations

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
