"""ColorSwatchRow: pressing a swatch picks that colour, and the Custom tile opens.

One of the five modules the combined-lane zero-coverage inventory named. It has
three live call sites — the Cell Browser's per-channel tint, the Review Image
colour, and the saved-selection recolour — and every one of them opens it from a
button press, so it is exactly the shape CLAUDE.md §7.6 describes: a control
offered in three places and driven in none.

The presses here are real ``QMouseEvent``s put through ``QApplication.sendEvent``
at coordinates computed from the widget's own geometry, because the geometry
*is* half the behaviour: which tile a click lands on is decided by
``_tile_rects``, and a test that called ``setCurrentIndex`` instead would pass
with the hit-testing broken.

The palette is chosen so a wrong answer differs from the right one. The curated
colours, the recents and the current selection are all distinct, so
"returns the first colour", "returns the current one" and "returns the tile that
was clicked" cannot be confused with each other.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import theme  # noqa: E402
from widgets.color_swatch_row import ColorSwatchRow  # noqa: E402

CURATED = ["#101010", "#202020", "#303030"]
RECENTS = ["#AA0000", "#00BB00"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _row(**kw) -> ColorSwatchRow:
    w = ColorSwatchRow(CURATED, **kw)
    # Polish before measuring, and it is not a formality. Qt applies the
    # application stylesheet on first polish, which changes the widget's font
    # and therefore ``_swatch()`` — 18 px bare, 19 px once a stylesheet is
    # live. Size it from an unpolished hint and the widget is one pixel short,
    # ``(height - swatch) / 2`` lands on a half pixel, and the selection ring
    # straddles two rows at 50% coverage each. Which is a test that passes
    # alone and fails in the lane, since whether a stylesheet is live depends
    # on what ran before it: exactly what happened here.
    w.ensurePolished()
    w.resize(w.sizeHint())
    return w


def _press(w: ColorSwatchRow, index: int) -> None:
    """A left press at the centre of tile *index*, through Qt's own dispatch."""
    centre = w._tile_rects()[index].center()
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(centre), QPointF(centre),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)


def _hexes(colors) -> list[str]:
    return [c.name().upper() for c in colors]


# ── the tile model ───────────────────────────────────────────────────────────

def test_without_custom_the_row_is_only_the_curated_colours(qapp):
    w = _row()
    assert [kind for kind, _ in w._tiles()] == ["curated"] * 3


def test_with_custom_the_row_is_curated_then_recents_then_the_custom_tile(qapp):
    """Order matters: the Custom tile is the escape hatch and lives at the end,
    so a press near the right edge is the picker rather than a colour."""
    w = _row(allow_custom=True, recents=RECENTS)
    assert [kind for kind, _ in w._tiles()] == [
        "curated", "curated", "curated", "recent", "recent", "custom",
    ]


def test_an_invalid_colour_is_dropped_rather_than_drawn_as_black(qapp):
    """``QColor("nonsense")`` is invalid and paints black — a swatch the user
    never chose, indistinguishable from one they did."""
    w = ColorSwatchRow(["#101010", "nonsense", "#303030"])
    assert _hexes(w.colors()) == ["#101010", "#303030"]


def test_set_colors_drops_an_invalid_colour_too(qapp):
    """The constructor and ``setColors`` each carry their own filter, so each
    needs its own test: dropping the one in ``setColors`` leaves the
    constructor's covering for it and the suite green."""
    w = _row()

    w.setColors(["#404040", "nonsense", "#505050"])

    assert _hexes(w.colors()) == ["#404040", "#505050"]


def test_the_groups_are_separated_by_a_wider_gap_than_their_members(qapp):
    """The visual grammar that says "these two recents are not two more curated
    colours". A uniform-gap layout passes any assertion weaker than this one."""
    w = _row(allow_custom=True, recents=RECENTS)
    rects = w._tile_rects()
    within_curated = rects[1].left() - rects[0].right()
    across_to_recents = rects[3].left() - rects[2].right()

    assert within_curated == pytest.approx(theme.Spacing.sm)
    assert across_to_recents == pytest.approx(2 * theme.Spacing.sm)


# ── pressing a swatch ────────────────────────────────────────────────────────

