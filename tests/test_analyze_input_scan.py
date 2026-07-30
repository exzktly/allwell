"""Analyze pre-fills its schema + channel fields by scanning the input folder."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_loose(in_dir, channels=("DAPI", "GFP")):
    in_dir.mkdir(parents=True)
    for w in ("A01", "A02"):
        for c in channels:
            (in_dir / f"exp_{c}_{w}_f0_t0.tif").write_bytes(b"")


def test_browse_scan_prefills_schema_and_channels(qapp, tmp_path):
    from analyze_tab import AnalyzeTab

    _write_loose(tmp_path / "ds" / "in")
    tab = AnalyzeTab()
    tab._autofill_from_input_scan(str(tmp_path / "ds"))

    # Schema + separator inferred from the filenames…
    assert tab._filename_sep_edit.text() == "_"
    assert tab._schema_str_edit.text() == "experiment:channel:well:fov:timepoint"
    # …and channel roles seeded (DAPI nuclear, GFP fluorescent).
    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._fluor_tokens_list() == ["GFP"]


def test_scan_leaves_form_untouched_when_unparseable(qapp, tmp_path):
    from analyze_tab import AnalyzeTab

    d = tmp_path / "notadataset"
    d.mkdir()
    (d / "random.txt").write_text("x")
    tab = AnalyzeTab()
    before_schema = tab._schema_str_edit.text()
    before_nuc = tab._nuclear_token_edit.text()
    tab._autofill_from_input_scan(str(d))
    assert tab._schema_str_edit.text() == before_schema
    assert tab._nuclear_token_edit.text() == before_nuc
