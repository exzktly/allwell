"""The Scatter cell popup reads the cell through the shared image path (tracker V3–V6).

It used to guess a zip member from the row's ``filename`` and take the first
match when several fitted, stretch each crop to its own min/max while the Cell
Browser used the shared per-channel window, keep a diagnostics box in front of
the user, and load on the GUI thread. These drive the real popup on a plate of
real TIFFs (``tests/_page_datasets.image_plate``: wells A01/A02, nuclei 1 and 2,
DAPI 3000 and GFP 900 inside each nucleus, 100 outside).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def plate_ctx(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests._page_datasets import image_plate

    root = image_plate(tmp_path / "plate")
    ctx, window = minimal_shell(qtbot)
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    ctx.state.dataset.in_dir = root / "in"
    return ctx


def _row_of(ctx, well, nid):
    from review.data.data_loading import get_rows

    rows = get_rows(ctx, well)
    return int(np.flatnonzero(rows["nucleus_id"].astype(int).to_numpy() == nid)[0])


def _open(qtbot, ctx, well, nid, *, nuclear_id=None):
    from review.features.scatter.cell_viewer_view import open_scatter_cell_viewer

    open_scatter_cell_viewer(ctx, well, "", str(nid if nuclear_id is None else nuclear_id),
                             _row_of(ctx, well, nid))
    viewer = ctx.runtime.scatter.cell_viewer
    qtbot.waitUntil(lambda: viewer._img_label.text() != "Loading…", timeout=10000)
    return viewer


def test_the_popup_shows_the_cell_it_was_asked_for(qtbot, plate_ctx):
    from review.data.cell_crop import CELL_PAD_PX

    viewer = _open(qtbot, plate_ctx, "A01", 2)
    assert viewer._title_label.text() == "Cell · A01 · F001 · 0h · #2"
    crops = viewer._crops
    assert not crops.problem
    gfp = crops.crops[next(c for c in crops.crops if c.lower() == "gfp")]
    # Nucleus 2 is rows 36:54, cols 30:50 of a 64-pixel field, padded and clipped.
    assert gfp.shape == (min(64, 54 + CELL_PAD_PX) - (36 - CELL_PAD_PX),
                         min(64, 50 + CELL_PAD_PX) - (30 - CELL_PAD_PX))
    assert gfp.max() == 900.0
    assert not viewer._img_label.pixmap().isNull()


def test_a_point_whose_row_no_longer_holds_its_nucleus_says_so(qtbot, plate_ctx):
    viewer = _open(qtbot, plate_ctx, "A01", 1, nuclear_id=2)
    assert viewer._crops is None
    assert "redraw the plot" in viewer._img_label.text()


def test_a_cell_missing_from_its_mask_is_a_sentence_not_an_empty_frame(qtbot, plate_ctx):
    from review.data.cell_crop import load_cell

    got = load_cell(plate_ctx, well="A01", fov="F001", tp="0h", nucleus_id=7, channels=["GFP"])
    assert got.problem == ("Nucleus 7 is not in the segmentation mask of A01, field F001, "
                           "timepoint 0h.")


def test_brightness_follows_the_shared_window_and_typing_writes_it(qtbot, plate_ctx):
    from review.data.cell_crop import display_window

    viewer = _open(qtbot, plate_ctx, "A01", 2)
    viewer._channel_dropdown.setCurrentText(next(c for c in viewer._crops.crops if c.lower() == "gfp"))
    channel = viewer._channel_dropdown.currentText()
    # No shared window yet: the crop's own percentile window, said so.
    auto, _ = display_window(viewer._crops.crops[channel], None)
    assert viewer._lut_max_edit.text() == f"{auto[1]:.1f}"
    assert viewer._window_note.text().startswith("Brightness: automatic for this cell")

    viewer._lut_min_edit.setText("0")
    viewer._lut_max_edit.setText("2000")
    viewer._on_lut_typed()
    assert plate_ctx.state.image_view.get_lut(channel) == (0.0, 2000.0)
    assert viewer._window_note.text().startswith(f"Brightness: the shared {channel} window")

    viewer._auto_lut()
    assert plate_ctx.state.image_view.get_lut(channel) is None


def test_a_load_that_finishes_after_another_click_is_dropped(qtbot, plate_ctx, monkeypatch):
    """Two clicks, the first load finishing last: the second cell stays on screen."""
    import core.worker_bridge as wb
    from review.data.cell_crop import CellCrops

    viewer = _open(qtbot, plate_ctx, "A01", 2)
    pending = []
    monkeypatch.setattr(wb, "run_off_thread",
                        lambda fn, *, on_done, on_failed=None, **_k: pending.append((fn, on_done)))
    viewer.reload()                           # the first click's load
    viewer.reload()                           # the second click's load
    first, second = pending
    second[1](CellCrops(problem="second"))
    first[1](CellCrops(problem="first"))      # finishes late
    assert viewer._img_label.text() == "second"


def test_the_popup_has_no_diagnostics_box():
    from PySide6.QtWidgets import QTextEdit

    from review.features.scatter import cell_viewer_view as mod

    assert not hasattr(mod.ScatterCellViewer, "_debug")
    assert "QTextEdit" not in vars(mod) and QTextEdit is not None
