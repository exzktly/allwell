"""A CZI conversion says what it is doing while it writes no planes, and does not copy the plate at the end.

Reported: every archive was written, the plate showed every well done, nothing in the log or the status line said
otherwise — and the folder held ``acquisition.pending.json`` and no ``acquisition.json``, so a nanowell run refused
it. The conversion had not finished. After the last plane it reads every converted tile's metadata to build the
record and, until this change, rewrote every archive in full to rename its members from a placeholder
(``…_t0.tif``) to the timepoint's final name; all of it silent. Closing the app in that window leaves exactly the
reported folder.

Pinned here: each metadata pass logs a line as it starts and ends and drives the status line between
(``czi_acquisition.StageReporter``); members are written under their final names, so a fresh conversion rewrites no
archive; and the Convert CZI page shows the stage lines and returns to the image count when the run ends.
"""

from __future__ import annotations

import pytest

import pipeline.czi_acquisition as acquisition
from pipeline.czi_acquisition import STAGE_ARCHIVES, STAGE_TILES, StageReporter
from pipeline.czi_explode import explode_czi
from tests.test_czi_acquisition_record import _source


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_stage_logs_its_start_and_end_and_throttles_the_status_line_between():
    logs, status, clock = [], [], _Clock()
    report = StageReporter(logs.append, status.append, interval=0.25, clock=clock)
    report(STAGE_TILES, 0, 1000)
    for done in range(1, 1000):
        clock.now = done * 0.001                       # a tile a millisecond: one second for the stage
        report(STAGE_TILES, done, 1000)
    clock.now = 1.5
    report(STAGE_TILES, 1000, 1000)
    report(STAGE_TILES, 1000, 1000)                    # a repeated end is not a second end
    assert logs == [
        "[czi] Writing the acquisition record: reading the metadata of every converted tile (1,000 tile(s))…\n",
        "[czi] Writing the acquisition record — done in 1.5s.\n"]
    assert status[0] == "Writing the acquisition record: 0 of 1,000 tile(s)…"
    assert status[1:5] == [f"Writing the acquisition record: {n} of 1,000 tile(s)…" for n in (250, 500, 750)] + [
        "Writing the acquisition record: 1,000 of 1,000 tile(s)"]
    assert len(status) == 5


def test_an_empty_stage_starts_and_ends_at_once():
    logs, status = [], []
    report = StageReporter(logs.append, status.append, clock=_Clock())
    report(STAGE_ARCHIVES, 0, 0)
    report(STAGE_ARCHIVES, 0, 0)
    assert len(logs) == 2 and logs[1].startswith("[czi] Writing the acquisition record: checking archives — done")
    assert status[-1] == "Writing the acquisition record: checking archives: 0 of 0 archive(s)"


def test_a_conversion_reports_its_metadata_passes_in_order_and_ends_with_the_record_written(tmp_path):
    source = _source(tmp_path)
    dest = tmp_path / "dataset" / "in"
    events = []
    explode_czi(source, dest, workers=1, log_fn=lambda line: events.append(("log", line.strip())),
                progress_fn=lambda done, total: events.append(("planes", done, total)),
                status_fn=lambda text: events.append(("status", text)))
    logs = [e[1] for e in events if e[0] == "log"]
    starts = [i for i, line in enumerate(logs) if line.endswith("…")]
    assert [logs[i].rsplit(" (", 1)[0] for i in starts] == [
        "[czi] Reading the acquisition time of every tile, to name the timepoints, before any archive is written",
        "[czi] All archives written. Writing the acquisition record (in/acquisition.json), which the dataset cannot "
        "be analysed without: checking the archives",
        "[czi] Writing the acquisition record: reading the metadata of every converted tile",
        "[czi] Writing the acquisition record: checking every archive's image names, and rewriting any an earlier "
        "conversion named under another rule"]
    assert logs[-1].startswith("[czi] Wrote 8 image(s) for 2 well(s)")
    # The record stage begins only once the last plane is written, and the status line says so meanwhile.
    last_plane = max(i for i, e in enumerate(events) if e[0] == "planes")
    record_start = next(i for i, e in enumerate(events) if e[0] == "log" and "All archives written" in e[1])
    assert last_plane < record_start
    assert ("status", "Writing the acquisition record: 8 of 8 tile(s)") in events
    assert (dest / "acquisition.json").exists() and not (dest / "acquisition.pending.json").exists()


def test_a_fresh_conversion_writes_final_names_and_rewrites_no_archive(tmp_path, monkeypatch):
    rewrites = []
    real = acquisition.atomic_zip

    def counting(path, *args, **kwargs):
        rewrites.append(path.name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(acquisition, "atomic_zip", counting)
    source = _source(tmp_path)
    explode_czi(source, tmp_path / "dataset" / "in", workers=1)
    assert rewrites == []
    import zipfile

    with zipfile.ZipFile(tmp_path / "dataset" / "in" / "A01.zip") as archive:
        names = sorted(info.filename for info in archive.infolist())
    assert names[0].endswith("_A01_f0_0h.tif") and not any("_t0.tif" in n or "_t1.tif" in n for n in names)


# ── the Convert CZI page ─────────────────────────────────────────────────────

pytest.importorskip("PySide6")


def test_the_page_shows_the_record_stages_and_ends_on_the_image_count(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from tests.test_czi_import_page import _named_czi, _set_source

    QApplication.instance() or QApplication([])
    from datasets.czi_import.controller import CziImportController

    controller = CziImportController(on_open_in_analyze=lambda *_a: None, on_add_to_queue=lambda *_a: None)
    try:
        page = controller.page
        shown = []
        real = page.set_stage_status
        monkeypatch.setattr(page, "set_stage_status", lambda text: (shown.append(text), real(text)))
        _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)
        controller._launch()
        controller._worker.wait(30_000)
        QApplication.processEvents()
        assert "Writing the acquisition record: 2 of 2 tile(s)" in shown
        assert shown[0] == "Reading acquisition times: 0 of 2 tile(s)…"
        assert page._dashboard._status.text() == "4 of 4 image(s)"
        assert any("All archives written. Writing the acquisition record" in line
                   for line in page.log_text().splitlines())
    finally:
        controller.shutdown()
        controller.page.deleteLater()
