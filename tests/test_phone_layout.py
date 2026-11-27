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
    assert not d._legend_chips.isHidden()        # the legend's phone form stays
    assert d._rail_host.maximumWidth() > rail_w
    d.setStacked(False)
    assert d._body_lay.direction() == QBoxLayout.LeftToRight
    assert not any(div.isHidden() for div in d._dividers)
    assert not d._legend_host.isHidden()
    assert d._legend_chips.isHidden()
    assert d._rail_host.maximumWidth() == rail_w
    d.hide()


def _chip_labels(d) -> list[str]:
    # Each chip is a dot then its label; the dot of a glyph state carries text too ("!").
    flow = d._legend_flow
    return [flow.itemAt(i).widget().layout().itemAt(1).widget().text() for i in range(flow.count())]


def test_a_stacked_run_dashboard_keeps_a_key_to_its_well_colours(qapp):
    """Phone layout stacks the dashboard and drops the legend's fixed-width
    column. The stage rail names stages, not well colours, so without a key a
    phone showed a coloured plate nobody could read. The legend stays as
    chips under the plate, with every row the desk legend has — including
    the rows a remote run adds when the dashboard is reset for one."""
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.show()
    d.setStacked(True)
    assert d._legend_chips.isVisible()
    assert _chip_labels(d) == ["pending", "processing", "processed", "complete",
                               "partial / unfinished", "failed"]
    d.reset(remote=True, total=4)                # a remote run renames and adds rows
    assert _chip_labels(d) == ["pending", "uploaded", "processing", "on server", "downloaded",
                               "partial / unfinished", "failed"]
    assert d._legend_chips.isVisible()
    d.hide()


def test_the_colour_key_follows_the_dashboard_s_body_not_only_the_stacking(qapp):
    """The chips show only while the plate they explain does: not in the
    compact strip Analyze's Settings view shows above its form, and not in a
    collapsed dashboard."""
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.show()
    d.setStacked(True)
    chips = d.legendChips()
    assert chips.isVisible()
    d.setCompact(True)
    assert not chips.isVisible()
    d.setCompact(False)
    assert chips.isVisible()
    d._toggle.setChecked(False)
    d._on_toggle()
    assert not chips.isVisible()
    d._toggle.setChecked(True)
    d._on_toggle()
    assert chips.isVisible()
    d.hide()


def test_sideways_the_colour_key_sits_above_the_plate_inside_the_dashboard(qapp):
    """Sideways (the rail beside the plate) the column has no height to spare
    under the plate, and a column of the key's own would come out of the log's
    width beside it. So the key moves above the body, at caption size, inside
    the dashboard's own width — the plate gives up the two lines. Upright it is
    back under the body at the body size."""
    from ui import theme
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=True, total=4)
    d.show()
    d.setStacked(True)
    root, chips = d.layout(), d.legendChips()

    def label_qss():
        flow = d._legend_flow
        return {flow.itemAt(i).widget().layout().itemAt(1).widget().styleSheet()
                for i in range(flow.count())}

    assert root.indexOf(chips) == root.indexOf(d._body) + 1
    assert not any("font-size" in q for q in label_qss())
    d.setRailBeside(True)
    assert chips.parentWidget() is d
    assert root.indexOf(chips) == root.indexOf(d._body) - 1
    assert chips.isVisible()
    assert label_qss() == {f"color:{theme.Colors.text_primary}; "
                           f"font-size:{theme.Typography.caption_size}px;"}
    assert _chip_labels(d) == ["pending", "uploaded", "processing", "on server", "downloaded",
                               "partial / unfinished", "failed"]
    d.setRailBeside(True)                        # idempotent
    assert root.indexOf(chips) == root.indexOf(d._body) - 1
    d.setRailBeside(False)
    assert root.indexOf(chips) == root.indexOf(d._body) + 1
    assert not any("font-size" in q for q in label_qss())
    assert chips.isVisible()
    d.hide()


def test_run_dashboard_stage_columns_follow_the_scale(qapp):
    from widgets.run_dashboard import _NM_W, RunDashboard
    d = RunDashboard()
    theme.apply_ui_scale(2.0)
    d._aw_rescale()
    names = [row.name for row in d._rail.values()]
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


def _rail_drawer(host, panels, closed=None):
    from ui.phone_drawer import RailDrawer
    return RailDrawer(host, panels=lambda: panels, top_inset=lambda: 0,
                      on_closed_inside=closed or (lambda: None))


def test_a_lent_panel_goes_back_to_its_place_pin_and_sizes(qapp):
    split = QSplitter(Qt.Horizontal)
    panel, content = QWidget(), QWidget()
    split.addWidget(panel)
    split.addWidget(content)
    split.resize(1000, 600)
    panel.setFixedWidth(329)
    split.setSizes([329, 671])
    sizes = split.sizes()
    host = QWidget()
    drawer = _rail_drawer(host, {"review": panel})
    holder = drawer.contentWidget()
    drawer.lend(panel)
    assert panel.parentWidget() is holder and split.count() == 1
    assert panel.maximumWidth() > 329            # fills the drawer
    drawer.lend(panel)                           # idempotent: remembers once
    drawer.give_back(panel)
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
    def __init__(self, w, h, dpr=1.0):
        self._r = QRect(0, 0, w, h)
        self._dpr = dpr

    def devicePixelRatio(self):
        return self._dpr

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


def test_startup_keeps_phone_layout_left_on_on_a_near_square_screen():
    """The Duo unfolded sideways at half its pixels (1335 x 939) holds the desk window and is not the Duo's own
    resolution, so it is not offered phone layout; but turned on there, it must reopen in it. Wrong implementation:
    re-entering only on a screen phone layout would be offered on, which reopened that Duo at the desk."""
    import allwell
    _store(True)
    assert not phone_layout.is_phone_shaped(1335, 939)
    assert allwell._phone_layout_at_startup(_App(1335, 939)) is True


