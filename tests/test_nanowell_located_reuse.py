"""A folder named as the registration to reuse is taken as it is: every timepoint's tile positions and addresses.

Every nanowell run writes what locating each timepoint found to ``nanowells/<well>_located.npz``
(``pipeline.nanowell_located``); a run given ``--nanowell_registration_from <folder>`` (the Analyze form's *Reuse
registration*) takes each timepoint from that folder's file and locates nothing, and stops before any well starts when
the folder does not fit the plate. The end-to-end tests run ``process_microscopy.main`` on rendered tiles through the
harness of ``test_nanowell_registration_reuse``, because the folder is read in ``nanowell_task.process_well_zip`` and
checked in ``prepare_nanowell_run``, and only the real entry point runs them in the app's order.
"""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from pipeline import nanowell_located as nl
from pipeline import nanowell_run as nr
from tests.test_nanowell_registration_reuse import WELL, _CountingRegister, _run, _write_plate


class _CountingLocate:
    """``nanowell_run.locate_timepoint`` with a call count, so a test can say whether locating ran at all."""

    def __init__(self, monkeypatch):
        self.calls = 0
        real = nr.locate_timepoint

        def counting(*args, **kwargs):
            self.calls += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(nr, "locate_timepoint", counting)


def _grid(out) -> dict:
    return json.loads((out / "nanowells" / f"{WELL}_grid.json").read_text())


def _without_provenance(grid: dict) -> dict:
    """The grid record without what says where a taken result came from, which is all taking it may change."""
    grid = json.loads(json.dumps(grid))
    for block in grid["timepoints"].values():
        block.pop("located_from", None)
        if isinstance(block.get("registration"), dict):
            block["registration"].pop("reused_from", None)
    return grid


def _table(out) -> str:
    return (out / "nanowells" / f"{WELL}.csv").read_text()


