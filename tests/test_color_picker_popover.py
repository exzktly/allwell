"""ColorPickerPopover: the picker behind Custom, and the two signals it owes.

The largest of the five modules the combined-lane zero-coverage inventory named
(306 statements, no executed lines). It is reached only from
``ColorSwatchRow._open_picker``, so it sat one level below a widget that was
itself never driven.

Two signals carry the whole contract and they mean different things.
``colorPicked`` is *live* — it fires on every drag and edit, and a caller uses it
to preview. ``colorCommitted`` fires once, when the user is finished, and it is
what writes a recent and what the three call sites persist. Conflating them is
the defect this file mostly guards: a picker that commits on every drag fills the
recents row with the colours the cursor crossed, and one that never commits loses
the colour the user actually chose.

The SV square and hue strip are driven with real ``QMouseEvent``s at coordinates
whose right answer is known independently — the top-left of the square is
saturation 0 / value 1 — so a flipped axis produces a different number rather
than merely a different-looking one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.color_picker_popover import (  # noqa: E402
    ColorPickerPopover, HueStrip, SvSquare,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _press(w, x: float, y: float) -> None:
    pt = QPointF(x, y)
    QApplication.sendEvent(w, QMouseEvent(QEvent.MouseButtonPress, pt, pt,
                                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def _drag(w, x: float, y: float) -> None:
    pt = QPointF(x, y)
    QApplication.sendEvent(w, QMouseEvent(QEvent.MouseMove, pt, pt,
                                          Qt.NoButton, Qt.LeftButton, Qt.NoModifier))


def _hexes(colors) -> list[str]:
    return [c.name().upper() for c in colors]


@pytest.fixture
def picker(qapp):
    p = ColorPickerPopover(color="#3366CC", recents=["#AA0000", "#00BB00"])
    yield p
    p.hide()


# ── SvSquare ─────────────────────────────────────────────────────────────────

def test_the_sv_square_reads_the_top_left_as_unsaturated_and_bright(qapp):
    """Value runs *up* the square, so y is inverted. A square that mapped v
    straight from y is still a plausible-looking gradient and gives 0.0 here."""
    sq = SvSquare()
    sq.resize(100, 100)
    seen: list[tuple] = []
    sq.svChanged.connect(lambda s, v: seen.append((s, v)))

    _press(sq, 0.0, 0.0)

    assert sq.sv() == (pytest.approx(0.0), pytest.approx(1.0))
    assert seen == [(pytest.approx(0.0), pytest.approx(1.0))]


def test_the_sv_square_reads_the_bottom_right_as_saturated_and_dark(qapp):
    sq = SvSquare()
    sq.resize(100, 100)

    _press(sq, 100.0, 100.0)

    assert sq.sv() == (pytest.approx(1.0), pytest.approx(0.0))


def test_dragging_across_the_sv_square_keeps_reporting(qapp):
    sq = SvSquare()
    sq.resize(100, 100)
    seen: list[tuple] = []
    sq.svChanged.connect(lambda s, v: seen.append((round(s, 2), round(v, 2))))

    _press(sq, 25.0, 25.0)
    _drag(sq, 75.0, 25.0)

    assert seen == [(0.25, 0.75), (0.75, 0.75)]


def test_a_negative_hue_leaves_the_square_where_it_was(qapp):
    """``QColor.getHsvF`` returns -1 for greys. Taking that literally swings the
    square to red every time the user drags value down to black."""
    sq = SvSquare()
    sq.setHue(0.4)

    sq.setHue(-1.0)

    assert sq.hue() == pytest.approx(0.4)


def test_the_hue_strip_maps_the_drop_down_its_length(qapp):
    strip = HueStrip()
    strip.resize(20, 200)
    seen: list[float] = []
    strip.hueChanged.connect(seen.append)

    _press(strip, 10.0, 50.0)

    assert strip.hue() == pytest.approx(0.25)
    assert seen == [pytest.approx(0.25)]


def test_the_hue_strip_clamps_a_drag_past_its_ends(qapp):
    strip = HueStrip()
    strip.resize(20, 200)

    _press(strip, 10.0, 400.0)

    assert strip.hue() == pytest.approx(1.0)


# ── the colour round trip ────────────────────────────────────────────────────

def test_setting_a_colour_fills_every_control_that_shows_it(picker):
    picker.setColor("#3366CC")

    assert picker._hex.text() == "#3366CC"
    assert picker._alpha.text() == "255"
    assert picker.color().name().upper() == "#3366CC"
    h, s, v, _a = QColor("#3366CC").getHsvF()
    assert picker._hue.hue() == pytest.approx(h)
    assert picker._sv.sv() == (pytest.approx(s), pytest.approx(v))


def test_an_invalid_colour_is_refused_rather_than_stored(picker):
    picker.setColor("nonsense")

    assert picker.color().name().upper() == "#3366CC"


def test_the_hex_field_is_uppercase_six_digit_regardless_of_what_was_set(picker):
    """The field is a value the user copies out. ``#3c6`` and ``#33CC66`` are the
    same colour and only one of them can be pasted into the other half of the
    app."""
    picker.setColor("#3c6")

    assert picker._hex.text() == "#33CC66"


def test_alpha_survives_the_round_trip(picker):
    c = QColor("#3366CC")
    c.setAlpha(128)

    picker.setColor(c)

    assert picker._alpha.text() == "128"
    assert picker.color().alpha() == 128


# ── live edits ───────────────────────────────────────────────────────────────

def test_editing_the_hex_field_reports_the_colour_live(picker):
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._hex.setText("#0A0B0C")
    picker._hex.editingFinished.emit()

    assert _hexes(seen) == ["#0A0B0C"]
    assert picker.color().name().upper() == "#0A0B0C"


def test_editing_the_hex_field_leaves_alpha_alone(picker):
    """A hex string carries no alpha, so parsing one produces an opaque colour.
    Taking it whole silently resets a half-transparent selection to solid."""
    c = QColor("#3366CC")
    c.setAlpha(64)
    picker.setColor(c)

    picker._hex.setText("#0A0B0C")
    picker._hex.editingFinished.emit()

    assert picker.color().alpha() == 64


def test_an_unparseable_hex_field_changes_nothing(picker):
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._hex.setText("")
    picker._hex.editingFinished.emit()

    assert seen == []
    assert picker.color().name().upper() == "#3366CC"


def test_a_blank_alpha_field_is_not_a_request_for_opaque(picker):
    """Clearing the field to retype is one keystroke, and snapping it to 255 in
    between emits a colour the user never asked for — over a preview they are
    watching. Blank means "not yet", and the field re-syncs to the live value."""
    c = QColor("#3366CC")
    c.setAlpha(64)
    picker.setColor(c)
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._alpha.setText("")
    picker._alpha.editingFinished.emit()

    assert seen == []
    assert picker.color().alpha() == 64
    assert picker._alpha.text() == "64", "the field shows the live value again"


def test_editing_alpha_reports_it_and_keeps_the_hue(picker):
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._alpha.setText("40")
    picker._alpha.editingFinished.emit()

    assert [c.alpha() for c in seen] == [40]
    assert _hexes(seen) == ["#3366CC"]


def test_setting_alpha_to_what_it_already_is_reports_nothing(picker):
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._alpha.setText("255")
    picker._alpha.editingFinished.emit()

    assert seen == []


def test_an_out_of_range_alpha_is_clamped_before_it_is_compared(picker):
    """``QIntValidator`` gates *typing*, not ``setText``, so the field really can
    hold "900" — pasted, or written by code.

    The alpha alone cannot tell a clamp from no clamp, because
    ``QColor.setAlpha(900)`` lands on 255 either way. The emit can: clamped,
    900 becomes 255, which is the alpha already in force, so nothing changed and
    nothing is announced. Unclamped, 900 != 255 reads as a change and the
    picker announces a colour identical to the one it already had."""
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._alpha.setText("900")
    picker._alpha.editingFinished.emit()

    assert picker.color().alpha() == 255
    assert seen == [], "clamping to the value already in force is not a change"


def test_a_negative_alpha_is_clamped_to_transparent(picker):
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker._alpha.setText("-5")
    picker._alpha.editingFinished.emit()

    assert picker.color().alpha() == 0
    assert [c.alpha() for c in seen] == [0]


def test_dragging_the_sv_square_reports_a_colour_live(picker):
    picker._sv.resize(100, 100)
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    _press(picker._sv, 0.0, 0.0)

    assert len(seen) == 1
    assert seen[0].name().upper() == "#FFFFFF", "top-left is unsaturated, full value"


def test_moving_the_hue_strip_takes_the_square_with_it(picker):
    """The square paints one hue's saturation/value plane, so a hue change that
    did not reach it leaves the user picking inside the previous hue while the
    strip shows the new one."""
    picker._hue.resize(20, 200)

    _press(picker._hue, 10.0, 100.0)

    assert picker._hue.hue() == pytest.approx(0.5)
    assert picker._sv.hue() == pytest.approx(0.5)


# ── committing ───────────────────────────────────────────────────────────────

def test_a_live_drag_does_not_commit(picker):
    """The distinction the whole file is about: dragging is not choosing."""
    picker._sv.resize(100, 100)
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)

    _press(picker._sv, 10.0, 20.0)
    _press(picker._sv, 60.0, 70.0)

    assert committed == []
    assert _hexes(picker.recents()) == ["#AA0000", "#00BB00"]


def test_return_in_the_hex_field_commits_and_records_a_recent(picker):
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)

    picker._hex.setText("#0A0B0C")
    picker._hex.returnPressed.emit()

    assert _hexes(committed) == ["#0A0B0C"]
    assert _hexes(picker.recents()) == ["#0A0B0C", "#AA0000", "#00BB00"]


def test_committing_the_colour_it_already_had_is_not_a_commit(picker):
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)

    picker._hex.setText("#3366CC")
    picker._hex.returnPressed.emit()

    assert committed == []
    assert _hexes(picker.recents()) == ["#AA0000", "#00BB00"]


def test_dismissing_the_popover_commits_what_was_being_dragged(picker):
    """Without this a user drags to a colour, clicks away, and loses it — the
    preview showed the new colour the whole time.

    ``show()`` first, and that is not ceremony: Qt sends no ``QHideEvent`` to a
    widget that was never visible, so ``hide()`` on a fresh popover is a no-op
    and this test passed against a deleted ``hideEvent`` before the ``show``
    was added."""
    picker._sv.resize(100, 100)
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)
    _press(picker._sv, 0.0, 0.0)

    picker.show()
    picker.hide()

    assert _hexes(committed) == ["#FFFFFF"]


def test_dismissing_without_a_change_commits_nothing(picker):
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)

    picker.show()
    picker.hide()

    assert committed == []


def test_set_color_moves_the_baseline_so_a_dismiss_is_silent(picker):
    """``setColor`` is the caller saying "this is where we are starting". A
    picker that kept its old baseline would commit on dismiss every time it was
    opened on a colour different from the last one it saw."""
    committed: list[QColor] = []
    picker.colorCommitted.connect(committed.append)

    picker.setColor("#0A0B0C")
    picker.show()
    picker.hide()

    assert committed == []


def test_picking_a_recent_selects_it_and_commits_in_one_go(picker):
    """The docstring promises ``colorCommitted`` "on clicking a recent swatch",
    and it could not fire: ``_on_recent_picked`` went through ``setColor``,
    which moves the committed baseline as well as the colour, so ``_commit``
    then compared the colour against itself and returned. Writing this test is
    what found it."""
    picked: list[QColor] = []
    committed: list[QColor] = []
    picker.colorPicked.connect(picked.append)
    picker.colorCommitted.connect(committed.append)

    picker._recents_row._on_picker_committed(QColor("#00BB00"))

    assert _hexes(picked) == ["#00BB00"]
    assert _hexes(committed) == ["#00BB00"]


def test_a_committed_colour_goes_to_the_front_of_the_recents(picker):
    picker._hex.setText("#00BB00")
    picker._hex.returnPressed.emit()

    assert _hexes(picker.recents()) == ["#00BB00", "#AA0000"], (
        "re-choosing an existing recent moves it, it does not duplicate it"
    )


def test_the_recents_row_shows_what_the_picker_recorded(picker):
    """The outbound half. Keeping the list and never pushing it to the row means
    the user commits a colour and the row below still shows the old set."""
    picker._hex.setText("#0A0B0C")
    picker._hex.returnPressed.emit()

    assert _hexes(picker._recents_row.colors()) == ["#0A0B0C", "#AA0000", "#00BB00"]


def test_recents_are_capped_at_eight(qapp):
    p = ColorPickerPopover(recents=[f"#{i:02X}0000" for i in range(1, 12)])
    try:
        assert len(p.recents()) == 8
        assert _hexes(p.recents())[0] == "#010000"
    finally:
        p.hide()


def test_set_recents_reaches_the_row_as_well_as_the_list(picker):
    picker.setRecents(["#111111", "#222222"])

    assert _hexes(picker.recents()) == ["#111111", "#222222"]
    assert _hexes(picker._recents_row.colors()) == ["#111111", "#222222"]


def test_an_empty_recents_list_shows_the_current_colour_rather_than_nothing(picker):
    """An empty ``ColorSwatchRow`` falls back to the theme's whole trace palette,
    which would put eight colours the user has never picked under a caption
    reading RECENTS."""
    picker.setRecents([])

    assert picker.recents() == []
    assert _hexes(picker._recents_row.colors()) == ["#3366CC"]


# ── what the two custom widgets actually paint ───────────────────────────────
#
# The interaction model above says x is saturation and y is inverted value. That
# is a claim about the *picture* as much as about the click handler: a square
# painted with the axes the other way round would take every press correctly and
# hand the user a colour from the opposite corner of what they aimed at. Nothing
# but reading pixels back can hold the two halves together.

def _render(w, width: int, height: int):
    from PySide6.QtGui import QImage
    w.resize(width, height)
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(QColor("#FF00FF"))       # a colour neither widget paints
    w.render(img)
    return img


def _hsv(img, x: int, y: int):
    return QColor(img.pixel(x, y)).getHsvF()[:3]


def test_the_square_paints_saturation_rightwards(qapp):
    """Sampled well inside the frame: the outer ring of pixels is the 1 px
    border and the corners are where its antialiasing meets the value overlay,
    so an edge sample measures the chrome rather than the gradient."""
    sq = SvSquare()
    sq.setHue(0.33)
    img = _render(sq, 100, 100)

    _h_left, s_left, _v_left = _hsv(img, 20, 10)
    _h_right, s_right, _v_right = _hsv(img, 80, 10)

    assert s_left < 0.35 and s_right > 0.65, (s_left, s_right)


def test_the_square_paints_value_downwards(qapp):
    sq = SvSquare()
    sq.setHue(0.33)
    img = _render(sq, 100, 100)

    _h, _s, v_top = _hsv(img, 80, 10)
    _h2, _s2, v_bottom = _hsv(img, 80, 90)

    assert v_top > 0.8 and v_bottom < 0.2, (v_top, v_bottom)


def test_the_square_paints_the_hue_it_was_given(qapp):
    """A square that ignored ``setHue`` still draws a perfectly good gradient —
    of the previous colour, while the strip beside it shows the new one."""
    sq = SvSquare()
    sq.setHue(0.33)
    img = _render(sq, 100, 100)

    hue, _s, _v = _hsv(img, 80, 10)

    assert hue == pytest.approx(0.33, abs=0.02)


def test_the_strip_paints_the_hue_wheel_top_to_bottom(qapp):
    """Each sampled row's own hue is compared against its position, so a strip
    painted in the wrong direction — or over half the wheel — fails on the
    numbers rather than on looking different."""
    strip = HueStrip()
    img = _render(strip, 20, 200)

    for y in (50, 100, 150):
        hue, _s, _v = _hsv(img, 10, y)
        assert hue == pytest.approx(y / 200.0, abs=0.02), y


def test_dragging_down_the_strip_keeps_reporting(qapp):
    strip = HueStrip()
    strip.resize(20, 200)
    seen: list[float] = []
    strip.hueChanged.connect(seen.append)

    _press(strip, 10.0, 20.0)
    _drag(strip, 10.0, 120.0)

    assert seen == [pytest.approx(0.1), pytest.approx(0.6)]


def test_a_right_press_moves_neither_control(qapp):
    sq, strip = SvSquare(), HueStrip()
    sq.resize(100, 100)
    strip.resize(20, 200)
    before = (sq.sv(), strip.hue())

    for w in (sq, strip):
        pt = QPointF(10.0, 10.0)
        QApplication.sendEvent(w, QMouseEvent(QEvent.MouseButtonPress, pt, pt,
                                              Qt.RightButton, Qt.RightButton,
                                              Qt.NoModifier))

    assert (sq.sv(), strip.hue()) == before


def test_a_move_with_no_button_held_moves_neither_control(qapp):
    sq, strip = SvSquare(), HueStrip()
    sq.resize(100, 100)
    strip.resize(20, 200)
    before = (sq.sv(), strip.hue())

    for w in (sq, strip):
        pt = QPointF(10.0, 10.0)
        QApplication.sendEvent(w, QMouseEvent(QEvent.MouseMove, pt, pt,
                                              Qt.NoButton, Qt.NoButton,
                                              Qt.NoModifier))

    assert (sq.sv(), strip.hue()) == before


def test_filling_the_controls_from_a_colour_does_not_echo_back_as_an_edit(picker):
    """Opening the picker on a colour must not look like the user choosing one:
    ``ColorSwatchRow`` treats every ``colorPicked`` as a choice, so an echo here
    would apply a colour to the plot the moment the popover appeared.

    Two things hold it, and it is worth being exact about which does what,
    because the obvious answer is only half right. Today nothing even tries to
    echo: ``setColor`` reaches the controls through ``HueStrip.setHue``,
    ``SvSquare.setSv`` and ``QLineEdit.setText``, and none of those emit — only
    ``_pick_at`` and ``editingFinished`` do. So dropping the ``_updating`` latch
    on its own leaves this suite green, which reads at first like a latch that
    guards nothing.

    It is not. Give ``HueStrip.setHue`` a ``hueChanged`` emit — an entirely
    reasonable thing for someone to add — and the latch absorbs it and this
    suite stays green; drop the latch as well and seven tests here fail. Both
    halves measured rather than reasoned about, because the first measurement
    alone says the opposite of the truth."""
    seen: list[QColor] = []
    picker.colorPicked.connect(seen.append)

    picker.setColor("#7F3300")

    assert seen == []