def _offers(qapp, monkeypatch, w, h, *, on):
    """What ``_maybe_offer`` does on a screen of this size: ``"phone"`` / ``"desk"`` for the banner it shows,
    ``None`` when it hides it."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QVBoxLayout
    win = _window()
    QVBoxLayout(win)
    shell = _shell(win, show=False)
    shown = []
    monkeypatch.setattr(shell, "_show_banner", lambda *, to_phone: shown.append("phone" if to_phone else "desk"))
    monkeypatch.setattr(shell, "_hide_banner", lambda: shown.append(None))
    phone_layout.set_on(on)
    shell._maybe_offer(QRect(0, 0, w, h))
    return shown[-1]


@pytest.mark.parametrize("w,h,on,offered", [
    (2360, 1640, False, None),     # an iPad Air landscape, at the desk: nothing offered (the report)
    (1640, 2360, False, None),     # and upright
    (2732, 2048, False, None),     # a 13" iPad
    (1080, 2000, False, "phone"),  # a phone upright: phone layout offered
    (1335, 939, True, None),       # the Duo sideways at half its pixels, in phone layout: the desk is not offered
    (1920, 1040, True, "desk"),    # a monitor in phone layout: the desk is
])
def test_the_banner_offers_the_layout_the_screen_fits(qapp, app_sheet, monkeypatch, w, h, on, offered):
    """Through the shell's own offer, not only ``is_phone_shaped``. Wrong implementations: offering phone layout by
    the Duo's shape (every iPad), or using the offer's rule to decide when phone layout already on is kept, which
    offered a half-resolution Duo the desk back."""
    assert _offers(qapp, monkeypatch, w, h, on=on) == offered


@pytest.mark.parametrize("dpr", [1.25, 1.5])
def test_the_banner_offers_the_duo_phone_layout_under_display_scaling(qapp, app_sheet, monkeypatch, dpr):
    """The Duo's unfolded screen at its own 1878 x 2670, with Windows' display scale at 125 % or 150 %: Qt's
    logical available size (1502 x 2088, 1252 x 1732 less a taskbar) names no Duo side. Through the shell's real
    offer, which must read the screen's device pixel ratio. Wrong implementation: comparing the logical size
    against the Duo's device pixels, which offered nothing here."""
    from PySide6.QtGui import QScreen
    monkeypatch.setattr(QScreen, "devicePixelRatio", lambda self: dpr)
    w, h = int(1878 / dpr), int(2670 / dpr) - 48
    assert _offers(qapp, monkeypatch, w, h, on=False) == "phone"


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
    # The swipe a plot page gets is test_phone_plot_swipe.py's subject.
    shell = SimpleNamespace(pt=lambda p: int(p * 2), _swipe_plot_area=lambda _c, _on: None)
    PhoneShell.size_canvas(shell, canvas, True)
    assert canvas.minimumHeight() == 3 * 440
    PhoneShell.size_canvas(shell, canvas, False)
    assert canvas.minimumHeight() == 150


def test_a_matplotlib_toolbar_shown_in_phone_layout_is_hidden(qapp, app_sheet):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    from widgets.mpl_toolbar import MplToolbar      # the real one: it declares its own kind

    win = _window()
    QVBoxLayout(win)
    shell = _shell(win)
    canvas = FigureCanvasQTAgg(Figure())
    canvas.setParent(win)
    bar = MplToolbar(canvas, win)
    win.layout().addWidget(bar)
    bar.show()
    phone_layout.set_on(True)
    try:
        shell.adapt(bar)
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
    from widgets.info_icon import info_icon
    host = QWidget()
    hint = info_icon(host, "What this number means")       # the app's factory: it declares itself
    filt = _PhoneEventFilter(QObject())
    tap = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2),
                      Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    assert filt.eventFilter(hint, tap) is True
    assert shown == ["What this number means"]
    # A label that only borrows the ⓘ look (a status line) is not a hint, tooltip or not.
    styled = QLabel("a status line", host)
    styled.setObjectName("InfoHint")
    styled.setToolTip("not a hint")
    assert filt.eventFilter(styled, tap) is False
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


def _fit_and_lay_out(qapp, group_qss=None):
    """Fit a 3-column grid with two row-label columns (a vertical group band
    and per-value labels) into a 500 px page in phone layout, then lay the grid
    out the way the controller does — ``facet_view``'s chrome plus one cell
    host per column — and return (available width, width the grid needs,
    columns, grid spacing)."""
    from PySide6.QtWidgets import QGridLayout, QScrollArea

    from review.features.image_browser.image_table import facet_view
    from review.features.image_browser.image_table._phone_fit import phone_cell_px
    phone_layout.set_on(True)
    scroll = QScrollArea()
    _WINDOWS.append(scroll)
    scroll.setWidgetResizable(True)
    inner = QWidget()
    QVBoxLayout(inner).setContentsMargins(0, 0, 0, 0)
    host = QWidget(inner)
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    inner.layout().addWidget(host)
    scroll.setWidget(inner)
    scroll.resize(500, 400)
    scroll.show()
    _pump(qapp)
    plan = facet_view.FacetPlan()
    plan.left = [("Well B05", 1, 0, 2, 1, True), ("GFP", 1, 1, 1, 1, False),
                 ("DAPI overlay", 2, 1, 1, 1, False)]
    plan.corner = "T:0h  ·  FOV:F001"
    plan.first_row, plan.first_col = 1, 2
    n = 3
    sz = phone_cell_px(None, [[{}] * n], plan, 1.0, grid)
    facet_view.render_facet_chrome(grid, plan)
    margin = getattr(facet_view, "CELL_MARGIN_PX", 2)
    for r in (1, 2):
        for c in range(n):
            cell = QWidget(host)
            cell.setFixedWidth(sz + 2 * margin)
            grid.addWidget(cell, r, 2 + c)
    _pump(qapp)
    avail = scroll.viewport().width()
    bar = scroll.verticalScrollBar()
    if not bar.isVisible():
        avail -= bar.sizeHint().width()
    return avail, grid.minimumSize().width(), n, max(0, grid.horizontalSpacing())


