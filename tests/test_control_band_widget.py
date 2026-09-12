"""Behaviour of the shared ControlBand and ModeBar widgets.

Single widgets, no ``WellViewerApp``: anything touching the ``viewer_app``
fixture is auto-marked ``slow`` and excluded from the lane CI runs, which would
make these invisible.

Each assertion here corresponds to a defect that showed up while mocking the
band against real screenshots — right-alignment that never happened, an action
zone drifting half a row down, a band collapsing behind its own buttons, and a
label wrapping away from its combo.
"""

import pytest

pytest.importorskip("PySide6")

from ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def styled_app(qapp):
    qapp.setStyleSheet(theme.qss())
    return qapp


def _band(styled_app, n_scope=3, n_actions=3):
    from PySide6.QtWidgets import QComboBox, QPushButton
    from widgets.control_band import ControlBand

    band = ControlBand()
    for i in range(n_scope):
        cb = QComboBox()
        cb.addItem(f"value {i}")
        band.add_scope(f"Label {i}", cb)
    for i in range(n_actions):
        band.add_action(QPushButton(f"Action {i}"))
    return band


# ── the right zone is genuinely flush ────────────────────────────────────────
def test_actions_stay_flush_to_the_right_edge_at_every_width(styled_app):
    """`FlowLayout.addStretch()` is a documented no-op, so the five control rows
    whose comments claim right-alignment never had any. The band must.

    Checking only "the last widget touches the edge" would pass for a plain
    QHBoxLayout too, because a box layout stretches its children to fill the
    width — the buttons reach the edge by being deformed, not by being aligned.
    So this also asserts each action keeps its natural width.
    """
    band = _band(styled_app)
    band.show()
    for width in (1400, 1000, 760, 520):
        band.setFixedWidth(width)
        band.adjustSize()
        styled_app.processEvents()
        actions = [band.layout().itemAt(1).layout().itemAt(i).widget()
                   for i in range(band.action_count())]
        rightmost = max(a.geometry().right() for a in actions)
        margin = band.layout().contentsMargins().right()
        assert rightmost >= width - margin - 2, (
            f"at {width}px the right zone ends at {rightmost}, "
            f"{width - margin - rightmost}px short of the edge"
        )
        for a in actions:
            assert abs(a.width() - a.sizeHint().width()) <= 2, (
                f"at {width}px action '{a.text()}' was stretched to {a.width()}px "
                f"from a natural {a.sizeHint().width()}px — the row is filling, "
                "not right-aligning"
            )


def test_action_zone_is_top_aligned_when_the_scope_zone_wraps(styled_app):
    """Without AlignTop the buttons centre against a wrapped scope zone."""
    band = _band(styled_app, n_scope=8)
    band.show()
    band.setFixedWidth(420)          # forces the scope flow onto several rows
    band.adjustSize()
    styled_app.processEvents()
    actions = [band.layout().itemAt(1).layout().itemAt(i).widget()
               for i in range(band.action_count())]
    top_margin = band.layout().contentsMargins().top()
    for a in actions:
        assert a.geometry().top() <= top_margin + 4, (
            f"action '{a.text()}' sits {a.geometry().top()}px down; "
            "it should hug the top of the band"
        )


# ── the height floor ─────────────────────────────────────────────────────────
def test_band_with_no_scope_controls_still_fits_its_actions(styled_app):
    """FlowLayout.heightForWidth returns ~0 when empty; the band must not
    collapse behind its own buttons."""
    band = _band(styled_app, n_scope=0, n_actions=2)
    band.show()
    band.setFixedWidth(900)
    styled_app.processEvents()
    actions = [band.layout().itemAt(1).layout().itemAt(i).widget()
               for i in range(band.action_count())]
    needed = max(a.sizeHint().height() for a in actions)
    assert band.minimumSizeHint().height() >= needed, (
        f"band minimum {band.minimumSizeHint().height()}px is shorter than its "
        f"{needed}px actions"
    )


