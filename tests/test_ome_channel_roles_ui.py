"""The Analyze tab's packed-OME-TIFF controls (headless / offscreen Qt).

Covers the channel-role picker (which drives the existing Channel-Tokens widgets)
and the explicit FOVs-per-well field, including how both feed validation and the
run-options dict.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from analyze_tab import AnalyzeTab
    return AnalyzeTab()


def _write_ome(path, *, positions, channel_names):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.full((1, len(channel_names), 1, 5, 5), pos, np.uint16)
            tw.write(
                arr,
                metadata={"axes": "TCZYX", "Channel": {"Name": list(channel_names)}},
            )
    return path


def _set_role(tab, name, value):
    for n, combo in tab._ome_role_combos:
        if n == name:
            combo.setCurrentIndex(
                [combo.itemData(i) for i in range(combo.count())].index(value))
            return
    raise AssertionError(f"no role combo for channel {name!r}")


@pytest.fixture
def ome_tab(tab, tmp_path):
    """A tab pointed at a 6-position, 3-channel (BF/DAPI/GFP) OME-TIFF."""
    p = _write_ome(
        tmp_path / "plate.ome.tif", positions=6,
        channel_names=("BF", "DAPI", "GFP"),
    )
    tab._input_edit.setText(str(p))
    return tab


def test_roles_populate_and_default_to_fluorescence(ome_tab):
    """Every channel defaults to Fluorescence — never Segmentation.

    Auto-assigning channel 0 to Segmentation is the bug this replaces (channel 0
    is often brightfield).
    """
    assert [n for n, _ in ome_tab._ome_role_combos] == ["BF", "DAPI", "GFP"]
    assert all(c.currentData() == "fluor" for _, c in ome_tab._ome_role_combos)
    assert ome_tab._nuclear_token_edit.text() == ""


def test_manual_channel_widgets_hidden_for_ome(ome_tab):
    parent = ome_tab._fluor_frame.parentWidget()
    assert not ome_tab._fluor_frame.isVisibleTo(parent)
    # The segmentation-channel field stays visible but read-only (it reflects the
    # role-picked channel; the picker is the source of truth).
    assert ome_tab._nuclear_token_edit.isReadOnly()


def test_roles_drive_channel_token_widgets(ome_tab):
    _set_role(ome_tab, "DAPI", "segmentation")
    _set_role(ome_tab, "BF", "phase")
    assert ome_tab._nuclear_token_edit.text() == "DAPI"
    assert set(ome_tab._fluor_tokens_list()) == {"GFP", "BF"}
    assert ome_tab._phase_token() == "BF"
    assert ome_tab._smfish_tokens_list() == []


def test_validation_requires_exactly_one_segmentation(ome_tab):
    # Default: no segmentation channel assigned.
    assert "Segmentation" in (ome_tab._ome_validation_error() or "")
    ome_tab._ome_rows_edit.setText("1")
    ome_tab._ome_cols_edit.setText("3")
    _set_role(ome_tab, "DAPI", "segmentation")
    assert ome_tab._ome_validation_error() is None


def test_explicit_fovs_mismatch_blocks_run(ome_tab):
    _set_role(ome_tab, "DAPI", "segmentation")
    ome_tab._ome_rows_edit.setText("1")
    ome_tab._ome_cols_edit.setText("3")
    ome_tab._ome_fovs_edit.setText("3")  # 3 wells × 3 = 9 ≠ 6 positions
    assert "stage position" in (ome_tab._ome_validation_error() or "")
    ome_tab._ome_fovs_edit.setText("2")
    assert ome_tab._ome_validation_error() is None


def _write_packed(path, *, n_frames, channel_names):
    y = x = 6
    data = np.zeros((n_frames, len(channel_names), y, x), np.uint16)
    tifffile.imwrite(
        str(path), data, ome=True, photometric="minisblack",
        metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}},
    )
    return path


def test_frame_order_row_hidden_for_multi_image(ome_tab):
    # The BF/DAPI/GFP fixture is multi-<Image> (one series per position).
    ome_tab._refresh_ome_readout()
    row = ome_tab._ome_frame_order_row
    assert not row.isVisibleTo(row.parentWidget())


def test_frame_order_row_shown_for_single_image(tab, tmp_path):
    p = _write_packed(tmp_path / "packed.ome.tif", n_frames=12,
                      channel_names=("BF", "DAPI", "GFP"))
    tab._input_edit.setText(str(p))
    row = tab._ome_frame_order_row
    assert row.isVisibleTo(row.parentWidget())
    assert tab._ome_read_layout(tab._ome_detected_file).single_image is True


def test_single_image_splits_frames_into_positions_and_timepoints(tab, tmp_path):
    p = _write_packed(tmp_path / "packed.ome.tif", n_frames=12,
                      channel_names=("BF", "DAPI", "GFP"))
    tab._input_edit.setText(str(p))
    _set_role(tab, "DAPI", "segmentation")
    tab._ome_rows_edit.setText("1")
    tab._ome_cols_edit.setText("3")
    tab._ome_fovs_edit.setText("2")
    # single-timepoint would need 3×2 = 6 frames, but there are 12 → error.
    tab._ome_frame_order_cb.setCurrentIndex(
        [tab._ome_frame_order_cb.itemData(i)
         for i in range(tab._ome_frame_order_cb.count())].index("single"))
    assert tab._ome_validation_error() is not None
    # positions-within-timepoint: 6 positions × 2 timepoints → valid.
    tab._ome_frame_order_cb.setCurrentIndex(
        [tab._ome_frame_order_cb.itemData(i)
         for i in range(tab._ome_frame_order_cb.count())].index("pos_in_time"))
    assert tab._ome_validation_error() is None
    opts = tab._collect_run_options()
    assert opts["ome_frame_order"] == "pos_in_time"
    assert opts["ome_fovs_per_well"] == 2


def test_run_options_carry_roles_and_fovs(ome_tab):
    _set_role(ome_tab, "DAPI", "segmentation")
    _set_role(ome_tab, "BF", "phase")
    ome_tab._ome_rows_edit.setText("1")
    ome_tab._ome_cols_edit.setText("3")
    ome_tab._ome_fovs_edit.setText("2")
    opts = ome_tab._collect_run_options()
    assert opts["ome_input"] is True
    assert opts["nuclear_token"] == "DAPI"
    assert set(opts["fluor_tokens"]) == {"GFP", "BF"}
    assert opts["phase_token"] == "BF"
    assert opts["ome_fovs_per_well"] == 2
    assert opts["filename_schema"] == "experiment:channel:well:fov:timepoint"
