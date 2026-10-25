"""A well whose central square is never found is addressed from one anchor set by hand, at every timepoint.

The user's choice: one click per well at one timepoint. Here B02 has no evidence of its own at either timepoint —
its square and its boundary give nothing, as on the reported plates — and ``in/nanowell_anchors.json`` holds one
anchor for it at 0h, a few micrometres off the square, as a click would be. ``process_microscopy.main`` runs for
real on the four-well plate of ``test_nanowell_reanchor``, which moves rigidly between its two timepoints: 0h is
anchored by hand, and the end-of-run pass carries it to 24h through the other wells. Every address is checked
against the one the synthetic plate was drawn with.
"""

from __future__ import annotations

import csv
import json
import sys

import numpy as np
import pytest

import pipeline.nanowell_lattice as lattice
import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, threshold_segmenter
from tests.test_nanowell_reanchor import TILE, _write_plate
from util.nanowell_anchors import anchors_path, set_anchor

#: Off the square by this much, as a click is: the run snaps it to the site.
CLICK_ERROR_UM = (8.0, -6.0)


def _run(monkeypatch, plate, blind=frozenset({("B02", "0h"), ("B02", "24h")})):
    """``main`` on *plate*, with every (well, timepoint) in *blind* given no square and no boundary."""
    real_locate, real_resolve = nr.locate_timepoint, lattice.resolve_origin
    state = {"blind": False}

    def locate(tp, record, well, *args, **kwargs):
        state["blind"] = (well, tp) in blind
        return real_locate(tp, record, well, *args, **kwargs)

    def resolve(fit, *, fiducial=None, array_centre=None, plate_rigid=None, **rest):
        if state["blind"]:
            fiducial = array_centre = None
        return real_resolve(fit, fiducial=fiducial, array_centre=array_centre, plate_rigid=plate_rigid, **rest)

    def serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema, sep,
               incremental, incremental_prior_dir, settings_key, force, force_wells=None, **_ignored):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                                     csv_prefix, schema, sep, incremental, incremental_prior_dir, settings_key, force)
        return []

    for obj, name, value in [(pm, "process_well_zips", serial), (nr, "locate_timepoint", locate),
                             (lattice, "resolve_origin", resolve),
                             (nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter()),
                             (_suspend, "install_handlers", lambda **k: None),
                             (_suspend, "watch_parent", lambda *a, **k: None)]:
        monkeypatch.setattr(obj, name, value)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(plate / "out"),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", "experiment:channel:well:fov:timepoint",
        "--workers", "1", "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check",
        "--nanowell_format", "100um"])
    pm.main()
    return plate / "out"


def _wrong_addresses(out, rendered, timepoint) -> tuple[int, int]:
    """``(addressed rows, rows whose address is not the drawn one)`` of B02 at *timepoint*."""
    drawn = {tuple(map(float, s)): a for s, a in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(drawn))
    rows = [r for r in csv.DictReader((out / "nanowells" / "B02.csv").open())
            if r["timepoint"] == timepoint and r["detected"] == "True" and r["nw_q"]]
    wrong = 0
    for r in rows:
        nearest = tuple(sites[np.argmin(np.hypot(sites[:, 0] - float(r["well_x_um"]),
                                                 sites[:, 1] - float(r["well_y_um"])))])
        wrong += (int(r["nw_q"]), int(r["nw_r"])) != drawn[nearest]
    return len(rows), wrong


@pytest.fixture(scope="module")
def plate_0h():
    """B02 at 0h as ``_write_plate`` draws it, for where its square is."""
    rendered, _ = build_well(rows=1, cols=1, tile=TILE, well="B02", timepoint="0h", seed=11, radius_fraction=0.26)
    return rendered


