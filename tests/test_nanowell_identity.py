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


def _timepoint(name, rotation, shift, seed, *, square=True, lattice="hex", noise=0.05, blur=1.0, cells=60):
    plan = TilePlan(2, 2, (700, 700), 0.1, PX)
    radius = 0.26 * (plan.step_um[0] + plan.extent_um[0])
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=100.0, radius_um=radius, rotation_deg=rotation,
                            noise_sigma=noise, blur_px=blur, seed=seed, cells=cells, debris=20, array_shift_um=shift,
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
    assert ni.register_on_square(ref, other).margin >= 0.3
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


def test_one_tile_a_step_off_leaves_out_exactly_that_tile_s_nanowells(pair):
    """A tile placed a step off moves its sites' positions and addresses together, so every address still lands on
    itself; the tile layout is what shows it. Only that tile's nanowells are left out (U5): the registration is
    trusted and the other tiles' nanowells land on themselves. Which of the two timepoints placed the tile wrongly
    cannot be told, so the addresses the tile owns at the reference are left out too (review tracker M3): the plate
    moved against the tile grid, so the reference's tile owns two addresses here that the other timepoint's F1
    owns."""
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
    assert result["verdict"] == ni.PARTIAL
    assert result["tiles"]["moved"] and result["tiles"]["moved"][0][0] == TILE
    assert result["tiles"]["moved"][0][1] == pytest.approx(PITCH, abs=1.0)
    in_tile = {a for a, owner in broken.owners.items() if owner == TILE}
    at_reference = {a for a, owner in ref.owners.items() if owner == TILE and a in broken.sites} - in_tile
    assert sorted(at_reference) == [(6, -1), (7, -1)]
    assert {(q, r) for q, r, why in result["excluded"] if why == "misregistered_tile"} == in_tile | at_reference
    assert len(result["excluded"]) == len(in_tile) + 2 < result["compared"] / 2


def test_wrong_names_confined_to_one_tile_leave_out_those_nanowells_only(pair):
    ref, other = pair
    broken = _relabel(other, lambda a: (a[0] + 1, a[1]) if other.owners[a] == TILE else a)
    result = ni.compare(ref, broken)
    assert result["verdict"] == ni.PARTIAL
    assert result["diagnosis"] == {"kind": "tile", "detail": result["diagnosis"]["detail"], "tile": TILE}
    wrong = {(q, r) for q, r, _d, lq, lr, _t in result["failing"] if (lq, lr) != (q, r)}
    assert wrong and wrong <= {(q, r) for q, r, _why in result["excluded"]}
    assert len(result["excluded"]) < result["compared"] / 2


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
    assert result["verdict"] == ni.INCONCLUSIVE and "leads the next place" in result["reason"]


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
    _write_dataset(tmp_path / "bad", "A01", {"T0": ref, "T1": _relabel(other, lambda a: (a[0] + 1, a[1]))})
    assert ni.main([str(tmp_path / "good")]) == 0
    assert ni.main([str(tmp_path / "bad")]) == 1
    out = capsys.readouterr().out
    assert "1 contradicted" in out and "one lattice step (+1, +0)" in out
    assert (tmp_path / "bad" / "nanowells" / "A01_identity.json").is_file()
    assert ni.main([str(tmp_path / "nothing")]) == 2


# ── the adversarial review's findings (tracker R) ────────────────────────────
def _tiles_stepped(t, tiles):
    """*t* with each tile of *tiles* placed one ``a1`` step off: its sites' positions and addresses move with it."""
    step = _a1(t)
    out = copy.deepcopy(t)
    out.sites, out.owners = {}, {}
    for a, p in t.sites.items():
        moved = t.owners[a] in tiles
        key = (a[0] + 1, a[1]) if moved else a
        if moved or key not in out.sites:
            out.sites[key] = tuple(np.add(p, step)) if moved else p
            out.owners[key] = t.owners[a]
    for tile in tiles:
        out.tiles[tile] = tuple(np.add(t.tiles[tile], step))
    return out


