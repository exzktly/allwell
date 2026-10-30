"""A nanowell well run on its worker's threads writes exactly what it writes on one, and reads each tile once.

A nanowell worker used one of the cores it was given: every timepoint located in turn, then every tile measured in
turn, with the GPU idle whenever the worker did CPU work. ``run_well`` now locates timepoints and measures tiles on the
worker's threads, prepares the next timepoint while this one is on the GPU, and keeps each tile in memory between
locating and measuring. None of that may change a single value or the order anything is written in: these run the
same rendered well serially and threaded and compare every output, serialised, character for character.
"""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from pipeline import nanowell_parallel as par
from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well

SETTINGS = nr.NanowellSettings("100um", segment_scope="nanowells", segment_scale="levelled")


@pytest.fixture(scope="module")
def well():
    first, record = build_well(rows=2, cols=2, tile=(700, 700), timepoint="0h", cells=60)
    second, more = build_well(rows=2, cols=2, tile=(700, 700), timepoint="24h", rotation=9.0,
                              array_shift_um=(100.0, -60.0), seed=2, cells=90)
    third, last = build_well(rows=2, cols=2, tile=(700, 700), timepoint="48h", rotation=8.0, seed=3, cells=120)
    for extra in (more, last):
        record["timepoints"].update(extra["timepoints"])
        record["tiles"] += extra["tiles"]
    images = {**{("0h", t): v for t, v in first.images.items()}, **{("24h", t): v for t, v in second.images.items()},
              **{("48h", t): v for t, v in third.images.items()}}
    return record, images


class _Segmenter:
    """Cellpose's call shape (a range, the area cap, batches of crops) with a deterministic threshold inside, and a
    count of how many of its calls were ever in flight at once."""

    def __init__(self):
        self.lock, self.inside, self.most = threading.Lock(), 0, 0

    def _labels(self, crop, lowhigh):
        lo, hi = lowhigh
        return ndimage.label(ndimage.gaussian_filter((np.asarray(crop, float) - lo) / (hi - lo), 1.5) > 0.6)[0]

    def build(self):
        def segment(image, lowhigh=None, max_size_fraction=None):
            return self._labels(image, lowhigh or (float(np.min(image)), float(np.max(image))))

        def segment_many(images, lowhigh, max_size_fraction=None):
            with self.lock:
                self.inside += 1
                self.most = max(self.most, self.inside)
            try:
                return [self._labels(c, lowhigh) for c in images]
            finally:
                with self.lock:
                    self.inside -= 1
        segment.accepts_range = True
        segment.accepts_size_cap = True
        segment.segment_many = segment_many
        return segment


def _plain(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, frozenset):
        return sorted(value)
    return repr(value)


def _serialised(outcome) -> str:
    inspection = None if outcome.inspection is None else [
        outcome.inspection[0], {str(k): {f: _plain(v) for f, v in crop.items()}
                                for k, crop in outcome.inspection[1].items()}]
    return json.dumps({"timepoints": outcome.timepoints, "sites": outcome.sites, "cells": outcome.cells,
                       "radius": outcome.radius, "notes": outcome.notes, "located": list(outcome.located),
                       "inspection": inspection}, default=_plain)


def _run(monkeypatch, record, images, threads: int, segmenter):
    monkeypatch.setenv(par.THREADS_ENV, str(threads))
    return nr.run_well("A01", record, ["0h", "24h", "48h"], lambda tp, tile: images.get((tp, tile)), SETTINGS,
                       segmenter=segmenter, progress=lambda d, n: None, site_progress=lambda tp, d, n: None)


def test_a_threaded_well_writes_what_a_serial_one_writes_and_never_overlaps_two_segmenter_calls(monkeypatch, well):
    record, images = well
    serial = _run(monkeypatch, record, images, 1, _Segmenter().build())
    counted = _Segmenter()
    threaded = _run(monkeypatch, record, images, 3, counted.build())
    assert serial.sites and serial.cells.get("label")              # the fixture measures something to compare
    assert _serialised(threaded) == _serialised(serial)
    assert counted.most == 1


def test_the_next_timepoint_is_prepared_while_this_one_is_measured(monkeypatch, well):
    """Prepared ahead, but only with what a serial loop would have given it: the earliest template so far."""
    record, images = well
    order = []
    real_prepare, real_finish = nr._prepare_timepoint, nr._finish_timepoint

    def prepare(located, *a, **k):
        order.append(("prepare", located.timepoint))
        return real_prepare(located, *a, **k)

    def finish(prepared, **k):
        measured = real_finish(prepared, **k)
        order.append(("finished", prepared.located.timepoint))
        return measured

    monkeypatch.setattr(nr, "_prepare_timepoint", prepare)
    monkeypatch.setattr(nr, "_finish_timepoint", finish)
    _run(monkeypatch, record, images, 3, _Segmenter().build())
    threaded = list(order)
    order.clear()
    _run(monkeypatch, record, images, 1, _Segmenter().build())
    # Threaded, 24h's preparation starts before 0h's tiles are done; serially, after (the fixture can tell the two).
    assert threaded.index(("prepare", "24h")) < threaded.index(("finished", "0h"))
    assert order.index(("prepare", "24h")) > order.index(("finished", "0h"))
    for run in (threaded, order):
        assert [step for step in run if step[0] == "finished"] == [("finished", tp) for tp in ("0h", "24h", "48h")]


def test_a_well_through_the_task_reads_each_phase_tile_from_its_archive_once(tmp_path, monkeypatch):
    from pipeline import zip_members
    from tests.test_nanowell_registration_reuse import _run as run_plate
    from tests.test_nanowell_registration_reuse import _write_plate

    rendered, record = build_well(timepoint="0h")
    plate = _write_plate(tmp_path, rendered, record)
    reads: list[str] = []
    real = zip_members.ZipMemberReader.read

    def counting(self, name):
        reads.append(Path(name).name)
        return real(self, name)

    monkeypatch.setattr(zip_members.ZipMemberReader, "read", counting)
    monkeypatch.setenv(par.THREADS_ENV, "3")
    out = run_plate(monkeypatch, plate, "out")
    assert (out / "nanowells" / "A01_grid.json").is_file()
    with zipfile.ZipFile(plate / "in" / "A01.zip") as zf:
        members = sorted(Path(n).name for n in zf.namelist())
    # Each once: a phase tile located and measured from one read, a fluorescence tile levelled and measured from one.
    assert sorted(reads) == members
