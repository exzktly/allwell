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

import theme  # noqa: E402


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
def _mode_bar(styled_app):
    from widgets.mode_bar import ModeBar

    bar = ModeBar(height=34)
    for name in ("Review", "Analyze", "Datasets"):
        bar.addSegment(name, data=name.lower())
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
