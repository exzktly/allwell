"""The behaviour behind ``tests/test_one_csv_save_dialog.py``'s structural rule.

The guard says nothing outside ``core/csv_export.py`` opens its own CSV
Save-As. This says what a user gets from that: whichever CSV export they press,
the picker starts beside the open dataset, suggests a filename, and offers the
same filter — three things the four private dialogs had drifted apart on.

Driven through the exporters rather than by reading their imports, because an
import proves the module was reached and not that the dialog was.
"""

from __future__ import annotations

from types import SimpleNamespace


class _Recorder:
    """Stands in for the dialog, and records what it was asked for."""

    def __init__(self):
        self.calls = []

    def __call__(self, ctx, title, default_name):
        self.calls.append((title, default_name))
        return ""            # cancelled: the exporter stops, nothing is written


def _ctx(tmp_path):
    return SimpleNamespace(
        state=SimpleNamespace(dataset=SimpleNamespace(data_dir=str(tmp_path))),
        window=None,
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
    )


def test_the_spatial_summary_export_asks_through_the_shared_dialog(
        tmp_path, monkeypatch):
    from review.core import csv_export
    from review.features.spatial import controller as spc

    recorder = _Recorder()
    monkeypatch.setattr(csv_export, "ask_save_csv", recorder)

    spc._write_summary_csv(_ctx(tmp_path), [{"well": "A01", "n": 3}])

    assert recorder.calls == [("Export Spatial Summary CSV",
                               "spatial_summary.csv")]


def test_the_shared_dialog_starts_beside_the_open_dataset(tmp_path, monkeypatch):
    """The suggested path, not just the name.

    One of the private dialogs passed the dataset *directory* as the initial
    path, so it opened in the right place with the name field empty; another
    resolved the directory off an attribute that does not exist and opened in
    the process working directory. Both look like a picker doing something
    reasonable until you compare them.
    """
    from PySide6.QtWidgets import QFileDialog
    from review.core import csv_export

    seen = {}

    def _fake(parent, title, initial, filter_):
        seen.update(initial=initial, filter=filter_)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_fake))
    csv_export.ask_save_csv(_ctx(tmp_path), "Export", "thing.csv")

    assert seen["initial"] == str(tmp_path / "thing.csv")
    assert seen["filter"] == "CSV files (*.csv);;All files (*)"


def test_the_all_files_filter_is_not_the_dos_era_spelling():
    """``(*.*)`` is honoured literally on some platforms, where it hides every
    extensionless name from a filter whose own label says it shows everything.
    The export feature's copy used it and this one did not."""
    from review.core.csv_export import _CSV_FILTER

    assert "(*.*)" not in _CSV_FILTER
