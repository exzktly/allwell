"""The nanowell identity check (``pipeline/nanowell_identity.py``; NANOWELL_IDENTITY_TRACKER rows A, E).

Two timepoints of one rendered well, the plate moved and turned between them and the cells drawn anew (cells move
between timepoints; nothing here assumes they stay). Each test injects one way the addresses can name the wrong
nanowell — a whole-well step, a turn or reflection by one of the lattice's own symmetries, a mirror, one tile placed
a step off, a decoy square — into the second timepoint's addresses as the run would have written them, and asserts
the check calls it ``contradicted`` and names it. The clean pair must be ``confirmed``; a pair with no square to
register on must be ``inconclusive``, never a pass.

The fixtures carry what the run writes (montage, lattice angle and pitch, accepted square, site positions per
address, owner tiles, tile placements) built from the renderer's ground truth, so every fault is exactly the one
injected. ``test_nanowell_identity_run.py`` drives the same check over the real pipeline's output.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import zipfile

import numpy as np
import pytest

from pipeline import nanowell_identity as ni
from pipeline.tile_register import build_montage
from tests._nanowell_synth import TilePlan, render_tiles

PX = 4.3
PITCH = 172.0
TILE = "F3"


def _timepoint(name, rotation, shift, seed, *, square=True, lattice="hex"):
    plan = TilePlan(2, 2, (700, 700), 0.1, PX)
    radius = 0.26 * (plan.step_um[0] + plan.extent_um[0])
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=100.0, radius_um=radius, rotation_deg=rotation,
                            noise_sigma=0.05, blur_px=1.0, seed=seed, cells=60, debris=20, array_shift_um=shift,
                            square_centre=square, lattice=lattice)
    montage = build_montage(rendered.images, rendered.true_left_top_um, PX)
    centre = tuple(rendered.array.origin)
    t = ni.Timepoint(name, "ok", montage.image.astype(np.float32), montage.um_per_px, tuple(montage.origin_um), PITCH,
                     rotation, lattice, False, centre if square else None, "", centre)
    half = plan.extent_um[0] / 2
    middles = {k: (left + half, top + half) for k, (left, top) in rendered.true_left_top_um.items()}
    for address, point in zip(rendered.array.roster, rendered.sites_um):
        t.sites[address] = tuple(float(v) for v in point)
        t.owners[address] = min(middles, key=lambda k: math.hypot(*np.subtract(middles[k], point)))
    t.tiles = {k: tuple(v) for k, v in rendered.true_left_top_um.items()}
    return t, montage


@pytest.fixture(scope="module")
def pair():
    """The reference, and a second timepoint: plate moved (60, -40) um and turned 2°, cells redrawn."""
    return _timepoint("T0", 7.0, (0.0, 0.0), 1)[0], _timepoint("T1", 9.0, (60.0, -40.0), 2)[0]


@pytest.fixture(scope="module")
def square_pair():
    return (_timepoint("T0", 5.0, (0.0, 0.0), 3, lattice="square")[0],
            _timepoint("T1", 6.5, (-50.0, 30.0), 4, lattice="square")[0])


def _relabel(t, rule):
    out = copy.deepcopy(t)
    out.sites = {rule(a): p for a, p in t.sites.items()}
    out.owners = {rule(a): o for a, o in t.owners.items()}
    return out


def _a1(t):
    a = math.radians(t.angle_deg)
    return np.array([t.pitch * math.cos(a), -t.pitch * math.sin(a)])


# ── E1: the clean pair ───────────────────────────────────────────────────────
def test_a_moved_and_turned_plate_with_moved_cells_is_confirmed(pair):
    ref, other = pair
    result = ni.compare(ref, other)
    assert result["verdict"] == ni.CONFIRMED, result["reason"]
    assert result["compared"] == len(ref.sites) and result["failing_count"] == 0
    assert result["max_pitches"] < 0.05
    assert result["square"]["margin"] >= ni.MIN_MARGIN + 0.1
    assert result["square_agreement_pitches"] < 0.05
    assert result["square"]["rotation_deg"] == pytest.approx(2.0)


def test_a_plate_moved_two_pitches_is_still_found():
    ref = _timepoint("T0", 7.0, (0.0, 0.0), 1)[0]
    moved = _timepoint("T1", 4.0, (300.0, 200.0), 5)[0]
    moved.square_um = None                  # the run did not find the square: the search must reach it unaided
    result = ni.compare(ref, moved)
    assert result["verdict"] == ni.CONFIRMED, result["reason"]


def test_the_square_is_told_from_its_neighbours_only_after_the_lattice_is_cancelled(pair, monkeypatch):
    """Plain correlation of the square peaks almost as high one lattice step away (a round nanowell shares its
    surroundings); the lattice residual is what makes the square unique. Without it the check must not decide."""
    ref, other = pair
    assert ni.register_on_square(ref, other).margin >= 0.25
    monkeypatch.setattr(ni, "lattice_residual", lambda image, *args, **kwargs: np.asarray(image, dtype=np.float32))
    plain = ni.register_on_square(ref, other)
    assert plain.margin < 0.05 and not plain.ok
    assert ni.compare(ref, other)["verdict"] == ni.INCONCLUSIVE


# ── E2 to E6: each way the addresses can be wrong ────────────────────────────
def test_a_whole_well_step_is_contradicted_and_named(pair):
    ref, other = pair
    result = ni.compare(ref, _relabel(other, lambda a: (a[0] + 1, a[1])))
    assert result["verdict"] == ni.CONTRADICTED
    assert result["diagnosis"]["kind"] == "step" and result["diagnosis"]["step"] == [1, 0]
    assert result["failing_count"] == result["compared"]


@pytest.mark.parametrize("rule, name", [
    (lambda a: (-a[1], a[0] + a[1]), "a turn by 60°"),
    (lambda a: (-a[0] - a[1], a[0]), "a turn by 120°"),
    (lambda a: (-a[0], -a[1]), "a turn by 180°"),
    (lambda a: (a[0] + a[1], -a[1]), "a reflection"),
])
def test_a_turn_or_reflection_by_the_hexagonal_lattice_s_symmetry_is_contradicted(pair, rule, name):
    """A circular roster turned by a lattice symmetry is the same set of addresses, and the run's angle check folds
    the turn away: only positions can see it."""
    ref, other = pair
    relabelled = _relabel(other, rule)
    assert set(relabelled.sites) == set(other.sites)            # the roster cannot tell
    result = ni.compare(ref, relabelled)
    assert result["verdict"] == ni.CONTRADICTED
    assert result["diagnosis"]["kind"] == "symmetry" and result["diagnosis"]["symmetry"] == name


def test_a_90_degree_turn_of_a_square_lattice_is_contradicted(square_pair):
    ref, other = square_pair
    assert ni.compare(ref, other)["verdict"] == ni.CONFIRMED
    result = ni.compare(ref, _relabel(other, lambda a: (-a[1], a[0])))
    assert result["verdict"] == ni.CONTRADICTED and result["diagnosis"]["symmetry"] == "a turn by 90°"


def test_a_mirrored_lattice_is_contradicted(pair):
    ref, other = pair
    mirrored = copy.deepcopy(other)
    mirrored.mirrored = True
    result = ni.compare(ref, mirrored)
    assert result["verdict"] == ni.CONTRADICTED and "mirrored" in result["reason"]


def test_one_tile_a_step_off_is_contradicted_and_named(pair):
    """A tile placed a step off moves its sites' positions and addresses together, so every address still lands on
    itself; the tile layout is what shows it."""
    ref, other = pair
    step = _a1(other)
    broken = copy.deepcopy(other)
    broken.sites, broken.owners = {}, {}
    for a, p in other.sites.items():
        moved = other.owners[a] == TILE
        key = (a[0] + 1, a[1]) if moved else a
        if moved or key not in broken.sites:
            broken.sites[key] = tuple(np.add(p, step)) if moved else p
            broken.owners[key] = other.owners[a]
    broken.tiles[TILE] = tuple(np.add(other.tiles[TILE], step))
    result = ni.compare(ref, broken)
    assert result["verdict"] == ni.CONTRADICTED
    assert result["tiles"]["moved"] and result["tiles"]["moved"][0][0] == TILE
    assert result["tiles"]["moved"][0][1] == pytest.approx(PITCH, abs=1.0)


def test_failures_confined_to_one_tile_are_diagnosed_as_that_tile(pair):
    ref, other = pair
    broken = _relabel(other, lambda a: (a[0] + 1, a[1]) if other.owners[a] == TILE else a)
    result = ni.compare(ref, broken)
    assert result["verdict"] == ni.CONTRADICTED
    assert result["diagnosis"] == {"kind": "tile", "detail": result["diagnosis"]["detail"], "tile": TILE}


def test_a_decoy_square_the_run_accepted_is_contradicted(pair):
    ref, other = pair
    decoy = copy.deepcopy(other)
    decoy.square_um = tuple(np.add(other.square_um, _a1(other)))
    result = ni.compare(ref, decoy)
    assert result["verdict"] == ni.CONTRADICTED
    assert result["square_agreement_pitches"] == pytest.approx(1.0, abs=0.05)
    assert "took something else for the square" in result["reason"]


# ── E7: nothing to register on ───────────────────────────────────────────────
def test_a_timepoint_where_the_square_cannot_be_seen_is_inconclusive(pair):
    ref, _ = pair
    hidden = _timepoint("T1", 9.0, (60.0, -40.0), 2, square=False)[0]
    result = ni.compare(ref, hidden)
    assert result["verdict"] == ni.INCONCLUSIVE and "correlates at most" in result["reason"]


def test_a_well_with_no_accepted_square_is_inconclusive_throughout(pair):
    ref, other = pair
    grid, rows, montages = _files_of({"T0": _without_square(ref), "T1": other})
    result = ni.check_well(grid, rows, montages, ["T0", "T1"])
    assert result["reference"] == "T1"                       # the earliest with a square, not the earliest
    grid, rows, montages = _files_of({"T0": _without_square(ref), "T1": _without_square(other)})
    result = ni.check_well(grid, rows, montages, ["T0", "T1"])
    assert result["reference"] is None
    assert {v["verdict"] for v in result["timepoints"].values()} == {ni.INCONCLUSIVE}


def test_an_unaddressed_timepoint_is_inconclusive_not_confirmed(pair):
    ref, other = pair
    other = copy.deepcopy(other)
    other.status = "unaddressed"
    assert ni.compare(ref, other)["verdict"] == ni.INCONCLUSIVE


def _without_square(t):
    out = copy.deepcopy(t)
    out.square_um = None
    return out


def test_the_point_groups_are_the_lattices_symmetries():
    for kind, size in (("hex", 12), ("square", 8)):
        group = ni.point_group(kind)
        assert len(group) == size and group[0][0] == "identity"
        assert len({m.tobytes() for _, m in group}) == size
        assert all(round(abs(np.linalg.det(m))) == 1 for _, m in group)


# ── the files: sidecar, staleness (E8), stage cross-check, the audit command ─
def _block(t: ni.Timepoint) -> dict:
    return {"status": t.status, "lattice_kind": t.kind, "pitch_um": t.pitch, "angle_deg": t.angle_deg,
            "mirrored": t.mirrored, "montage_um_per_px": t.um_per_px, "montage_origin_um": list(t.origin_um),
            "origin_method": "fiducial" if t.square_um else "array_centre",
            "fiducial": {"position_um": list(t.square_um) if t.square_um else None, "best": 0.6, "runner_up": 0.1,
                         "margin": 0.15, "ratio": 3.0, "reason": "" if t.square_um else "not found"},
            "array_boundary": {"centre_um": list(t.centre_um), "radius_um": 500.0},
            "tiles": [{"tile": k, "left_um": v[0], "top_um": v[1]} for k, v in t.tiles.items()]}


def _rows(t: ni.Timepoint) -> list[dict]:
    return [{"timepoint": t.name, "nw_q": str(a[0]), "nw_r": str(a[1]), "well_x_um": str(p[0]), "well_y_um": str(p[1]),
             "owner": t.owners.get(a, "")} for a, p in t.sites.items()]


def _files_of(timepoints: dict):
    grid = {"timepoints": {name: _block(t) for name, t in timepoints.items()}}
    rows = [row for t in timepoints.values() for row in _rows(t)]
    montages = {name: t.image.astype(np.uint8) for name, t in timepoints.items()}
    return grid, rows, montages


def _write_dataset(out, well, timepoints: dict, *, stage_origin=None):
    from skimage.io import imsave

    grid, rows, montages = _files_of(timepoints)
    for name, block in grid["timepoints"].items():
        block["origin_stage_um"] = None if stage_origin is None else list(stage_origin[name])
    folder = out / "nanowells"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{well}_grid.json").write_text(json.dumps(grid))
    with (folder / f"{well}.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with zipfile.ZipFile(out / f"{well}_out.zip", "w") as zf:
        for name, image in montages.items():
            buffer = io.BytesIO()
            imsave(buffer, image, extension=".png", check_contrast=False)
            zf.writestr(f"{well}_{name}_montage.png", buffer.getvalue())


def test_the_sidecar_is_written_read_and_goes_stale_with_its_tables(pair, tmp_path):
    ref, other = pair
    _write_dataset(tmp_path, "A01", {"T0": ref, "T1": _relabel(other, lambda a: (a[0] + 1, a[1]))})
    payload = ni.check_well_dir(tmp_path, "A01")
    assert payload["reference"] == "T0"
    assert ni.verdicts(tmp_path, "A01", ["T0", "T1"]) == {
        "T0": (ni.REFERENCE, "the reference timepoint"), "T1": (ni.CONTRADICTED, payload["timepoints"]["T1"]["reason"])}
    sites = tmp_path / "nanowells" / "A01.csv"
    sites.write_bytes(sites.read_bytes())                     # rewritten, same bytes: still current
    assert ni.verdicts(tmp_path, "A01", ["T1"])["T1"][0] == ni.CONTRADICTED
    sites.write_bytes(sites.read_bytes().replace(b"T1,1,", b"T1,2,", 1))     # one address relabelled
    verdict, reason = ni.verdicts(tmp_path, "A01", ["T1"])["T1"]
    assert verdict == ni.NOT_CHECKED and "changed since" in reason


def test_a_well_never_checked_reads_not_checked(tmp_path):
    assert ni.verdicts(tmp_path, "A01", ["T0"]) == {
        "T0": (ni.NOT_CHECKED, "the identity check has not been run on these tables")}


def test_the_stage_cross_check_flags_the_well_off_the_plate_s_motion(pair, tmp_path):
    """Four wells' address origins in stage coordinates, the plate moved and turned between timepoints; one well's
    origin at T1 is a pitch off the plate's motion. Its good image verdict is overruled."""
    ref, other = pair
    wells = {"A01": (0.0, 0.0), "A02": (9000.0, 0.0), "B01": (0.0, 9000.0), "B02": (9000.0, 9000.0),
             "C01": (0.0, 18000.0), "C02": (9000.0, 18000.0)}
    turn = ni._screen_rotation(0.5)
    for well, origin in wells.items():
        moved = turn @ np.asarray(origin) + (120.0, -80.0)
        if well == "B02":
            moved = moved + _a1(other)
        _write_dataset(tmp_path, well, {"T0": ref, "T1": other}, stage_origin={"T0": origin, "T1": moved})
        ni.check_well_dir(tmp_path, well)
    result = ni.stage_check(tmp_path)
    assert result["reference"] == "T0"
    assert {w: e["T1"]["ok"] for w, e in result["wells"].items()} == {
        "A01": True, "A02": True, "B01": True, "B02": False, "C01": True, "C02": True}
    assert ni.verdicts(tmp_path, "A01", ["T1"])["T1"][0] == ni.CONFIRMED
    verdict, reason = ni.verdicts(tmp_path, "B02", ["T1"])["T1"]
    assert verdict == ni.CONTRADICTED and "rigid motion" in reason


