"""The Array view's controls, rearranged so the band reads where → how drawn → edit and fits two rows.

Asked for: "the arrays tab needs a UX expert to fix the menu and buttons across the top; they're a mess". The band
had wrapped into three ragged rows, the timepoint stranded at its end, the zoom and *Overlays* among the settings,
and a walk of the Anchor queue added a fourth group in front of it all. This file pins the behaviour of the new
arrangement through the real page; ``test_nanowell_array_band_layout.py`` (UI lane) pins its geometry.

- Well and Timepoint are one item (*Where*); while the Anchor queue walks, its group stands in that item's place,
  and *Re-run wells…* steps aside.
- The zoom, *Fit*, *1:1* and *Overlays* sit under the figure, beside its navigation buttons, and *Reset view* there
  is *Fit* on the Array view.
- Every Array control that cannot act is greyed with a tooltip that says why — and says the right reason, on
  fixtures where the reasons differ.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_full_montage import montage_page  # noqa: E402,F401
from tests.test_nanowell_page_array_layers import qc_page  # noqa: E402,F401
from tests.test_nanowell_qc_view import qc_page as qc_view_page  # noqa: E402,F401


def _shown(ctx, name) -> bool:
    return ctx.widgets.get(name).isVisibleTo(ctx.window)


def _reason(ctx, name) -> str | None:
    """The reason after a greyed control's own tooltip, or ``None`` while it is enabled."""
    widget = ctx.widgets.get(name)
    return None if widget.isEnabled() else widget.toolTip().split("\n\n")[-1]


def test_well_and_timepoint_are_one_item_and_the_timepoint_stands_alone_where_no_well_is_drawn(qc_page):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    where = ctx.widgets.get("_nw_where_group")
    well, tp = ctx.widgets.get("_nw_well_cb"), ctx.widgets.get("_nw_tp_cb")
    assert where.isAncestorOf(well) and where.isAncestorOf(tp)
    assert _shown(ctx, "_nw_well_cb") and _shown(ctx, "_nw_tp_cb")
    nwc.set_view(ctx, "position")                                     # one timepoint, every well
    assert where.isVisibleTo(ctx.window) and _shown(ctx, "_nw_tp_cb") and not _shown(ctx, "_nw_well_cb")
    nwc.set_view(ctx, "trajectories")                                 # neither
    assert not where.isVisibleTo(ctx.window)


def test_the_zoom_and_overlays_sit_under_the_figure_on_the_array_view_only(qc_page):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    toolbar = ctx.widgets.get("_nw_card").toolbar
    names = ("_nw_zoom_out_btn", "_nw_zoom_lbl", "_nw_zoom_in_btn", "_nw_fit_btn", "_nw_one_btn", "_nw_overlays_btn",
             "_nw_notes_btn")
    assert all(toolbar.isAncestorOf(ctx.widgets.get(n)) for n in names)
    assert all(_shown(ctx, n) for n in names)
    nwc.set_view(ctx, "distribution")
    assert not any(_shown(ctx, n) for n in names)


def test_reset_view_under_the_figure_is_fit_on_the_array_view(qc_page, qtbot):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    ctx.widgets.get("_nw_zoom_in_btn").click()
    ctx.widgets.get("_nw_zoom_in_btn").click()
    assert st.region is not None and nwc.array_zoom_factor(st.drawn) > 1.5
    ctx.widgets.get("_nw_card").toolbar._home.click()
    assert st.region is None                                         # what Fit leaves: the whole well
    assert nwc.array_zoom_factor(st.drawn) == pytest.approx(1.0, rel=0.05)


def test_the_rerun_button_is_short_here_and_full_on_the_qc_view(qc_page):
    ctx = qc_page
    assert ctx.widgets.get("_nw_rerun_btn").text() == "Re-run wells…"
    assert ctx.widgets.get("_nw_qc_rerun_btn").text() == "Re-run anchored wells…"