def test_one_anchor_at_one_timepoint_addresses_the_well_at_both(tmp_path, monkeypatch, plate_0h):
    plate, truth = _write_plate(tmp_path)
    square = plate_0h.sites_um[plate_0h.array.addresses.index((0, 0))]
    set_anchor(anchors_path(plate / "in"), "B02", "0h", np.add(square, CLICK_ERROR_UM))
    out = _run(monkeypatch, plate)
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]
    assert (grid["0h"]["status"], grid["0h"]["origin_method"]) == ("ok", "manual")
    assert grid["0h"]["manual_anchor_um"] == pytest.approx(list(np.add(square, CLICK_ERROR_UM)))
    assert (grid["24h"]["status"], grid["24h"]["origin_method"]) == ("ok", "plate_rigid")
    # Both stamped with the plate's anchors as they were, which keeps 24h from outliving a moved click.
    assert grid["24h"].get("anchors_digest") == grid["0h"].get("anchors_digest") != ""
    for tp, rendered in (("0h", plate_0h), ("24h", truth["B02"])):
        addressed, wrong = _wrong_addresses(out, rendered, tp)
        assert addressed > 40 and wrong == 0, (tp, addressed, wrong)
    info = json.loads((out / "pipeline_info.json").read_text())
    assert info["nanowell"]["reanchor"]["anchored"] == ["B02/24h"]


def test_without_the_anchor_the_same_well_is_left_unaddressed(tmp_path, monkeypatch):
    """The control: what the anchor is for. Nothing else gives B02 an origin."""
    plate, _ = _write_plate(tmp_path)
    out = _run(monkeypatch, plate)
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]
    assert [grid[tp]["status"] for tp in ("0h", "24h")] == ["unaddressed", "unaddressed"]
    assert "manual_anchor_um" not in grid["0h"]


def test_the_anchor_is_read_in_the_well_frame_where_tiles_sit_far_from_its_origin(monkeypatch):
    """The anchor is stated in the frame the grid record's tiles and montage origin are in, which starts wherever
    the record put the tiles (a stage position, on a real plate), not at the montage's corner."""
    shift_px = (3000.0, 1500.0)
    rendered, record = build_well()
    for tile in record["tiles"]:
        tile["mosaic_xy_px"] = [tile["mosaic_xy_px"][0] + shift_px[0], tile["mosaic_xy_px"][1] + shift_px[1]]
    settings = nr.NanowellSettings("100um")
    reference = nr.locate_timepoint("T0", record, "A01", rendered.images, settings)
    assert reference.status == nr.OK and reference.origin_method == "fiducial"
    assert min(reference.montage.origin_um) > 1000                  # the frames really are far apart here
    square = np.add(reference.origin_montage, reference.montage.origin_um)       # in the well frame

    real_resolve = lattice.resolve_origin

    def blind(fit, *, fiducial=None, array_centre=None, plate_rigid=None, **rest):
        return real_resolve(fit, plate_rigid=plate_rigid, **rest)

    monkeypatch.setattr(lattice, "resolve_origin", blind)
    assert nr.locate_timepoint("T0", record, "A01", rendered.images, settings).status == nr.UNADDRESSED
    clicked = nr.locate_timepoint("T0", record, "A01", rendered.images, settings,
                                  manual_anchor_um=np.add(square, CLICK_ERROR_UM))
    assert (clicked.status, clicked.origin_method) == (nr.OK, "manual")
    np.testing.assert_allclose(clicked.origin_montage, reference.origin_montage, atol=1e-6)
    # Off the array, where the fitted lattice still runs: refused, not snapped to a site out there.
    off = nr.locate_timepoint("T0", record, "A01", rendered.images, settings,
                              manual_anchor_um=np.add(square, 40 * np.asarray(reference.fit.a1)))
    assert off.status == nr.UNADDRESSED and "from the nearest nanowell found" in off.reason


def _only(monkeypatch, *flags):
    """Make the next ``_run`` add *flags* to the command line, as Analyze adds them."""
    real_main = pm.main

    def main():
        sys.argv += list(flags)
        return real_main()

    monkeypatch.setattr(pm, "main", main)


