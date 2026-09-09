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
    from analyze.tab import AnalyzeTab

    _write_loose(tmp_path / "ds" / "in")
    tab = AnalyzeTab()
    tab._autofill_from_input_scan(str(tmp_path / "ds"))

    # Schema + separator inferred from the filenames…
    assert tab._schema_picker.separator() == "_"
    assert tab._schema_picker.schema() == "experiment:channel:well:fov:timepoint"
    # …and channel roles seeded (DAPI nuclear, GFP fluorescent).
    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._fluor_tokens_list() == ["GFP"]


def test_scan_keeps_a_phase_channel_out_of_the_segmentation_slot(qapp, tmp_path):
    """The reported bug, end to end through the auto-fill.

    Channels come back in sorted-filename order whatever order they were
    acquired in, so DIC leads. It must be designated Phase, keep a channel row
    to carry that designation, and never take the segmentation slot.
    """
    from analyze.tab import AnalyzeTab

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
    from analyze.tab import AnalyzeTab

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
    from analyze.tab import AnalyzeTab

    d = tmp_path / "notadataset"
    d.mkdir()
    (d / "random.txt").write_text("x")
    tab = AnalyzeTab()
    before_schema = tab._schema_picker.schema()
    before_nuc = tab._nuclear_token_edit.text()
    tab._autofill_from_input_scan(str(d))
    assert tab._schema_picker.schema() == before_schema
    assert tab._nuclear_token_edit.text() == before_nuc


def test_the_scan_applies_the_schema_it_infers_not_only_the_channels(qapp, tmp_path):
    """A plate in a layout that is *not* the form's default.

    The three tests above all write ``exp_<channel>_<well>_f0_t0.tif``, which is
    the schema a fresh form already shows, so asserting the picker holds it
    afterwards is satisfied by a scan that applies nothing at all — and one did.
    ``_autofill_from_input_scan`` handed ``_apply_pipeline_info`` the flattened
    argparse names ``filename_schema`` / ``filename_sep`` while ``_pinfo_flat``
    reads the sidecar's ``schema`` / ``separator``, so those two keys were
    dropped on the way in. The channel keys came through because they are
    already sidecar-shaped, which is what made the gap invisible: the status
    line named the channels it found, and the schema quietly stayed default.

    So this fixture differs from the default in both the separator and the slot
    layout, and the first two assertions are what say the fixture is doing its
    job.
    """
    from analyze.tab import AnalyzeTab

    in_dir = tmp_path / "ds" / "in"
    in_dir.mkdir(parents=True)
    for well in ("A01", "A02", "B01"):
        for fov in (1, 2):
            for channel in ("DAPI", "GFP"):
                (in_dir / f"{channel}-{well}-f{fov}.tif").write_bytes(b"II*\0")

    tab = AnalyzeTab()
    assert tab._schema_picker.schema() != "channel:well:fov"
    assert tab._schema_picker.separator() != "-"

    tab._autofill_from_input_scan(str(tmp_path / "ds"))

    assert tab._schema_picker.separator() == "-"
    assert tab._schema_picker.schema() == "channel:well:fov"
    # …and the schema the run would actually be given, not just the widget.
    assert tab._build_schema_arg() == "channel:well:fov"
    assert tab._filename_sep() == "-"
    assert tab._nuclear_token_edit.text() == "DAPI"