@pytest.mark.parametrize("tiles", [("F0", "F1"), ("F0", "F2"), ("F1", "F3")])
def test_half_the_grid_a_step_off_is_contradicted_not_waved_through(pair, tiles):
    """R1: a median of the tiles' motions falls between two halves, every tile then departs by half a pitch, and
    "most tiles moved" read as "not one grid" — confirmed. Two halves a pitch apart are one grid that disagrees."""
    ref, other = pair
    result = ni.compare(ref, _tiles_stepped(other, tiles))
    assert result["verdict"] == ni.CONTRADICTED, result["reason"]
    assert result["tiles"]["applies"] and not result["tiles"]["ok"]
    assert "which group is right cannot be told" in result["tiles"]["reason"]


def test_one_of_two_tiles_a_step_off_is_contradicted(pair):
    ref, other = pair
    keep = ("F0", "F1")
    ref2, other2 = copy.deepcopy(ref), _tiles_stepped(other, ("F1",))
    ref2.tiles = {k: v for k, v in ref.tiles.items() if k in keep}
    other2.tiles = {k: v for k, v in other2.tiles.items() if k in keep}
    assert ni.tile_layout_test(ref2, other2)["ok"] is False


def test_tiles_from_another_grid_withhold_a_confirmation(pair):
    ref, other = pair
    other = copy.deepcopy(other)
    other.tiles = {k: (v[0] + 2000.0 * i, v[1]) for i, (k, v) in enumerate(other.tiles.items())}
    result = ni.compare(ref, other)
    assert result["verdict"] == ni.INCONCLUSIVE and "not acquired on one tile grid" in result["reason"]


def test_a_rim_centre_that_disagrees_with_the_square_withholds_a_confirmation(pair):
    """R7: the square is the registration's only landmark; a decoy that moved a site and was taken for the square at
    both timepoints is followed. The array's fitted centre, mapped through the registration, must land near the
    other's, or the timepoint is not confirmed."""
    ref, other = pair
    moved = copy.deepcopy(other)
    moved.centre_um = tuple(np.add(other.centre_um, _a1(other)))
    result = ni.compare(ref, moved)
    assert result["verdict"] == ni.INCONCLUSIVE
    assert result["centre_agreement_pitches"] == pytest.approx(1.0, abs=0.05)
    assert ni.compare(ref, other)["centre_agreement_pitches"] < 0.05


def test_a_reference_addressed_off_its_own_square_is_not_the_reference(pair):
    """R4: the earliest timepoint addressed a step off made every correct timepoint contradicted and kept itself."""
    ref, other = pair
    third = _timepoint("T2", 8.0, (-30.0, 20.0), 6)[0]
    wrong = _relabel(ref, lambda a: (a[0] + 1, a[1]))
    grid, rows, montages = _files_of({"T0": wrong, "T1": other, "T2": third})
    result = ni.check_well(grid, rows, montages, ["T0", "T1", "T2"])
    assert result["reference"] == "T1"
    assert result["timepoints"]["T0"]["verdict"] == ni.CONTRADICTED
    assert result["timepoints"]["T2"]["verdict"] == ni.CONFIRMED


def test_the_majority_decides_when_every_square_carries_its_origin(pair):
    """R4, the fallback: a 180° relabel keeps (0, 0) on the square, so the square cannot say which timepoint is
    wrong; the timepoints that agree with one another can."""
    ref, other = pair
    third = _timepoint("T2", 8.0, (-30.0, 20.0), 6)[0]
    turned = _relabel(ref, lambda a: (-a[0], -a[1]))
    grid, rows, montages = _files_of({"T0": turned, "T1": other, "T2": third})
    result = ni.check_well(grid, rows, montages, ["T0", "T1", "T2"])
    assert result["reference"] == "T1" and "confirmed by more" in result["reference_reason"]
    assert [result["timepoints"][t]["verdict"] for t in ("T0", "T2")] == [ni.CONTRADICTED, ni.CONFIRMED]


def test_two_timepoints_that_disagree_alike_are_both_contradicted(pair):
    ref, other = pair
    grid, rows, montages = _files_of({"T0": ref, "T1": _relabel(other, lambda a: (-a[0], -a[1]))})
    result = ni.check_well(grid, rows, montages, ["T0", "T1"])
    assert result["reference"] is None and "cannot be told" in result["reference_reason"]
    assert {v["verdict"] for v in result["timepoints"].values()} == {ni.CONTRADICTED}