def test_one_row_is_the_same_height_whatever_the_row_holds(styled_app):
    """The band is chrome shared by every page, so a single row is a constant.

    Qt sizes each control from its own font and padding — a QPushButton at 24px,
    a LabeledComboBox at 25, a QToolButton at 27, a QComboBox at 28 — which made
    an unwrapped band 41, 43, 44 or 49px tall purely from which control a page
    happened to put in it, and started the content under it at four different
    heights. A page with no controls at all reserves the same row, which is what
    lets ``widgets.page_body`` keep a band-less page level with its neighbours.
    """
    from PySide6.QtWidgets import (
        QComboBox, QPushButton, QToolButton, QVBoxLayout, QWidget,
    )
    from widgets.control_band import ControlBand

    def _tool_button(parent):
        # With a label, as every tool button in the app's bands has one. Bare,
        # Qt gives it a taller default than any real control in a band.
        btn = QToolButton(parent)
        btn.setText("Display ▾")
        return btn

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    cases = {}
    for name, make in (
        ("empty", None),
        ("button", lambda b: b.add_action(QPushButton("Clear", b))),
        ("combo", lambda b: b.add_scope("Channel", QComboBox(b))),
        ("toolbutton", lambda b: b.add_action(_tool_button(b))),
    ):
        band = ControlBand(host)
        if make is not None:
            make(band)
        layout.addWidget(band)
        cases[name] = band
    layout.addWidget(QWidget(), 1)
    host.resize(1200, 600)
    host.show()
    styled_app.processEvents()

    heights = {name: band.height() for name, band in cases.items()}
    # Sameness first, and stated without ``band_height()`` — the claim is that
    # four bands holding four different controls agree, which is measurable on
    # any version of this widget. (A guard whose only assertion names a constant
    # the pre-fix code lacks raises AttributeError there and measures nothing.)
    assert len(set(heights.values())) == 1, f"single-row bands differ: {heights}"
    assert set(heights.values()) == {ControlBand.band_height()}, (
        f"a single row is {heights}, not the declared "
        f"{ControlBand.band_height()}px"
    )


# ── labels stay with their controls ──────────────────────────────────────────
def test_label_and_control_are_one_flow_item(styled_app):
    """A label added as its own flow item can wrap away from the widget it
    names — the defect visible on Spatial's stranded `Timepoint:`."""
    from PySide6.QtWidgets import QComboBox
    from widgets.control_band import ControlBand

    band = ControlBand()
    cb = QComboBox()
    cb.addItem("Mean Intensity")
    item = band.add_scope("Property", cb)
    assert cb.parent() is item, "the control was not bundled with its label"
    assert band.scope_count() == 1, "label and control were added as separate items"


def test_band_height_recovers_after_being_shown_narrow(styled_app):
    """The band must answer for the width it is *given*, not the width it
    happened to have when first shown.

    A tab built inside a stacked page is realised at a narrow transient width.
    The flow layout wraps every control onto its own row there and
    ``sync_flow_height`` pins that as the host's minimum; without an explicit
    ``heightForWidth`` the band then inherits QWidget's cached hint and stays
    that tall forever. This rendered a 45px band at 151px.
    """
    band = _band(styled_app, n_scope=6, n_actions=3)
    band.setFixedWidth(180)          # the narrow transient width
    band.show()
    styled_app.processEvents()
    tall = band.heightForWidth(180)

    band.setFixedWidth(1400)
    styled_app.processEvents()
    wide = band.heightForWidth(1400)

    assert band.hasHeightForWidth(), "the band must answer heightForWidth"
    assert wide < tall, (
        f"band still reports {wide}px at 1400px after being shown at 180px "
        f"({tall}px); its height is stuck at the narrow layout"
    )
    assert band.sizeHint().height() == wide, (
        f"sizeHint ({band.sizeHint().height()}px) disagrees with "
        f"heightForWidth ({wide}px) — the cached hint is winning"
    )
    assert wide == band.minimumSizeHint().height(), (
        "a band whose controls all fit on one row should be exactly its "
        f"minimum height ({band.minimumSizeHint().height()}px), got {wide}px"
    )


