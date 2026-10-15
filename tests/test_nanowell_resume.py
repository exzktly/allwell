"""A killed nanowell well resumes at the timepoint it had not finished (tracker P7)."""

from __future__ import annotations

import pytest

from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from tests._nanowell_run_fixture import build_well, threshold_segmenter

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def well():
    first, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h")
    second, second_record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="24h", rotation=9.0,
                                       array_shift_um=(100.0, -60.0), seed=2)
    record["timepoints"].update(second_record["timepoints"])
    record["tiles"] += second_record["tiles"]
    images = {("0h", t): v for t, v in first.images.items()} | {("24h", t): v for t, v in second.images.items()}
    return record, (lambda tp, tile: images.get((tp, tile)))


def _run(record, load, **kwargs):
    return nr.run_well("A01", record, ["0h", "24h"], load, SETTINGS, segmenter=threshold_segmenter(), **kwargs)


def _rows(outcome):
    return [{k: v for k, v in r.items()} for r in outcome.sites]


def test_a_killed_run_takes_back_the_finished_timepoint_and_measures_only_the_rest(tmp_path, monkeypatch, well):
    record, load = well
    clean = _run(record, load)
    store = nt.TimepointCheckpoints(tmp_path / "staging", "key")
    real = nr.measure_timepoint
    measured = []

    def dies_on_the_second(located, *a, **k):
        if measured:
            raise KeyboardInterrupt("killed")
        measured.append(located.timepoint)
        return real(located, *a, **k)

    monkeypatch.setattr(nr, "measure_timepoint", dies_on_the_second)
    with pytest.raises(KeyboardInterrupt):
        _run(record, load, checkpoint=store)
    assert measured == ["0h"] and (tmp_path / "staging" / ".fov_nw_0h.done").exists()
    assert not (tmp_path / "staging" / ".fov_nw_24h.done").exists()

    again = []

    def counting(located, *a, **k):
        again.append(located.timepoint)
        return real(located, *a, **k)

    monkeypatch.setattr(nr, "measure_timepoint", counting)
    resumed = _run(record, load, checkpoint=store)
    assert again == ["24h"]                                     # 0h came back from its checkpoint
    assert _rows(resumed) == _rows(clean)                       # and nothing differs from an uninterrupted run
    assert resumed.cells == clean.cells


def test_a_checkpoint_is_not_taken_back_under_a_different_roster_or_record(tmp_path, well):
    record, load = well
    store = nt.TimepointCheckpoints(tmp_path / "staging", "key")
    _run(record, load, checkpoint=store)
    base = {"radius_um": None, "record_digest": nr.record_digest(record, "A01", "0h")}
    radius = _run(record, load).radius["radius_um"]
    assert store.load("0h", {**base, "radius_um": radius}) is not None
    assert store.load("0h", {**base, "radius_um": radius + 10.0}) is None             # another roster
    assert store.load("0h", {"radius_um": radius, "record_digest": "0" * 16}) is None   # another placement
    assert nt.TimepointCheckpoints(tmp_path / "staging", "other settings").load("0h", {**base, "radius_um": radius}) is None


def test_a_checkpoint_without_its_marker_is_ignored(tmp_path, well):
    record, load = well
    store = nt.TimepointCheckpoints(tmp_path / "staging", "key")
    _run(record, load, checkpoint=store)
    (tmp_path / "staging" / ".fov_nw_0h.done").unlink()                  # killed between the two writes
    radius = _run(record, load).radius["radius_um"]
    assert store.load("0h", {"radius_um": radius, "record_digest": nr.record_digest(record, "A01", "0h")}) is None
