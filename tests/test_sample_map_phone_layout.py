"""Home → Sample Map in phone layout: each layer one column that scrolls by a drag.

Reported: "Render sample map tab on mobile; it's unusable. Fix it". Measured
before the change, on a 428 × 932 phone with the mini dataset: nothing above the
layer stack scrolled, and the layers' minimum widths were 1,030 px (Replicates),
423 px (Variables, 774 px with a variable defined) and 761 px (Well Labels), all
squeezed into 428 — the plate's labels ran together, Quick Replicates' fields
were cut to "Group R" and "Replica", the Active-group line and the Clear / Add
buttons ran off the right edge. Sideways the stack had 195 px of height.

Built through the real path: the production ``ViewerContext`` with the mini
dataset loaded (``shell_ctx``), the real ``build_sample_map_subtab``, phone
layout's flag, touch unit and type scale applied as the shell applies them, and
each layer reached through ``set_layer``. What is asserted is the layout as laid
out — widths against the viewport, the place of each control — because the
report is about what is on the screen.

Each test names the wrong implementation it rules out, since several of them
look like fixes:

- a scroll area with the desk layers in it scrolls sideways (the width checks);
- ``phone_fit`` alone, without a scroll area, overlapped the rows it stacked
  (the overlap check);
- ``phone_fit`` alone inside a scroll area stacks Quick Replicates as three
  label-and-field pairs with the fields at three different x (the form check),
  puts the Variables definition above the plate, and leaves Add Prefix / Add
  Suffix at their desk width;
- a stack that keeps sizing itself by every layer, the one showing or not, is
  held as wide as the widest stacked layer (the width check: with the pages
  always counting, the Replicates layer held the stack 566 px wide on a
  414 px viewport);
- a phone form that is not undone leaves the desk changed (the round trip).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QComboBox, QLabel,
    QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from core import phone_layout

#: A phone held upright and sideways, as the window's page area has it: the
#: screen less the top bar and the foot strip.
UPRIGHT = (432, 760)

#: The phone form's side gutter in phone points (``sample_map/_phone.GUTTER_PT``),
#: restated so the file runs against a tree that predates it.
GUTTER_PT = 12
SIDEWAYS = (936, 380)


@pytest.fixture
def phone_type(qapp):
    """Phone layout's flag, touch unit and type scale, as ``PhoneShell`` sets
    them for a 432 px-wide screen — and the desk's back afterwards."""
    from ui import theme

    before = qapp.styleSheet()
    unit = phone_layout.phone_unit(432, 936)
    scale = phone_layout.type_scale(unit, "M", theme.desk_body_size())
    theme.set_touch_unit(unit)
    theme.apply_ui_scale(scale)
    qapp.setStyleSheet(theme.qss())
    phone_layout.set_on(True)
    try:
        yield unit
    finally:
        phone_layout.set_on(False)
        theme.set_touch_unit(None)
        theme.apply_ui_scale(1.0)
        qapp.setStyleSheet(before)