def test_an_unaddressed_timepoint_is_never_the_reference(pair):
    ref, other = pair
    unaddressed = copy.deepcopy(ref)
    unaddressed.status = "unaddressed"
    grid, rows, montages = _files_of({"T0": unaddressed, "T1": other})
    assert ni.check_well(grid, rows, montages, ["T0", "T1"])["reference"] == "T1"


def _plate_dataset(tmp_path, ref, other, wells, motion, *, plate=None, off=(), method="fiducial"):
    turn, shift = motion
    for well, origin in wells.items():
        moved = turn @ np.asarray(origin) + shift + (_a1(other) if well in off else 0.0)
        _write_dataset(tmp_path, well, {"T0": ref, "T1": other}, stage_origin={"T0": origin, "T1": moved})
        if plate is not None or method != "fiducial":
            path = tmp_path / "nanowells" / f"{well}_grid.json"
            grid = json.loads(path.read_text())
            if plate is not None:
                grid["plate"] = plate
            for block in grid["timepoints"].values():
                block["origin_method"] = method
            path.write_text(json.dumps(grid))
        ni.check_well_dir(tmp_path, well)


SIX = {"A01": (0.0, 0.0), "A02": (9000.0, 0.0), "B01": (0.0, 9000.0), "B02": (9000.0, 9000.0),
       "C01": (0.0, 18000.0), "C02": (9000.0, 18000.0)}


def test_each_plate_of_a_merged_dataset_is_fitted_on_its_own(pair, tmp_path):
    """R3: two plates of one Merge Plates dataset moved differently; one fit over both flagged all twelve wells."""
    ref, other = pair
    _plate_dataset(tmp_path, ref, other, SIX, (ni._screen_rotation(0.5), np.array([120.0, -80.0])), plate="P1",
                   off=("B02",))
    _plate_dataset(tmp_path, ref, other, {f"{w}x": o for w, o in SIX.items()},
                   (ni._screen_rotation(-0.8), np.array([-300.0, 410.0])), plate="P2")
    result = ni.stage_check(tmp_path)
    flagged = sorted(w for w, e in result["wells"].items() if e["T1"]["ok"] is False)
    assert flagged == ["B02"]


def test_plate_rigid_origins_take_no_part_in_the_stage_check(pair, tmp_path):
    ref, other = pair
    _plate_dataset(tmp_path, ref, other, SIX, (ni._screen_rotation(0.5), np.array([120.0, -80.0])),
                   method="plate_rigid")
    result = ni.stage_check(tmp_path)
    assert result["reference"] is None and "own evidence" in result["reason"]


def test_the_stage_block_survives_a_released_well_s_archive(pair, tmp_path):
    """R2: in a batched remote run the final pass's stage check runs after a released well's montage archive was
    deleted, which failed the digest there and dropped the block without a word."""
    ref, other = pair
    _plate_dataset(tmp_path, ref, other, SIX, (ni._screen_rotation(0.5), np.array([120.0, -80.0])), off=("B02",))
    archive = tmp_path / "B02_out.zip"
    kept = archive.read_bytes()
    archive.unlink()                                    # released on the remote
    ni.stage_check(tmp_path)
    archive.write_bytes(kept)                           # fetched back beside the tables
    verdict, reason = ni.verdicts(tmp_path, "B02", ["T1"])["T1"]
    assert verdict == ni.CONTRADICTED and "rigid motion" in reason


def test_a_well_s_memory_does_not_grow_with_its_timepoints(pair):
    """R8: every montage held at once (as float32) was about a gigabyte for fifteen timepoints per worker. Checked
    from loaders, the peak a well's check allocates is the same for two timepoints as for eight: each compared
    montage is dropped once compared. (The peak itself is the correlation's transient arrays over the search box.)"""
    import tracemalloc

    ref, other = pair

    def peak_for(count):
        timepoints = {"T0": ref}
        for k in range(1, count):
            timepoints[f"T{k}"] = copy.deepcopy(other)
            timepoints[f"T{k}"].name = f"T{k}"
        grid, rows, _ = _files_of(timepoints)
        pixels = {name: t.image.astype(np.uint8) for name, t in timepoints.items()}
        montages = {name: (lambda name=name: pixels[name].copy()) for name in timepoints}
        tracemalloc.start()
        try:
            result = ni.check_well(grid, rows, montages, sorted(timepoints))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert {v["verdict"] for n, v in result["timepoints"].items() if n != "T0"} == {ni.CONFIRMED}
        return peak

    one = other.image.size                                # one uint8 montage, bytes
    peak_for(2)                                           # first imports (scikit-image, scipy) are not the check's
    grown = peak_for(8) - peak_for(2)
    assert grown < 1.5 * one, f"the peak grew by {grown / one:.1f} montages over six more timepoints"



