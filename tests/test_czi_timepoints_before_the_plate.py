"""Convert CZI lists a split plate's timepoints before the rest of the plate has been read.

Reported: on a folder of CZI files held on a network share, the Timepoints dropdown could not be used — it
"only loads after the conversion begins". The page listed the timepoints only when the full read landed, and
the full read walks every file's subblock directory, one network round trip per file, while the timepoints
come from one file. Convert is available during the read, so a user who could not wait converted every
timepoint, and the list appeared while that run was going.

Every page case drives the real ``CziImportController`` and holds the full read (``read_source``) on an event,
which is what a slow share does to it; the timepoints must be listed, and tickable, while it is held.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pipeline.czi_read import forget_plate_metadata
from pipeline.czi_set import read_czi_set, read_timepoint_tokens
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi
from tests.test_czi_import_page import _settle

WELLS = ("C07", "A02", "H11")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    from datasets.czi_import.controller import CziImportController

    ctrl = CziImportController(on_open_in_analyze=lambda *_a: None, on_add_to_queue=lambda *_a: None)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def _split_plate(tmp_path, *, timepoints=3, master=False):
    folder = tmp_path / "Exp7_split"
    folder.mkdir(parents=True)
    if master:
        # ZEN's split-scene master: sorts first, describes the plate, holds no image tiles.
        write_czi(folder / "Exp7.czi", [], xml=plate_metadata_xml(channels=["DAPI", "GFP"]))
    for well in WELLS:
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=timepoints, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well}, channels=["DAPI", "GFP"]))
    return folder


def _spin_until(predicate, timeout_s=20.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    QApplication.processEvents()
    return predicate()


def _hold(monkeypatch, name):
    """Hold the page's call of ``view.<name>`` until the returned event is set."""
    from datasets.czi_import import view as view_module

    released = threading.Event()
    real = getattr(view_module, name)

    def held(*args, **kwargs):
        released.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(view_module, name, held)
    return released


# ── the compute half ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("master", [False, True], ids=["plain", "master-first"])
def test_the_early_read_lists_exactly_what_the_full_read_lists(tmp_path, master):
    folder = _split_plate(tmp_path, master=master)
    forget_plate_metadata()
    early, _note = read_timepoint_tokens(folder)
    assert early == ["t0", "t1", "t2"]
    assert early == read_czi_set(folder).timepoint_tokens


# ── the page ─────────────────────────────────────────────────────────────────
def test_the_timepoints_are_listed_and_tickable_while_the_rest_of_the_plate_is_read(controller, tmp_path,
                                                                                   monkeypatch):
    forget_plate_metadata()
    released = _hold(monkeypatch, "read_source")
    page = controller.page
    page._src_field.set_path(str(_split_plate(tmp_path)))
    page._on_source_changed()
    try:
        assert _spin_until(lambda: page._timepoint_pick.source_tokens() == ["t0", "t1", "t2"]), (
            page._timepoint_pick.source_tokens())
        assert page.is_scanning(), "the full read was held, so it cannot have landed"
        assert page._timepoint_pick.checkedLabels() == ["t0", "t1", "t2"]
        # Untick one while the plate is still being read: what a user does on a slow share.
        page._timepoint_pick._set_checked(1, False)
        assert page.params().timepoint_subset == ("t0", "t2")
    finally:
        released.set()
    _settle(page)
    # The full read landing keeps the choice rather than resetting it.
    assert page._timepoint_pick.checkedLabels() == ["t0", "t2"]
    assert page.params().timepoint_subset == ("t0", "t2")


def test_the_picker_says_it_is_reading_rather_than_that_nothing_is_ticked(controller, tmp_path, monkeypatch):
    forget_plate_metadata()
    released = _hold(monkeypatch, "source_timepoint_tokens")
    page = controller.page
    page._src_field.set_path(str(_split_plate(tmp_path)))
    page._on_source_changed()
    try:
        assert _spin_until(lambda: "Reading the timepoints" in page._readout.text()), page._readout.text()
        assert page._timepoint_pick.is_reading()
        assert page._timepoint_pick._button.text().startswith("Reading the timepoints…")
    finally:
        released.set()
    _settle(page)
    assert not page._timepoint_pick.is_reading()
    assert page._timepoint_pick._button.text().startswith("All 3 timepoints")


def test_a_failed_early_read_still_reads_the_plate(controller, tmp_path, monkeypatch):
    from datasets.czi_import import view as view_module

    def broken(*_a, **_k):
        raise OSError("the share blinked")

    monkeypatch.setattr(view_module, "source_timepoint_tokens", broken)
    page = controller.page
    page._src_field.set_path(str(_split_plate(tmp_path)))
    page._on_source_changed()
    _settle(page)
    assert page._timepoint_pick.source_tokens() == ["t0", "t1", "t2"]   # listed by the full read instead
    assert not page._timepoint_pick.is_reading()


def test_the_menu_itself_says_the_timepoints_are_still_being_read(controller, tmp_path, monkeypatch):
    """Asked for after the label fix: opening the dropdown while the timepoints are not known yet showed only
    All and None. The menu's first line now says why it is empty, and goes once the timepoints are listed."""
    from widgets import menu_exec

    shown = []
    monkeypatch.setattr(menu_exec, "exec_transient_menu",
                        lambda menu, _pos: shown.append([a.text() for a in menu.actions() if a.text()]))
    forget_plate_metadata()
    released = _hold(monkeypatch, "source_timepoint_tokens")
    page = controller.page
    page._src_field.set_path(str(_split_plate(tmp_path)))
    page._on_source_changed()
    try:
        assert _spin_until(page._timepoint_pick.is_reading)
        page._timepoint_pick._button.click()
        assert shown[-1][0].startswith("Reading the timepoints…"), shown[-1]
    finally:
        released.set()
    _settle(page)
    page._timepoint_pick._button.click()
    assert shown[-1] == ["All", "None"], shown[-1]     # the checkboxes are widget actions, without text
    assert not any(t.startswith("Reading") for t in shown[-1])