def test_an_anchor_at_the_second_timepoint_reaches_the_first_from_the_other_wells_grid_records_alone(
        tmp_path, monkeypatch):
    """The reported case, as a remote re-run of one well now meets it: the anchor is at 24h, the well is run alone
    into an output folder holding nothing of the plate but the other wells' grid records — what
    ``services.remote_plate_grids`` sends to the host. 24h is addressed from the anchor and 0h from the plate's
    motion. Without those records (what the host had before) 0h comes back unaddressed, with the reported reason."""
    import shutil

    plate, truth = _write_plate(tmp_path)
    full = _run(monkeypatch, plate)                                        # the plate's first run, no anchor
    square = truth["B02"].sites_um[truth["B02"].array.addresses.index((0, 0))]
    set_anchor(anchors_path(plate / "in"), "B02", "24h", np.add(square, CLICK_ERROR_UM))
    shutil.move(str(full), str(tmp_path / "first_run"))

    def run_b02_alone(with_records: bool):
        shutil.rmtree(plate / "out", ignore_errors=True)
        (plate / "out" / "nanowells").mkdir(parents=True)
        if with_records:
            for well in ("A01", "A02", "B01"):
                shutil.copy(tmp_path / "first_run" / "nanowells" / f"{well}_grid.json", plate / "out" / "nanowells")
        _only(monkeypatch, "--only_wells", "B02", "--force_wells", "B02")
        out = _run(monkeypatch, plate)
        monkeypatch.undo()
        return json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]

    alone = run_b02_alone(with_records=False)
    assert (alone["24h"]["status"], alone["24h"]["origin_method"]) == ("ok", "manual")
    assert alone["0h"]["status"] == "unaddressed"
    assert alone["0h"]["reason"].startswith("No accepted fiducial, array centre, or precise plate-rigid prediction")
    grid = run_b02_alone(with_records=True)
    assert (grid["24h"]["status"], grid["24h"]["origin_method"]) == ("ok", "manual")
    assert (grid["0h"]["status"], grid["0h"]["origin_method"]) == ("ok", "plate_rigid")
    addressed, wrong = _wrong_addresses(plate / "out", build_well(rows=1, cols=1, tile=TILE, well="B02",
                                                                  timepoint="0h", seed=11, radius_fraction=0.26)[0],
                                        "0h")
    assert addressed > 40 and wrong == 0


def test_a_plain_re_run_forces_a_well_whose_anchor_was_set_since_and_the_anchor_is_used(tmp_path, monkeypatch):
    """No Force: B02 has output under these settings, so a plain re-run skipped it and the anchor set since was
    never read. ``main`` now hands the per-well loop B02 among the forced wells (``force_wells_with_new_anchors``),
    and the run addresses it from the anchor. ``_run``'s serial loop runs every well it is handed, so the forced
    list is asserted where ``main`` passes it, as well as the result."""
    plate, truth = _write_plate(tmp_path)
    out = _run(monkeypatch, plate)
    assert json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]["24h"]["status"] == "unaddressed"
    square = truth["B02"].sites_um[truth["B02"].array.addresses.index((0, 0))]
    set_anchor(anchors_path(plate / "in"), "B02", "24h", np.add(square, CLICK_ERROR_UM))
    monkeypatch.undo()
    forced = []
    real_main = pm.main

    def main():
        loop = pm.process_well_zips                       # ``_run``'s serial loop, patched in before ``main`` runs

        def recording(**kwargs):
            forced.append(list(kwargs.get("force_wells") or []))
            return loop(**kwargs)

        monkeypatch.setattr(pm, "process_well_zips", recording)
        return real_main()

    monkeypatch.setattr(pm, "main", main)
    out = _run(monkeypatch, plate)
    assert forced == [["B02"]]
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]
    assert (grid["24h"]["status"], grid["24h"]["origin_method"]) == ("ok", "manual")
    assert (grid["0h"]["status"], grid["0h"]["origin_method"]) == ("ok", "plate_rigid")