# ── the stress test's findings (tracker S) ───────────────────────────────────
def test_a_noisy_blurred_crowded_pair_is_still_confirmed(pair):
    """S3: camera noise 0.3, a 2.5-pixel blur and 600 cells left the square under the old peak threshold; the
    band-passed residual finds it."""
    ref, _ = pair
    rough = _timepoint("T1", 9.0, (60.0, -40.0), 7, noise=0.3, blur=2.5, cells=600)[0]
    result = ni.compare(ref, rough)
    assert result["verdict"] == ni.CONFIRMED, result["reason"]
    assert result["square"]["margin"] >= ni.MIN_MARGIN


def test_a_pitch_estimate_three_percent_off_is_refitted_not_accused(pair):
    """S5: the turn and scale are refitted on sites paired by position, so an estimate's error is not a wrong name."""
    ref, other = pair
    off = copy.deepcopy(other)
    off.pitch = other.pitch * 1.03
    off.angle_deg = other.angle_deg + 1.2
    result = ni.compare(ref, off)
    assert result["verdict"] == ni.CONFIRMED, result["reason"]
    refined = result["square"]["refined"]
    assert refined["kept"] and refined["scale"] == pytest.approx(1.0, abs=0.005)
    assert result["max_pitches"] < 0.1


def test_the_refit_cannot_absorb_a_wrong_name(pair):
    """Paired by position, never by address: a relabelled timepoint's sites sit where they sat."""
    ref, other = pair
    result = ni.compare(ref, _relabel(other, lambda a: (a[0] + 1, a[1])))
    assert result["verdict"] == ni.CONTRADICTED
    assert abs(result["square"]["refined"]["rotation_deg"] - result["square"]["refined"]["registered_rotation_deg"]) < 0.1


def test_one_site_a_quarter_pitch_out_is_not_a_wrong_well(pair):
    """S6: one site of 265 fitted 0.3 pitch off its place, still nearest its own address, flipped the timepoint."""
    ref, other = pair
    loose = copy.deepcopy(other)
    loose.sites[(2, 1)] = tuple(np.add(other.sites[(2, 1)], (0.3 * PITCH, 0.0)))
    result = ni.compare(ref, loose)
    assert result["verdict"] == ni.CONFIRMED and result["loose_count"] == 1
    # Twenty such sites are still twenty right names (U6): reported, nothing left out.
    many = copy.deepcopy(other)
    for a in list(other.sites)[:20]:
        many.sites[a] = tuple(np.add(other.sites[a], (0.3 * PITCH, 0.0)))
    result = ni.compare(ref, many)
    assert result["verdict"] == ni.CONFIRMED and result["loose_count"] == 20 and "name is right" in result["reason"]


def test_a_corner_tile_placed_a_third_of_a_pitch_differently_is_imprecise_not_wrong(pair):
    """U6, the suspected cause of the first real plate's verdicts: an empty corner tile has only the stage's position,
    which wobbles by tens of micrometres between timepoints. Its nanowells stay on their nearest lattice sites, so
    their names are right; it is reported, nothing is left out."""
    ref, other = pair
    shifted = copy.deepcopy(other)
    nudge = np.array([0.33 * PITCH, 0.0])
    shifted.tiles[TILE] = tuple(np.add(other.tiles[TILE], nudge))
    for a, owner in other.owners.items():
        if owner == TILE:
            shifted.sites[a] = tuple(np.add(other.sites[a], nudge))
    result = ni.compare(ref, shifted)
    assert result["verdict"] == ni.CONFIRMED, result["reason"]
    assert result["tiles"]["moved"] == [] and result["tiles"]["imprecise"][0][0] == TILE
    assert result["loose_count"] > 0 and "names unaffected" in result["reason"]


