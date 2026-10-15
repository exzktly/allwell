"""The Nanowells page's Array map, Trajectories, Position and Array QC views, driven through the real builder,
controls, worker and canvas in a minimal shell (tracker V2, V3a2, V3c, V4, V6, V7, V7a; CLAUDE.md §7.6, §7.8).

Two datasets: the synthetic plate of ``test_nanowell_page`` (three wells, two timepoints, four nanowells each)
with a grid record that carries a pitch and a montage, for the controls; and a real nanowell run's output, for
the click on the map and the crop cut from the owner tile — the case whose positions only a real run has.
Numbers are read back from the figures' text artists.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from tests.test_nanowell_cli import WELL as RUN_WELL  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _texts, _write_nanowell_dataset  # noqa: E402
from tests.test_nanowell_review_load import run_with_a_cell  # noqa: E402,F401  (module fixture)

PITCH = 143.0
#: The montage: 4 µm per pixel from (−100, −100) µm, so the four nanowells at x = 100 µm sit inside it.
ORIGIN, SCALE = (-100.0, -100.0), 4.0


def _montage_png() -> bytes:
    from PIL import Image

    image = np.zeros((100, 120), dtype=np.uint8)
    image[40:60, 40:60] = 255                  # the well-frame point (100, 100) µm is pixel (50, 50)
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, format="PNG")
    return buf.getvalue()


def _with_grids(out):
    """Give every well's grid record a pitch, and A01's a montage and two tiles placed two different ways."""
    for path in (out / "nanowells").glob("*_grid.json"):
        record = json.loads(path.read_text())
        for tp, block in record["timepoints"].items():
            block.update(pitch_um=PITCH, angle_deg=0.5, overlap_um=40.0, n_detected=4, roster_size=4)
            if record["well"] == "A01":
                block.update(montage_origin_um=list(ORIGIN), montage_um_per_px=SCALE,
                             tiles=[{"tile": "F0", "left_um": -100.0, "top_um": -100.0, "source": "lattice",
                                     "residual_um": 0.4},
                                    {"tile": "F1", "left_um": 200.0, "top_um": -100.0, "source": "recorded"}])
        path.write_text(json.dumps(record))
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        for tp in ("t_c", "t_a"):
            zf.writestr(f"A01_{tp}_montage.png", _montage_png())
    return out


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _with_grids(_write_nanowell_dataset(tmp_path)))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _fig_texts(ctx, gid):
    return _texts(ctx, gid)


def _choose_view(ctx, key):
    from review.features.nanowell.tab_view import VIEWS

    seg = ctx.widgets.get("_nw_view_seg")
    seg.setCurrentIndex([k for k, _l in VIEWS].index(key))          # emits currentChanged, as a click does


def _visible_scopes(ctx):
    names = {"_nw_metric_cb": "Metric", "_nw_axis_seg": "Along x", "_nw_baseline_cb": "Baseline",
             "_nw_tp_cb": "Timepoint", "_nw_control_cb": "vs", "_nw_well_cb": "Well", "_nw_tp_slider": "Slider"}
    return {label for name, label in names.items() if ctx.widgets.get(name).isVisibleTo(ctx.window)}


# ── the view switch and the controls each view shows ─────────────────────────
def test_the_switch_lists_every_view_and_each_shows_only_the_controls_it_uses(page, qtbot):
    _loaded(qtbot, page)
    seg = page.widgets.get("_nw_view_seg")
    assert [seg.segmentText(i) for i in range(seg.count())] == [
        "Distribution", "Array map", "Trajectories", "Position", "Array QC"]
    assert _visible_scopes(page) == {"Metric", "Along x", "Baseline"}
    _choose_view(page, "array_map")
    assert _visible_scopes(page) == {"Metric", "Baseline", "Well", "Slider"}
    _choose_view(page, "trajectories")
    assert _visible_scopes(page) == {"Metric", "Baseline"}
    _choose_view(page, "position")
    assert _visible_scopes(page) == {"Metric", "Baseline", "Timepoint"}
    _choose_view(page, "array_qc")
    assert _visible_scopes(page) == {"Well", "Slider"}


# ── V2: the array map ────────────────────────────────────────────────────────
def test_the_map_draws_the_chosen_well_and_the_slider_steps_its_timepoints(page, qtbot):
    _loaded(qtbot, page)
    _choose_view(page, "array_map")
    st = page.state.feature_slice("nanowell")
    title = [t.get_text() for ax in page.widgets.get("_nw_fig").axes for t in (ax.title, ax._left_title)]
    assert "A01 · t_c" in title                                       # the first well, at its first timepoint
    footer = _fig_texts(page, "nw-map-footer")
    assert footer[-1].startswith("4 nanowells in the roster · with a value 3 · measured 2 · empty 1 · occluded 1")
    slider = page.widgets.get("_nw_tp_slider")
    assert (slider.minimum(), slider.maximum(), slider.value()) == (0, 1, 0)
    slider.setValue(1)
    assert st.timepoint == "t_a"
    title = [ax._left_title.get_text() for ax in page.widgets.get("_nw_fig").axes]
    assert "A01 · t_a" in title
    assert _fig_texts(page, "nw-map-footer")[-1].startswith("4 nanowells in the roster · with a value 4 ·")
    well_cb = page.widgets.get("_nw_well_cb")
    assert [well_cb.itemText(i) for i in range(well_cb.count())] == ["A01", "B01"]
    well_cb.setCurrentIndex(1)
    assert st.well == "B01"
    assert "B01 · t_a" in [ax._left_title.get_text() for ax in page.widgets.get("_nw_fig").axes]


def test_a_well_whose_grid_record_has_no_pitch_says_why_there_is_no_map(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))                 # grid records without a pitch
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    _choose_view(ctx, "array_map")
    assert _fig_texts(ctx, "nw-map-missing") == [
        "A01 has no addressed nanowells and no measured pitch to place them on, so there is no map to draw."]


def _click_site(qtbot, ctx, site, modifier=Qt.NoModifier):
    """A real mouse press where *site* is drawn on the map — read from the drawn collections, not the hit map."""
    st = ctx.state.feature_slice("nanowell")
    canvas = ctx.widgets.get("_nw_canvas")
    for ax, xs, ys, sites in st.hits.entries:
        if tuple(site) in [tuple(s) for s in sites]:
            k = [tuple(s) for s in sites].index(tuple(site))
            drawn = np.concatenate([c.get_offsets() for c in ax.collections if len(c.get_offsets())])
            assert np.min(np.hypot(drawn[:, 0] - xs[k], drawn[:, 1] - ys[k])) < 1e-9, "hit map is not the drawing"
            px, py = ax.transData.transform((xs[k], ys[k]))
            ratio = canvas.devicePixelRatioF()
            QTest.mouseClick(canvas, Qt.LeftButton, modifier,
                             QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio))))
            return
    raise AssertionError(f"{site} is not on the map")


def test_clicking_a_nanowell_on_the_map_selects_it_and_cuts_its_crop_from_the_montage(page, qtbot):
    _loaded(qtbot, page)
    _choose_view(page, "array_map")
    _click_site(qtbot, page, ("A01", 1, 0))
    st = page.state.feature_slice("nanowell")
    assert st.selected_sites == [["A01", 1, 0]]
    assert page.widgets.get("_nw_detail_pane").isVisible()
    qtbot.waitUntil(lambda: ("A01", 1, 0) in st.crops, timeout=20000)
    crops = st.crops[("A01", 1, 0)]
    # No input archive beside this dataset, so each crop comes from the montage and says so.
    assert {tp: (c.source, c.note) for tp, c in crops.items()} == {
        "t_c": ("montage", "from the montage: the input archive is not beside this dataset"),
        "t_a": ("montage", "from the montage: the input archive is not beside this dataset")}
    assert crops["t_c"].image.max() == 255                    # centred on the bright square at (100, 100) µm
    detail = page.widgets.get("_nw_detail_fig")
    images = [im for ax in detail.axes for im in ax.images]
    assert len(images) == 2


# ── V4 / V6 ──────────────────────────────────────────────────────────────────
def test_trajectories_draw_one_line_per_nanowell_with_a_value(page, qtbot):
    _loaded(qtbot, page)
    _choose_view(page, "trajectories")
    footer = _fig_texts(page, "nw-traj-footer")
    assert any(t.startswith("8 nanowells drawn, one line each; 0 with no value at any timepoint not drawn.")
               for t in footer)
    assert [ax._left_title.get_text() for ax in page.widgets.get("_nw_fig").axes] == ["A01", "B01"]


def test_a_per_nanowell_metric_on_trajectories_is_greyed_and_says_what_is_drawn(page, qtbot):
    _loaded(qtbot, page)
    nwc.set_metric(page, "occupancy_class")
    _choose_view(page, "trajectories")
    metric_cb = page.widgets.get("_nw_metric_cb")
    index = [metric_cb.itemData(i) for i in range(metric_cb.count())].index("occupancy_class")
    assert not metric_cb.model().item(index).isEnabled()
    assert ("Occupancy at baseline (cells) has one value per nanowell, not one per timepoint, so Trajectories shows Cell count."
            in _fig_texts(page, "nw-traj-footer"))


def test_position_defaults_to_the_latest_timepoint_and_follows_the_combo(page, qtbot):
    _loaded(qtbot, page)
    _choose_view(page, "position")
    axes = page.widgets.get("_nw_fig").axes
    assert axes[0]._left_title.get_text() == "By ring from the central nanowell · t_a"
    tp_cb = page.widgets.get("_nw_tp_cb")
    tp_cb.setCurrentIndex([tp_cb.itemData(i) for i in range(tp_cb.count())].index("t_c"))
    assert page.widgets.get("_nw_fig").axes[0]._left_title.get_text() == "By ring from the central nanowell · t_c"
    # Rings 0–3 hold one nanowell each, in both wells; at t_c only A01's ring 3 is occluded.
    share = page.widgets.get("_nw_fig").axes[1]
    heights = sorted(round(p.get_height(), 3) for p in share.patches)
    assert heights == [0.0] * 7 + [1.0]


# ── V7 / V7a: Array QC ───────────────────────────────────────────────────────
def test_array_qc_draws_the_montage_the_tiles_by_how_they_were_placed_and_the_status(page, qtbot):
    _loaded(qtbot, page)
    _choose_view(page, "array_qc")
    fig = page.widgets.get("_nw_fig")
    assert len(fig.axes[0].images) == 1
    assert sorted(_fig_texts(page, "nw-qc-tile")) == ["F0 · lattice · 0.4 µm", "F1 · recorded"]
    # This record states no tile size, so the corners are marked and the figure says so.
    assert _fig_texts(page, "nw-qc-unsized") == [
        "2 tile(s) without a size in the acquisition record: their placed corner is marked (+), not an outline."]
    status = _fig_texts(page, "nw-qc-status")
    assert status[:2] == ["t_c: ok", "pitch 143.0 µm · angle 0.50° · overlap 40.0 µm · 4 detected of 4"]
    assert status[2:] == ["t_a: ok"]


def test_array_qc_on_a_well_without_a_montage_says_why(page, qtbot):
    _loaded(qtbot, page)
    nwc.set_well(page, "B01")
    _choose_view(page, "array_qc")
    assert _fig_texts(page, "nw-qc-missing") == ["The grid record has no montage for B01 at t_c (ok)."]


# ── a real run: the map click and the crop from the owner tile ───────────────
def test_on_a_real_run_the_map_click_selects_the_lit_nanowell_and_its_crop_is_full_resolution(
        qtbot, run_with_a_cell):  # noqa: F811
    ctx = _page(qtbot, run_with_a_cell)
    ctx.state.selection.selected_wells = {RUN_WELL}
    st = _loaded(qtbot, ctx)
    _choose_view(ctx, "array_map")
    _click_site(qtbot, ctx, (RUN_WELL, 1, 2))
    assert st.selected_sites == [[RUN_WELL, 1, 2]]
    qtbot.waitUntil(lambda: (RUN_WELL, 1, 2) in st.crops, timeout=20000)
    crop = st.crops[(RUN_WELL, 1, 2)]["0h"]
    assert (crop.source, crop.note.endswith("full resolution")) == ("tile", True)
    _choose_view(ctx, "array_qc")
    fig = ctx.widgets.get("_nw_fig")
    assert len(fig.axes[0].images) == 1 and len(fig.axes[0].patches) == 4       # four sized tile outlines
    assert _texts(ctx, "nw-qc-unsized") == []
