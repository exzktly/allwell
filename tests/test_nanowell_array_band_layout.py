"""The Array view's band, measured: three rows of related controls at 1500 px with the app's stylesheet, walking or not, and no control
moving when a layer, a colouring, a preset, the positions, the image or the overlays change.

Asked for: "the arrays tab needs a UX expert to fix the menu and buttons across the top; they're a mess" — the band
had three ragged rows, and a walk of the Anchor queue added its group in front. Geometry, so the UI lane (local only):
``test_nanowell_array_band.py`` holds the behaviour CI runs.

Also here, because the redesign is what exposed them: ``MplToolbar.add_group``, which puts the zoom under the figure,
and the stylesheet rule that makes a greyed ``secondary`` button look greyed — the ``[variant="secondary"]`` rule
outranked the plain ``:disabled`` one, so every greyed secondary button in the app painted exactly as an enabled one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_nanowell_full_montage import montage_page  # noqa: E402,F401
from tests.test_nanowell_page_array_layers import _toggle, qc_page  # noqa: E402,F401


@pytest.fixture
def themed():
    from ui import theme

    app = QApplication.instance()
    before = app.styleSheet()
    app.setStyleSheet(theme.qss())
    yield app
    app.setStyleSheet(before)        # app-wide: left applied, it changes later tests' layout


def _band(ctx):
    widget = ctx.widgets.get("_nw_metric_cb")
    while widget is not None and type(widget).__name__ != "ControlBand":
        widget = widget.parentWidget()
    return widget


def _rows(ctx) -> list[int]:
    """The top of each row of the band's shown items. Items of different heights are centred in their row, so tops
    a few pixels apart are one row; a new row starts more than half an item's height lower."""
    host = _band(ctx)._scope_host
    items = [w for w in host.children() if getattr(w, "isWidgetType", lambda: False)() and w.isVisibleTo(host)]
    rows: list[int] = []
    for top in sorted(w.y() for w in items):
        if not rows or top - rows[-1] > 16:
            rows.append(top)
    return rows


def _settle(qtbot, ctx, width: int) -> None:
    ctx.window.resize(width, 900)
    qtbot.wait(200)
    QApplication.processEvents()


def _row_of(ctx, name: str) -> int:
    """Which of the band's rows (``_rows``) the control *name* sits on."""
    host = _band(ctx)._scope_host
    widget = ctx.widgets.get(name)
    while widget.parentWidget() is not host:
        widget = widget.parentWidget()
    tops = _rows(ctx)
    return max(k for k, top in enumerate(tops) if widget.y() >= top - 16)


#: The Array view's rows, by what the controls act on (asked for: "clean up / group on rows related controls in
#: array. They're a mess" — the data controls, the well, the anchor and the picture's layers had shared rows, and
#: the wrap split them wherever the width ran out).
_ARRAY_ROWS = (("_nw_metric_cb", "_nw_channel_cb", "_nw_baseline_cb", "_nw_rim_focus_box", "_nw_subset_cb"),
               ("_nw_well_cb", "_nw_tp_cb", "_nw_anchor_btn", "_nw_clear_anchor_btn", "_nw_rerun_btn"),
               ("_nw_positions_seg", "_nw_image_cb", "_nw_layers_btn"))


@pytest.mark.parametrize("width", [1500, 1200])
def test_the_array_band_is_three_rows_of_related_controls(qc_page, themed, qtbot, width):
    """Data · well and its anchor · the picture, each on its own row, at a wide and a narrower window: a row that
    does not fit wraps within itself and never takes the next row's controls onto it."""
    ctx = qc_page
    _settle(qtbot, ctx, width)
    rows = [{_row_of(ctx, name) for name in names} for names in _ARRAY_ROWS]
    # In this order, and no two groups share a row: each group's last row is above the next group's first.
    assert all(max(a) < min(b) for a, b in zip(rows, rows[1:])), rows
    if width == 1500:
        assert rows == [{0}, {1}, {2}], rows                           # wide enough: one row each


def test_the_excluded_chip_ends_the_well_row_on_the_array_view(qc_page, themed, qtbot):
    """*Excluded (n)* names wells: on the Array view it sits at the end of the well row, not after the picture's
    controls, and on another view it is back at the end of the band."""
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    nwc.set_well_excluded(ctx, "A01", True)
    _settle(qtbot, ctx, 1500)
    chip = ctx.widgets.get("_nw_excluded_wells_btn")
    assert chip.isVisibleTo(ctx.window)
    assert _row_of(ctx, "_nw_excluded_wells_btn") == _row_of(ctx, "_nw_anchor_btn")
    host = _band(ctx)._scope_host
    order = [host.layout().itemAt(i).widget() for i in range(host.layout().count())]
    group = chip.parentWidget() if chip.parentWidget() is not host else chip
    assert order.index(group) < order.index(ctx.widgets.get("_nw_positions_seg").parentWidget())
    nwc.set_view(ctx, "distribution")
    QApplication.processEvents()
    order = [host.layout().itemAt(i).widget() for i in range(host.layout().count())]
    assert order[-1] is group