@pytest.mark.parametrize("group_padding", [None, "padding:24px 6px;"])
def test_image_table_fitted_columns_fill_the_page_exactly(
        qapp, app_sheet, monkeypatch, group_padding):
    """Phone layout's fit measures the row labels ``facet_view`` draws, rather
    than restating their padding as numbers of its own: at 100 % the columns
    fill the page — no sideways scroll, and no more slack than the spacing
    after the last column plus the rounding of one pixel per column. The
    wrong implementation this rejects is the copied estimate (a group band's
    line height + 16, a label's text + 8, per column spacing + 4), which left
    a strip unused at the desk's styling and scrolled sideways once the band's
    padding grew — the second case widens it by 44 px."""
    from review.features.image_browser.image_table import facet_view
    qapp.setStyleSheet(theme.qss())
    if group_padding:
        base = facet_view._group_qss
        monkeypatch.setattr(
            facet_view, "_group_qss",
            lambda pt: base(pt).replace("padding:2px 6px;", group_padding))
    avail, need, n, spacing = _fit_and_lay_out(qapp)
    assert need <= avail, f"the fitted grid scrolls sideways by {need - avail} px"
    assert avail - need <= spacing + n, (
        f"the fitted grid leaves {avail - need} px of the page unused")


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


def test_a_scroll_area_built_after_the_switch_scrolls_by_a_drag(qapp, app_sheet):
    """Reported: the Properties panel, and other windows, did not scroll by a
    drag in phone layout. Grabbing was a roster of roots fixed at the switch,
    so anything outside it, or built later — a dialog, a page built lazily, a
    combo box's list — took no drag. The sweep now grabs every scroll area it
    finds, and leaving phone layout gives each one back. The wrong
    implementation this rejects grabs only what existed at the switch."""
    from PySide6.QtWidgets import QDialog, QListWidget, QScrollArea

    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    before = QScrollArea(win)
    win.layout().addWidget(before)
    shell = _shell(win)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()            # the test ticks the sweep itself
    assert before.viewport().property("_awDragScroll")
    dialog = QDialog(win)
    _WINDOWS.append(dialog)
    later = QListWidget(dialog)
    assert not later.viewport().property("_awDragScroll")
    shell._sweep(force=False)
    assert later.viewport().property("_awDragScroll"), \
        "a list in a window opened after the switch does not scroll by a drag"
    shell.set_on(False)
    _pump(qapp)
    assert not before.viewport().property("_awDragScroll")
    assert not later.viewport().property("_awDragScroll")


def _wells_window(qapp, picker):
    """A window with a page, and a rail the way Review's holds its picker:
    a head, the plate slot, the All / None footer, then saved selections."""
    from PySide6.QtWidgets import QLabel
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win).setContentsMargins(0, 0, 0, 0)
    win.layout().addWidget(QWidget(win), 1)          # the page
    rail = QWidget(win)
    QVBoxLayout(rail)
    head, slot, footer, tail = QLabel("dataset"), QWidget(), QWidget(), QLabel("saved")
    slot.setMinimumHeight(120)
    footer.setMinimumHeight(40)
    for w in (head, slot, footer, tail):
        rail.layout().addWidget(w)
    rail.hide()                                      # the rail is in ☰, closed
    selected = {"A01", "A02", "B01"}
    shell = _shell(win, well_picker=lambda: picker(slot, footer),
                   wells_count=lambda: len(selected))
    return win, shell, rail, slot, footer


def test_the_wells_sheet_lends_the_picker_above_the_foot_strip_and_gives_it_back(
        qapp, app_sheet):
    """Picking wells on a phone was ☰, a scroll past the dataset and nine
    section tiles, the taps, then ✕ — with the plot those wells change hidden
    behind the drawer throughout. Wells, in the strip at the foot, lends the
    plate and its All / None to a sheet that rises from above the strip and
    leaves the page in view, then puts each back at its place in the rail.

    Wrong implementations this rejects: a strip left at the top (out of thumb
    reach), a sheet laid over the strip (its "Done" hidden), and a give-back
    that appends to the rail rather than returning each widget to its index."""
    from PySide6.QtTest import QTest
    win, shell, rail, slot, footer = _wells_window(qapp, lambda s, f: [s, f])
    shell.set_on(True)
    _pump(qapp)
    strip = shell._strip
    assert win.layout().indexOf(strip) == win.layout().count() - 1
    assert strip.isVisible()
    assert strip.mapTo(win, strip.rect().bottomLeft()).y() == win.height() - 1, \
        "the strip is not at the foot of the window"
    assert shell.wells_button.isVisible()
    assert shell.wells_button.text() == "Wells · 3"

    shell.wells_button.click()
    QTest.qWait(300)
    sheet = shell._wells_sheet
    assert sheet.isOpen() and sheet.isVisible()
    assert slot.parentWidget() is sheet._aw_holder and footer.parentWidget() is sheet._aw_holder
    assert shell.wells_button.text() == "" and shell.wells_button.toolTip() == "Close the wells"
    strip_top = strip.mapTo(win, strip.rect().topLeft()).y()
    assert sheet.geometry().bottom() < strip_top, "the sheet covers its own Done button"
    assert sheet._backdrop._alpha == 0, "a scrim would dim the plot the wells change"

    shell.wells_button.click()
    QTest.qWait(300)
    assert not sheet.isOpen()
    lay = rail.layout()
    assert (lay.indexOf(slot), lay.indexOf(footer)) == (1, 2)
    assert shell.wells_button.text() == "Wells · 3"


def test_a_page_with_no_picker_offers_no_wells_button(qapp, app_sheet):
    _win, shell, *_ = _wells_window(qapp, lambda s, f: [])
    shell.set_on(True)
    _pump(qapp)
    assert not shell.wells_button.isVisible()
    shell.open_wells()                 # nothing to lend: nothing opens
    assert shell._wells_sheet is None