def test_the_band_paints_one_continuous_fill(styled_app):
    """theme.qss fills every plain ``QWidget`` with the app canvas colour, so
    the flow host and the label/control groups painted a lighter rectangle over
    the band. Sample the band's own margin against the strip the host covers.
    """
    band = _band(styled_app, n_scope=3, n_actions=2)
    band.setFixedWidth(900)
    band.show()
    band.setFixedHeight(band.heightForWidth(900))
    styled_app.processEvents()
    img = band.grab().toImage()

    margin = band.layout().contentsMargins().left() // 2   # band chrome only
    host = band.scope_host.geometry()
    inside = host.right() - 4          # inside the host, past the last control
    y = img.height() // 2
    last = max(band.scope_host.layout().itemAt(i).widget().geometry().right()
               for i in range(band.scope_count()))
    assert inside > last + 8, "no empty host strip to sample; widen the band"
    assert img.pixelColor(margin, y) == img.pixelColor(inside, y), (
        f"band fill {img.pixelColor(margin, y).name()} at x={margin} but "
        f"{img.pixelColor(inside, y).name()} at x={inside}, over the scope "
        "host — a container is painting its own background"
    )
    assert img.pixelColor(margin, y).name().lower() != theme.Colors.surface.lower(), (
        "the band is painted in the canvas colour; it has no fill of its own"
    )


def test_scope_zone_wraps_rather_than_squeezing(styled_app):
    """Narrowing must add rows, not overlap controls."""
    band = _band(styled_app, n_scope=6, n_actions=1)
    band.show()
    band.setFixedWidth(1400)
    band.adjustSize()
    styled_app.processEvents()
    wide = band.scope_host.layout().heightForWidth(1200)
    narrow = band.scope_host.layout().heightForWidth(320)
    assert narrow > wide, (
        f"scope zone did not grow when narrowed ({wide} -> {narrow}); "
        "it is being compressed instead of wrapped"
    )


# ── ModeBar ──────────────────────────────────────────────────────────────────
def _mode_bar(styled_app, icons=False):
    from widgets.mode_bar import ModeBar

    bar = ModeBar()          # no pinned height: it measures a QTabBar
    glyphs = {"Review": "eye", "Analyze": "sliders-horizontal", "Datasets": "boxes"}
    for name in ("Review", "Analyze", "Datasets"):
        bar.addSegment(name, icon=glyphs[name] if icons else None, data=name.lower())
    return bar


def test_every_mode_segment_is_filled_at_rest(styled_app):
    """Inactive modes must not be muted: muting is how the rail nav and the tab
    bar express 'not selected', and this control is a level above both."""
    bar = _mode_bar(styled_app)
    bar.show()
    styled_app.processEvents()
    for i in range(bar.count()):
        seg = bar.layout().itemAt(i).widget()
        sheet = seg.styleSheet()
        assert "transparent" not in sheet, (
            f"segment '{bar.segmentText(i)}' has no fill at rest"
        )
    active, inactive = [], []
    for i in range(bar.count()):
        (active if bar.isSegmentActive(i) else inactive).append(i)
    assert len(active) == 1 and len(inactive) == 2


def test_mode_segments_carry_full_strength_text(styled_app):
    from PySide6.QtWidgets import QLabel

    bar = _mode_bar(styled_app)
    bar.show()
    styled_app.processEvents()
    muted = theme.Colors.text_muted.lower()
    for i in range(bar.count()):
        seg = bar.layout().itemAt(i).widget()
        for lbl in seg.findChildren(QLabel):
            if lbl.text():
                assert muted not in lbl.styleSheet().lower(), (
                    f"segment '{bar.segmentText(i)}' uses muted text"
                )


