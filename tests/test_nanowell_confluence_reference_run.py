"""Every well's confluence is read against a named empty well, through the real run.

Reported: a plate whose nanowells held 47-58 cells read 0-3 % confluent at every timepoint, because the empty-nanowell
template and the texture threshold were taken from the loaded well itself. The run is now told which plate well is
empty (``--nanowell_empty_well``); it is measured first (``nanowell_task.reference_well_first``) and saved
(``pipeline.nanowell_reference``), and every other well is read against it. Driven through ``process_microscopy.main``
on a rendered two-well plate, so the order the wells run in, what is saved between them and what reaches the sites
CSV and the grid record are all the app's own.
"""

from __future__ import annotations

import json
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest
from tifffile import imwrite

import pipeline.suspend as _suspend
import process_microscopy as pm
from pipeline import nanowell_measure as nm
from pipeline import nanowell_reference as nref
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, paint_cell, record_for, threshold_segmenter
from util.acquisition_record import write_record_file

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
LOADED, EMPTY = "A01", "H12"          # the reference sorts last, so running it first is the run's doing
#: Cells per nanowell on the loaded well: dense, as the reported plate was. Measured on this scene, read against the
#: empty well its median nanowell reads 0.73; read against itself (the defect) 0.013. At two per nanowell the cells
#: are still the minority of the floor and the self-read gives 0.25, which could not tell the two apart.
DENSE = 6
#: A two-well plate run takes about 80 s under a loaded lane, past the lane's 90 s default with any contention.
pytestmark = pytest.mark.timeout(300)


def _cells(rendered, per_site, seed):
    """*per_site* cells at random places in every nanowell (cells at one place in every nanowell are the template)."""
    rng = np.random.default_rng(seed)
    for site in rendered.sites_um:
        for _ in range(per_site):
            angle, reach = rng.uniform(0, 2 * np.pi), rng.uniform(0, 28.0)
            paint_cell(rendered, site, dx_um=reach * np.cos(angle), dy_um=reach * np.sin(angle))


def _plate(root, wells: dict):
    """``in/`` with one archive per well; *wells* maps a well to its cells per nanowell."""
    plate = root / "plate"
    (plate / "in").mkdir(parents=True)
    record = None
    tif = root / "frame.tif"
    for k, (well, per_site) in enumerate(wells.items()):
        rendered, _ = build_well(well=well, timepoint="0h", seed=1)
        _cells(rendered, per_site, seed=10 + k)
        one = record_for(rendered, well=well, timepoint="0h")
        if record is None:
            record = one
        else:
            record["tiles"] += one["tiles"]
        with zipfile.ZipFile(plate / "in" / f"{well}.zip", "w") as zf:
            for fov, image in rendered.images.items():
                for channel, pixels in (("PHASE", np.array(image, dtype="float32")),
                                        ("GFP", np.full(image.shape, 10.0, dtype="float32"))):
                    imwrite(str(tif), pixels)
                    zf.write(tif, f"exp_{channel}_{well}_{fov}_0h.tif")
    write_record_file(plate / "in" / "acquisition.json", record)
    return plate


class _Now:
    """An executor that runs each task as it is submitted, in this process (the stand-in segmenter is patched here)."""

    def submit(self, fn, *args, **kwargs):
        from concurrent.futures import Future

        done = Future()
        done.set_result(fn(*args, **kwargs))
        return done


def _run(monkeypatch, plate, *extra):
    """``(output folder, the wells in the order they ran)``. The real ``process_well_zips`` runs, so the order is
    its own; only the worker pool under it is replaced, by one that runs each well here, one at a time."""
    order = []

    def _pool(*, items, submit_task, **_ignored):
        for label, path in items:
            order.append(label)
            submit_task(_Now(), label, path).result()
        return []

    monkeypatch.setattr(pm, "_run_well_worker_pool", _pool)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    out = plate / "out"
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"), "--output_dir", str(out),
        "--phase_token", "PHASE", "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
        "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check",
        "--nanowell_format", "100um", *extra])
    pm.main()
    return out, order