def test_the_page_menu_lists_this_section_s_pages_and_never_the_sections(qapp, app_sheet):
    """Reported: the bottom-left page menu duplicated ☰ — it listed every other
    Review section under "Go to", which ☰'s section tiles already offer. The
    page menu now lists only the current section's pages; changing section is
    ☰'s alone. A section with no pages has no page button at all, rather than
    a menu with nothing in it."""
    from PySide6.QtWidgets import QTabBar
    from widgets.rail_nav import RailNav
    qapp.setStyleSheet(theme.qss())
    win = _window()
    QVBoxLayout(win)
    nav = RailNav(win)
    for key in ("Home", "Plotting", "Statistics"):
        nav.addItem(key)
    nav.setCurrentKey("Plotting")
    pages = QTabBar(win)
    for title in ("Bar Plots", "Line Graphs", "Scatter Plot"):
        pages.addTab(title)
    pages.setCurrentIndex(1)
    current_bar = {"bar": pages}
    shell = _shell(win, rail_nav=nav, current_page_bar=lambda: current_bar["bar"])
    shell.set_on(True)
    _pump(qapp)
    shell.refresh()
    assert shell.page_button.isVisible() and shell.page_button.text() == "Line Graphs ▾"
    shell._fill_page_menu()
    picks = [a.text() for a in shell._page_menu.actions() if not a.isSeparator() and a.isEnabled()]
    assert picks == ["Bar Plots", "Line Graphs", "Scatter Plot"]
    # A section with no pages of its own: no page button.
    current_bar["bar"] = None
    nav.setCurrentKey("Statistics")
    shell.refresh()
    assert not shell.page_button.isVisible()


def test_an_empty_plot_s_hint_says_where_the_plate_is_on_a_phone():
    """At the desk the plate is in the rail at the left; on a phone it is under
    Wells at the foot, and Saved selections are in ☰."""
    from review.core.empty_state_phone import phone_wording
    desk = "Click wells on the plate at the left, or open Saved selections below it."
    assert phone_wording(desk) == desk
    phone_layout.set_on(True)
    assert phone_wording(desk) == ("Tap wells on the plate (Wells, at the foot of the "
                                   "screen), or open Saved selections in ☰.")


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


def test_a_close_confirmation_that_could_not_be_asked_keeps_the_window_open(
        monkeypatch):
    """ARCHITECTURE §15: a confirmation that could not be obtained is not
    consent. Offscreen, the autouse ``_no_modal_dialogs`` makes
    ``QMessageBox.question`` raise — the dialog-unavailable case. The close
    must be refused (event ignored, no exception out of ``closeEvent``) and
    nothing that a close tears down may run: the run keeps going and the
    window stays whole, rather than leaving a closed window with the run
    stopped by nobody and Review never torn down.

    The wrong implementation this rejects is the bare ask: the exception
    escapes ``closeEvent`` with the event still accepted, so Qt closes the
    window having skipped every teardown step below the question.
    """
    import types

    import allwell
    from PySide6.QtGui import QCloseEvent

    phone_layout.set_on(True)
    monkeypatch.setattr(allwell, "_analyze_run_busy", lambda _a: True)
    torn = []
    monkeypatch.setattr(allwell, "_stop_analyze_runner",
                        lambda _a: torn.append("stop_analyze_runner"))
    monkeypatch.setattr(allwell, "_save_review_before_close",
                        lambda _r: torn.append("save_review"))
    win = types.SimpleNamespace(_analyze=object(), _review=None,
                                _datasets=None)
    win._confirm_close_in_phone_layout = (
        lambda: allwell.AllWellApp._confirm_close_in_phone_layout(win))
    win._save_window_state = lambda: torn.append("save_window_state")

    ev = QCloseEvent()
    ev.accept()
    try:
        allwell.AllWellApp.closeEvent(win, ev)
    except AssertionError as e:   # ModalDialogInTest is an AssertionError
        raise AssertionError(
            "a close confirmation that raised escaped closeEvent with the "
            f"event accepted, so the window closes without consent: {e}") from e
    assert not ev.isAccepted(), "an unanswerable close question closed the window"
    assert torn == [], f"a refused close still ran teardown: {torn}"
    # And the answer the user does give still decides it.
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.Close)
    assert allwell.AllWellApp._confirm_close_in_phone_layout(win) is True
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.Cancel)
    assert allwell.AllWellApp._confirm_close_in_phone_layout(win) is False


def _qss_rule(sheet: str, selector_start: str) -> str:
    """The body of the first rule in *sheet* whose selector list starts with
    *selector_start*."""
    import re
    m = re.search(re.escape(selector_start) + r"[^{]*\{([^}]*)\}", sheet)
    assert m, f"no rule starting {selector_start!r} in the touch stylesheet"
    return m.group(1)


def test_the_stylesheet_touch_floors_follow_the_named_constants(monkeypatch):
    """``core.phone_layout`` names the floors; the touch stylesheet must read
    them. Values chosen so nothing else in the sheet can produce them, and so
    the literal 44 / 48 the sheet once restated would leave the old floors in
    place — the wrong implementation this rejects."""
    monkeypatch.setattr(phone_layout, "TOUCH_MIN_PT", 50)
    monkeypatch.setattr(phone_layout, "TOUCH_ROW_PT", 61)
    sheet = theme._touch_qss(1.0)
    assert "min-height: 50px" in _qss_rule(sheet, "QPushButton, QToolButton")
    assert "min-height: 61px" in _qss_rule(sheet, "QComboBox QAbstractItemView")
    assert "min-height: 61px" in _qss_rule(sheet, "QFrame#RailNavRow")
    assert "min-height: 61px" in _qss_rule(sheet, 'QWidget#Drawer[phoneSheet="true"]')
    assert "min-height: 50px" in _qss_rule(sheet, "QCheckBox, QRadioButton")
    assert "44px" not in sheet and "48px" not in sheet, (
        "a touch floor in the stylesheet is a literal beside the named constant")


def test_scroll_bars_are_a_thumb_wide_in_phone_layout(monkeypatch):
    """Reported: on a phone the scroll bar itself is hard to use — 10 pt wide
    with a handle that could shrink to 24 px. The track is ``SCROLLBAR_PT``
    across, the handle drawn ``SCROLL_HANDLE_PT`` thick inside it and never
    shorter than a row. Odd values, so a literal could not produce them."""
    monkeypatch.setattr(phone_layout, "SCROLLBAR_PT", 23)
    monkeypatch.setattr(phone_layout, "SCROLL_HANDLE_PT", 9)
    monkeypatch.setattr(phone_layout, "TOUCH_ROW_PT", 61)
    sheet = theme._touch_qss(2.0)
    assert "width: 46px" in _qss_rule(sheet, "QScrollBar:vertical")
    assert "height: 46px" in _qss_rule(sheet, "QScrollBar:horizontal")
    vertical = _qss_rule(sheet, "QScrollBar::handle:vertical")
    assert "min-height: 122px" in vertical
    assert "margin: 0px 14px" in vertical            # (46 − 18) / 2 each side
    assert "min-width: 122px" in _qss_rule(sheet, "QScrollBar::handle:horizontal")


