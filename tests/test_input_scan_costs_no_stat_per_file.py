"""Picking an input folder must not cost a metadata call per file in it.

The Analyze pane answers three questions the moment a folder is chosen — is
this a packed OME-TIFF, what layout is it, and what filename schema do its
names follow — and each of them reads the directory. Written the obvious way,
``[p for p in folder.iterdir() if p.is_file()]``, each read is also one
``os.stat`` per entry, and the three of them ran twice per click.

On macOS and Linux that is invisible: ``stat()`` is one syscall out of the
kernel's name cache. On Windows CPython implements ``os.stat`` by opening a
handle to the file, so on a network share, a VPN-ed mount or a cloud-sync root
every one of those is a round trip on the wire. A plate of 5,800 loose images
cost about 17,000 of them, and picking the folder sat for twenty minutes at
under one percent CPU — no error, no progress, nothing computing anything.

So the property this file pins is not a duration, which would measure the
machine it runs on. It is that **the cost does not grow with the number of
files**: the same folder holding four times as many images must cost the same
number of metadata calls, because every one of them is answered out of the
directory listing that had to happen anyway (:mod:`util.dir_scan`).

The counter is proved live in each test that uses it — a test that counts
nothing because it patched the wrong function passes just as happily as one
whose code is fast, and that is the failure this file would be reporting.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plate(root: Path, count: int) -> Path:
    """A dataset folder holding *count* loose images, in ``<root>/in``."""
    in_dir = root / "in"
    in_dir.mkdir(parents=True)
    written = 0
    for well in WELLS:
        for fov in range(1, 9):
            for channel in ("DAPI", "GFP"):
                if written >= count:
                    return root
                (in_dir / f"exp_{channel}_{well}_f{fov}_t0.tif").write_bytes(b"II*\0")
                written += 1
    raise AssertionError(f"the name generator cannot reach {count} files")


class _StatCounter:
    """Counts every ``os.stat`` made while it is installed.

    Installed over ``os.stat`` itself rather than over ``pathlib``: that is the
    call whose cost this is about, and it is the one both ``Path.is_file`` and
    ``DirEntry.is_file`` reach when they need it. ``os.scandir`` is deliberately
    *not* counted — one enumeration of a directory is the irreducible cost of
    reading it, and it is what the fix spends instead.
    """

    def __init__(self, monkeypatch) -> None:
        self._real = os.stat
        self.count = 0
        monkeypatch.setattr(os, "stat", self._counting)

    def _counting(self, *args, **kwargs):
        self.count += 1
        return self._real(*args, **kwargs)

    def prove_live(self, files: list[Path]) -> None:
        """Fail unless stat-ing *files* one by one is actually seen.

        Without this the whole file fails open: if the patch stopped reaching
        the code under test, every assertion below would be comparing zero
        against zero and would pass with nothing measured.
        """
        before = self.count
        for path in files:
            path.is_file()
        seen = self.count - before
        assert seen >= len(files), (
            f"the stat counter is not measuring anything: {len(files)} explicit "
            f"is_file() calls registered {seen} os.stat calls"
        )
        self.count = before


def _browse(tab, folder: Path, monkeypatch) -> None:
    """Drive the real Browse… handler, with only the file dialog stubbed."""
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(folder)),
    )
    tab._browse_input()


def test_picking_a_folder_does_not_stat_every_file_in_it(qapp, tmp_path, monkeypatch):
    """The same folder with four times the images costs the same metadata calls.

    Two sizes rather than one absolute number: the constant is whatever the
    layout decision genuinely needs (is the selected path a directory, is there
    an ``in/`` beside it) and pinning it exactly would be pinning an
    implementation. The *slope* is the defect — one stat per file — and it is
    what a per-file listing cannot hide.
    """
    from analyze.tab import AnalyzeTab

    small = _plate(tmp_path / "small", 120)
    large = _plate(tmp_path / "large", 480)

    counter = _StatCounter(monkeypatch)
    counter.prove_live(sorted((small / "in").iterdir()))

    tab_small = AnalyzeTab()
    counter.count = 0
    _browse(tab_small, small, monkeypatch)
    small_stats = counter.count

    tab_large = AnalyzeTab()
    counter.count = 0
    _browse(tab_large, large, monkeypatch)
    large_stats = counter.count

    assert large_stats - small_stats <= 4, (
        f"picking a folder costs one metadata call per file: 120 images took "
        f"{small_stats} os.stat calls, 480 took {large_stats}"
    )
    # And the work it was doing is still done — a readout that got cheap by
    # not looking at the folder would satisfy the assertion above.
    assert "480" in tab_large._layout_lbl.text()
    assert tab_large._schema_picker.schema() == SCHEMA
    assert tab_large._nuclear_token_edit.text() == "DAPI"


def test_the_readout_is_computed_once_per_pick_and_with_the_inferred_schema(
        qapp, tmp_path, monkeypatch):
    """One layout plan per click, and it knows the schema the scan just found.

    The old flow computed it twice: ``textChanged`` fired as soon as the path
    was set, walking the new folder against the *previous* folder's schema, and
    the folder scan then applied the inferred schema and walked it again. The
    first of those is both wasted and wrong, and the count is what says so —
    asserting only the final schema would pass against the two-walk version.
    """
    import analyze.tab as tab_mod
    from analyze.tab import AnalyzeTab

    folder = _plate(tmp_path / "ds", 24)
    calls: list[tuple[str, str]] = []
    real = tab_mod.plan_input_layout

    def spy(raw, *, filename_schema="", filename_sep="_"):
        calls.append((filename_schema, filename_sep))
        return real(raw, filename_schema=filename_schema, filename_sep=filename_sep)

    monkeypatch.setattr(tab_mod, "plan_input_layout", spy)

    tab = AnalyzeTab()
    _browse(tab, folder, monkeypatch)

    assert calls == [(SCHEMA, "_")], (
        "the layout plan should be computed exactly once per pick, from the "
        f"schema the folder scan inferred; got {calls}"
    )


def test_a_folder_the_scan_cannot_read_still_updates_the_readout(
        qapp, tmp_path, monkeypatch):
    """The held refresh is released even when the scan bails out early.

    ``_autofill_from_input_scan`` returns without applying anything when the
    filenames do not parse, so the one recomputation at the end of
    ``_browse_input`` is the only one there is. Losing it would leave the
    readout describing the folder picked before this one — which is worse than
    the cost it was introduced to save.
    """
    from analyze.tab import AnalyzeTab

    first = _plate(tmp_path / "first", 24)
    unparseable = tmp_path / "second" / "in"
    unparseable.mkdir(parents=True)
    for i in range(7):
        (unparseable / f"{i}.tif").write_bytes(b"II*\0")

    tab = AnalyzeTab()
    _browse(tab, first, monkeypatch)
    assert "24" in tab._layout_lbl.text()

    _browse(tab, tmp_path / "second", monkeypatch)
    assert "7" in tab._layout_lbl.text(), (
        "the readout still describes the previously picked folder: "
        f"{tab._layout_lbl.text()!r}"
    )
    assert str(tmp_path / "second" / "out") == tab._output_lbl.text()