def test_mode_bar_matches_the_tab_bar_height(styled_app):
    """The two bands share a bottom edge across the top of the window, so a few
    pixels either way reads as a misalignment of the whole chrome.

    A segment is a label plus a glyph, so left to its own content the bar is
    about 14px and sits as a thin strip beside a full-height tab row.
    """
    from PySide6.QtWidgets import QTabBar

    tabs = QTabBar()
    for name in ("Bar Plots", "Line Graphs", "Scatter Plot"):
        tabs.addTab(name)
    bar = _mode_bar(styled_app, icons=True)
    for w in (tabs, bar):
        w.show()
    styled_app.processEvents()
    assert bar.height() == tabs.height(), (
        f"mode bar is {bar.height()}px against a {tabs.height()}px tab bar; "
        "the two bands will not line up"
    )


def test_mode_icons_are_recoloured_for_the_active_fill(styled_app):
    """A fixed QIcon paints its Normal pixmap in `text_secondary`, which on the
    accent-filled active segment is a dark glyph on saturated blue. The segment
    repaints the glyph in its own foreground instead."""
    bar = _mode_bar(styled_app, icons=True)
    bar.show()
    styled_app.processEvents()
    pix = [bar.layout().itemAt(i).widget()._icon_lbl.pixmap() for i in range(bar.count())]
    assert all(not p.isNull() for p in pix), "a segment icon did not render"
    same_glyph = _mode_bar(styled_app, icons=True)
    same_glyph.setCurrentIndex(1)          # move the accent off segment 0
    styled_app.processEvents()
    other = same_glyph.layout().itemAt(0).widget()._icon_lbl.pixmap()
    assert pix[0].toImage() != other.toImage(), (
        "the 'Review' glyph looks identical active and inactive; it is not "
        "being tinted to the segment's foreground"
    )


def test_mode_bar_selection_round_trips(styled_app):
    bar = _mode_bar(styled_app)
    seen = []
    bar.currentChanged.connect(seen.append)
    bar.setCurrentIndex(2)
    assert bar.currentIndex() == 2
    assert bar.currentData() == "datasets"
    assert bar.isSegmentActive(2) and not bar.isSegmentActive(0)
    bar.setCurrentData("analyze")
    assert bar.currentIndex() == 1
    assert seen == [2, 1]


def test_mode_bar_segments_are_flush(styled_app):
    """No gutters: the bar is one band, not three chips."""
    bar = _mode_bar(styled_app)
    bar.show()
    bar.setFixedWidth(340)
    bar.adjustSize()
    styled_app.processEvents()
    assert bar.layout().spacing() == 0
    m = bar.layout().contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0)
    segs = [bar.layout().itemAt(i).widget() for i in range(bar.count())]
    for a, b in zip(segs, segs[1:]):
        assert b.geometry().left() - a.geometry().right() <= 1, "gutter between segments"


# ── the page tab strip sits on the band's bottom edge ───────────────────────
@pytest.mark.parametrize("band_h,point_size", [(33, 9), (33, 12), (44, 9), (28, 8)])
def test_page_tab_bar_is_flush_with_the_band_bottom(styled_app, band_h, point_size):
    """The selected tab's underline must land on the band's bottom edge, level
    with the active mode segment beside it.

    A ``QTabBar`` sizes its tabs from the font and its padding and lays them
    from the *top* of whatever height it is given. Stretched to the band's
    height it drew the underline part-way up — measured at eight pixels clear
    of the bottom edge, while the mode bar's fill ran all the way down to it.
    The fix is to pin the bar to its own height and push it to the bottom, so
    this holds at any band height or font size rather than at one tuned pair.
    """
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QHBoxLayout, QTabBar, QWidget
    from well_viewer.core.app_shell_view import (
        build_page_tabs_host, register_page_tabs, show_page_tabs,
    )

    previous = styled_app.font()
    font = QFont(previous)
    font.setPointSize(point_size)
    styled_app.setFont(font)
    try:
        win = QWidget()
        layout = QHBoxLayout(win)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build_page_tabs_host(win, win), 1)
        win._notebook = None

        bar = QTabBar()
        bar.setObjectName("PlottingSubTabs")
        bar.setDrawBase(False)
        for name in ("Bar Plots", "Line Graphs"):
            bar.addTab(name)
        bar.setCurrentIndex(0)
        register_page_tabs(win, "Plotting", bar)
        show_page_tabs(win, "Plotting")

        win.setFixedHeight(band_h)
        win.show()
        styled_app.processEvents()

        page = win._page_tabs_by_name["Plotting"]
        gap = page.height() - (bar.pos().y() + bar.height())
        assert gap == 0, (
            f"at a {band_h}px band and {point_size}pt text the tab bar ends "
            f"{gap}px above the band's bottom edge, so the active tab's "
            "underline floats clear of it"
        )
    finally:
        styled_app.setFont(previous)


