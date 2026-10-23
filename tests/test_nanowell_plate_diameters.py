"""Each well's nanowell diameter against its plate's (tracker F11).

The wells of one plate share a mold and therefore a diameter, and each well measures its own on one tile; a well far
from the others has something wrong with that tile, or is not this plate's. The run says which, in the log and in
``pipeline_info.json``.
"""

import json
import logging

import pytest

from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt


def _grid(diameter, plate=None):
    grid = {"array_radius": {} if diameter is None else {"diameter_used_um": diameter}}
    if plate:
        grid["plate"] = plate
    return grid


def test_a_well_far_from_its_plate_s_median_is_named_with_how_far():
    # The odd one is not last, so taking the first or last well as the reference is caught.
    found = nt.plate_diameters({"B02": _grid(125.8), "B03": _grid(140.0), "B04": _grid(125.6), "B05": _grid(126.0),
                                "B06": _grid(None)})
    plate = found[""]
    assert plate["median_um"] == pytest.approx(125.9)
    assert plate["wells"] == {"B02": 125.8, "B03": 140.0, "B04": 125.6, "B05": 126.0}
    assert list(plate["outliers"]) == ["B03"] and plate["outliers"]["B03"] == pytest.approx(140.0 / 125.9 - 1)


def test_a_well_inside_the_tolerance_is_not_named():
    found = nt.plate_diameters({"B02": _grid(100.0), "B03": _grid(104.9), "B04": _grid(100.0)})
    assert found[""]["outliers"] == {}


def test_a_plate_of_two_wells_has_no_median_to_compare_against():
    found = nt.plate_diameters({"B02": _grid(100.0), "B03": _grid(150.0)})
    assert found[""]["median_um"] is None and found[""]["outliers"] == {}


def test_two_plates_merged_into_one_dataset_are_compared_separately():
    grids = {"A01": _grid(100.0, "p1"), "A02": _grid(100.5, "p1"), "A03": _grid(99.5, "p1"),
             "B01": _grid(50.0, "p2"), "B02": _grid(50.2, "p2"), "B03": _grid(49.8, "p2")}
    found = nt.plate_diameters(grids)
    assert found["p1"]["outliers"] == {} and found["p2"]["outliers"] == {}
    assert found["p2"]["median_um"] == pytest.approx(50.0)


def test_the_end_of_the_run_records_and_logs_it(tmp_path, monkeypatch, caplog):
    """Through ``nanowell_endgame``, the caller the pipeline runs: the comparison reaches the run's sidecar and its
    log, for the wells analysed under this run's settings only."""
    settings = nr.NanowellSettings("100um")
    folder = tmp_path / nt.NANOWELL_DIR
    folder.mkdir()
    for well, diameter, key in (("B02", 125.8, settings.key()), ("B03", 140.0, settings.key()),
                                ("B04", 125.6, settings.key()), ("B05", 126.0, settings.key()),
                                ("B06", 300.0, nr.NanowellSettings("50um").key())):
        payload = {"scheme_version": nt.SCHEME_VERSION, "well": well, "settings": key, "timepoints": {},
                   "array_radius": {"diameter_used_um": diameter}}
        (folder / f"{well}_grid.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(nt, "reanchor_plate", lambda *a, **k: {"anchored": [], "remaining": {}})
    with caplog.at_level(logging.WARNING, logger="allwell.nanowell"):
        nt.nanowell_endgame({"nanowell": True}, tmp_path, [], {"_nanowell": {"plate_format": "100um"}}, "",
                            [], "", "", "_", None, grouper=None, parse=None)
    recorded = json.loads((tmp_path / "pipeline_info.json").read_text(encoding="utf-8"))["nanowell"]
    plate = recorded["plate_diameters"][""]
    assert set(plate["wells"]) == {"B02", "B03", "B04", "B05"}            # B06 was analysed as another format
    assert list(plate["outliers"]) == ["B03"]
    assert any("Well B03: its nanowells measure 140.0 um, +11 % off the 125.9 um" in r.getMessage()
               for r in caplog.records)
