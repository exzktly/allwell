"""What the Zip Loose Folder page costs to *describe* a folder, before any run.

Picking a folder of ~10^5 loose TIFs on a network share took twenty minutes to
show the readout, and ticking **Group in place** took another twenty. Both walk
the folder on the GUI thread, and on a share every ``stat`` is a round trip.
Three causes, each pinned here by what it asks the filesystem or the scanner:

* ``scan_folder`` called ``os.path.isfile`` on every matched file and
  ``Path.is_dir`` on every top-level entry -- two ``stat`` calls per file. The
  preview now asks no entry anything; the run (``group_files_by_well_counting``)
  still drops a broken symlink and reports the count in its log.
* ``_on_in_place_toggled`` called ``refresh_readout()`` with no scan to hand it,
  which walked the folder again for an answer the page already held.
* **Detect from filenames** wrote the separator and then the schema, and each
  write emitted ``changed``, i.e. a scan.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.import_loose import import_loose_compute as compute  # noqa: E402
from datasets.import_loose import view as view_mod  # noqa: E402

_SCHEMA = "experiment:channel:well:fov:timepoint"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plate(folder, *, wells=("A01", "A02", "B01"), fovs=3, channels=("GFP", "DAPI")):
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for well in wells:
        for fov in range(fovs):
            for chan in channels:
                (folder / f"Exp_{chan}_{well}_F{fov:03d}_1h.tif").write_bytes(b"")
                n += 1
    return n


def _count_calls(monkeypatch, *names):
    calls = {n: 0 for n in names}
    for name in names:
        real = getattr(os, name)

        def wrapped(*a, _real=real, _name=name, **k):
            calls[_name] += 1
            return _real(*a, **k)

        monkeypatch.setattr(os, name, wrapped)
    return calls


def test_a_scan_stats_no_file_in_the_folder(tmp_path, monkeypatch):
    n = _plate(tmp_path / "plate")
    calls = _count_calls(monkeypatch, "stat", "lstat")
    preview = compute.scan_folder(
        tmp_path / "plate", filename_schema=_SCHEMA, filename_sep="_")
    assert preview.matched == n
    assert calls == {"stat": 0, "lstat": 0}


def test_a_scan_asks_no_entry_what_kind_of_file_it_is(tmp_path, monkeypatch):
    """Zero ``stat`` on a local disk proves little: there the listing carries
    each entry's type and ``is_dir`` / ``is_file`` are free. On a share that
    reports no types each is a ``stat``. So count the *questions*."""
    n = _plate(tmp_path / "plate")
    asked = {"is_dir": 0, "is_file": 0, "is_symlink": 0}
    real_scandir = os.scandir

    class _Entry:
        def __init__(self, entry):
            self._e = entry
            self.name, self.path = entry.name, entry.path

        def __getattr__(self, attr):
            if attr in asked:
                asked[attr] += 1
            return getattr(self._e, attr)

    class _Scan:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

        def __iter__(self):
            return (_Entry(e) for e in self._it)

    monkeypatch.setattr(os, "scandir", lambda p: _Scan(real_scandir(p)))
    preview = compute.scan_folder(
        tmp_path / "plate", filename_schema=_SCHEMA, filename_sep="_")
    assert preview.matched == n
    assert asked == {"is_dir": 0, "is_file": 0, "is_symlink": 0}


def test_the_run_still_drops_and_counts_a_dangling_symlink(tmp_path):
    folder = tmp_path / "plate"
    n = _plate(folder)
    os.symlink(tmp_path / "nowhere.tif", folder / "Exp_GFP_C03_F000_1h.tif")
    by_well, unreadable = compute.group_files_by_well_counting(
        folder, filename_schema=_SCHEMA, filename_sep="_")
    assert unreadable == 1
    assert sum(len(v) for v in by_well.values()) == n
    assert "C03" not in by_well


def test_existing_well_folders_still_found_among_loose_files(tmp_path):
    folder = tmp_path / "plate"
    _plate(folder)
    (folder / "C03").mkdir()
    (folder / "C03" / "x.tif").write_bytes(b"")
    (folder / "D04").mkdir()                    # a well folder with no TIFs
    (folder / "notes").mkdir()
    (folder / "notes" / "x.tif").write_bytes(b"")  # TIFs, but not a well name
    assert compute.existing_well_folders(folder) == ("C03",)


@pytest.fixture
def scans(monkeypatch):
    seen = []
    real = view_mod.scan_folder

    def counting(*a, **k):
        seen.append(a)
        return real(*a, **k)

    monkeypatch.setattr(view_mod, "scan_folder", counting)
    return seen


def _loaded_page(qapp, tmp_path):
    folder = tmp_path / "plate"
    _plate(folder)
    page = view_mod.ImportLoosePage()
    page.set_filename_sep("_")
    page.set_filename_schema(_SCHEMA)
    page.set_source_path(str(folder))
    page._on_source_changed()
    return page


def test_ticking_group_in_place_does_not_walk_the_folder_again(qapp, tmp_path, scans):
    page = _loaded_page(qapp, tmp_path)
    scans.clear()
    page.set_in_place(True)
    page.set_in_place(False)
    assert scans == []
    # ...and the readout still says what the folder holds.
    assert "3 well(s)" in page._readout.text()


def test_changing_the_schema_does_scan_again(qapp, tmp_path, scans):
    page = _loaded_page(qapp, tmp_path)
    scans.clear()
    page.set_filename_sep("-")
    assert len(scans) == 1


def test_a_run_starting_drops_the_cached_scan(qapp, tmp_path, scans):
    page = _loaded_page(qapp, tmp_path)
    page.set_running(True)
    page.set_running(False)
    scans.clear()
    page.refresh_readout()
    assert len(scans) == 1


def test_detect_from_filenames_walks_the_folder_once(qapp, tmp_path, scans):
    page = _loaded_page(qapp, tmp_path)
    # Start from a schema that differs in both fields, so detection changes both.
    page.set_filename_sep("-")
    page.set_filename_schema("well:fov")
    scans.clear()
    page._detect_schema()
    assert page.filename_sep() == "_"
    assert page.filename_schema() == _SCHEMA
    assert len(scans) == 1


def test_a_new_destination_run_reports_the_broken_symlink_it_skipped(tmp_path):
    from datasets.import_loose.apply import convert_to_new_destination
    folder = tmp_path / "plate"
    _plate(folder)
    os.symlink(tmp_path / "nowhere.tif", folder / "Exp_GFP_C03_F000_1h.tif")
    lines: list[str] = []
    convert_to_new_destination(
        folder, tmp_path / "out", filename_schema=_SCHEMA, filename_sep="_",
        log=lines.append)
    assert any("1 unreadable file(s) (broken symlinks) skipped" in ln for ln in lines)
