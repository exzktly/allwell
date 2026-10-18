"""Phone layout's parts, below the window: rules, tokens, the rescale pass.

The real window is driven end to end in ``tests/test_phone_layout_app.py``
(slow lane). Everything here runs without building the app, so the rules that
decide what phone layout does are pinned in the lane every push runs.

Each test is written so the implementation it replaced, or the obvious wrong
one, fails it: a scale that compounds, a rescale that rounds 11 px back to
10.9, a sheet rebuilt from scratch that loses its state, a fixed width scaled
twice, a font the restyle wiped and nobody put back.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QSplitter, QVBoxLayout, QWidget,
)

from core import phone_layout
from ui import theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_scale_and_flag():
    """Token classes and the flag are process-wide; a worker runs many files."""
    yield
    theme.apply_ui_scale(1.0)
    theme.set_touch_unit(None)
    phone_layout.set_on(False)


# ── rules ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("w,h,expected", [
    (1080, 2000, True),    # upright
    (1000, 800, True),     # narrower than the desk window's minimum
    (1600, 700, True),     # shorter than it
    (1920, 1040, False),   # a monitor
])
def test_is_phone_shaped(w, h, expected):
    assert phone_layout.is_phone_shaped(w, h) is expected


@pytest.mark.parametrize("w,h,expected", [
    (390, 844, 1.0),       # a desktop already phone-sized: one px per point
    (1080, 2000, 1080 / 390),
    (2340, 1080, 1080 / 390),   # sideways: the short side, so the same unit
    (300, 600, 1.0),       # never below one
])
def test_phone_unit_is_the_short_side_over_390(w, h, expected):
    assert phone_layout.phone_unit(w, h) == pytest.approx(expected)


def test_type_scale_puts_body_text_at_15_points_times_the_size():
    u = 1080 / 390
    assert phone_layout.type_scale(u, "M", 13) * 13 == pytest.approx(15 * u)
    assert phone_layout.type_scale(u, "L", 13) == pytest.approx(
        phone_layout.type_scale(u, "M", 13) * 1.13)
    assert phone_layout.touch_px(u, 44) == round(44 * u)      # targets: unit only


def test_a_stored_size_reads_back_and_junk_reads_as_medium():
    assert phone_layout.parse_text_size("L") == "L"
    assert phone_layout.parse_text_size("1.5") == "M"
    assert phone_layout.parse_text_size(None) == "M"
    assert phone_layout.parse_bool("true") is True
    assert phone_layout.parse_bool("false") is False


def test_plot_dpi_factor_makes_a_point_a_phone_point():
    u = 1080 / 390
    assert phone_layout.plot_dpi_factor(u) == pytest.approx(0.72 * u)
    assert phone_layout.plot_dpi_factor(1.0) == 1.0      # never shrinks a plot


@pytest.mark.parametrize("w,h,expected", [
    (2340, 1080, True),    # 19.5:9 phone sideways at full resolution
    (2532, 1170, True),
    (1920, 1080, False),   # 16:9 monitor
    (2560, 1440, False),   # 16:9, taller
    (3440, 1440, False),   # 21:9 ultrawide at 1440: too tall for a phone
])
def test_a_phone_on_its_side_reads_as_a_phone(w, h, expected):
    assert phone_layout.is_phone_shaped(w, h) is expected


def test_listeners_hear_only_real_changes():
    heard = []
    phone_layout.add_listener(heard.append)
    try:
        phone_layout.set_on(True)
        phone_layout.set_on(True)
        phone_layout.set_on(False)
    finally:
        phone_layout.remove_listener(heard.append)
    assert heard == [True, False]


# ── tokens ──────────────────────────────────────────────────────────────────

def test_ui_scale_multiplies_from_the_desk_value_and_never_compounds():
    body, pad, rail = (theme.Typography.body_size, theme.Metrics.btn_pad_h,
                       theme.Metrics.rail_w)
    theme.apply_ui_scale(1.5)
    assert theme.Typography.body_size == round(body * 1.5)
    assert theme.Metrics.btn_pad_h == round(pad * 1.5)
    theme.apply_ui_scale(2.0)
    assert theme.Typography.body_size == round(body * 2.0)   # not 1.5 × 2.0
    assert theme.Metrics.rail_w == rail                       # layout widths stay
    assert theme.Typography.body == (theme.Typography.body_size, theme.Typography.regular)
    assert f"font-size: {round(body * 2.0)}px" in theme.qss()
    theme.apply_ui_scale(1.0)
    assert (theme.Typography.body_size, theme.Metrics.btn_pad_h) == (body, pad)


def test_a_theme_applied_under_a_scale_keeps_the_scale():
    body = theme.Typography.body_size
    theme.apply_ui_scale(1.5)
    # "blue" overrides nothing (the token classes' own palette, which the test
    # session runs on), so the reset runs without leaving another theme's
    # colours behind for the next test file on this worker.
    theme.apply_theme("blue")
    assert theme.ui_scale() == 1.5
    assert theme.Typography.body_size == round(body * 1.5)
    theme.apply_ui_scale(1.0)
    assert theme.Typography.body_size == body


# ── the rescale pass ────────────────────────────────────────────────────────

def test_scale_sheet_scales_sizes_only_and_comes_back_exactly():
    from ui.phone_shell import scale_sheet
    sheet = "color: #EAECEE; font-size: 11px; padding: 3px; font-size:12.5px;"
    up = scale_sheet(sheet, 1.75)
    assert up == "color: #EAECEE; font-size: 19px; padding: 3px; font-size:22px;"
    # The way back recovers the desk sizes, half pixels included — rounding to
    # whole pixels would turn 12.5 into 13, rounding to tenths 11 into 10.9.
    assert scale_sheet(up, 1 / 1.75, step=0.5) == sheet


def test_a_sheet_keeps_its_state_and_round_trips(qapp):
    from ui.phone_shell import rescale_widgets
    lbl = QLabel("Review")
    # A state the widget's own code chose (the active segment's fill): a pass
    # that rebuilt the sheet from a factory would lose it.
    lbl.setStyleSheet("background-color: #2D8CEB; font-size: 11px;")
    rescale_widgets(1.0, 1.75, [lbl])
    assert lbl.styleSheet() == "background-color: #2D8CEB; font-size: 19px;"
    # The widget rewrites its own sheet while scaled, from the scaled token.
    lbl.setStyleSheet("background-color: #24272C; font-size: 19px;")
    rescale_widgets(1.75, 1.0, [lbl])
    assert lbl.styleSheet() == "background-color: #24272C; font-size: 11px;"


def test_fixed_text_sizes_scale_and_come_back(qapp):
    from ui.phone_shell import rescale_widgets
    edit = QLineEdit()
    edit.setFixedWidth(60)
    rescale_widgets(1.0, 1.75, [edit])
    assert (edit.minimumWidth(), edit.maximumWidth()) == (105, 105)
    rescale_widgets(1.75, 1.0, [edit])
    assert (edit.minimumWidth(), edit.maximumWidth()) == (60, 60)


def test_a_font_set_under_a_scale_comes_back_at_its_desk_size(qapp):
    """A font set while a scale is in force is the desk's times that scale: the
    application stylesheet re-polishes every widget's font (its ``*`` rule
    sets a size on all of them), so a font that survives to be read was put
    there by the stylesheet or by code reading a scaled token. This replaces
    ``test_a_fixed_size_set_under_a_scale_is_left_alone``, which pinned the
    font half of the reported defect: taken as a literal, the run log of a
    window launched in phone layout stayed 31 px at the desk instead of 13."""
    from ui.phone_shell import explicit_fonts, rescale_widgets
    host = QWidget()
    QVBoxLayout(host)
    lbl = QLabel("legend", host)
    host.layout().addWidget(lbl)
    f = QFont(lbl.font())
    f.setPixelSize(round(13 * 1.75))
    lbl.setFont(f)
    rescale_widgets(1.75, 1.0, [lbl], explicit_fonts())
    assert lbl.font().pixelSize() == 13            # a literal reading keeps 23
    # From there it makes the same round trip as a desk-built font.
    rescale_widgets(1.0, 1.75, [lbl], explicit_fonts())
    assert lbl.font().pixelSize() == 23
    rescale_widgets(1.75, 1.0, [lbl], explicit_fonts())
    assert lbl.font().pixelSize() == 13


def test_a_fixed_size_set_under_a_scale_is_left_as_built(qapp):
    """A fixed size set under a scale is nearly always the desk's: a widget
    measures itself in its constructor, before the stylesheet polishes it, so
    off the application font that no scale touches — or it is a literal.
    Measured on the real window launched in phone layout, dividing every such
    size by the scale on the way out shrank about 300 widgets below their desk
    size (Analyze's form fields 40 px → 17). The sizes that really are read off
    the scale re-derive themselves in ``_aw_rescale`` (the empty state's glyph,
    a plate's caps, a plot card's chrome)."""
    from ui.phone_shell import rescale_widgets
    edit = QLineEdit()
    edit.setFixedWidth(60)
    rescale_widgets(1.75, 1.0, [edit])
    assert (edit.minimumWidth(), edit.maximumWidth()) == (60, 60)


def test_a_widget_that_owns_its_sizes_is_rescaled_by_its_own_hook(qapp):
    from ui.phone_shell import rescale_widgets
    calls = []

    class Owner(QWidget):
        _aw_owns_sizes = True

        def _aw_rescale(self):
            calls.append(self.inner.maximumWidth())

    owner = Owner()
    owner.inner = QLineEdit(owner)
    owner.inner.setFixedWidth(40)
    rescale_widgets(1.0, 2.0, [owner, owner.inner])
    # The hook ran, and ran after the pass had left the subtree alone: a pass
    # that scaled the child too would hand the hook 80 and the owner would
    # scale it again.
    assert calls == [40]


def test_widgets_rescale_themselves_innermost_first(qapp):
    """An owner that measures its children — a plot card's chrome holds the
    header's icon buttons — must run after them. In the order the widget list
    happened to give, a card measured its header with the phone's icons still
    in it and kept 21 px too much minimum height at the desk."""
    from ui.phone_shell import rescale_widgets
    order = []

    class Owner(QWidget):
        def _aw_rescale(self):
            order.append(self.objectName())

    outer = Owner()
    outer.setObjectName("outer")
    middle = Owner(outer)
    middle.setObjectName("middle")
    inner = Owner(middle)
    inner.setObjectName("inner")
    rescale_widgets(1.0, 2.0, [outer, middle, inner])
    assert order == ["inner", "middle", "outer"]


def test_a_font_set_in_code_survives_the_restyle(qapp):
    from ui.phone_shell import explicit_fonts, rescale_widgets
    host = QWidget()
    QVBoxLayout(host)
    lbl = QLabel("legend", host)
    host.layout().addWidget(lbl)
    host.show()
    f = QFont(lbl.font())
    f.setPointSize(7)
    lbl.setFont(f)
    before = qapp.styleSheet()
    try:
        fonts = explicit_fonts()
        qapp.setStyleSheet(before + "\nQLabel { font-size: 15px; }")
        assert lbl.font().pointSize() != 7          # the restyle did wipe it
        rescale_widgets(1.0, 2.0, [lbl], fonts)
        assert lbl.font().pointSizeF() == 14.0
        fonts = explicit_fonts()
        rescale_widgets(2.0, 1.0, [lbl], fonts)
        assert lbl.font().pointSizeF() == 7.0
    finally:
        qapp.setStyleSheet(before)
        host.hide()


def test_a_stepper_keeps_the_width_its_caller_pinned(qapp):
    from ui.phone_shell import rescale_widgets
    from widgets.stepper import Stepper
    st = Stepper(value=50.0)
    st.setFixedWidth(96)
    rescale_widgets(1.0, 1.75, [st, *st.findChildren(QWidget)])
    assert st.maximumWidth() == 168
    rescale_widgets(1.75, 1.0, [st, *st.findChildren(QWidget)])
    # Re-deriving the width from the digits gave 132 here: the caller's 96
    # must come back, not the widget's own measurement.
    assert (st.minimumWidth(), st.maximumWidth()) == (96, 96)


def test_icon_button_glyph_follows_the_scale(qapp):
    from widgets.icon_button import IconButton
    btn = IconButton("menu", size=16)
    theme.apply_ui_scale(2.0)
    btn._aw_rescale()
    assert btn.iconSize().width() == 32
    built_scaled = IconButton("menu", size=16)
    assert built_scaled.iconSize().width() == 32


# ── plots ───────────────────────────────────────────────────────────────────

def test_canvas_dpi_scales_from_its_own_base_and_returns(qapp):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from ui.phone_shell import scale_canvas
    fig = Figure(dpi=100)
    canvas = FigureCanvasQTAgg(fig)
    canvas.resize(400, 300)
    scale_canvas(canvas, 1.5)
    assert fig._original_dpi == 150
    scale_canvas(canvas, 1.5)                      # idempotent, not 225
    assert fig._original_dpi == 150
    scale_canvas(canvas, 1.0)
    assert fig._original_dpi == 100
    w_in, _ = fig.get_size_inches()
    assert abs(w_in * fig.dpi - canvas.width() * canvas.device_pixel_ratio) < 1


# ── widgets phone layout rearranges ─────────────────────────────────────────

def test_run_dashboard_stacks_and_unstacks(qapp):
    from PySide6.QtWidgets import QBoxLayout
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.show()
    rail_w = d._rail_host.maximumWidth()
    d.setStacked(True)
    assert d._body_lay.direction() == QBoxLayout.TopToBottom
    assert all(div.isHidden() for div in d._dividers)
    assert d._legend_host.isHidden()
    assert d._rail_host.maximumWidth() > rail_w
    d.setStacked(False)
    assert d._body_lay.direction() == QBoxLayout.LeftToRight
    assert not any(div.isHidden() for div in d._dividers)
    assert not d._legend_host.isHidden()
    assert d._rail_host.maximumWidth() == rail_w
    d.hide()


def test_run_dashboard_stage_columns_follow_the_scale(qapp):
    from widgets.run_dashboard import _NM_W, RunDashboard
    d = RunDashboard()
    theme.apply_ui_scale(2.0)
    d._aw_rescale()
    names = [row[1] for row in d._rail.values()]
    assert names and all(n.maximumWidth() == 2 * _NM_W for n in names)


def test_mode_bar_compact_drops_glyphs_keeps_labels(qapp):
    from widgets.mode_bar import ModeBar
    bar = ModeBar()
    bar.addSegment("Review", icon="eye")
    bar.addSegment("Analyze", icon="sliders-horizontal")
    bar.setCompact(True)
    assert all(s._icon_lbl.isHidden() and not s._text_lbl.isHidden() for s in bar._segments)
    bar.setCompact(False)
    assert not any(s._icon_lbl.isHidden() for s in bar._segments)


def test_compact_mode_bar_widths_follow_labels_enlarged_by_a_restyle(qapp):
    # Phone layout makes the bar compact *before* the restyle enlarges its
    # labels; widths measured then clipped "Datasets" to "Dataset:" on a
    # phone. The rescale hook has to measure them again.
    from widgets.mode_bar import ModeBar
    bar = ModeBar()
    for name in ("Review", "Analyze", "Datasets"):
        bar.addSegment(name, icon="eye")
    bar.setCompact(True, pad=12)
    before = bar._segments[2].minimumWidth()
    from PySide6.QtWidgets import QWidget

    from ui.phone_shell import rescale_widgets
    tree = [bar, *bar.findChildren(QWidget)]
    rescale_widgets(1.0, 2.0, tree)       # the restyle's own pass
    for seg in bar._segments:
        seg.layout().activate()
    widest = max(s.sizeHint().width() for s in bar._segments)
    assert widest > before                    # the fixture can tell them apart
    assert [s.minimumWidth() for s in bar._segments] == [widest] * 3
    bar.setCompact(False)
    bar._aw_rescale()
    assert [s.minimumWidth() for s in bar._segments] == [0] * 3


def test_a_lent_panel_goes_back_to_its_place_pin_and_sizes(qapp):
    from review.core.phone_drawer import _give_back, _lend
    split = QSplitter(Qt.Horizontal)
    panel, content = QWidget(), QWidget()
    split.addWidget(panel)
    split.addWidget(content)
    split.resize(1000, 600)
    panel.setFixedWidth(329)
    split.setSizes([329, 671])
    sizes = split.sizes()
    holder = QWidget()
    QVBoxLayout(holder)
    _lend(panel, holder)
    assert panel.parentWidget() is holder and split.count() == 1
    assert panel.maximumWidth() > 329            # fills the drawer
    _lend(panel, holder)                         # idempotent: remembers once
    _give_back(panel)
    # Back at index 0 — not appended after the content, which a plain
    # addWidget would do — with its pin and the splitter's sizes.
    assert split.indexOf(panel) == 0 and split.count() == 2
    assert (panel.minimumWidth(), panel.maximumWidth()) == (329, 329)
    assert split.sizes() == sizes


def test_a_left_drawer_slides_from_the_left_below_its_inset(qapp):
    from widgets.drawer import Drawer
    host = QWidget()
    host.resize(1000, 800)
    drawer = Drawer(host, edge="left", width_fraction=0.86, margin=0, top_inset=lambda: 50)
    content = QLabel("rail")
    drawer.setContentWidget(content)
    assert drawer._open_rect() == QRect(0, 50, 860, 750)
    assert drawer._closed_rect() == QRect(-860, 50, 860, 750)
    drawer._sync_backdrop_geom()
    assert drawer._backdrop.geometry() == QRect(0, 50, 1000, 750)   # band left usable
    closed = []
    drawer.closed.connect(lambda: closed.append(True))
    drawer.open()
    drawer.closeNow()
    assert closed == [True] and drawer.isHidden() and not drawer._backdrop.isVisible()
    taken = drawer.takeContentWidget()
    assert taken is content and taken.parentWidget() is None   # not deleted
    assert taken.text() == "rail"


# ── geometry ────────────────────────────────────────────────────────────────

def test_fitted_rect_moves_first_and_shrinks_only_when_it_must():
    from ui.phone_shell import fitted_rect
    avail = QRect(0, 0, 1080, 2000)
    # Off the right edge but small enough: moved, not shrunk.
    assert fitted_rect(QRect(900, 100, 500, 400), avail) == QRect(580, 100, 500, 400)
    # Wider than the screen: as wide as the screen, at its left edge.
    assert fitted_rect(QRect(50, 50, 1640, 980), avail) == QRect(0, 50, 1080, 980)


# ── startup ─────────────────────────────────────────────────────────────────

class _Screen:
    def __init__(self, w, h):
        self._r = QRect(0, 0, w, h)

    def availableGeometry(self):
        return self._r

    def geometry(self):
        return self._r


class _App:
    def __init__(self, w, h):
        self._s = _Screen(w, h)

    def primaryScreen(self):
        return self._s


def _store(on, size=phone_layout.DEFAULT_TEXT_SIZE):
    from core.app_settings import app_settings
    s = app_settings()
    s.setValue(phone_layout.KEY_ON, on)
    s.setValue(phone_layout.KEY_TEXT_SIZE, size)
    s.sync()


def test_startup_reenters_phone_layout_only_on_a_phone_shaped_screen():
    import allwell
    _store(True)
    # Left on at the phone, launched at the desk: the desk wins, unscaled.
    assert allwell._phone_layout_at_startup(_App(1920, 1040)) is False
    assert theme.ui_scale() == 1.0 and theme.touch_unit() is None
    # Launched on the phone: phone layout, tokens scaled before any widget.
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is True
    u = 1080 / 390
    assert theme.touch_unit() == pytest.approx(u)
    assert theme.ui_scale() == pytest.approx(15 * u / 13)


def test_startup_honours_a_chosen_text_size_and_an_off_preference():
    import allwell
    _store(True, "L")
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is True
    assert theme.ui_scale() == pytest.approx(15 * (1080 / 390) * 1.13 / 13)
    theme.apply_ui_scale(1.0)
    theme.set_touch_unit(None)
    _store(False)
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is False
    assert theme.ui_scale() == 1.0


# ── touch sizing ────────────────────────────────────────────────────────────

def test_touch_unit_adds_44_and_48_point_floors_to_the_stylesheet():
    u = 1080 / 390
    assert "min-height: 122px" not in theme.qss()
    theme.set_touch_unit(u)
    q = theme.qss()
    assert f"min-height: {round(44 * u)}px" in q        # buttons, combos, fields
    assert f"min-height: {round(48 * u)}px" in q        # list and popup rows
    theme.set_touch_unit(None)
    assert f"min-height: {round(44 * u)}px" not in theme.qss()


def test_stepper_touch_mode_swaps_arrows_for_minus_plus_and_gives_its_pin_back(qapp):
    from widgets.stepper import Stepper
    st = Stepper(value=50.0, decimals=1)
    st.setFixedWidth(96)
    st.setTouchMode(True)
    assert st._up.isHidden() and st._down.isHidden()
    assert not st._minus.isHidden() and not st._plus.isHidden()
    # Wide enough for the value *and* two square buttons — the pinned 96 left
    # the value a sliver between them.
    assert st.maximumWidth() >= st._full_width() > 96
    st._plus.click()
    assert st.value() == 51.0
    st.setTouchMode(False)
    assert (st.minimumWidth(), st.maximumWidth()) == (96, 96)
    assert not st._up.isHidden() and st._minus.isHidden()


def test_a_stacked_control_band_flows_its_actions_and_puts_them_back(qapp):
    from PySide6.QtWidgets import QPushButton
    from widgets.control_band import ControlBand
    band = ControlBand()
    band.add_scope("Channel", QLineEdit())
    a, b = QPushButton("Fold change"), QPushButton("Save")
    band.add_action(a)
    band.add_action(b)
    b.hide()                                    # a page's own choice
    band.setStacked(True)
    assert band.action_count() == 0 and band.scope_count() == 3
    assert a.parentWidget() is band.scope_host
    band.setStacked(False)
    assert band.action_count() == 2 and band.scope_count() == 1
    assert [band._actions.itemAt(i).widget() for i in range(2)] == [a, b]
    assert b.isHidden() and not a.isHidden()   # hidden stays hidden


def test_rail_nav_grid_puts_shown_rows_first_and_goes_back_to_a_list(qapp):
    from PySide6.QtWidgets import QGridLayout, QVBoxLayout as _V
    from widgets.rail_nav import RailNav
    nav = RailNav()
    for t in ("Home", "Plotting", "Review CSV", "Statistics"):
        nav.addItem(t, icon="home")
    nav._rows["Review CSV"].hide()
    nav.setColumns(2)
    grid = nav.layout()
    assert isinstance(grid, QGridLayout)
    # Statistics fills the hole Review CSV would have left at row 1, col 0.
    assert grid.itemAtPosition(1, 0).widget() is nav._rows["Statistics"]
    nav.setColumns(1)
    assert isinstance(nav.layout(), _V)
    nav.setCurrentKey("Statistics")
    assert nav.currentKey() == "Statistics"


def test_rail_nav_item_clicked_is_a_users_click_only(qapp):
    from widgets.rail_nav import RailNav
    nav = RailNav()
    for t in ("Home", "Plotting"):
        nav.addItem(t)
    clicked = []
    nav.itemClicked.connect(clicked.append)
    nav.setCurrentKey("Plotting")              # code: no click
    assert clicked == []
    nav._rows["Plotting"].clicked.emit("Plotting")   # the current one, tapped
    assert clicked == ["Plotting"]


def test_drawer_takes_a_live_width_and_a_scrim(qapp):
    from widgets.drawer import Drawer
    host = QWidget()
    host.resize(1000, 800)
    width = [300]
    d = Drawer(host, edge="left", width_hint=lambda: width[0], scrim=0.5)
    assert d._open_rect().width() == 300
    width[0] = 700                              # the screen turned
    assert d._open_rect().width() == 700
    assert d._backdrop._alpha == 0.5


def test_a_stacked_dashboard_is_the_page_and_says_what_a_tapped_well_is(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(total=96)
    d.set_well_stage("C05", "error")
    d.show()
    cap = d._plate.maximumWidth()
    d.setStacked(True)
    assert d._title_label.isHidden() and d._toggle.isHidden()
    assert d._plate.maximumWidth() > cap        # the desk cap is lifted
    d._plate.wellActivated.emit("C05")
    assert d._well_detail.text() == "C05: failed" and not d._well_detail.isHidden()
    # A well still running goes through the dashboard's per-stage detail —
    # the path a label named ``_detail`` once shadowed, raising TypeError for
    # every well that had not failed.
    d.set_well_stage("A01", "group")
    d._plate.wellActivated.emit("A01")
    assert d._well_detail.text() == "A01: grouped"
    d.setStacked(False)
    assert d._plate.maximumWidth() == cap
    assert not d._title_label.isHidden() and d._well_detail.isHidden()
    d.hide()


def test_a_form_row_stacks_its_label_and_gets_its_pin_back(qapp):
    from PySide6.QtWidgets import QBoxLayout, QHBoxLayout
    from ui.phone_shell import PhoneShell
    row = QWidget()
    row.setObjectName("AnalyzeFormRow")
    lay = QHBoxLayout(row)
    label = QLabel("Nucleus probability")
    label.setFixedWidth(110)
    lay.addWidget(label)
    lay.addWidget(QLineEdit())
    PhoneShell._stack_form_row(None, row, True)
    assert lay.direction() == QBoxLayout.TopToBottom
    assert label.maximumWidth() > 110
    PhoneShell._stack_form_row(None, row, False)
    assert lay.direction() == QBoxLayout.LeftToRight
    assert (label.minimumWidth(), label.maximumWidth()) == (110, 110)


def test_a_title_too_wide_for_a_phone_is_broken_into_full_lines():
    from review.core.plot_style import _break_to_fit
    text = "GFP Mean Intensity (above threshold) ± SEM — t = 0 h"
    # Measured 1.5× the room: two lines, each as full as the room allows —
    # not three short ones, and never split mid-word.
    out = _break_to_fit(text, 1500, 1000)
    assert out.count("\n") == 1
    assert all(len(line) <= len(text) * 1000 / 1500 for line in out.split("\n"))
    assert _break_to_fit(text, 900, 1000) == text           # fits: untouched
    assert _break_to_fit("a\nb", 5000, 10) == "a\nb"        # already broken


def test_a_title_breaks_at_its_dash_clause_first():
    # The timepoint after " — " is the natural second line: broken anywhere
    # else, "— t = 0 h" split across lines on a phone.
    from matplotlib.figure import Figure

    from review.core.plot_style import _fit_labels_to_phone
    fig = Figure(figsize=(3, 3), dpi=100)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_title("Fraction of Cells Above Threshold — t = 0 h", fontsize=12)
    _fit_labels_to_phone(fig)
    lines = ax.get_title().split("\n")
    assert lines[-1] == "— t = 0 h"
    assert " ".join(lines[:-1]) == "Fraction of Cells Above Threshold"


def test_plots_get_220_points_per_row_of_axes_and_their_own_height_back(qapp):
    from types import SimpleNamespace

    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    from ui.phone_shell import PhoneShell
    fig = Figure()
    for i in range(3):
        fig.add_subplot(3, 1, i + 1)
    canvas = FigureCanvasQTAgg(fig)
    canvas.setMinimumHeight(150)
    shell = SimpleNamespace(pt=lambda p: int(p * 2))
    PhoneShell.size_canvas(shell, canvas, True)
    assert canvas.minimumHeight() == 3 * 440
    PhoneShell.size_canvas(shell, canvas, False)
    assert canvas.minimumHeight() == 150


def test_a_matplotlib_toolbar_shown_in_phone_layout_is_hidden(qapp):
    from types import SimpleNamespace

    from ui.phone_shell import PhoneShell

    class MplToolbar(QWidget):      # matched by class name, as the app's is
        pass

    bar = MplToolbar()
    bar.show()
    shell = SimpleNamespace(_hide=lambda w: w.hide(), _sheet_band=None)
    phone_layout.set_on(True)
    try:
        PhoneShell.adapt(shell, bar)
    finally:
        phone_layout.set_on(False)
    assert bar.isHidden()


def test_an_info_hint_answers_a_tap_with_its_text(qapp, monkeypatch):
    # Nothing hovers on a phone, so an ⓘ's tooltip would never show.
    from PySide6.QtCore import QEvent, QObject, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QLabel, QToolTip

    from ui.phone_shell import _PhoneEventFilter
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *a: shown.append(a[1]))
    hint = QLabel("ⓘ")
    hint.setObjectName("InfoHint")
    hint.setToolTip("What this number means")
    filt = _PhoneEventFilter(QObject())
    tap = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2),
                      Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    assert filt.eventFilter(hint, tap) is True
    assert shown == ["What this number means"]
    hint.setObjectName("Plain")
    assert filt.eventFilter(hint, tap) is False
    assert shown == ["What this number means"]


# ── the Image Table in phone layout ─────────────────────────────────────────

def test_image_table_cells_share_the_width_between_their_columns():
    from review.features.image_browser.image_table._phone_fit import (
        MIN_CELL_PX, fit_cell_px,
    )
    # 1000 px, less 100 of labels, 40 of group gaps and 3 × 10 of per-column
    # spacing, leaves 830 for three columns.
    assert fit_cell_px(1000, 3, label_w=100, per_col_px=10, spacer_w=40, zoom=1.0) == 276
    # Zoom multiplies the fitted size: 150 % scrolls sideways, as on a phone.
    assert fit_cell_px(1000, 3, label_w=100, per_col_px=10, spacer_w=40, zoom=1.5) == 415
    # Never smaller than a field of view can be read at.
    assert fit_cell_px(300, 12, label_w=100, per_col_px=10, spacer_w=0, zoom=1.0) == MIN_CELL_PX


def test_image_table_labels_grow_with_the_ui_scale_on_screen_only():
    from review.features.image_browser.image_table import facet_view
    assert "font-size:11px" in facet_view._inner_qss(11)
    theme.apply_ui_scale(2.0)
    try:
        assert "font-size:22px" in facet_view._inner_qss(11)
        assert "font-size:22px" in facet_view._group_qss(11)
        assert "font-size:20px" in facet_view._corner_qss(11)
    finally:
        theme.apply_ui_scale(1.0)


def test_image_table_builder_stacks_into_one_column_and_back(qapp):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QBoxLayout, QHBoxLayout

    from review.features.image_browser.image_table.builder_view import (
        _Z_MIN_H, builder_set_stacked,
    )
    host = QWidget()
    header = QHBoxLayout()
    axes_host = QWidget(host)
    axes = QHBoxLayout(axes_host)
    zones = [QWidget(axes_host), QWidget(axes_host)]
    for z in zones:
        z.setMinimumHeight(_Z_MIN_H)
        axes.addWidget(z, 1)
    shape = QLabel("Well › [DAPI,GFP] ↓ · Timepoint →", host)
    rt = SimpleNamespace(shelf_header=header, axes_row_layout=axes, shape_label=shape)
    ctx = SimpleNamespace(runtime=SimpleNamespace(image_table=rt))
    builder_set_stacked(ctx, True)
    assert header.direction() == axes.direction() == QBoxLayout.TopToBottom
    assert shape.isHidden()
    # One above the other, each zone is as tall as its content: no equal share
    # of the height, no floor that lets the layout squeeze its rows.
    assert [axes.stretch(i) for i in range(2)] == [0, 0]
    assert [z.minimumHeight() for z in zones] == [0, 0]
    builder_set_stacked(ctx, False)
    assert header.direction() == axes.direction() == QBoxLayout.LeftToRight
    assert not shape.isHidden()
    assert [axes.stretch(i) for i in range(2)] == [1, 1]
    assert [z.minimumHeight() for z in zones] == [_Z_MIN_H, _Z_MIN_H]


# ── the switch itself ───────────────────────────────────────────────────────

def test_the_restyle_is_a_switch_and_its_notice_breaks_its_line(qapp, monkeypatch):
    from types import SimpleNamespace

    import ui.phone_shell as ps
    texts, during = [], []

    class _Recording(ps._BusyOverlay):
        def __init__(self, parent, text):
            texts.append(text)
            super().__init__(parent, text)

    monkeypatch.setattr(ps, "_BusyOverlay", _Recording)
    monkeypatch.setattr(ps, "rescale_widgets", lambda *a, **k: 0)
    monkeypatch.setattr(QApplication, "setStyleSheet",
                        lambda self, _s: during.append(phone_layout.is_switching()))
    shell = SimpleNamespace(_win=QWidget(), _applying=False)
    try:
        ps.PhoneShell._restyle(shell, 1.5, None)
    finally:
        theme.apply_ui_scale(1.0)
        theme.set_touch_unit(None)
    # A line break, not the two characters "\" and "n" on screen.
    assert texts == ["Resizing for your screen…\nThis takes a few seconds."]
    assert during == [True] and not phone_layout.is_switching()


def test_an_image_table_redraw_runs_when_its_timer_fires(qapp):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QScrollArea
    from types import SimpleNamespace

    from review.features.image_browser.image_table._phone_fit import install_refit
    scroll = QScrollArea()
    host = QWidget()
    scroll.setWidget(host)
    ctx = SimpleNamespace(state=SimpleNamespace(
        image_table=SimpleNamespace(last_render={(0, 0): None})))
    calls = []
    install_refit(ctx, scroll, host, calls.append)
    timer = host.findChild(QTimer)
    # It has no guard against a switch: it runs from a timer, and the restyle
    # delivers none (test_nothing_queued_runs_inside_the_rescale_pass).
    timer.timeout.emit()
    assert calls == [ctx]


def test_nothing_queued_runs_inside_the_rescale_pass(qapp):
    """The pass delivers polish and layout events only. It used to call
    processEvents, which runs due timers — on Windows until the queue is
    empty, so a zero-delay timer that re-armed itself held the restyle, and
    its busy overlay, up for good. Then it called a bare sendPostedEvents(),
    which still ran every queued call: about 9,400 in one switch on a loaded
    app. An interval-0 QTimer *object* is no test of either — sendPostedEvents
    never fires one on Linux — so the calls here are the queued kinds that
    really ran inside it: a singleShot(0), a queued signal, a queued
    invokeMethod."""
    from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal, Slot

    from ui.phone_shell import rescale_widgets
    ran = []

    class _Target(QObject):
        poke = Signal()

        @Slot()
        def invoked(self):
            ran.append("invokeMethod")

    target = _Target()
    target.poke.connect(lambda: ran.append("queued signal"), Qt.QueuedConnection)
    QTimer.singleShot(0, lambda: ran.append("singleShot"))
    target.poke.emit()
    QMetaObject.invokeMethod(target, "invoked", Qt.QueuedConnection)
    host = QWidget()
    QVBoxLayout(host).addWidget(QLabel("x", host))
    host.show()
    with phone_layout.switching():
        rescale_widgets(1.0, 1.0, [], {})
        assert ran == []
    # Delivered later, by the event loop, as they would have been anyway.
    for _ in range(5):
        qapp.processEvents()
    assert sorted(ran) == ["invokeMethod", "queued signal", "singleShot"]


def test_the_queued_refresh_does_not_re_arm_inside_a_switch(qapp):
    from types import SimpleNamespace

    from PySide6.QtCore import QTimer

    from ui.phone_shell import PhoneShell
    refreshed = []
    shell = SimpleNamespace(_refresh_soon=QTimer(), refresh=lambda: refreshed.append(1),
                            _sweep=lambda force: None)
    shell._refresh_soon.setSingleShot(True)
    with phone_layout.switching():
        PhoneShell._refresh_when_settled(shell)
        # The switch ends with a refresh of its own; restarting this timer
        # from inside the switch is what can hold an event loop.
        assert refreshed == [] and not shell._refresh_soon.isActive()
    PhoneShell._refresh_when_settled(shell)
    assert refreshed == [1]


# ── the switch, driven through the shell ────────────────────────────────────
# These go through PhoneShell itself — set_on, set_text_size, the turn — in the
# order the window calls it, on widgets the app builds, because each defect
# here lived in the *sequence* (what ran before the restyle, what was built
# under a scale), which a test of one helper cannot see.

#: Windows a shell test built, deleted by ``app_sheet`` in a fixed order.
_WINDOWS: list = []


@pytest.fixture
def app_sheet(qapp):
    """The shell re-applies the application stylesheet; give the old one back.

    It also takes down what the test built, deliberately rather than by luck.
    Each window goes first, deleted outright, which takes its shell, its plot
    card and its popups with it in Qt's own order. Then what was handed to
    ``deleteLater`` meanwhile: a Stepper measures its row height off a
    throwaway combo it deletes that way, and without an event loop the combo
    outlives the test as a top-level window, which the widget reaper then
    deleted after its own popup container — a segfault in teardown, on
    ``origin/main`` too. Left to the garbage collector or to the reaper's
    order, these tests crashed a worker one run in three.
    """
    import shiboken6
    from PySide6.QtCore import QEvent
    before = qapp.styleSheet()
    yield
    while _WINDOWS:
        w = _WINDOWS.pop()
        if shiboken6.isValid(w):
            w.hide()
            shiboken6.delete(w)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.setStyleSheet(before)


def _window():
    """A window the ``app_sheet`` fixture will take down."""
    win = QWidget()
    _WINDOWS.append(win)
    return win


def _shell(win, *, show=True, **handles):
    """A shell on *win*, mounted the way ``allwell`` mounts it: its controls in
    a band at the top of the window, the desk button among them."""
    from PySide6.QtWidgets import QHBoxLayout

    from ui.phone_shell import PhoneHandles, PhoneShell
    if win not in _WINDOWS:
        _WINDOWS.append(win)
    band = QWidget(win)
    QHBoxLayout(band)
    win.layout().insertWidget(0, band)
    win.resize(400, 700)
    if show:
        win.show()
    shell = PhoneShell(win, PhoneHandles(band=band, **handles))
    band.layout().addWidget(shell.desk_button)
    shell._rail_status.setParent(win)
    return shell


def _pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def _app_widgets(host):
    """Widgets the way the app builds them, each one a kind the report named:
    the run log (a 9 pt font the stylesheet replaces — ``analyze/log_pane``),
    an empty state (a glyph and a two-line floor measured off the font), a
    picker plate (caps read off the UI scale), a band stepper pinned to its
    token (``review/core/centre_view``), a label pinned to its text, the FOV
    toggle (room reserved for "FOV ✓", measured once polished) and a plot card
    pinned to its figure plus its chrome (``review/features/barplot``)."""
    from PySide6.QtWidgets import QTextEdit

    from widgets.empty_state import EmptyState
    from widgets.plate_geometry import apply_plate_geometry
    from widgets.plot_card import PlotCard
    from widgets.reserved_width_button import ReservedWidthButton
    from widgets.stepper import Stepper
    from widgets.well_plate_selector import WellPlateSelector
    lay = QVBoxLayout(host)
    log = QTextEdit(host)
    f = log.font()
    f.setPointSize(9)
    log.setFont(f)
    empty = EmptyState("Nothing to show", parent=host)
    plate = WellPlateSelector(host)
    apply_plate_geometry(plate, role="picker")
    stepper = Stepper(host, value=50.0, minimum=0.0, maximum=1e9, decimals=1)
    stepper.setFixedWidth(theme.Metrics.stepper_w)
    label = QLabel("Threshold", host)
    label.setFixedHeight(label.fontMetrics().height() + theme.Spacing.sm)
    cell = QLineEdit(host)              # a Home › Labels cell
    cell.setFixedHeight(26)
    fov = ReservedWidthButton("FOV", host)
    fov.reserve(("FOV", "FOV ✓"), theme.Spacing.md)
    card = PlotCard(host, figsize=(4, 3))
    card.pinFigureHeight(300)
    for w in (log, empty, plate, stepper, label, cell, fov, card):
        lay.addWidget(w)
    return dict(log=log, empty=empty, plate=plate, stepper=stepper, label=label,
                cell=cell, fov=fov, card=card)


def _sizes(ws):
    f = ws["log"].font()
    glyph = ws["empty"]._glyph
    return {
        "log font": (f.pointSizeF(), f.pixelSize()),
        "empty glyph": (glyph.minimumWidth(), glyph.maximumWidth()),
        "empty floor": ws["empty"]._text.minimumHeight(),
        "plate caps": (ws["plate"].maximumWidth(), ws["plate"].maximumHeight()),
        "stepper": (ws["stepper"].minimumWidth(), ws["stepper"].maximumWidth()),
        "label": (ws["label"].minimumHeight(), ws["label"].maximumHeight()),
        "cell": (ws["cell"].minimumHeight(), ws["cell"].maximumHeight()),
        "fov": ws["fov"].minimumWidth(),
        "card": ws["card"].minimumHeight(),
    }


# The widgets live only inside these helpers, which hand back plain numbers: a
# failing assertion keeps its frame's locals alive, and a frame holding the
# window and its shell crashed the interpreter while pytest wrote the report.

def _desk_sizes(qapp) -> dict:
    qapp.setStyleSheet(theme.qss())
    host = _window()
    desk = _app_widgets(host)
    host.show()
    _pump(qapp)
    return _sizes(desk)


def _launched_in_phone_layout_then_left(qapp) -> tuple[dict, dict]:
    """``main``'s sequence: ``_phone_layout_at_startup`` scales the tokens, the
    stylesheet goes on, the window is built and enters phone layout with
    ``set_on(True, restyle=False)``; then the user switches back."""
    import allwell
    _store(True)
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is True
    qapp.setStyleSheet(theme.qss())
    win = _window()
    built = _app_widgets(win)
    # AllWellApp.__init__ enters before the window is first shown.
    shell = _shell(win, show=False)
    shell.set_on(True, restyle=False)
    win.show()
    _pump(qapp)
    at_phone = _sizes(built)
    shell.set_on(False)
    _pump(qapp)
    assert theme.ui_scale() == 1.0
    return at_phone, _sizes(built)


def _round_trip(qapp) -> tuple[dict, dict, dict]:
    qapp.setStyleSheet(theme.qss())
    win = _window()
    built = _app_widgets(win)
    shell = _shell(win)
    _pump(qapp)
    before = _sizes(built)
    shell.set_on(True)
    _pump(qapp)
    at_phone = _sizes(built)
    shell.set_on(False)
    _pump(qapp)
    return before, at_phone, _sizes(built)


def test_a_window_launched_in_phone_layout_leaves_it_like_one_built_at_the_desk(
        qapp, app_sheet):
    """Reported: launched on the phone, then switched back at the desk, the
    run log stayed at 31 px against 13, eleven empty-state glyphs at 111 px
    against 48, and the plate caps at 781×497 against 330×210 — everything
    built under the scale kept it, because the pass took a size it had no
    desk record of as a literal."""
    want = _desk_sizes(qapp)
    at_phone, after = _launched_in_phone_layout_then_left(qapp)
    # Built at the phone's size: the defect is only in the way back.
    assert at_phone["empty glyph"][0] > want["empty glyph"][0] * 2
    assert after == want


def test_a_round_trip_gives_back_a_fixed_size_and_font_set_at_the_desk(
        qapp, app_sheet):
    before, at_phone, after = _round_trip(qapp)
    assert at_phone["label"] != before["label"]          # it did scale
    assert after == before


@pytest.mark.parametrize("change", ["text size", "the phone turning"])
def test_a_restyle_inside_phone_layout_gives_a_pinned_stepper_back(
        qapp, app_sheet, monkeypatch, change):
    """Reported: desk → phone → text size L → desk left a band's stepper
    227 px wide instead of 96. The restyle ran with the stepper still in its
    touch form, whose width is its own, so the pass lost the caller's pin.
    Both restyles made inside phone layout — a text-size change, and the
    phone turning (a new screen geometry) — take the touch form off first."""
    from ui.phone_shell import PhoneShell
    from widgets.stepper import Stepper
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    st = Stepper(win, value=50.0, minimum=0.0, maximum=1e9, decimals=1)
    st.setFixedWidth(theme.Metrics.stepper_w)
    win.layout().addWidget(st)
    shell = _shell(win)
    _pump(qapp)
    shell.set_on(True)
    _pump(qapp)
    assert st.isTouchMode()
    if change == "text size":
        shell.set_text_size("L")
    else:
        unit = shell.unit() * 1.3
        monkeypatch.setattr(PhoneShell, "unit", lambda self: unit)
        shell._on_geometry_changed(None)
    _pump(qapp)
    assert st.isTouchMode()          # its phone form is back after the restyle
    shell.set_on(False)
    _pump(qapp)
    assert not st.isTouchMode()
    assert (st.minimumWidth(), st.maximumWidth()) == (96, 96)


def test_a_round_trip_gives_back_minimum_heights_the_touch_floors_took(
        qapp, app_sheet):
    """Reported: all 96 Home › Labels cells (a QLineEdit pinned 26 px tall)
    came back from one round trip with a minimum height of 0. Qt resets a
    minimum to 0 when the stylesheet rule that set it goes; the touch floors
    put a ``min-height`` on fields and buttons."""
    from PySide6.QtWidgets import QPushButton
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    cell = QLineEdit(win)
    cell.setFixedHeight(26)
    button = QPushButton("Go", win)
    button.setMinimumHeight(20)
    label = QLabel("not floored", win)
    label.setMinimumHeight(17)
    for w in (cell, button, label):
        win.layout().addWidget(w)
    shell = _shell(win)
    _pump(qapp)
    shell.set_on(True)
    _pump(qapp)
    assert cell.minimumHeight() > 26          # the touch floor owns it now
    shell.set_on(False)
    _pump(qapp)
    assert (cell.minimumHeight(), cell.maximumHeight()) == (26, 26)
    assert button.minimumHeight() == 20
    assert label.minimumHeight() == 17


def test_a_widget_replaced_in_place_between_sweeps_is_watched(qapp, app_sheet):
    """A stepper deleted and another built between two ticks leaves the widget
    count unchanged — and the allocator often hands the new one the old one's
    address — so a sweep that compared counts never watched it."""
    from PySide6.QtCore import QEvent

    from ui.phone_shell import _P_WATCHED
    from widgets.stepper import Stepper
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    old = Stepper(win, value=1.0)
    win.layout().addWidget(old)
    shell = _shell(win)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()            # the test ticks the sweep itself
    assert old.property(_P_WATCHED)
    # A Stepper deletes a throwaway combo with deleteLater; flush those so the
    # count below is the widgets', then take the sweep's baseline.
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    shell._sweep(force=True)
    count = len(QApplication.allWidgets())
    old.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    new = Stepper(win, value=2.0)
    win.layout().addWidget(new)
    new.show()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(QApplication.allWidgets()) == count
    shell._sweep(force=False)
    assert new.property(_P_WATCHED)
    assert new.isTouchMode()             # and adapted, since it is showing


def test_a_page_change_watches_what_the_new_page_built_at_once(qapp, app_sheet):
    """ARCHITECTURE said a page change sweeps at once; it only refreshed the
    band, so a lazily built page showed its desk controls until the next
    300 ms tick. The pages handle's ``currentChanged`` now sweeps."""
    from PySide6.QtWidgets import QStackedWidget

    from ui.phone_shell import _P_WATCHED
    from widgets.stepper import Stepper
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    pages = QStackedWidget(win)
    pages.addWidget(QWidget())
    win.layout().addWidget(pages)
    shell = _shell(win, pages=pages)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()            # no tick may do it instead
    page = QWidget()
    QVBoxLayout(page)
    st = Stepper(page, value=3.0)
    page.layout().addWidget(st)
    pages.addWidget(page)
    pages.setCurrentWidget(page)         # built, then shown: a lazy page
    assert st.property(_P_WATCHED)
    assert st.isTouchMode()


def test_a_rail_section_glyph_is_measured_off_the_font_it_is_drawn_in(
        qapp, app_sheet):
    """A rail row measured its glyph box off the font before the stylesheet
    polished it, and ``_aw_rescale`` off the polished one, so every glyph
    came back from a round trip a pixel larger (15 → 16)."""
    from widgets.rail_nav import RailNav
    qapp.setStyleSheet(theme.qss())
    host = _window()
    QVBoxLayout(host)
    nav = RailNav(host)
    nav.addItem("Home", icon="home", key="home")
    host.layout().addWidget(nav)
    host.show()
    _pump(qapp)
    row = nav._rows["home"]
    built = row._glyph_px
    row._aw_rescale()
    assert row._glyph_px == built
    assert row._glyph.maximumWidth() == built