# ── greyed, saying why ───────────────────────────────────────────────────────
def test_each_image_control_says_the_reason_it_is_greyed(qc_page):
    ctx = qc_page
    image = ctx.widgets.get("_nw_image_cb")
    image.setCurrentIndex([image.itemData(i) for i in range(image.count())].index("montage"))
    assert _reason(ctx, "_nw_image_cb") is None
    assert _reason(ctx, "_nw_array_image_channel_cb") == ("The run montage is the phase image only: choose Image ▸ "
                                                         "Full resolution to draw a channel.")
    assert _reason(ctx, "_nw_array_range_btn") == ("The display range applies to Image ▸ Full resolution; the run "
                                                   "montage is drawn as the run saved it.")
    image.setCurrentIndex([image.itemData(i) for i in range(image.count())].index("off"))
    assert _reason(ctx, "_nw_array_range_btn") == "Image is Off."
    assert _reason(ctx, "_nw_one_btn") == ("No image is drawn (Image Off, or Lattice positions): there is no image "
                                           "pixel to match.")
    ctx.widgets.get("_nw_positions_seg").setCurrentIndex(1)           # Lattice
    lattice = "Lattice positions have no image under them: choose Positions ▸ Measured."
    assert _reason(ctx, "_nw_image_cb") == lattice
    assert _reason(ctx, "_nw_array_range_btn") == lattice


def test_set_anchor_says_which_of_its_conditions_is_missing(montage_page):
    from review.features.nanowell import controller as nwc

    ctx, _in_dir = montage_page
    ctx.widgets.get("_nw_preset_full").trigger()                      # the anchor layer on, measured positions
    assert ctx.widgets.get("_nw_anchor_btn").isEnabled()             # t_a is unaddressed: an anchor can be set
    assert nwc.anchor_unavailable_reason(ctx) is None
    ctx.widgets.get("_nw_layer_anchor").trigger()                     # the anchor layer off
    assert _reason(ctx, "_nw_anchor_btn") == "Turn on Layers ▸ Anchor and show Overlays."
    assert _reason(ctx, "_nw_clear_anchor_btn") == "Turn on Layers ▸ Anchor and show Overlays."
    ctx.widgets.get("_nw_layer_anchor").trigger()
    ctx.widgets.get("_nw_positions_seg").setCurrentIndex(1)
    assert _reason(ctx, "_nw_anchor_btn") == "Choose Positions ▸ Measured: an anchor is set in the well frame."
    ctx.widgets.get("_nw_positions_seg").setCurrentIndex(0)
    nwc.set_timepoint(ctx, "t_c")                                     # addressed: an anchor can still re-anchor it
    assert nwc.anchor_unavailable_reason(ctx) is None and ctx.widgets.get("_nw_anchor_btn").isEnabled()
    assert _reason(ctx, "_nw_clear_anchor_btn") == "No anchor is set for this well at this timepoint."
    assert _reason(ctx, "_nw_rerun_btn") == "No well has an anchor yet."


# ── the walk stands in for Where ─────────────────────────────────────────────
def test_the_walk_takes_the_place_of_well_and_timepoint_and_gives_them_back(qc_view_page, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    ctx, st, _out = qc_view_page
    window = ctx.widgets.get("_nw_canvas").window()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow, timeout=5000)

    def ready():
        qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.anchor_mode, timeout=20000)

    ctx.widgets.get("_nw_queue_btn").click()
    ready()
    assert st.view == "array"
    assert _shown(ctx, "_nw_queue_lbl")
    assert not _shown(ctx, "_nw_well_cb") and not _shown(ctx, "_nw_tp_cb")
    assert not ctx.widgets.get("_nw_where_group").isVisibleTo(ctx.window)
    assert not _shown(ctx, "_nw_rerun_btn")
    QTest.keyClick(window, Qt.Key_Right)                              # the keys still reach the walk
    ready()
    assert st.anchor_queue.index == 1
    QTest.keyClick(window, Qt.Key_Escape)
    assert st.anchor_queue is None and st.view == "qc"
    from review.features.nanowell import controller as nwc

    ctx.state.selection.selected_wells = {"A01"}
    nwc.set_view(ctx, "array")
    assert _shown(ctx, "_nw_well_cb") and _shown(ctx, "_nw_tp_cb") and _shown(ctx, "_nw_rerun_btn")
    assert not _shown(ctx, "_nw_queue_lbl")
