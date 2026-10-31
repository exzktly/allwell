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


# ── a timepoint anchored by hand is a reference ──────────────────────────────
def _hand_anchored_block(t: ni.Timepoint, click) -> dict:
    """The grid block the run writes for a timepoint addressed from an anchor set by hand at *click* (well frame),
    the automatic square search having failed: the scores it records are the failure that made the anchor needed."""
    return {"status": "ok", "lattice_kind": t.kind, "pitch_um": t.pitch, "angle_deg": t.angle_deg, "mirrored": False,
            "montage_um_per_px": t.um_per_px, "montage_origin_um": list(t.origin_um), "origin_method": "manual",
            "manual_anchor_um": list(click),
            "fiducial": {"position_um": [3 * P, 0.0], "best": 0.31, "runner_up": 0.29, "margin": 0.15, "ratio": 3.0,
                         "reason": ""},
            "array_boundary": {"centre_um": [0.0, 0.0]}, "tiles": []}


def test_a_well_anchored_by_hand_has_a_reference(registered):
    """A well whose every timepoint was addressed from an anchor set by hand, because the automatic search found no
    central square in any of them. The anchor is the square — the person put it there and the run addressed the
    timepoint from it — so the first timepoint is the reference and the second is checked against it. Read as the
    search's scores alone, no timepoint had a square, and every one was ``inconclusive`` "against no reference",
    with the reason that no addressed timepoint has a central square the run accepted.

    The clicks are a fifth of a pitch off the nanowell the run snapped them to, and the search's best outline three
    pitches away, so the reference square must be the snapped site: neither the click nor the search's position."""
    timepoints = {"T0": _small("T0", SMALL), "T1": _small("T1", SMALL)}
    grid = {"timepoints": {name: _hand_anchored_block(t, (P / 5, 0.0)) for name, t in timepoints.items()}}
    rows = [{"timepoint": t.name, "nw_q": a[0], "nw_r": a[1], "well_x_um": p[0], "well_y_um": p[1], "owner": "F0"}
            for t in timepoints.values() for a, p in t.sites.items()]
    montages = {name: np.zeros((4, 4), np.uint8) for name in timepoints}
    result = ni.check_well(grid, rows, montages, ["T0", "T1"])
    assert (result["reference"], result["reference_reason"]) == ("T0", "")
    assert result["timepoints"]["T0"]["verdict"] == ni.REFERENCE
    assert result["timepoints"]["T0"]["square_um"] == [0.0, 0.0]
    assert result["timepoints"]["T1"]["verdict"] == ni.CONFIRMED


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


# ── L19: a partial timepoint is said, not passed ─────────────────────────────
def _write_well(out, well, timepoints: dict):
    """The tables the run writes for *timepoints* (``name -> Timepoint``), its square accepted at the origin."""
    folder = out / "nanowells"
    folder.mkdir(parents=True, exist_ok=True)
    blocks = {t.name: {"status": "ok", "lattice_kind": t.kind, "pitch_um": t.pitch, "angle_deg": t.angle_deg,
                       "mirrored": False, "montage_um_per_px": t.um_per_px, "montage_origin_um": list(t.origin_um),
                       "origin_method": "fiducial", "fiducial": {"position_um": list(t.square_um)},
                       "array_boundary": {"centre_um": list(t.centre_um)},
                       "tiles": [{"tile": k, "left_um": v[0], "top_um": v[1]} for k, v in t.tiles.items()]}
              for t in timepoints.values()}
    (folder / f"{well}_grid.json").write_text(json.dumps({"well": well, "timepoints": blocks}))
    lines = ["timepoint,nw_q,nw_r,well_x_um,well_y_um,owner"]
    lines += [f"{t.name},{a[0]},{a[1]},{p[0]},{p[1]},{t.owners[a]}" for t in timepoints.values()
              for a, p in t.sites.items()]
    (folder / f"{well}.csv").write_text("\n".join(lines) + "\n")


@pytest.fixture
def partial_well(tmp_path, registered, monkeypatch):
    """A01, whose T1 is ``partial``: the reference's tile F1 a lattice step off (the M3 tables). Montages are read
    as blank images; the registration they would give is the one ``registered`` takes as found."""
    monkeypatch.setattr(ni, "montage_loaders",
                        lambda out_dir, well, names: {n: (lambda: np.zeros((4, 4), np.uint8)) for n in names})
    _write_well(tmp_path, "A01", {"T0": _two_tiles("T0", -P)[0], "T1": _two_tiles("T1", 0.0)[0]})
    return tmp_path


def test_a_partial_timepoint_is_logged_by_the_run(partial_well, caplog):
    """L19: the run logged a warning for every contradicted or inconclusive timepoint and nothing for a partial one,
    though ARCHITECTURE says it logs each timepoint that is not good, and partial is not good."""
    with caplog.at_level("WARNING", logger="allwell.nanowell"):
        payload = ni.check_after_run(partial_well, "A01")
    count = len(payload["timepoints"]["T1"]["excluded"])
    assert payload["timepoints"]["T1"]["verdict"] == ni.PARTIAL and count == 34
    assert [r.getMessage()[:90] for r in caplog.records if r.levelname == "WARNING"] == [
        f"Well A01 timepoint T1: nanowell identity partial against T0 ({count} of 154 nanowells left out (misreg"[:90]]