def _populate(ctx) -> None:
    """Two variables with values on the plate, two groups, the first current."""
    import review.domain.sample_variables as sv
    from domain.selections_format import make_selection

    wells = sorted(ctx.state.dataset.well_paths or {})
    assert wells, "premise: the mini dataset loaded wells"
    vset = sv.from_block({
        "schema_version": 1,
        "variables": [
            {"id": "dose", "name": "Dose", "kind": "number", "unit": "µM",
             "values": [{"id": "v1", "label": "1"}, {"id": "v2", "label": "10"},
                        {"id": "v3", "label": "100"}]},
            {"id": "line", "name": "Cell line", "kind": "text",
             "values": [{"id": "h", "label": "HeLa"}, {"id": "u", "label": "U2OS"}]},
        ],
        "assignments": {}})
    half = max(1, len(wells) // 2)
    vset = sv.assign(vset, "dose", wells[:half], "v1")
    vset = sv.assign(vset, "line", wells, "h")
    ctx.state.selection.variables = vset
    # ``make_selection`` is what ``sel_add`` stores; ``sel_add`` itself ends in
    # the full app's redraw fan-out, which this shell does not have. The groups
    # are painted into the list by the layer's own refresh on ``set_layer``.
    groups = ctx.state.selection.selections
    for i, (name, members) in enumerate((("Control DMSO", wells[:half]),
                                         ("Treated 10 µM", wells[half:]))):
        groups.append(make_selection(
            name=name, wells=members, used_names={g["name"] for g in groups},
            used_ids={g["id"] for g in groups}, fallback_color_idx=i))


def _sample_map(shell_ctx, qtbot, size, *, populated=False):
    from review.features.home.sample_map.tab_view import build_sample_map_subtab

    ctx, _window = shell_ctx
    if populated:
        _populate(ctx)
    # A window of a fixed size, as the tab sits in the app: a page of a tab
    # widget in a window the size of the screen. A window that may resize
    # grows to any minimum its content reports, if only for one layout pass,
    # and never shrinks back.
    frame = QWidget()
    QVBoxLayout(frame)
    qtbot.addWidget(frame)
    frame.setFixedSize(*size)
    build_sample_map_subtab(ctx, frame)
    frame.show()
    qtbot.waitExposed(frame)
    qtbot.wait(50)
    return ctx, frame


def _show(ctx, qtbot, layer):
    from review.features.home.sample_map.controller import set_layer
    from review.features.home.variables.controller import refresh_variables

    set_layer(ctx, layer)
    if layer == "Variables":
        refresh_variables(ctx)
    _settle(ctx, qtbot)


def _settle(ctx, qtbot, *, timeout_ms: int = 3000) -> None:
    """Wait until the tab's geometry stops changing.

    The fit is queued to the next turn of the loop, and a layer's repaint
    builds widgets that are polished and laid out over the turns after it; a
    fixed wait measured some of them mid-way under a loaded parallel run (a
    value chip read at its unpolished 80 × 23 one time and 100 × 30 the next).
    Bounded: geometry that never settles is itself a failure, and the
    assertions after this then measure whatever state it was left in.
    """
    root = ctx.widgets.get("_sample_map_layers").parentWidget()

    def snapshot():
        return [(w.geometry().getRect(), w.isVisible())
                for w in root.findChildren(QWidget)]

    last = snapshot()
    steady = 0
    for _ in range(timeout_ms // 30):
        qtbot.wait(30)
        now = snapshot()
        steady = steady + 1 if now == last else 0
        if steady >= 2:
            return
        last = now


def _scroll_over(ctx) -> QScrollArea | None:
    """The scroll area the layer stack sits in, if any."""
    w = ctx.widgets.get_or_none("_sample_map_stack")
    while w is not None:
        if isinstance(w, QScrollArea) and w.widget() is not None \
                and w.widget().isAncestorOf(ctx.widgets.get("_sample_map_stack")):
            return w
        w = w.parentWidget()
    return None


def _viewport(ctx) -> QWidget:
    scroll = _scroll_over(ctx)
    assert scroll is not None, "nothing above the layer stack scrolls"
    return scroll.viewport()


def _min_width(w: QWidget) -> int:
    return max(w.minimumWidth(), w.minimumSizeHint().width())


def _too_wide(content: QWidget, room: int) -> list[str]:
    return [f"{type(w).__name__} {w.objectName()!r} {_min_width(w)}px "
            f"{(getattr(w, 'text', lambda: '')() or '')[:30]!r}"
            for w in content.findChildren(QWidget)
            if w.isVisibleTo(content) and _min_width(w) > room][:8]


def _in_layout(lay, w) -> bool:
    if lay is None:
        return False
    for i in range(lay.count()):
        item = lay.itemAt(i)
        if item.widget() is w or _in_layout(item.layout(), w):
            return True
    return False


def _overlaps(page: QWidget) -> list[str]:
    """Pairs of visible, laid-out siblings whose rectangles intersect."""
    found = []
    for parent in [page, *page.findChildren(QWidget)]:
        if not parent.isVisible() or parent.layout() is None \
                or isinstance(parent, (QAbstractScrollArea, QAbstractSpinBox, QComboBox)):
            continue
        kids = [k for k in parent.children()
                if isinstance(k, QWidget) and k.isVisible() and not k.isWindow()
                and _in_layout(parent.layout(), k)]
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                common = a.geometry().intersected(b.geometry())
                if common.width() > 0 and common.height() > 0:
                    found.append(f"{type(a).__name__} {_label(a)!r} × "
                                 f"{type(b).__name__} {_label(b)!r} in "
                                 f"{type(parent).__name__} {parent.objectName()!r}")
    return found[:8]


def _label(w) -> str:
    text = getattr(w, "text", None)
    return (text() if callable(text) else w.objectName())[:30]


def _box(w: QWidget, viewport: QWidget) -> QRect:
    return QRect(w.mapTo(viewport, w.rect().topLeft()), w.size())


def _assert_in_view(ctx, scroll, names, *, touch_px: int) -> None:
    viewport = scroll.viewport()
    for name in names:
        w = ctx.widgets.get_or_none(name)
        assert w is not None, f"{name} was never registered"
        assert w.isVisible(), f"{name} is not on screen"
        box = _box(w, viewport)
        assert box.left() >= 0 and box.right() < viewport.width(), (
            f"{name} {_label(w)!r} spans x {box.left()}..{box.right()} in a "
            f"{viewport.width()} px viewport — cut at an edge")
        assert w.height() >= touch_px, (
            f"{name} {_label(w)!r} is {w.height()} px tall, under the "
            f"{touch_px} px touch floor")


LAYER_CONTROLS = {
    "Replicates": ("_rep_quick_pair_dir_var", "_rep_quick_size_var",
                   "_rep_quick_iter_order_var", "_rep_quick_apply"),
    "Variables": ("_var_add",),
    "Well Labels": ("_lbl_add_prefix", "_lbl_add_suffix"),
}

LAYER_PLATES = {"Replicates": "_rep_map_plate", "Variables": "_var_plate",
                "Well Labels": "_lbl_grid"}


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
@pytest.mark.parametrize("size", [UPRIGHT, SIDEWAYS], ids=["upright", "sideways"])
def test_every_layer_is_one_column_that_fits_the_phone(
        shell_ctx, qtbot, phone_type, size, populated):
    from review.features.home.sample_map.controller import LAYERS

    ctx, frame = _sample_map(shell_ctx, qtbot, size, populated=populated)
    touch_px = int(phone_layout.TOUCH_MIN_PT * phone_type)
    for layer in LAYERS:
        _show(ctx, qtbot, layer)
        page = ctx.widgets.get("_sample_map_stack").currentWidget()
        scroll = _scroll_over(ctx)
        room = scroll.viewport().width() if scroll is not None else size[0]
        content = scroll.widget() if scroll is not None else page

        # Wrong implementation: any form that leaves a row wider than the phone.
        assert _min_width(content) <= room, (
            f"{layer}: the layer needs {_min_width(content)} px of a {room} px "
            "phone:\n  " + "\n  ".join(_too_wide(content, room)))
        # Wrong implementation: phone_fit alone, which stacked rows that then
        # had no height to stack into.
        assert not _overlaps(page), (
            f"{layer}: widgets overlap:\n  " + "\n  ".join(_overlaps(page)))
        # Wrong implementation: the desk layers in a scroll area that scrolls
        # sideways, or one that does not scroll at all.
        assert scroll is not None and scroll.isVisible(), (
            f"{layer}: nothing above the layer stack scrolls")
        assert scroll.horizontalScrollBar().maximum() == 0, (
            f"{layer}: the layer scrolls sideways by "
            f"{scroll.horizontalScrollBar().maximum()} px")
        assert page.width() <= scroll.viewport().width()

        _assert_in_view(ctx, scroll, LAYER_CONTROLS[layer] if layer != "Variables"
                        else (("_var_add",) if populated else ("_var_add", "_var_empty")),
                        touch_px=0 if layer == "Variables" and not populated else touch_px)

        plate = ctx.widgets.get(LAYER_PLATES[layer])
        if layer != "Variables" or populated:
            box = _box(plate, scroll.viewport())
            assert plate.isVisible() and box.left() >= 0 \
                and box.right() < scroll.viewport().width(), (
                    f"{layer}: the plate is cut at an edge ({box})")
        if size == UPRIGHT and (layer != "Variables" or populated):
            # As large as the width allows: the full width less the gutters,
            # where the editor role's 330 px cap held it to three quarters.
            assert plate.width() >= scroll.viewport().width() - 2 * round(
                12 * phone_type) - 2, (
                    f"{layer}: the plate is {plate.width()} px of a "
                    f"{scroll.viewport().width()} px page")


def test_a_layer_that_grows_on_the_phone_is_fitted_again(shell_ctx, qtbot, phone_type):
    """Pressing the empty state's *+ Variable* on a phone swaps the empty state
    for the plate, the chips and the fill sentence — rows the fit has never
    seen, built after it ran, at the same viewport width. Wrong
    implementation: re-fitting only when the width changes, which left the
    fill sentence holding the layer wider than the phone."""
    from PySide6.QtCore import Qt

    ctx, _frame = _sample_map(shell_ctx, qtbot, UPRIGHT)
    _show(ctx, qtbot, "Variables")
    empty = ctx.widgets.get("_var_empty")
    assert empty.isVisible(), "premise: the layer opens on its empty state"
    button = next(b for b in empty.findChildren(QAbstractButton)
                  if "Variable" in b.text())
    qtbot.mouseClick(button, Qt.LeftButton)
    try:
        _settle(ctx, qtbot)
        assert ctx.widgets.get("_var_plate").isVisible(), (
            "premise: + Variable put the plate in the empty state's place")
        scroll = _scroll_over(ctx)
        room = scroll.viewport().width()
        assert _min_width(scroll.widget()) <= room, (
            f"after + Variable the layer needs {_min_width(scroll.widget())} px "
            f"of {room}:\n  " + "\n  ".join(_too_wide(scroll.widget(), room)))
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        # + Variable puts the cursor in the name field, whose focus-out commits
        # the name and repaints the layer. Let that happen now, while the tab
        # exists: left to the teardown that deletes the window, the focus-out
        # arrives mid-delete and the repaint builds rows into a dead list.
        ctx.widgets.get("_var_name").clearFocus()
        _settle(ctx, qtbot)


def test_the_page_scrolls_by_a_drag(shell_ctx, qtbot, phone_type):
    """The layer is taller than a sideways phone, and the area holding it is
    one ``PhoneShell`` grabs for drag-scrolling."""
    from ui.phone_shell import drag_scroll_eligible, grab_every_scroll_area

    ctx, _frame = _sample_map(shell_ctx, qtbot, SIDEWAYS, populated=True)
    _show(ctx, qtbot, "Replicates")
    scroll = _scroll_over(ctx)
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0, (
        "premise: the Replicates layer is taller than a sideways phone")
    assert drag_scroll_eligible(scroll)
    grab_every_scroll_area(True)
    try:
        assert scroll.viewport().property("_awDragScroll"), (
            "the page's scroll area is not drag-scrolled")
    finally:
        grab_every_scroll_area(False)


def test_each_layer_takes_its_own_phone_form(shell_ctx, qtbot, phone_type):
    """The forms, not just the widths: what the generic pass alone gets wrong."""
    ctx, _frame = _sample_map(shell_ctx, qtbot, UPRIGHT, populated=True)
    reg = ctx.widgets

    _show(ctx, qtbot, "Replicates")
    viewport = _viewport(ctx)
    fields = [reg.get(n) for n in LAYER_CONTROLS["Replicates"][:3]]
    lefts = {_box(f, viewport).left() for f in fields}
    assert len(lefts) == 1, (
        f"Quick Replicates' fields start at x {sorted(lefts)}: not one form "
        "column, so each sits wherever its own label ends")
    tops = [_box(f, viewport).top() for f in fields]
    assert tops == sorted(tops) and len(set(tops)) == 3, (
        f"Quick Replicates' fields are not one under another: tops {tops}")
    apply_button = reg.get("_rep_quick_apply")
    assert _box(apply_button, viewport).top() > max(tops)
    assert apply_button.width() >= viewport.width() - 2 * round(GUTTER_PT * phone_type) - 2, (
        f"Apply Quick Replicates is {apply_button.width()} px, not the page's width")

    _show(ctx, qtbot, "Variables")
    viewport = _viewport(ctx)
    assert _box(reg.get("_var_plate"), viewport).top() \
        < _box(reg.get("_var_list"), viewport).top(), (
            "the definition is above the plate: the plate is what the layer is for")

    _show(ctx, qtbot, "Well Labels")
    viewport = _viewport(ctx)
    prefix, suffix = reg.get("_lbl_add_prefix"), reg.get("_lbl_add_suffix")
    assert _box(prefix, viewport).top() == _box(suffix, viewport).top(), (
        "Add Prefix and Add Suffix are not side by side")
    assert min(prefix.width(), suffix.width()) >= 0.4 * viewport.width(), (
        f"Add Prefix / Add Suffix are {prefix.width()} / {suffix.width()} px of "
        f"{viewport.width()}: not sharing the width")


def _desk_geometry(ctx, qtbot) -> dict:
    from review.features.home.sample_map.controller import LAYERS

    out = {}
    root = ctx.widgets.get("_sample_map_layers").parentWidget()
    for layer in LAYERS:
        _show(ctx, qtbot, layer)
        rows = []
        for w in root.findChildren(QWidget):
            if not w.isVisible() or not isinstance(
                    w, (QAbstractButton, QLabel, QLineEdit, QComboBox,
                        QAbstractSpinBox, QAbstractScrollArea)):
                continue
            if isinstance(w, QLineEdit) and isinstance(w.parentWidget(), QAbstractSpinBox):
                continue
            rows.append((type(w).__name__, _label(w),
                         w.mapTo(root, w.rect().topLeft()).toTuple(), w.size().toTuple()))
        out[layer] = sorted(rows)
    return out


def test_leaving_phone_layout_gives_the_desk_its_form_back(shell_ctx, qtbot, qapp):
    """Every visible control of every layer is where it was, the size it was,
    after a visit to phone layout — and the body is back out of the scroll area.
    Wrong implementation: a phone form with no way back (the editor column left
    without its fixed width, Quick Replicates left stacked, the plate left at
    the canvas role)."""
    from ui import theme

    ctx, frame = _sample_map(shell_ctx, qtbot, (1250, 900), populated=True)
    desk = _desk_geometry(ctx, qtbot)
    before = qapp.styleSheet()
    unit = phone_layout.phone_unit(432, 936)
    theme.set_touch_unit(unit)
    phone_layout.set_on(True)
    try:
        qtbot.wait(50)
        assert _scroll_over(ctx) is not None, "premise: the phone form was taken"
    finally:
        phone_layout.set_on(False)
        theme.set_touch_unit(None)
        qapp.setStyleSheet(before)
    qtbot.wait(50)
    body = ctx.widgets.get("_sample_map_body")
    assert body.parentWidget() is frame, "the body was not put back in the tab"
    assert not ctx.widgets.get("_sample_map_scroll").isVisible()
    after = _desk_geometry(ctx, qtbot)
    for layer, rows in desk.items():
        moved = sorted(set(rows) ^ set(after[layer]))
        assert not moved, f"{layer} changed at the desk:\n  " + "\n  ".join(map(str, moved[:8]))