def test_pressing_a_curated_swatch_picks_that_colour_and_says_which(qapp):
    """Tile 2, not tile 0 and not the current selection — so "emits the first
    colour" and "emits the colour it already had" both fail."""
    w = _row()
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)

    _press(w, 2)

    assert w.currentColor().name().upper() == "#303030"
    assert _hexes(seen) == ["#303030"]
    assert w.currentIndex() == 2


def test_pressing_a_recent_picks_it_just_like_a_curated_one(qapp):
    w = _row(allow_custom=True, recents=RECENTS)
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)

    _press(w, 4)                     # the second recent

    assert _hexes(seen) == ["#00BB00"]
    assert w.currentColor().name().upper() == "#00BB00"


def test_pressing_the_same_swatch_twice_still_reports_the_pick(qapp):
    """Deliberate: the three call sites treat ``colorPicked`` as "the user chose
    this", and a popover that swallowed the second press would leave a user who
    re-picked the current colour with a popover that appears to have ignored
    them."""
    w = _row()
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)

    _press(w, 1)
    _press(w, 1)

    assert _hexes(seen) == ["#202020", "#202020"]


def test_pressing_between_the_groups_hits_nothing(qapp):
    """The extra inter-group gap is wider than the hit slop, so the dead strip
    between the last curated colour and the first recent belongs to neither."""
    w = _row(allow_custom=True, recents=RECENTS)
    rects = w._tile_rects()
    x = (rects[2].right() + rects[3].left()) / 2.0
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)

    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, w.height() / 2),
                     QPointF(x, w.height() / 2), Qt.LeftButton, Qt.LeftButton,
                     Qt.NoModifier)
    QApplication.sendEvent(w, ev)

    assert seen == []


def test_a_right_press_picks_nothing(qapp):
    w = _row()
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)
    centre = w._tile_rects()[1].center()

    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(centre), QPointF(centre),
                     Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)

    assert seen == []


# ── the Custom tile ──────────────────────────────────────────────────────────

def test_pressing_the_custom_tile_opens_the_picker_anchored_at_it(qapp, monkeypatch):
    """The outbound call, per §7.4 #1. Building the popover and never calling
    ``popup`` leaves the picker correct and invisible, which is what the user
    sees as "the Custom tile does nothing"."""
    from widgets.color_picker_popover import ColorPickerPopover

    w = _row(allow_custom=True, recents=RECENTS)
    opened: list[tuple] = []
    monkeypatch.setattr(ColorPickerPopover, "popup",
                        lambda self, anchor, **kw: opened.append((anchor, kw)))

    _press(w, 5)                      # the Custom tile

    assert len(opened) == 1, "the Custom tile must actually open the picker"
    anchor, _kw = opened[0]
    assert isinstance(w._picker, ColorPickerPopover)
    assert anchor is w._picker_anchor
    tile = w._tile_rects()[5]
    assert anchor.geometry().topLeft().x() == int(tile.center().x())


def test_the_picker_opens_on_the_colour_the_row_is_showing(qapp, monkeypatch):
    """Opening the picker on the palette default rather than the current colour
    is a silent substitution: the user opens Custom to nudge the colour they
    have, and is handed a different one already applied."""
    from widgets.color_picker_popover import ColorPickerPopover

    w = _row(allow_custom=True)
    monkeypatch.setattr(ColorPickerPopover, "popup", lambda self, anchor, **kw: None)
    _press(w, 1)                      # current is now #202020

    _press(w, len(w._tiles()) - 1)    # Custom

    assert w._picker.color().name().upper() == "#202020"


def test_the_picker_is_built_once_and_reused(qapp, monkeypatch):
    from widgets.color_picker_popover import ColorPickerPopover

    w = _row(allow_custom=True)
    monkeypatch.setattr(ColorPickerPopover, "popup", lambda self, anchor, **kw: None)
    custom = len(w._tiles()) - 1

    _press(w, custom)
    first = w._picker
    _press(w, custom)

    assert w._picker is first, "a fresh popover per press leaks one window per click"


def test_the_picker_carries_the_rows_recents_across(qapp, monkeypatch):
    from widgets.color_picker_popover import ColorPickerPopover

    w = _row(allow_custom=True, recents=RECENTS)
    monkeypatch.setattr(ColorPickerPopover, "popup", lambda self, anchor, **kw: None)

    _press(w, 5)

    assert _hexes(w._picker.recents()) == ["#AA0000", "#00BB00"]


