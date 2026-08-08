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


def test_scan_keeps_a_phase_channel_out_of_the_segmentation_slot(qapp, tmp_path):
    """The reported bug, end to end through the auto-fill.

    Channels come back in sorted-filename order whatever order they were
    acquired in, so DIC leads. It must be designated Phase, keep a channel row
    to carry that designation, and never take the segmentation slot.
    """
    from analyze_tab import AnalyzeTab

    _write_loose(tmp_path / "ds" / "in", channels=("mCherry", "GFP", "DIC"))
    tab = AnalyzeTab()
    tab._autofill_from_input_scan(str(tmp_path / "ds"))

    assert tab._nuclear_token_edit.text() != "DIC"
    assert "DIC" in tab._fluor_tokens_list()
    assert tab._phase_token() == "DIC"
    # …and the form the scan produced is one the pipeline will accept.
    assert tab._segmentation_validation_errors() == []


def test_scan_without_a_channel_slot_keeps_the_typed_channel_list(qapp, tmp_path):
    """A scan that recognised no channel slot must not reset the channel rows.

    ``ChannelTokens.as_pipeline_info()`` always carries a ``fluor_tokens`` key,
    and ``_set_fluor_rows`` reads an empty list as "no channels" and collapses to
    the single default row — so an unrecognised layout used to wipe a list the
    operator had typed by hand.
    """
    from analyze_tab import AnalyzeTab

    d = tmp_path / "ds" / "in"
    d.mkdir(parents=True)
    # A well slot and a timepoint slot, but nothing that reads as a channel.
    for w in ("A01", "A02", "B01"):
        for t in ("t0", "t1"):
            (d / f"exp_{w}_{t}.tif").write_bytes(b"")

    tab = AnalyzeTab()
    tab._set_fluor_rows(["GFP", "DIC"], smfish=[], phase="DIC")
    tab._nuclear_token_edit.setText("RFP")
    tab._autofill_from_input_scan(str(tmp_path / "ds"))

    assert tab._fluor_tokens_list() == ["GFP", "DIC"]
    assert tab._phase_token() == "DIC"
    assert tab._nuclear_token_edit.text() == "RFP"


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