def test_the_text_size_buttons_follow_the_named_touch_sizes(
        qapp, app_sheet, monkeypatch):
    """The ⋯ menu's Text size buttons are TOUCH_ROW_PT × TOUCH_MIN_PT, spaced
    TOUCH_GAP_PT apart — read from the constants, not restated beside them."""
    from PySide6.QtWidgets import QToolButton
    monkeypatch.setattr(phone_layout, "TOUCH_MIN_PT", 50)
    monkeypatch.setattr(phone_layout, "TOUCH_ROW_PT", 61)
    monkeypatch.setattr(phone_layout, "TOUCH_GAP_PT", 11)
    win = _window()
    QVBoxLayout(win)
    shell = _shell(win)
    row = shell._text_size_row().defaultWidget()
    buttons = row.findChildren(QToolButton, "PhoneTextSizeButton")
    assert len(buttons) == len(phone_layout.TEXT_SIZES)
    for b in buttons:
        assert (b.minimumWidth(), b.minimumHeight()) == (shell.pt(61), shell.pt(50))
    assert row.layout().spacing() == shell.pt(11)


def test_every_kind_phone_layout_adapts_is_watched_and_dispatched(qapp):
    """``PhoneShell.adapt`` and ``_phone_relevant`` read one roster: every
    kind ``_adapt_kind`` can name has an adapter, every adapter's kind can be
    named, and a widget of each kind — the real classes, named the way the app
    names them — is watched. The drift this rejects: a type taught to
    ``adapt`` but not to the watch list is never watched, so a widget built
    after the switch never gets its phone form, and nothing fails."""
    from matplotlib.figure import Figure

    from ui import phone_shell as ps
    from widgets import form_registry
    from widgets.canvas import make_canvas
    from widgets.control_band import ControlBand
    from widgets.info_icon import info_icon
    from widgets.mpl_toolbar import MplToolbar
    from widgets.stepper import Stepper

    assert set(ps.PhoneShell._ADAPTERS) == set(ps.ADAPTED_KINDS)
    for meth in ps.PhoneShell._ADAPTERS.values():
        assert callable(getattr(ps.PhoneShell, meth, None)), meth
    host = QWidget()
    canvas = make_canvas(Figure(), host)
    block = QWidget(host)
    form_registry.declare(block, "controls")
    block.setStacked = lambda _on: None
    custom = QWidget(host)
    form_registry.register(custom, lambda _on: None)      # a view's own form
    widgets = [ControlBand(host), MplToolbar(canvas, host), Stepper(host), block, custom, canvas,
               info_icon(host, "An explanation")]
    named = {ps._adapt_kind(w) for w in widgets}
    assert named == set(ps.ADAPTED_KINDS), named
    assert all(ps._phone_relevant(w) for w in widgets)
    assert ps._adapt_kind(QLabel("plain", host)) is None
    host.deleteLater()


def test_the_rail_drawer_lends_the_current_mode_s_panel_and_gives_all_back(qapp):
    """As Review's chokepoint drives it: in phone layout only
    the current mode's panel shows, in the drawer; at the desk every panel is
    back in its splitter under the desk's visibility rule (the rail in Review
    only, the others whenever expanded)."""
    host = QWidget()
    host.resize(800, 600)
    panels = {}
    for mode in ("review", "analyze", "datasets"):
        split = QSplitter(Qt.Horizontal, host)
        panels[mode] = QWidget()
        split.addWidget(panels[mode])
        split.addWidget(QWidget())
    drawer = _rail_drawer(host, panels)
    host.show()
    phone_layout.set_on(True)
    drawer.apply("analyze", False)
    assert all(p.parentWidget() is drawer.contentWidget() for p in panels.values())
    assert {m for m, p in panels.items() if not p.isHidden()} == {"analyze"}
    assert drawer.isOpen()
    phone_layout.set_on(False)
    drawer.apply("analyze", False)
    assert all(isinstance(p.parentWidget(), QSplitter) for p in panels.values())
    assert {m for m, p in panels.items() if not p.isHidden()} == {"analyze", "datasets"}
    assert not any(drawer.is_lent(p) for p in panels.values())
    host.deleteLater()


# ── following the layout for as long as an owner lives ──────────────────────

def test_follow_calls_back_while_the_owner_lives_and_leaves_when_it_is_gone(qapp):
    """``phone_layout.follow``: a page-lifetime listener. Wrong implementations: one that never removes itself (a
    process-wide listener keeping a deleted page's callbacks alive and calling into it), and one that checks only the
    Python wrapper (which outlives the C++ widget and then raises on first use)."""
    import gc

    from PySide6.QtCore import QObject

    seen = []
    owner = QObject()
    before = len(phone_layout._listeners)
    phone_layout.follow(owner, seen.append)
    assert len(phone_layout._listeners) == before + 1
    phone_layout.set_on(True)
    phone_layout.set_on(False)
    assert seen == [True, False]
    # The C++ object is deleted while the wrapper is still referenced: the next layout change drops the listener
    # without calling it.
    import shiboken6
    shiboken6.delete(owner)
    phone_layout.set_on(True)
    phone_layout.set_on(False)
    assert seen == [True, False], "the listener was called for a deleted owner"
    assert len(phone_layout._listeners) == before
    # And a collected owner is dropped the same way.
    gone = QObject()
    phone_layout.follow(gone, seen.append)
    del gone
    gc.collect()
    phone_layout.set_on(True)
    phone_layout.set_on(False)
    assert seen == [True, False] and len(phone_layout._listeners) == before