@pytest.fixture(scope="module")
def rendered_well():
    from tests._nanowell_run_fixture import build_well, paint_cell

    rendered, record = build_well(timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    paint_cell(rendered, truth[(1, 2)], dx_um=3.0, dy_um=-2.0)
    return rendered, record


def test_a_named_folder_is_taken_and_the_result_matches_locating_again(tmp_path, monkeypatch, rendered_well):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    locates = _CountingLocate(monkeypatch)
    first = _run(monkeypatch, plate, "out1")
    assert locates.calls == 1
    assert (first / "nanowells" / f"{WELL}{nl.LOCATED_SUFFIX}").is_file()

    second = _run(monkeypatch, plate, "out2", "--nanowell_cell_diameter_um", "11",
                  "--nanowell_registration_from", str(first))
    assert locates.calls == 1, "a timepoint was located although the named folder gave it"
    block = _grid(second)["timepoints"]["0h"]
    assert block["located_from"] == str(first) and block["registration"]["reused_from"] == str(first)
    assert (second / "nanowells" / f"{WELL}{nl.LOCATED_SUFFIX}").is_file(), "the run did not keep what it took"

    # The same settings with no folder named locate afresh and write the same grid record and the same table.
    fresh = _run(monkeypatch, plate, "out3", "--nanowell_cell_diameter_um", "11")
    assert locates.calls == 2
    assert "located_from" not in _grid(fresh)["timepoints"]["0h"]
    assert _without_provenance(_grid(fresh)) == _without_provenance(_grid(second))
    assert _table(fresh) == _table(second)


def test_a_named_folder_is_taken_as_it_is_whatever_else_changed(tmp_path, monkeypatch, rendered_well):
    """Naming the folder says these are this plate's positions and addresses: a changed tile, or another setting
    locating reads, does not set them aside."""
    rendered, record = rendered_well
    first = _run(monkeypatch, _write_plate(tmp_path / "a", rendered, record), "out")
    locates, registers = _CountingLocate(monkeypatch), _CountingRegister(monkeypatch)
    corrected = _write_plate(tmp_path / "b", rendered, record, nudge_tile=sorted(rendered.images)[0])
    second = _run(monkeypatch, corrected, "out", "--nanowell_square_edge_deg", "2",
                  "--nanowell_registration_from", str(first))
    assert (locates.calls, registers.calls) == (0, 0)
    assert _grid(second)["timepoints"]["0h"]["tiles"] == _grid(first)["timepoints"]["0h"]["tiles"]


def test_a_folder_that_does_not_fit_stops_the_run_before_any_well_and_says_why(tmp_path, monkeypatch, rendered_well,
                                                                                capsys):
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    first = _run(monkeypatch, plate, "out1")
    (first / "nanowells" / f"{WELL}{nl.LOCATED_SUFFIX}").unlink()
    locates = _CountingLocate(monkeypatch)
    with pytest.raises(SystemExit):
        _run(monkeypatch, plate, "out2", "--nanowell_registration_from", str(first))
    assert locates.calls == 0
    assert not (plate / "out2" / "nanowells" / f"{WELL}.csv").exists()
    err = capsys.readouterr().err
    assert "does not fit this plate" in err and f"{WELL}: {WELL}{nl.LOCATED_SUFFIX} is not there" in err


def test_a_carried_folder_is_reused_only_where_it_fits(tmp_path, monkeypatch, rendered_well):
    """The folder a remote run carries from this computer's output folder, which nobody named
    (``--nanowell_registration_carried``): its registration is reused under its key, and every timepoint is located."""
    rendered, record = rendered_well
    plate = _write_plate(tmp_path, rendered, record)
    first = _run(monkeypatch, plate, "out1")
    locates, registers = _CountingLocate(monkeypatch), _CountingRegister(monkeypatch)
    second = _run(monkeypatch, plate, "out2", "--nanowell_registration_from", str(first),
                  "--nanowell_registration_carried")
    assert (locates.calls, registers.calls) == (1, 0)
    block = _grid(second)["timepoints"]["0h"]
    assert "located_from" not in block and block["registration"]["reused_from"] == str(first)


def test_a_well_whose_timepoints_disagree_is_taken_whole_and_gives_the_same_outcome(tmp_path):
    """A timepoint whose tiles sit a lattice step from where the others place them is located twice in one run (once,
    then with the agreed layout); the second is the one kept, so a run given the folder locates nothing and gives
    exactly the first run's outcome, the record of the correction included."""
    from pipeline.nanowell_task import _KeptLocated
    from tests._nanowell_run_fixture import threshold_segmenter
    from tests.test_nanowell_identity_run import LAYOUT_POSES, SETTINGS, _three_timepoints

    record, images = _three_timepoints(cells=0)
    timepoints = list(LAYOUT_POSES)
    calls = []
    real = nr.locate_timepoint

    def run(**kw):
        nr.locate_timepoint = lambda *a, **k: calls.append(k.get("preferred") is not None) or real(*a, **k)
        try:
            return nr.run_well(WELL, record, timepoints, lambda tp, tile: images[tp].get(tile), SETTINGS,
                               segmenter=threshold_segmenter(), **kw)
        finally:
            nr.locate_timepoint = real

    kept = _KeptLocated()
    first = run(keep_located=kept.__setitem__)
    nl.write_located(nl.located_path(tmp_path / "nanowells", WELL), dict(kept))
    assert calls == [False, False, False, True], "the fixture no longer locates one timepoint again"
    assert "tile_layout" in first.timepoints["T1"]
    calls.clear()
    second = run(given_located=nl.given_located(tmp_path, WELL))
    assert calls == [], "a timepoint the folder gave was located"

    def plain(outcome, taken):
        out = json.loads(json.dumps({"timepoints": outcome.timepoints, "sites": outcome.sites,
                                     "cells": outcome.cells, "radius": outcome.radius}, default=str, sort_keys=True))
        for block in out["timepoints"].values():
            assert (block.pop("located_from", None) == str(tmp_path)) is taken
            block["registration"].pop("reused_from")
        return out
    assert plain(second, True) == plain(first, False)


# ── the file ────────────────────────────────────────────────────────────────

def _a_located() -> nr.Located:
    from pipeline.nanowell_lattice import IndexedPoint, LatticeFit

    fiducial = {"found_um": [1.0, 2.0], "reason": ""}
    return nr.Located("0h", nr.OK, "", grid={"status": nr.OK, "fiducial": fiducial, "tiles": [{"tile": "F0"}]},
                      positions={"F0": (0.0, 0.0)}, pixel_size_um=0.65,
                      fit=LatticeFit(True, "", (1.0, 0.0), (0.5, 0.8), np.float64(1.0), inlier_mask=(True, False)),
                      origin_montage=(3.0, 4.0), detected_common=np.arange(6, dtype=float).reshape(3, 2),
                      indexed=(IndexedPoint(0, 1, 0.1, False), IndexedPoint(None, None, 0.4, True, "why")),
                      array_found=np.array([True, False]), notes=["a note"], fiducial=fiducial)


def test_an_entry_decodes_to_the_same_values_types_and_shared_objects(tmp_path):
    original = _a_located()
    nl.write_located(nl.located_path(tmp_path / "nanowells", WELL), {"0h": nl.encode_located(original)})
    fields = nl.given_located(tmp_path, WELL)["0h"]
    assert fields.pop("_reused_from") == str(tmp_path)
    assert set(fields) == {f.name for f in dataclasses.fields(nr.Located)} - {"montage", "plan"}
    for name, value in fields.items():
        kept = getattr(original, name)
        if isinstance(kept, np.ndarray):
            assert value.dtype == kept.dtype and np.array_equal(value, kept), name
        else:
            assert value == kept and type(value) is type(kept), name
    assert type(fields["fit"].pitch) is np.float64 and isinstance(fields["fit"].a1, tuple)
    assert fields["fiducial"] is fields["grid"]["fiducial"], "the grid block and the Located no longer share one"


def test_writing_keeps_the_timepoints_this_run_did_not_locate(tmp_path):
    path = nl.located_path(tmp_path / "nanowells", WELL)
    nl.write_located(path, {"0h": nl.encode_located(_a_located()), "1h": nl.encode_located(_a_located())})
    other = _a_located()
    other.reason = "this run's"
    nl.write_located(path, {"0h": nl.encode_located(other)})
    kept = nl.given_located(tmp_path, WELL)
    assert sorted(kept) == ["0h", "1h"] and kept["0h"]["reason"] == "this run's" and kept["1h"]["reason"] == ""


def test_an_entry_holding_an_unknown_class_is_refused_by_the_codec():
    located = _a_located()
    located.notes = [object()]
    with pytest.raises(nl.NotKept):
        nl.encode_located(located)


def test_the_fit_check_names_every_well_and_timepoint_that_does_not_fit(tmp_path):
    nl.write_located(nl.located_path(tmp_path / "nanowells", "A01"), {"0h": nl.encode_located(_a_located())})
    (tmp_path / "nanowells" / "A03_located.npz").write_bytes(b"not a zip")
    records = {"A01.zip": {"tiles": [{"well": "A01", "timepoint": tp, "field": f}
                                     for tp in ("0h", "1h") for f in ("F0", "F1")]},
               "A02.zip": {"tiles": [{"well": "A02", "timepoint": "0h", "field": "F0"}]},
               "A03.zip": {"tiles": [{"well": "A03", "timepoint": "0h", "field": "F0"}]}}
    said = []

    def error(text):
        said.append(text)
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        nl.refuse_unfitting_folder(tmp_path, [(w, f"{w}.zip") for w in ("A01", "A02", "A03")], records.get, error)
    text = said[0]
    assert "A01 at 0h: the folder's result covers tiles ['F0'], the acquisition record lists ['F0', 'F1']" in text
    assert "A01 at 1h: the folder has no located result for this timepoint" in text
    assert "A02: A02_located.npz is not there" in text
    assert "A03: A03_located.npz could not be read" in text
    nl.refuse_unfitting_folder(tmp_path, [("A01", "A01.zip")],
                               lambda _z: {"tiles": [{"well": "A01", "timepoint": "0h", "field": "F0"}]}, error)
    assert len(said) == 1, "a folder that fits was refused"