# ── per-leaf slots: one band, several sub-tabs ───────────────────────────────
def _slotted(styled_app):
    """A band with shared controls plus two leaves, as the Plotting page has."""
    from PySide6.QtWidgets import QComboBox, QPushButton
    from widgets.control_band import ControlBand

    band = ControlBand()
    shared = QComboBox()
    shared.addItem("GFP")
    band.add_scope("Channel", shared)

    left = band.slot("left")
    left.add_scope("Timepoints", QComboBox())
    left.add_action(QPushButton("Export ▾"))

    right = band.slot("right")
    right.add_scope("Bins", QComboBox())

    return band, left, right


def test_a_slot_is_returned_again_for_the_same_name(styled_app):
    """Leaves are built lazily and may ask for their slot more than once."""
    band, left, _ = _slotted(styled_app)
    assert band.slot("left") is left
    assert band.slot_names() == ("left", "right")


def test_showing_one_slot_hides_the_others(styled_app):
    band, left, right = _slotted(styled_app)
    band.show_slot("left")
    band.show()
    styled_app.processEvents()

    assert all(w.isVisible() for w in left._items)
    assert not any(w.isVisible() for w in right._items)


def test_shared_controls_survive_every_slot_switch(styled_app):
    """The page's own controls are not owned by any leaf and never hide."""
    band, _, _ = _slotted(styled_app)
    shared = band._scope.itemAt(0).widget()
    band.show()
    for name in ("left", "right", None):
        band.show_slot(name)
        styled_app.processEvents()
        assert shared.isVisible(), f"shared control hidden while showing {name!r}"


def test_a_slot_built_after_the_switch_arrives_hidden(styled_app):
    """The regression that makes lazy sub-tabs work.

    ``QLayout.addWidget`` shows what it is handed. A sub-tab is built the first
    time it is switched to — but Split View and a restored session both build
    one while a *different* sub-tab is on screen, and its controls would then
    appear in the row beside the current tab's and stay there.
    """
    from PySide6.QtWidgets import QPushButton

    band, _, _ = _slotted(styled_app)
    band.show_slot("left")
    band.show()

    late = band.slot("late")
    button = late.add_action(QPushButton("Late"))
    styled_app.processEvents()

    assert not button.isVisible()
    band.show_slot("late")
    styled_app.processEvents()
    assert button.isVisible()


def test_a_band_with_no_slots_behaves_as_before(styled_app):
    """Most tabs are a single leaf and never touch the slot API."""
    band = _band(styled_app)
    band.show()
    styled_app.processEvents()
    assert band.current_slot() is None
    assert band.slot_names() == ()
    assert all(band._scope.itemAt(i).widget().isVisible()
               for i in range(band.scope_count()))


def test_the_band_is_one_row_shorter_for_hiding_a_slot(styled_app):
    """The point of the exercise: the row carries one leaf's controls, not all.

    With every leaf's controls in the row at once, Plotting's band wrapped to
    three lines — taller than the two separate bands it replaced.
    """
    band, _, _ = _slotted(styled_app)
    band.resize(420, band.sizeHint().height())
    band.show()

    band.show_slot(None)
    styled_app.processEvents()
    bare = band.heightForWidth(420)

    for name in band.slot_names():
        band.show_slot(name)
        styled_app.processEvents()
        assert band.heightForWidth(420) >= bare

    for slot in band._slots.values():
        slot.setSlotVisible(True)
    styled_app.processEvents()
    everything = band.heightForWidth(420)

    band.show_slot("left")
    styled_app.processEvents()
    assert band.heightForWidth(420) <= everything
