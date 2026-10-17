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
    phone_layout.set_on(False)


# ── rules ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("w,h,expected", [
    (1080, 2000, True),    # upright
    (1000, 800, True),     # narrower than the desk window's minimum
    (1600, 700, True),     # shorter than it
    (1920, 1040, False),   # a monitor
    (2532, 1170, False),   # a phone sideways at full resolution: by size alone,
                           # a wide monitor — the toggle is how to say otherwise
])
def test_is_phone_shaped(w, h, expected):
    assert phone_layout.is_phone_shaped(w, h) is expected


@pytest.mark.parametrize("w,h,expected", [
    (540, 1170, 1.0),      # small desktop: no enlargement
    (1080, 2000, 1.75),    # 1080 / 600 = 1.8 → nearest preset 1.75
    (1170, 2532, 2.0),     # 1.95 → 2.0
    (2000, 900, 1.5),      # landscape: the short side decides (1.5 exactly)
    (3000, 3000, 2.5),     # clamped to the largest preset
])
def test_auto_text_size_targets_the_short_side(w, h, expected):
    assert phone_layout.auto_text_size(w, h) == expected


def test_a_stored_preset_wins_over_auto_and_junk_reads_as_auto():
    assert phone_layout.resolve_text_size(1.25, 1080, 2000) == 1.25
    assert phone_layout.resolve_text_size(phone_layout.AUTO, 1080, 2000) == 1.75
    assert phone_layout.parse_text_size("1.5") == 1.5
    assert phone_layout.parse_text_size("1.3") == phone_layout.AUTO   # not offered
    assert phone_layout.parse_text_size(None) == phone_layout.AUTO
    assert phone_layout.parse_bool("true") is True
    assert phone_layout.parse_bool("false") is False


def test_plot_factor_is_the_square_root_and_never_shrinks():
    assert phone_layout.plot_factor(2.25) == 1.5
    assert phone_layout.plot_factor(1.0) == 1.0
    assert phone_layout.plot_factor(0.5) == 1.0


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


def test_a_panel_alone_hides_only_what_it_hid_and_restores_its_pin(qapp):
    from review.core.tab_controller import _show_panel_alone
    split = QSplitter(Qt.Horizontal)
    panel, content, dock = QWidget(), QWidget(), QWidget()
    for w in (panel, content, dock):
        split.addWidget(w)
    panel.setFixedWidth(329)
    dock.hide()                                    # a closed dock
    _show_panel_alone(panel, True)
    assert content.isHidden() and dock.isHidden()
    assert panel.maximumWidth() > 329
    _show_panel_alone(panel, False)
    assert not content.isHidden()
    assert dock.isHidden()                         # it was closed before
    assert (panel.minimumWidth(), panel.maximumWidth()) == (329, 329)


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


class _App:
    def __init__(self, w, h):
        self._s = _Screen(w, h)

    def primaryScreen(self):
        return self._s


def _store(on, size=phone_layout.AUTO):
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
    assert theme.ui_scale() == 1.0
    # Launched on the phone: phone layout, tokens scaled before any widget.
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is True
    assert theme.ui_scale() == 1.75


def test_startup_honours_a_chosen_text_size_and_an_off_preference():
    import allwell
    _store(True, 1.25)
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is True
    assert theme.ui_scale() == 1.25
    theme.apply_ui_scale(1.0)
    _store(False)
    assert allwell._phone_layout_at_startup(_App(1080, 2000)) is False
    assert theme.ui_scale() == 1.0