def test_the_stage_cross_check_accuses_no_well_on_too_few_wells(pair, tmp_path):
    """Four wells cannot outvote one: the rigid fit absorbs a pitch-off well and spreads it over the others, which
    then read as off themselves. Nothing is flagged, and the reason says why."""
    ref, other = pair
    wells = {"A01": (0.0, 0.0), "A02": (9000.0, 0.0), "B01": (0.0, 9000.0), "B02": (9000.0, 9000.0)}
    for well, origin in wells.items():
        moved = np.asarray(origin) + (120.0, -80.0) + (_a1(other) if well == "B02" else 0.0)
        _write_dataset(tmp_path, well, {"T0": ref, "T1": other}, stage_origin={"T0": origin, "T1": moved})
    result = ni.stage_check(tmp_path)
    assert result["reference"] is None and f"{ni.STAGE_MIN_WELLS} wells" in result["reason"]
    assert all(not entries for entries in result["wells"].values())


def test_the_audit_command_writes_sidecars_and_exits_by_the_worst_verdict(pair, tmp_path, capsys):
    ref, other = pair
    _write_dataset(tmp_path / "good", "A01", {"T0": ref, "T1": other})
    _write_dataset(tmp_path / "bad", "A01", {"T0": ref, "T1": _relabel(other, lambda a: (-a[0], -a[1]))})
    assert ni.main([str(tmp_path / "good")]) == 0
    assert ni.main([str(tmp_path / "bad")]) == 1
    out = capsys.readouterr().out
    assert "1 contradicted" in out and "a turn by 180°" in out
    assert (tmp_path / "bad" / "nanowells" / "A01_identity.json").is_file()
    assert ni.main([str(tmp_path / "nothing")]) == 2