def test_the_row_breaks_are_the_array_view_s_alone(qc_page, themed, qtbot):
    """On another view the same band flows as it did: the breaks are hidden, so Distribution's controls are not
    split across rows the Array view needs."""
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    _settle(qtbot, ctx, 1500)
    host = _band(ctx)._scope_host
    breaks = [w for w in host.children() if getattr(w, "objectName", lambda: "")() == "FlowBreak"]
    assert len(breaks) == 2 and all(b.isVisibleTo(host) for b in breaks)
    nwc.set_view(ctx, "distribution")
    QApplication.processEvents()
    assert not any(b.isVisibleTo(host) for b in breaks)


def test_a_walk_keeps_the_array_band_to_three_rows(qc_page, themed, qtbot):
    from review.features.nanowell import _anchor_queue as aq
    from review.features.nanowell import anchor_queue_compute as aqc

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    # A queue standing on this well, its label as wide as a long walk makes it.
    queue = aqc.AnchorQueue([aqc.QueueItem("A01", "t_a")] * 47, index=11, data_dir=str(ctx.state.dataset.data_dir))
    st.anchor_queue = queue
    ctx.widgets.get("_nw_refresh_controls")()
    ctx.widgets.get("_nw_queue_lbl").setText("12 of 47 · B04 at 24h")
    _settle(qtbot, ctx, 1500)
    assert aq.active_queue(ctx) is queue and ctx.widgets.get("_nw_queue_lbl").isVisibleTo(ctx.window)
    assert len(_rows(ctx)) == 3, _rows(ctx)
    st.anchor_queue = None


def test_no_layer_choice_moves_a_control(qc_page, themed, qtbot):
    """Stronger than showing or hiding: a control greyed in place can still move if a sibling resizes."""
    ctx = qc_page
    _settle(qtbot, ctx, 1500)
    names = ("_nw_metric_cb", "_nw_well_cb", "_nw_tp_cb", "_nw_positions_seg", "_nw_image_cb", "_nw_layers_btn",
             "_nw_anchor_btn", "_nw_clear_anchor_btn", "_nw_rerun_btn", "_nw_fit_btn", "_nw_overlays_btn")

    def where():
        QApplication.processEvents()
        return {n: ctx.widgets.get(n).mapTo(ctx.window, QPoint()) for n in names}

    before = where()
    for key in ("nanowells", "addresses", "values", "tiles", "anchor", "record"):
        _toggle(ctx, key)
        assert where() == before, key
        _toggle(ctx, key)
    for colour in ("state", "metric"):
        ctx.widgets.get(f"_nw_colour_{colour}").trigger()
        assert where() == before, colour
    image = ctx.widgets.get("_nw_image_cb")
    for key in ("off", "tiles", "montage"):
        image.setCurrentIndex([image.itemData(i) for i in range(image.count())].index(key))
        assert where() == before, key
    positions = ctx.widgets.get("_nw_positions_seg")
    positions.setCurrentIndex(1)
    assert where() == before, "lattice"
    positions.setCurrentIndex(0)
    ctx.widgets.get("_nw_overlays_btn").click()
    assert where() == before, "overlays"


# ── what the redesign needed of the shared widgets ───────────────────────────
def test_a_toolbar_group_sits_after_the_built_in_buttons_and_hides_with_its_separator(qtbot):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from PySide6.QtWidgets import QPushButton, QWidget

    from widgets.mpl_toolbar import MplToolbar

    host = QWidget()
    canvas = FigureCanvasQTAgg(Figure())
    canvas.setParent(host)
    toolbar = MplToolbar(canvas, host)
    qtbot.addWidget(host)
    host.resize(600, 60)
    toolbar.resize(600, 30)
    host.show()
    qtbot.waitExposed(host)
    home_x, save_x = toolbar._home.x(), toolbar._save.x()
    button = QPushButton("Fit")
    group = toolbar.add_group(button)
    QApplication.processEvents()
    assert button.x() + group.x() > save_x                          # after the built-in buttons
    separators = [w for w in group.children() if getattr(w, "objectName", lambda: "")() == "MplToolbarSep"]
    assert len(separators) == 1
    group.hide()
    QApplication.processEvents()
    assert not separators[0].isVisibleTo(toolbar)
    assert (toolbar._home.x(), toolbar._save.x()) == (home_x, save_x)
    called = []
    toolbar.setHomeHandler(lambda: called.append(1))
    toolbar._home.click()
    assert called == [1]


def test_a_greyed_secondary_button_is_painted_greyed(themed, qtbot):
    from PySide6.QtWidgets import QPushButton

    from ui import theme

    on, off = QPushButton("Clear anchor"), QPushButton("Clear anchor")
    for button in (on, off):
        button.setProperty("variant", "secondary")
        qtbot.addWidget(button)
        button.resize(120, 30)
        button.show()
    off.setEnabled(False)
    qtbot.waitExposed(off)
    middle = QPoint(4, 15)
    assert off.grab().toImage().pixelColor(middle).name() == theme.Colors.panel.lower()
    assert on.grab().toImage().pixelColor(middle).name() == theme.Colors.panel_elevated.lower()
