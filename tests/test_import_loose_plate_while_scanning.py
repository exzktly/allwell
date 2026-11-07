"""The Zip Loose Folder plate map while the run is still scanning.

A run -- queued or by hand -- starts a worker whose first act is its own walk of
the source folder, and only when that finishes does it report which wells it
will zip (``wellsPlanned``). On a plate of ~10^5 loose TIFs on a share that walk
takes minutes, and for all of them the dashboard sat on an empty grid -- a
progress bar and a plate with no well drawn on it -- although the page had
scanned that same folder, with those same settings, before the run was started
(a queued job's ``show_job`` scans it on the way in).

The assertion is at the boundary the user sees, the plate's own calls
(``setEnabledWells`` / ``setWellColor``), not the dashboard's bookkeeping.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import datasets.import_loose.worker as worker_mod  # noqa: E402
from datasets.import_loose.controller import ImportLooseController  # noqa: E402

_SCHEMA = "experiment:channel:well:fov:timepoint"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spec(tmp_path, wells=("A01", "A02", "B01")):
    src = tmp_path / "src"
    src.mkdir()
    for well in wells:
        for chan in ("GFP", "DAPI"):
            (src / f"Exp_{chan}_{well}_F000_1h.tif").write_bytes(b"II*\0" + b"\0" * 64)
    return {"source": str(src), "destination": str(tmp_path / "out"), "in_place": False,
            "filename_schema": _SCHEMA, "filename_sep": "_"}


def _pump(qapp, seconds=0.0, until=None):
    end = time.time() + seconds
    while time.time() < end or (until is not None and not until() and time.time() < end + 30):
        qapp.processEvents()
        time.sleep(0.01)
        if until is not None and until():
            break


@pytest.fixture
def held_worker_scan(monkeypatch):
    """The worker's own up-front scan, held open until the test releases it."""
    release = threading.Event()
    real = worker_mod.scan_folder

    def held(*a, **k):
        release.wait(30)
        return real(*a, **k)

    monkeypatch.setattr(worker_mod, "scan_folder", held)
    yield release
    release.set()


def _spy_plate(controller):
    plate = controller.page.dashboard._plate
    calls = {"enabled": [], "colours": {}}
    real_enable, real_colour = plate.setEnabledWells, plate.setWellColor

    def enable(wells, *a, **k):
        calls["enabled"].append(None if wells is None else sorted(wells))
        return real_enable(wells, *a, **k)

    def colour(well, colour, *a, **k):
        calls["colours"][well] = colour
        return real_colour(well, colour, *a, **k)

    plate.setEnabledWells, plate.setWellColor = enable, colour
    return calls


@pytest.mark.parametrize("route", ["queue", "button"])
def test_the_plate_shows_the_wells_while_the_worker_is_still_scanning(
        qapp, tmp_path, held_worker_scan, route):
    controller = ImportLooseController()
    spec = _spec(tmp_path)
    if route == "queue":
        calls = _spy_plate(controller)
        controller.start_from_queue(spec)
    else:
        controller.show_job(spec)
        calls = _spy_plate(controller)
        controller._launch()
    _pump(qapp, 0.5)                      # the worker is parked in its scan
    assert controller._worker.isRunning()
    # The last word the plate was given is the page's known wells, each drawn
    # (pending), not an empty grid.
    assert calls["enabled"][-1] == ["A01", "A02", "B01"]
    assert set(calls["colours"]) == {"A01", "A02", "B01"}
    assert controller.page.dashboard._headline.text().startswith("Scanning")

    held_worker_scan.set()
    _pump(qapp, 0, until=lambda: not controller._worker.isRunning())
    _pump(qapp, 0.3)
    assert controller._last_result[0] is True
    assert controller.page.dashboard._well_stage == dict.fromkeys(
        ("A01", "A02", "B01"), "done")


def test_the_workers_wells_replace_a_stale_seed(qapp, tmp_path, held_worker_scan):
    """The seed is the page's scan of the folder *before* the run; the worker's
    is the one the run acts on. When they disagree the run's wins, and a well
    only the seed had must not linger on the plate."""
    controller = ImportLooseController()
    spec = _spec(tmp_path)
    controller.show_job(spec)
    # A file arrives between the page's scan and the run's.
    (tmp_path / "src" / "Exp_GFP_C03_F000_1h.tif").write_bytes(b"II*\0" + b"\0" * 64)
    (tmp_path / "src" / "Exp_DAPI_C03_F000_1h.tif").write_bytes(b"II*\0" + b"\0" * 64)
    controller._launch()
    _pump(qapp, 0.3)
    assert controller.page.dashboard._wells == ["A01", "A02", "B01"]
    held_worker_scan.set()
    _pump(qapp, 0, until=lambda: not controller._worker.isRunning())
    _pump(qapp, 0.3)
    assert controller.page.dashboard._wells == ["A01", "A02", "B01", "C03"]