def test_a_decoy_followed_two_sites_off_is_contradicted_by_the_array_s_outline(pair):
    """S1: with the square hidden, a square-like decoy two sites off taken by the run is the best square there and
    the correlation follows it: every address then lands on itself. The rim-fitted centre does not move with it."""
    ref, other = pair
    decoy = copy.deepcopy(other)
    decoy.centre_um = tuple(np.add(other.centre_um, 2 * _a1(other)))
    result = ni.compare(ref, decoy)
    assert result["verdict"] == ni.CONTRADICTED and "decoy" in result["reason"]
    assert result["failing_count"] == 0


def test_a_hexagonal_plate_turned_past_the_fold_is_caught(pair):
    """R5: turned 40°, the lattice angle reads −20°; without the square the run labels it 60° off. The folded turn
    finds no square, the turns beyond the fold do, and the labels are contradicted."""
    ref, _ = pair
    turned = _timepoint("T1", 7.0 + 40.0, (60.0, -40.0), 2)[0]
    folded = _relabel(turned, lambda a: (a[0] + a[1], -a[0]))         # the labels a fit 60° short would give
    folded.angle_deg = turned.angle_deg - 60.0
    result = ni.compare(ref, folded)
    assert result["square"]["turned_deg"] in (60.0, -120.0)
    assert result["verdict"] == ni.CONTRADICTED


def test_a_peak_on_the_edge_of_the_search_is_not_a_lock(pair):
    """S7: a plate moved just past the search radius put the best place on the window's edge, up to half a pitch
    off. With the search shrunk under the true shift the square is out of reach and the edge is not taken."""
    ref, other = pair
    far = copy.deepcopy(other)
    far.square_um, far.centre_um = None, None
    original = ni.SEARCH_PITCHES
    try:
        ni.SEARCH_PITCHES = 0.35                         # the square sits 0.42 pitch from the reference's place
        match = ni.register_on_square(ref, far)
    finally:
        ni.SEARCH_PITCHES = original
    assert not match.ok and "edge of the search window" in match.reason



def test_the_numbers_table_carries_the_measurements_and_no_sentences(pair, tmp_path, capsys):
    """``--numbers``: one CSV row per well and timepoint, the values the verdict was decided from, a code for why a
    timepoint is not good — what can be shared from a real plate to set the thresholds (tracker Q1, Q2, Q4, Q5)."""
    import csv as _csv

    ref, other = pair
    hidden = _timepoint("T2", 8.0, (20.0, 10.0), 3, square=False)[0]
    _write_dataset(tmp_path, "A01", {"T0": ref, "T1": other, "T2": hidden})
    _write_dataset(tmp_path, "A02", {"T0": ref, "T1": _relabel(other, lambda a: (a[0] + 1, a[1]))})
    assert ni.main([str(tmp_path), "--numbers"]) == 1
    rows = {(r["well"], r["timepoint"]): r for r in _csv.DictReader(io.StringIO(capsys.readouterr().out))}
    assert list(next(iter(rows.values()))) == list(ni.NUMBER_COLUMNS)
    good, unseen, stepped = rows[("A01", "T1")], rows[("A01", "T2")], rows[("A02", "T1")]
    assert (good["verdict"], good["why"], good["compared"], good["failing"]) == ("confirmed", "", "265", "0")
    assert float(good["margin"]) >= ni.MIN_MARGIN and float(good["max_pitches"]) < 0.05
    assert (unseen["verdict"], unseen["why"], unseen["compared"]) == ("inconclusive", "margin", "")
    assert float(unseen["margin"]) < ni.MIN_MARGIN
    assert (stepped["verdict"], stepped["diagnosis"], stepped["why"]) == ("contradicted", "step", "addresses")
    assert float(stepped["p95_pitches"]) == pytest.approx(1.0, abs=0.01)
    assert rows[("A01", "T0")]["verdict"] == "reference"



def test_a_tile_that_holds_no_nanowell_is_not_compared(pair):
    """U6: an empty corner tile is placed by the stage alone, so it wobbles a pitch or more between timepoints; it
    names nothing, so it can make no name wrong and is left out of the layout test."""
    ref, other = pair
    ref2, other2 = copy.deepcopy(ref), copy.deepcopy(other)
    ref2.tiles["F9"] = (5000.0, 5000.0)
    other2.tiles["F9"] = (5000.0 + 1.5 * PITCH, 5000.0)             # far off, and it owns nothing
    result = ni.compare(ref2, other2)
    assert result["verdict"] == ni.CONFIRMED and result["tiles"]["moved"] == []