# ── what the picker sends back ───────────────────────────────────────────────

def test_a_live_pick_moves_the_selection_without_touching_recents(qapp):
    """Live drags must not fill the recents row with every colour the cursor
    passed over on the way to the one that was chosen."""
    w = _row(allow_custom=True, recents=RECENTS)

    w._on_picker_live(QColor("#123456"))

    assert w.currentColor().name().upper() == "#123456"
    assert _hexes(w.recents()) == ["#AA0000", "#00BB00"]


def test_a_committed_pick_becomes_the_newest_recent(qapp):
    w = _row(allow_custom=True, recents=RECENTS)
    seen: list[QColor] = []
    w.colorPicked.connect(seen.append)

    w._on_picker_committed(QColor("#123456"))

    assert _hexes(w.recents()) == ["#123456", "#AA0000", "#00BB00"]
    assert w.currentColor().name().upper() == "#123456"
    assert _hexes(seen) == ["#123456"]


def test_re_committing_an_existing_recent_moves_it_to_the_front(qapp):
    """Not "appends a duplicate", and not "leaves it where it was" — the recents
    row is most-recent-first, so a colour picked again is now the most recent."""
    w = _row(allow_custom=True, recents=RECENTS)

    w._on_picker_committed(QColor("#00BB00"))

    assert _hexes(w.recents()) == ["#00BB00", "#AA0000"]


def test_recents_are_capped_and_the_oldest_falls_off(qapp):
    w = ColorSwatchRow(CURATED, allow_custom=True, recents=["#010101", "#020202"],
                       max_recents=2)

    w.addRecent("#030303")

    assert _hexes(w.recents()) == ["#030303", "#010101"]


def test_an_invalid_recent_is_ignored_rather_than_stored_as_black(qapp):
    w = _row(allow_custom=True, recents=RECENTS)

    w.addRecent("not-a-colour")

    assert _hexes(w.recents()) == ["#AA0000", "#00BB00"]


# ── setColors / current ──────────────────────────────────────────────────────

def test_replacing_the_palette_keeps_a_selection_that_is_still_offered(qapp):
    """The surviving colour is put **second** in the new palette on purpose. Put
    it first and "keep the selection when it is still offered" and "always reset
    to the first colour" return the same answer, and no assertion of any
    strength could tell the two apart."""
    w = _row()
    w.setCurrentIndex(2)                      # #303030

    w.setColors(["#909090", "#303030"])

    assert w.currentColor().name().upper() == "#303030"
    assert w.currentIndex() == 1


def test_replacing_the_palette_falls_back_when_the_selection_is_gone(qapp):
    """The alternative is a widget whose ``currentColor`` names a swatch it no
    longer draws — the plot inked one way and the control saying another."""
    w = _row()
    w.setCurrentIndex(2)                      # #303030

    w.setColors(["#707070", "#909090"])

    assert w.currentColor().name().upper() == "#707070"


def test_current_index_is_minus_one_for_a_colour_no_swatch_carries(qapp):
    w = _row()

    w.setCurrentColor("#ABCDEF")

    assert w.currentIndex() == -1
    assert w.currentColor().name().upper() == "#ABCDEF"


def test_hovering_moves_the_highlight_and_leaving_clears_it(qapp):
    w = _row()
    centre = w._tile_rects()[1].center()

    ev = QMouseEvent(QEvent.MouseMove, QPointF(centre), QPointF(centre),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)
    assert w._hover == 1

    QApplication.sendEvent(w, QEvent(QEvent.Leave))
    assert w._hover == -1


def test_the_row_reserves_room_for_the_selection_ring(qapp):
    """``sizeHint`` has to include the ring, which is drawn *outside* the fill.
    A hint that measured only the tiles clips the ring on the first and last
    swatches, which is how a selected end swatch reads as unselected."""
    w = _row()
    hint = w.sizeHint()

    assert hint.height() == w._swatch() + 2 * w._ring()
    assert hint.width() == round(w._tile_rects()[-1].right() + w._ring())


# ── what is actually drawn ───────────────────────────────────────────────────
#
# Everything above asserts what the widget *decided*. These assert what it
# *painted*, which is CLAUDE.md §7.4: the computed value and the pixels are two
# different things, and the ring in particular is the one piece of state a user
# reads straight off the screen with no other indicator to fall back on.

