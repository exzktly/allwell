"""Array QC's montage and overlay can each be turned off (the Nanowells page's **Show: Montage / Overlay**).

Asked for: "On the QC nanowell page, can the montage be toggled on and off, and the overlay toggled on and off as
well?" Driven through the real page and its real checkboxes; what the figure then holds is asserted: the image,
the tile outlines and their labels, each present or absent as the boxes say. The choice is kept with the page's
other settings.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_full_montage import montage_page  # noqa: E402,F401


@pytest.fixture
def qc_page(montage_page, qtbot):  # noqa: F811
    from PIL import Image

    from review.features.nanowell import controller as nwc

    ctx, in_dir = montage_page
    out = in_dir.parent / "out"
    buffer = io.BytesIO()
    Image.fromarray(np.full((100, 100), 128, np.uint8)).save(buffer, format="PNG")
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        zf.writestr("A01_t_a_montage.png", buffer.getvalue())
    grid_path = out / "nanowells" / "A01_grid.json"
    grid = json.loads(grid_path.read_text())
    grid["timepoints"]["t_a"].update(montage_origin_um=[0.0, 0.0], montage_um_per_px=2.48)
    grid_path.write_text(json.dumps(grid))
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: not st.fetching, timeout=10000)         # the fixture's own read, before the montage
    st.data.grids["A01"] = grid["timepoints"]
    st.montages.clear()
    nwc.redraw(ctx)
    qtbot.waitUntil(lambda: ("A01", "t_a") in st.montages, timeout=10000)
    assert st.montages[("A01", "t_a")][0] is not None, st.montages[("A01", "t_a")][1]
    return ctx


def _drawn(ctx):
    ax = ctx.widgets.get("_nw_fig").axes[0]
    return {"image": len(ax.images), "outlines": len(ax.patches),
            "tile labels": sum(t.get_gid() == "nw-qc-tile" for t in ax.texts)}


def test_each_box_takes_its_layer_off_the_figure_and_back(qc_page):
    ctx = qc_page
    montage, overlay = ctx.widgets.get("_nw_qc_montage_box"), ctx.widgets.get("_nw_qc_overlay_box")
    assert montage.isVisible() and overlay.isVisible() and montage.isChecked() and overlay.isChecked()
    assert _drawn(ctx) == {"image": 1, "outlines": 4, "tile labels": 4}
    montage.setChecked(False)
    assert _drawn(ctx) == {"image": 0, "outlines": 4, "tile labels": 4}
    ax = ctx.widgets.get("_nw_fig").axes[0]
    assert ax.get_xlim() == (0.0, 248.0) and ax.get_ylim() == (248.0, 0.0)     # the same frame, without the image
    overlay.setChecked(False)
    assert _drawn(ctx) == {"image": 0, "outlines": 0, "tile labels": 0}
    montage.setChecked(True)
    assert _drawn(ctx) == {"image": 1, "outlines": 0, "tile labels": 0}


def test_the_choice_is_kept_with_the_page_s_settings_and_both_start_on():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    assert (st.qc_montage, st.qc_overlay) == (True, True)
    st.qc_montage = False
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert (restored.qc_montage, restored.qc_overlay) == (False, True)
    old = NanowellState()
    old.apply_config({"view": "array_qc"})                   # saved before the boxes existed: both on
    assert (old.qc_montage, old.qc_overlay) == (True, True)


def test_the_boxes_show_only_on_array_qc(qc_page):
    from review.features.nanowell import controller as nwc

    nwc.set_view(qc_page, "distribution")
    assert not qc_page.widgets.get("_nw_qc_montage_box").isVisible()