def _band_window(qapp):
    """A window with a page whose control band holds one wide row — four buttons in one scope group, as the Nanowells
    QC band's "Problem:" filters are — managed by a shell the way Review's pages are."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    from widgets.control_band import ControlBand
    win = QWidget()
    QVBoxLayout(win)
    page = QWidget(win)
    QVBoxLayout(page)
    win.layout().addWidget(page)
    band = ControlBand(page)
    page.layout().addWidget(band)
    buttons = [QPushButton(f"Problem filter number {i}") for i in range(4)]
    band.add_scope("Show", *buttons)
    shell = _shell(win, band_roots=lambda: [page])
    win.resize(400, 700)
    return win, shell, band, buttons


def test_the_controls_sheet_fits_a_band_row_wider_than_the_phone_and_gives_it_back(qapp, app_sheet):
    """Nanowells QC's band put four "Problem:" filters in one scope group, and in the ⚙ Controls sheet the form kept
    the group on one line beside its label: 769 px of content in a 414 px sheet, which scrolled sideways (as did the
    sheets of every Nanowells page, Multivariate, Spatial and the Image Browser's). The sheet is now fitted like a
    Datasets page (``ui/phone_fit``): the row stacks until it fits. Closing the sheet gives the band its row back,
    and the band opens again — the first version of this fit wrapped Qt's layout items for Python, and the band's own
    layout then raised "Internal C++ object already deleted" on the second opening."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea
    win, shell, band, buttons = _band_window(qapp)
    shell.set_on(True)
    _pump(qapp)
    for opening in range(2):
        shell.open_controls()
        QTest.qWait(300)
        area = next(a for a in shell._sheet.findChildren(QScrollArea) if a.isVisible())
        content = area.widget()
        assert band.form() is not None
        assert content.minimumSizeHint().width() <= area.viewport().width(), (
            f"opening {opening + 1}: the sheet's content needs {content.minimumSizeHint().width()} px of "
            f"{area.viewport().width()}")
        shell.close_controls(now=True)
        _pump(qapp)
        assert band.form() is None
        assert [band._scope.itemAt(i).widget() is not None for i in range(band._scope.count())], \
            "the band's own row is empty after the sheet closed"


# ── the iPhone Duo: a foldable whose screens are both near-square ──────────

#: The Duo's screens at their published resolutions, each held both ways, and a
#: 19.5:9 phone's.
_DUO_IN_UP, _DUO_IN_SIDE = QRect(0, 0, 1878, 2670), QRect(0, 0, 2670, 1878)
_DUO_OUT_UP, _DUO_OUT_SIDE = QRect(0, 0, 1398, 2034), QRect(0, 0, 2034, 1398)
_PHONE_UP, _PHONE_SIDE = QRect(0, 0, 1179, 2556), QRect(0, 0, 2556, 1179)


@pytest.mark.parametrize("w,h,screen", [
    (1878, 2670, "inner"), (2670, 1878, "inner"),     # unfolded, at Apple's resolution, either way up
    (1398, 2034, "outer"), (2034, 1398, "outer"),     # folded
    (939, 1335, "inner"), (699, 1017, "outer"),       # the same shapes at half the pixels: read by shape
    (1024, 768, "inner"), (2048, 1536, "inner"),      # 4:3, which the user asked to be a foldable
    (2076, 2152, "inner"),                            # an Android fold's inner screen: squarer still
    (2256, 1504, None),                               # 3:2, a Surface laptop
    (1179, 2556, None), (750, 1334, None),            # phones: 19.5:9 and 16:9
    (1920, 1080, None),                               # a monitor
])
def test_which_of_a_foldable_s_screens_a_screen_is(w, h, screen):
    """Wrong implementations: one threshold for both screens (the Duo folded is near-square too, so it read as
    unfolded and took the inner screen's size), or one that let 3:2 or a 16:9 phone in."""
    assert phone_layout.duo_screen(w, h) == screen
    assert phone_layout.is_fold_shaped(w, h) is (screen is not None)
    assert phone_layout.is_unfolded(w, h) is (screen == "inner")


@pytest.mark.parametrize("w,h,expected", [
    (1878, 2670, 1878 / 626),  # unfolded: 626 pt across
    (2670, 1878, 1878 / 626),  # sideways: the same unit, as a phone keeps its own
    (1398, 2034, 1398 / 466),  # folded: 466 pt across
    (1179, 2556, 1179 / 390),  # a phone still takes 390
    (626, 890, 1.0),           # a desktop already at the inner screen's points
])
def test_a_duo_screen_s_short_side_is_its_published_width_in_points(w, h, expected):
    """Wrong implementation: a phone's 390 for every screen, which drew the unfolded Duo's targets and text 1.6 times
    their size and the folded Duo's 1.2 times."""
    assert phone_layout.phone_unit(w, h) == pytest.approx(expected)


@pytest.mark.parametrize("w,h,expected", [
    (2670, 1878, True), (2034, 1398, True),           # the Duo sideways, at its own size: offered phone layout
    (1878, 2670, True), (1398, 2034, True),           # and upright
    (2670, 1830, True), (1878, 2622, True),           # less a 48 px taskbar off either side
    (2034, 1278, True),                               # less a 120 px one (250 % scaling)
    (939, 1335, True), (699, 1017, True),             # at half its pixels, too small for the desk window
    (2670, 1700, False),                              # more than a taskbar short of the Duo: not it
    (2048, 1536, False),                              # a 4:3 monitor is the Duo's shape, not its size
    (2256, 1504, False), (1920, 1080, False),         # nor are 3:2 and 16:9
])
def test_the_duo_is_offered_phone_layout_by_its_resolution(w, h, expected):
    """Wrong implementations: offering it by the Duo's shape, which every iPad and 4:3 monitor has (the report:
    an iPad over VNC was offered phone layout); or an exact match that the taskbar's strip off the available
    geometry defeats."""
    assert phone_layout.is_phone_shaped(w, h) is expected


#: Every current iPad's native resolution, landscape: 10.2", 10.9"/11" Air, 11" Pro, 11" Pro (M4), 12.9"/13",
#: 13" Pro (M4), mini. Between them 1.33 to 1.52 : 1, the Duo's 1.42 and 1.45 inside that.
_IPADS = [(2160, 1620), (2360, 1640), (2388, 1668), (2420, 1668), (2732, 2048), (2752, 2064), (2266, 1488)]


@pytest.mark.parametrize("w,h", _IPADS)
@pytest.mark.parametrize("upright", [False, True], ids=["landscape", "upright"])
@pytest.mark.parametrize("taskbar", [0, 48])
def test_an_ipad_over_vnc_is_not_offered_phone_layout(w, h, upright, taskbar):
    """The report: viewing the app over VNC on an iPad offered phone layout. Landscape an iPad is the Duo's shape;
    upright it is taller than wide. So the wrong implementations are reading the Duo from its shape, and calling
    every upright screen a phone — 23 of these 28 cases were offered phone layout by one or the other."""
    if upright:
        w, h = h, w
    h -= taskbar
    assert phone_layout.is_phone_shaped(w, h) is False


@pytest.mark.parametrize("w,h", [(1179, 2556 - 48), (1290, 2796), (1440, 2560 - 48)])
def test_a_phone_upright_at_full_resolution_is_still_a_phone(w, h):
    """Wide and tall enough for the desk window, but a phone's proportions (2.17, 1.78 less a taskbar): narrowing
    the upright rule for the iPad must not let these through. Wrong implementation: 'upright and too narrow for the
    desk window' alone, or an aspect floor above 1.7."""
    assert phone_layout.is_phone_shaped(w, h) is True


@pytest.mark.parametrize("screen,wide,short", [
    (_PHONE_UP, False, False),
    (_PHONE_SIDE, True, True),
    (_DUO_IN_UP, True, False),     # 626 pt across
    (_DUO_IN_SIDE, True, False),   # 626 pt tall: the full bar
    (_DUO_OUT_UP, False, False),   # 466 pt across: a phone's form
    (_DUO_OUT_SIDE, True, True),   # 466 pt tall: the slim bar
])
def test_the_shell_reads_a_screen_s_size_in_points(qapp, screen, wide, short):
    from tests._screens import FakeScreen
    from ui.phone_shell import PhoneHandles, PhoneShell
    host = QWidget()
    try:
        shell = PhoneShell(host, PhoneHandles())
        with FakeScreen(screen):
            assert (shell.is_wide(), shell.is_short()) == (wide, short)
    finally:
        host.deleteLater()


def _history_shell(qapp):
    from PySide6.QtWidgets import QVBoxLayout

    from ui.page_history import PageHistory
    from ui.phone_drawer import RailDrawer
    win = _window()
    QVBoxLayout(win)
    drawer = RailDrawer(win, panels=dict, top_inset=lambda: 0, on_closed_inside=lambda: None)
    history = PageHistory(win, location=lambda: "home", go=lambda _loc: None)
    return _shell(win, rail_drawer=drawer, history=history), drawer


@pytest.mark.parametrize("screen", [_DUO_IN_UP, _DUO_IN_SIDE], ids=["upright", "sideways"])
def test_the_unfolded_duo_gets_a_full_bar_a_rail_panel_and_forward_in_the_bar(qapp, app_sheet, screen):
    """The inner screen is a phone's touch targets on a screen 1.6 times as wide: the full 52 pt bar with 48 pt ☰
    and ⋯ (sideways too: at 626 pt tall it has the height a phone on its side lacks), the rail a 360 pt panel rather
    than the whole width less a strip, and Forward beside Back rather than in ⋯. Wrong implementation: one keyed on
    orientation alone, which gave the Duo held upright a 570 pt rail and Forward in ⋯, and sideways the slim bar."""
    from tests._screens import FakeScreen
    from ui.phone_shell import LANDSCAPE_PANEL_PT
    with FakeScreen(screen):
        shell, drawer = _history_shell(qapp)
        shell.set_on(True, restyle=False)
        try:
            _pump(qapp)
            u = 1878 / 626
            assert shell.unit() == pytest.approx(u)
            assert shell._h.band.height() == round(52 * u)
            assert shell.menu_button.width() == round(48 * u)
            assert drawer._width_hint() == round(LANDSCAPE_PANEL_PT * u)
            assert not shell.forward_button.isHidden()
            shell._fill_menu()
            assert not [a for a in shell._menu.actions() if a.text().startswith("Forward")]
        finally:
            shell.set_on(False)
            _pump(qapp)


def test_the_folded_duo_is_a_phone_at_its_own_size(qapp, app_sheet):
    """Folded, the Duo is 466 pt across: a phone's form — the full bar, the rail the width less ``RAIL_GAP_PT``,
    Forward in ⋯ — at a phone point of 1398 / 466 px; sideways, the slim bar of a phone on its side."""
    from tests._screens import FakeScreen
    from ui.phone_shell import LANDSCAPE_PANEL_PT, RAIL_GAP_PT
    u = 1398 / 466
    with FakeScreen(_DUO_OUT_UP) as screen:
        shell, drawer = _history_shell(qapp)
        shell.set_on(True, restyle=False)
        try:
            _pump(qapp)
            assert shell.unit() == pytest.approx(u)
            assert shell._h.band.height() == round(52 * u)
            assert drawer._width_hint() == drawer._host.width() - round(RAIL_GAP_PT * u)
            assert shell.forward_button.isHidden()
            screen.turn(_DUO_OUT_SIDE)
            shell._apply_sizes()
            _pump(qapp)
            assert shell._h.band.height() == round(44 * u)
            assert drawer._width_hint() == round(LANDSCAPE_PANEL_PT * u)
        finally:
            shell.set_on(False)
            _pump(qapp)


def test_a_phone_keeps_its_upright_and_sideways_forms(qapp, app_sheet):
    """The phone itself is unchanged: upright, the full bar, the rail the whole
    width less ``RAIL_GAP_PT``, Forward in ⋯; sideways the slim bar with 40 pt
    ☰ and ⋯, the rail a panel and Forward in the bar."""
    from tests._screens import FakeScreen
    from ui.phone_shell import LANDSCAPE_PANEL_PT, RAIL_GAP_PT
    u = 1179 / 390
    with FakeScreen(_PHONE_UP) as screen:
        shell, drawer = _history_shell(qapp)
        shell.set_on(True, restyle=False)
        try:
            _pump(qapp)
            assert shell._h.band.height() == round(52 * u)
            assert drawer._width_hint() == drawer._host.width() - round(RAIL_GAP_PT * u)
            assert shell.forward_button.isHidden()
            shell._fill_menu()
            assert [a for a in shell._menu.actions() if a.text().startswith("Forward")]
            screen.turn(_PHONE_SIDE)
            shell._apply_sizes()
            _pump(qapp)
            assert shell._h.band.height() == round(44 * u)
            assert shell.menu_button.width() == round(40 * u)
            assert drawer._width_hint() == round(LANDSCAPE_PANEL_PT * u)
            assert not shell.forward_button.isHidden()
        finally:
            shell.set_on(False)
            _pump(qapp)


# ── the unfolded Duo's Controls, docked left of the page ────────────────────

#: The Duo's inner screen at half its pixels, so the docked column (300 pt, 450 px) and a page beside it fit the
#: offscreen platform's 800 px window.
_DUO_HALF_UP = QRect(0, 0, 939, 1335)
_DUO_HALF_FOLDED = QRect(0, 0, 699, 1017)


def _docked_window(qapp):
    """A window whose page, with a control band, sits in a splitter as Review's pages sit in ``_h_pane``."""
    from PySide6.QtWidgets import QPushButton, QSplitter, QVBoxLayout

    from widgets.control_band import ControlBand
    win = _window()
    QVBoxLayout(win)
    split = QSplitter(win)
    win.layout().addWidget(split)
    page = QWidget()
    QVBoxLayout(page)
    split.addWidget(page)
    band = ControlBand(page)
    page.layout().addWidget(band)
    band.add_scope("Channel", QPushButton("GFP"))
    shell = _shell(win, band_roots=lambda: [page], centre_split=split)
    return win, shell, split, page, band


def test_the_unfolded_duo_docks_the_page_s_controls_left_of_the_page(qapp, app_sheet):
    """Unfolded, the page's controls are a 300 pt column *in* the layout, left of the page — not an overlay over it —
    opened with the page, hidden and shown again by ⚙ Controls, and the choice held across a refresh. Folding the
    phone takes the column away and gives the page its controls back.

    Wrong implementations: the phone's overlay sheet on the Duo (the page under the controls, closed until asked
    for), a column that does not follow ⚙ Controls, or one left behind in the splitter once the phone folds."""
    from tests._screens import FakeScreen
    from ui.phone_dock import DockedSheet
    with FakeScreen(_DUO_HALF_UP) as screen:
        win, shell, split, page, band = _docked_window(qapp)
        shell.set_on(True, restyle=False)
        try:
            _pump(qapp)
            u = 939 / 626
            assert shell.is_docked()
            sheet = shell._sheet
            assert isinstance(sheet, DockedSheet) and sheet.isOpen()
            assert split.indexOf(sheet) == 0 and split.indexOf(page) == 1
            assert sheet.isAncestorOf(band)
            # Upright the column is 626 - 360 = 266 pt, so the page beside it keeps the narrowest phone's width.
            assert sheet.width() == round(266 * u) - split.handleWidth()
            assert page.mapTo(win, page.rect().topLeft()).x() >= sheet.width(), "the page is under the controls"
            # ⚙ Controls stays in reach while the page's controls are in the column (it counted the lent band as no
            # page's, and went).
            shell.refresh()
            _pump(qapp)
            assert shell._sheet.isOpen() and shell.controls_button.isVisibleTo(win)
            assert shell.controls_button.text() == "Hide controls"
            # ⚙ Controls hides it, and a refresh (a page change) does not bring it back.
            shell.toggle_controls()
            _pump(qapp)
            assert not sheet.isOpen() and split.indexOf(sheet) < 0 and page.isAncestorOf(band)
            shell.refresh()
            _pump(qapp)
            assert not shell._sheet.isOpen()
            shell.toggle_controls()
            _pump(qapp)
            assert shell._sheet.isOpen() and shell._sheet.isAncestorOf(band)
            # A page with no controls on screen (here: the page gone): the column goes and the band goes home.
            page.hide()
            shell.refresh()
            _pump(qapp)
            assert not shell._sheet.isOpen() and page.isAncestorOf(band)
            page.show()
            shell.refresh()
            _pump(qapp)
            assert shell._sheet.isOpen() and shell._sheet.isAncestorOf(band)
            # Sideways the screen is 890 pt across, so the column takes its whole 300 pt.
            screen.turn(QRect(0, 0, 1335, 939))
            shell._on_geometry_changed(None)
            _pump(qapp)
            assert shell._sheet.isOpen() and shell._sheet.width() == round(300 * u) - split.handleWidth()
            # Folded: no column, and the controls are back on the page.
            screen.turn(_DUO_HALF_FOLDED)
            # A refresh alone takes the column away (a screen change that reached the window without a geometry
            # change of its own), and the geometry change after it restyles for the folded screen.
            shell.refresh()
            _pump(qapp)
            assert split.count() == 1 and page.isAncestorOf(band)
            shell._on_geometry_changed(None)
            _pump(qapp)
            assert not shell.is_docked()
            assert split.count() == 1 and page.isAncestorOf(band)
            assert not (shell._sheet is not None and shell._sheet.isOpen())
        finally:
            shell.set_on(False)
            _pump(qapp)
    assert split.count() == 1 and page.isAncestorOf(band)


def test_a_phone_s_controls_stay_an_overlay(qapp, app_sheet):
    """On a phone the same window keeps the overlay sheet, closed until ⚙ Controls opens it."""
    from tests._screens import FakeScreen
    from widgets.drawer import Drawer
    with FakeScreen(QRect(0, 0, 432, 936)):
        win, shell, split, page, band = _docked_window(qapp)
        shell.set_on(True, restyle=False)
        try:
            _pump(qapp)
            assert not shell.is_docked()
            assert shell._sheet is None or not shell._sheet.isOpen()
            assert split.count() == 1
            shell.open_controls()
            _pump(qapp)
            assert isinstance(shell._sheet, Drawer) and split.count() == 1
        finally:
            shell.set_on(False)
            _pump(qapp)