def _grab(w: ColorSwatchRow):
    from PySide6.QtGui import QImage
    img = QImage(w.width(), w.height(), QImage.Format_ARGB32)
    img.fill(QColor("#000000"))
    w.render(img)
    return img


def _at(img, x: float, y: float) -> str:
    return QColor(img.pixel(int(x), int(y))).name().upper()


def _margin_above(img, w: ColorSwatchRow, index: int) -> set[str]:
    """Every colour painted in the strip ``sizeHint`` reserves above tile
    *index* — which is where the selection ring goes, and nothing else does.

    A scan rather than one sampled row: the ring is drawn with a pen of width
    ``_ring()`` centred on the tile's outset edge, so exactly which row carries
    the ink is a rounding detail, and a test that pinned one row would report a
    missing ring every time the swatch size changed."""
    rect = w._tile_rects()[index]
    x = int(rect.center().x())
    return {_at(img, x, y) for y in range(0, int(rect.top()))}


def test_each_swatch_is_painted_in_its_own_colour(qapp):
    """Three near-identical greys would pass "something was drawn"; these are
    read back one at a time and matched to the colour that tile carries."""
    w = _row()
    img = _grab(w)

    for i, expected in enumerate(CURATED):
        centre = w._tile_rects()[i].center()
        assert _at(img, centre.x(), centre.y()) == expected.upper(), f"tile {i}"


def test_the_selection_ring_is_painted_around_the_current_swatch(qapp):
    """The ring is drawn *outside* the fill, in the margin ``sizeHint``
    reserves. Reading a pixel in that margin is the only way to tell a selected
    swatch from an unselected one — the fill is identical for both."""
    w = _row()
    w.setCurrentIndex(1)
    img = _grab(w)

    assert QColor(theme.Colors.accent).name().upper() in _margin_above(img, w, 1)


def test_the_ring_fills_the_margin_that_size_hint_reserves_for_it(qapp):
    """Where the two plausible implementations differ, measured rather than
    guessed. Drawn outside the fill, the ring reaches the **outermost** row of
    the widget — the row ``sizeHint`` adds ``_ring()`` for. Drawn on the tile's
    own edge it lands one ring-width inward: still visible above the swatch, so
    the margin scan above cannot tell the two apart, but eating ``_ring()``
    pixels of fill on every side, rounding the corners to the wrong radius, and
    leaving the reserved margin blank.

    Both edges, because a ring that reached only the top would satisfy half of
    this and still be wrong."""
    w = _row()
    w.setCurrentIndex(1)
    img = _grab(w)

    accent = QColor(theme.Colors.accent).name().upper()
    x = w._tile_rects()[1].center().x()

    assert _at(img, x, 0) == accent, "nothing painted in the reserved top margin"
    assert _at(img, x, w.height() - 1) == accent, "…or the bottom one"


def test_an_unselected_swatch_has_no_ring(qapp):
    """The half that makes the test above mean something: if every swatch were
    ringed, "the selected one is ringed" would be true and useless."""
    w = _row()
    w.setCurrentIndex(1)
    img = _grab(w)

    accent = QColor(theme.Colors.accent).name().upper()
    for i in (0, 2):
        assert accent not in _margin_above(img, w, i), i


def test_the_ring_follows_the_selection(qapp):
    w = _row()
    w.setCurrentIndex(0)
    accent = QColor(theme.Colors.accent).name().upper()

    _press(w, 2)
    img = _grab(w)

    assert accent in _margin_above(img, w, 2)
    assert accent not in _margin_above(img, w, 0)


def test_the_custom_tile_is_painted_as_a_gradient_not_a_flat_swatch(qapp):
    """It is the one tile that is not a colour, and it has to read that way —
    a flat tile among flat tiles is a colour the user will try to pick."""
    w = _row(allow_custom=True)
    img = _grab(w)

    rect = w._tile_rects()[-1]
    y = rect.center().y()
    left = _at(img, rect.left() + rect.width() * 0.15, y)
    right = _at(img, rect.right() - rect.width() * 0.15, y)

    assert left != right, "the conic gradient must vary across the tile"
    assert left not in {c.upper() for c in CURATED}
