"""A source scan reports what it is doing while it does it.

Reading a plate's headers takes long enough on a network share — up to
ninety-six files for a split export — that one unchanging sentence on the page
is indistinguishable from a page that has hung. That is the ordinary reading
of a static notice, and it was the reported symptom.

The reporting goes through ``core.worker_bridge``'s existing reporter, so the
scan thread emits and the GUI thread displays: no widget is touched off the
UI thread (CLAUDE.md §5).

Asserted at the **boundary** — what reaches the readout label — rather than on
the reporter calls behind it (§7.4 #1). A scan that computed a perfect count
and never wrote it to the label leaves exactly the frozen page this fixes.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication  # noqa: E402

pytest.importorskip("nd2")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _recording(page):
    """Every string the readout is given, in order."""
    seen: list[str] = []
    original = page._readout.setText

    def _spy(text):
        seen.append(text)
        return original(text)

    page._readout.setText = _spy
    return seen


def _settle(page, timeout_s: float = 25.0):
    deadline = time.monotonic() + timeout_s
    while page.is_scanning() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert not page.is_scanning(), "the scan never finished"


# ── ND2: one file, so named steps rather than a file count ───────────────────

def test_the_nd2_page_names_each_step_of_the_read(qapp, tmp_path):
    from datasets.nd2_import.controller import Nd2ImportController
    from tests._nd2_fixture import write_nd2

    src = write_nd2(tmp_path / "Plate7.nd2", n_pos=4, n_t=6, n_c=1,
                    well_names=["A01", "A01", "B01", "B01"],
                    times_ms=[i * 3_600_000.0 for i in range(6)])
    ctrl = Nd2ImportController()
    try:
        page = ctrl.page
        seen = _recording(page)
        page.set_source_path(str(src))
        _settle(page)

        # More than the one opening sentence, and the file is named — a step
        # that says which file it is working on cannot be mistaken for a
        # leftover from the previous source.
        assert len(seen) > 2, seen
        assert any("Plate7.nd2" in t for t in seen), seen
        # The one phase with a real total counts through it.
        counted = [t for t in seen if "of 6)" in t]
        assert len(counted) == 6, seen
        assert "1 of 6" in counted[0] and "6 of 6" in counted[-1]
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_the_nd2_readout_ends_on_the_layout_not_on_a_progress_line(qapp,
                                                                   tmp_path):
    """The count is transient. A page left saying "6 of 6" would be a second
    kind of stuck — one that has finished and does not say so."""
    from datasets.nd2_import.controller import Nd2ImportController
    from tests._nd2_fixture import write_nd2

    src = write_nd2(tmp_path / "P.nd2", n_pos=2, n_t=3, n_c=1,
                    times_ms=[0.0, 3_600_000.0, 7_200_000.0])
    ctrl = Nd2ImportController()
    try:
        page = ctrl.page
        page.set_source_path(str(src))
        _settle(page)

        final = page._readout.text()
        assert "Reading" not in final, final
        assert "position(s)" in final, final
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


# ── CZI: a split export is many files, so the count is the files ─────────────

def _split(tmp_path, wells=("A01", "A02", "B01", "B02", "C01", "C02")):
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    folder = tmp_path / "Exp_split"
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        write_czi(folder / f"Exp_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI"]))
    return folder


def test_the_czi_page_counts_the_files_through_both_stages(qapp, tmp_path):
    """Both reads walk the same file list, and both are slow enough to matter:
    the peek is a header each, the full read an index each."""
    from datasets.czi_import.controller import CziImportController

    folder = _split(tmp_path)
    ctrl = CziImportController()
    try:
        page = ctrl.page
        seen = _recording(page)
        page._src_field.set_path(str(folder))
        page._on_source_changed()
        _settle(page)

        headers = [t for t in seen if t.startswith("Reading the file headers (")]
        rest = [t for t in seen if t.startswith("Reading the rest of the plate (")]
        assert len(headers) == 6, seen
        assert len(rest) == 6, seen
        assert "1 of 6" in headers[0] and "6 of 6" in headers[-1]
        assert "1 of 6" in rest[0] and "6 of 6" in rest[-1]
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_single_czi_file_is_not_given_a_count_of_one(qapp, tmp_path):
    """"(1 of 1)" is noise: it says nothing a plain sentence does not, and it
    invites the reader to wait for a second number that never comes."""
    from datasets.czi_import.controller import CziImportController
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    src = write_czi(tmp_path / "One.czi",
                    plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
                    xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                                           channels=["DAPI"]))
    ctrl = CziImportController()
    try:
        page = ctrl.page
        seen = _recording(page)
        page._src_field.set_path(str(src))
        page._on_source_changed()
        _settle(page)

        assert not any("of 1)" in t for t in seen), seen
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_stale_scan_s_count_never_reaches_the_readout(qapp, tmp_path,
                                                        monkeypatch):
    """The progress callbacks are guarded on the scan generation like every
    other one. Without that, a slow scan the user has already moved off would
    go on counting files over the readout of the source they picked instead.
    """
    import datasets.nd2_import.view as view_module

    from datasets.nd2_import.controller import Nd2ImportController
    from tests._nd2_fixture import write_nd2

    first = write_nd2(tmp_path / "slow.nd2", n_pos=2, n_t=4, n_c=1,
                      times_ms=[i * 3_600_000.0 for i in range(4)])
    second = write_nd2(tmp_path / "quick.nd2", n_pos=2, n_t=1, n_c=1)
    real = view_module.read_source

    def _slow_for_the_first(src, *args, **kwargs):
        if str(src) == str(first):
            time.sleep(0.6)
        return real(src, *args, **kwargs)

    monkeypatch.setattr(view_module, "read_source", _slow_for_the_first)
    ctrl = Nd2ImportController()
    try:
        page = ctrl.page
        page.set_source_path(str(first))
        page.set_source_path(str(second))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if set(page._scan_bridges) <= page._scan_delivered:
                break
            time.sleep(0.002)
        QApplication.processEvents()

        final = page._readout.text()
        assert "Reading" not in final, final
        assert "1 timepoint(s)" in final, f"the stale scan won: {final}"
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()
