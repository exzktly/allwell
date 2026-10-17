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
    theme.apply_theme(theme.DEFAULT_THEME)
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


def test_a_fixed_size_set_under_a_scale_is_left_alone(qapp):
    """Built in phone layout: literal or font-measured, the pass cannot tell
    which, and halving a literal on the way out would be worse than leaving
    it."""
    from ui.phone_shell import rescale_widgets
    edit = QLineEdit()
    edit.setFixedWidth(60)
    rescale_widgets(1.75, 1.0, [edit])
    assert edit.minimumWidth() == 60


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
    assert d._detail.text() == "C05: failed" and not d._detail.isHidden()
    d.setStacked(False)
    assert d._plate.maximumWidth() == cap
    assert not d._title_label.isHidden() and d._detail.isHidden()
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