def _well(out, well):
    sites = pd.read_csv(out / "nanowells" / f"{well}.csv")
    grid = json.loads((out / "nanowells" / f"{well}_grid.json").read_text())
    return sites[sites["state"].isin(["measured", "empty"])], grid["timepoints"]["0h"]


def test_a_loaded_well_is_read_against_the_empty_well_measured_before_it(tmp_path, monkeypatch):
    """One plate run, every claim on it: a module fixture shared by several tests is built once per worker under
    ``--dist load`` (measured: twice, 83 s and 76 s, in one lane).

    The reference sorts last and runs first; it is saved empty; and the loaded well, six cells of 4 px in radius in
    each nanowell of 11.6 px, reads its median nanowell above 0.5 against it (measured 0.73; read against itself,
    the defect, 0.013), while the empty well reads zero."""
    out, order = _run(monkeypatch, _plate(tmp_path, {LOADED: DENSE, EMPTY: 0}), "--nanowell_empty_well", EMPTY)
    assert order == [EMPTY, LOADED]
    index = nref.read_index(out)
    assert index["well"] == EMPTY and index["timepoints"]["0h"]["status"] == nref.EMPTY
    loaded, block = _well(out, LOADED)
    empty, empty_block = _well(out, EMPTY)
    assert empty_block["confluence_reference"]["status"] == nref.EMPTY
    assert loaded["confluence"].notna().all() and empty["confluence"].notna().all()
    assert loaded["confluence"].median() > 0.5 and loaded["confluence"].quantile(0.1) > 0.4
    assert empty["confluence"].median() == 0.0
    assert not any(str(n).startswith(nref.NOTE_PREFIX) for n in block["notes"])
    # The count's rim-removed input (the command line's default) was divided by the empty well's template in every
    # counted nanowell, and the empty well's false-count baseline — the cells it counted per nanowell — is saved with
    # its verdict and written on the loaded well's timepoint.
    assert block["template"]["segmenter_template"] == {
        "source": nr.SEGMENT_TEMPLATE_REFERENCE, "nanowells_on_reference": len(loaded), "nanowells_on_own": 0}
    baseline = empty["n_cells"].sum() / len(empty)
    assert index["timepoints"]["0h"]["false_cells_per_nanowell"] == pytest.approx(baseline)
    assert empty_block["confluence_reference"]["false_cells_per_nanowell"] == pytest.approx(baseline)
    assert block["template"]["reference_false_cells_per_nanowell"] == pytest.approx(baseline)


def test_a_named_empty_well_with_counted_objects_is_still_the_reference(tmp_path, monkeypatch):
    """Reported: a remote run given a blank well had no confluence anywhere, because more than 70 % of the blank's
    nanowells held an object the segmenter counted (debris), and the count-based rule refused the reference. The
    well named empty is believed: painted here with two objects in every nanowell, it is saved ``empty`` with a
    template, keeps its own confluence, and says what was counted in a note that is not a missing-confluence one."""
    out, _order = _run(monkeypatch, _plate(tmp_path, {EMPTY: 2}), "--nanowell_empty_well", EMPTY)
    saved = nref.read_index(out)["timepoints"]["0h"]
    assert saved["status"] == nref.EMPTY and saved.get("template")      # a template the other wells can read
    assert saved["looks_loaded"] is True and saved["loaded_share"] > nref.MAX_LOADED_SHARE
    seen, block = _well(out, EMPTY)
    assert len(seen) and seen["confluence"].notna().all()
    assert seen["n_cells"].notna().all()
    assert not any(str(n).startswith(nref.NOTE_PREFIX) for n in block["notes"])
    assert any(str(n).startswith(nref.LOOKS_LOADED_PREFIX) for n in block["notes"])