def test_the_audit_says_a_partial_timepoint_and_exits_3(partial_well, capsys):
    """L19: the audit's summary listed only contradicted and inconclusive timepoints, and its exit status was 0 — the
    one that means every timepoint is good — for a well whose timepoint leaves 34 nanowells out."""
    assert ni.main([str(partial_well)]) == 3
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "A01: 1 reference, 1 partial (reference T0)"
    assert lines[1].startswith("  T1: partial — 34 of 154 nanowells left out (misregistered_tile: tile(s) F1 ")


# ── M4: a sidecar is believed only under the rules that wrote it ─────────────
def test_a_sidecar_from_the_earlier_rules_reads_not_checked_and_says_why(partial_well):
    """M4: the verdict rules changed (an imprecise tile stopped contradicting, partial timepoints arrived, and the
    review's fixes since) while ``IDENTITY_VERSION`` stayed 1, so a sidecar written by the earlier rules, over tables
    that have not changed, was read as current and kept verdicts the rules now reverse. Such a sidecar must read not
    checked, and say that it is the check that changed, not the tables."""
    payload = ni.check_well_dir(partial_well, "A01")
    assert ni.verdicts(partial_well, "A01", ["T1"])["T1"][0] == ni.PARTIAL
    ni.write_sidecar(partial_well, "A01", {**payload, "version": 1})
    assert ni.read_sidecar(partial_well, "A01") is None
    assert ni.verdicts(partial_well, "A01", ["T1"]) == {"T1": (
        ni.NOT_CHECKED, "the identity check that wrote this well's result was an earlier version, whose rules have "
                        "changed since: run it again")}


#: Top-level names in ``pipeline/nanowell_identity.py`` whose code cannot change what a sidecar records: how a
#: sidecar is read back (a reader change reinterprets every sidecar alike), how the run and the audit report, and
#: the command line. Everything else in the module — thresholds, geometry, registration, the verdict rules, the
#: stage fit, what is written — is the rules a sidecar's verdicts were decided by.
NOT_RULES = frozenset({
    "IDENTITY_VERSION", "log", "NOT_GOOD", "FROZEN_SENTINEL", "read_sidecar", "verdicts", "verdict_in", "exclusions",
    "_stale_reason", "check_after_run", "stage_check_after_run", "summary_lines", "_effective", "wells_in",
    "NUMBER_COLUMNS", "_WHY", "number_rows", "audit_argv", "main"})
#: ``IDENTITY_VERSION -> digest of the rules`` (:func:`_rules_digest`). When this test fails, the rules changed: if
#: the change can alter any verdict, reason or exclusion a sidecar records, raise ``IDENTITY_VERSION`` and pin the
#: new digest under the new number, so every sidecar the old rules wrote reads not checked; if it truly cannot (a
#: rename, a refactor shown to change no output), re-pin under the current number and say why in the commit.
RULES = {3: "3762a80e2ba65c67079660b33326062a"}


def _rules_digest() -> str:
    """A digest of the identity module's rules: every top-level statement outside :data:`NOT_RULES`, its source with
    comments, docstrings and whitespace removed (so prose edits do not count and the digest is the same on every
    Python version, which an ``ast.dump`` is not)."""
    import ast
    import hashlib
    import io
    import tokenize

    from tests._repo_scan import REPO_ROOT, parse_source

    path = REPO_ROOT / "pipeline" / "nanowell_identity.py"
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def offset(row, col_bytes):
        return starts[row - 1] + len(lines[row - 1].encode("utf-8")[:col_bytes].decode("utf-8"))

    drop = [(starts[t.start[0] - 1] + t.start[1], starts[t.end[0] - 1] + t.end[1])
            for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
    tree = parse_source(path)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            doc = body[0]
            drop.append((offset(doc.lineno, doc.col_offset), offset(doc.end_lineno, doc.end_col_offset)))

    def named(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {node.name}
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        return {t.id for t in targets if isinstance(t, ast.Name)}

    digest = hashlib.sha256()
    for node in tree.body:
        if named(node) & NOT_RULES or (node is tree.body[0] and isinstance(node, ast.Expr)):
            continue
        start, end = offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset)
        text = list(source[start:end])
        for a, b in drop:
            for k in range(max(a, start), min(b, end)):
                text[k - start] = " "
        digest.update("".join("".join(text).split()).encode("utf-8"))
    return digest.hexdigest()[:32]


def test_the_identity_rules_carry_the_version_they_were_written_under():
    """M4's guard: changing a rule without raising ``IDENTITY_VERSION`` fails here, so a sidecar the old rules wrote
    is never read as the new rules' verdict. See :data:`RULES` for what to do when it fails."""
    assert ni.IDENTITY_VERSION in RULES, f"IDENTITY_VERSION {ni.IDENTITY_VERSION} has no pinned rules digest"
    assert _rules_digest() == RULES[ni.IDENTITY_VERSION], (
        f"the identity rules changed under IDENTITY_VERSION {ni.IDENTITY_VERSION}: digest now {_rules_digest()}")
