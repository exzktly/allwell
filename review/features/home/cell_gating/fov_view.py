"""The Cell Gating tab's field-of-view picker (Qt).

One row in the tab's *Cell inclusion* section: a button naming which fields of
view count, opening a checkbox menu of every FOV the dataset has, and a muted
line under it saying what the selection keeps and where it could not apply.

The selection itself is ``ctx.state.cell_gating.fov_selection``; what it does
to the data is :mod:`review.data.fov_selection`'s ``FOV_Pass`` layer, which the
inclusion chokepoint ANDs with every other layer, so every plot, statistic and
export honours it. This module only builds and repaints widgets. Applying a
selection — saving it, re-projecting the layers, redrawing — is
``controller.apply_fov_selection_with_progress``, which the tab builder
hands in as ``on_apply``; this module names no controller, so the controller can import it.

* :func:`build_fov_selection_row` — the row, registered on ``ctx.widgets``.
* :func:`refresh_fov_selection_widgets` — repaint the button and the status line.
* :func:`open_fov_menu` — the checklist; calls ``on_apply`` only on a change.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from review.data.fov_selection import (
    available_fovs,
    fov_button_text,
    fov_match_key,
    fov_selection_status,
    selected_fovs,
)

# Pixel geometry for this row, named rather than written at the call sites
# (ARCHITECTURE §8: never hardcode a pixel size at a call site). The vertical
# inset and the gap between the button row and its status line match the QC
# gate's rows above it, so the two always-applied controls read as one block.
_FOV_BTN_MIN_W = 150
_ROW_MARGIN_V = 2
_ROW_SPACING = 2

_TOOLTIP = (
    "Restrict every plot, statistic and export to the chosen fields of view "
    "(FOV numbers). Cells in the other fields are excluded in every well. "
    "Saved with the dataset.")


def build_fov_selection_row(ctx, parent: QWidget, *, indent: int,
                            on_apply: Callable) -> QWidget:
    """The picker row: ``Fields of view: [All FOVs]`` over its status line.

    *on_apply* is ``(ctx, selection)`` — called with the ticked FOV labels when
    the menu closes having changed something.
    """
    box = QWidget(parent)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(indent, _ROW_MARGIN_V, 0, _ROW_MARGIN_V)
    outer.setSpacing(_ROW_SPACING)

    row = QWidget(box)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel("Fields of view:", row)
    label.setToolTip(_TOOLTIP)
    layout.addWidget(label)
    btn = QPushButton("All FOVs", row)
    btn.setMinimumWidth(_FOV_BTN_MIN_W)
    btn.setToolTip(_TOOLTIP)
    btn.clicked.connect(lambda _=False: open_fov_menu(ctx, on_apply))
    layout.addWidget(btn)
    layout.addStretch(1)
    outer.addWidget(row)

    status = QLabel("", box)
    status.setObjectName("Muted")
    status.setWordWrap(True)
    status.setVisible(False)
    outer.addWidget(status)

    ctx.widgets.register("_cell_gating_fov_btn", btn)
    ctx.widgets.register("_cell_gating_fov_status", status)
    refresh_fov_selection_widgets(ctx)
    return box


def refresh_fov_selection_widgets(ctx) -> None:
    """Repaint the button text and the status line from the live slice.

    Called on every inclusion-count repaint, so with no selection — the common
    case — it reads nothing but the slice: the status line is empty then, and
    the dataset's FOV list and untrusted wells are not looked up at all.
    """
    rt = ctx.runtime.cell_gating
    btn = getattr(rt, "fov_btn", None)
    status = getattr(rt, "fov_status", None)
    if btn is None and status is None:
        return
    selection = selected_fovs(ctx)
    if btn is not None:
        btn.setText(fov_button_text(selection))
    if status is None:
        return
    text = ""
    if selection is not None:
        from review.data.field_quality_cache import untrusted_wells
        text = fov_selection_status(selection, available_fovs(ctx), untrusted_wells(ctx))
    status.setText(text)
    status.setVisible(bool(text))


#: How long after the last tick of a burst a Cell Gating picker applies (ms).
#: Each application re-projects every layer over every cached well behind a
#: progress popup, so it runs once per burst, not once per tick — and at once
#: when the page is left with a change pending. Long enough to swallow a run
#: of ticks; the pass itself was timed only on the test plates (a few wells,
#: tens of milliseconds), not on a full plate, so this is the length of a
#: pause between ticks rather than a figure derived from the pass.
APPLY_SETTLE_MS = 900


def open_fov_menu(ctx, on_apply: Callable) -> None:
    """Show one box per FOV, under All / None, as a page of the inspector.

    The page applies as the user ticks, once per burst of ticks
    (:data:`APPLY_SETTLE_MS`): each application re-projects every layer over
    every cached well and redraws, which is not something to do per click.

    Nothing is applied when the boxes end as they began — a look is not an
    edit. And a selected FOV the dataset does not have (one
    carried over from a saved plot or an earlier run, which the status line
    names) has no box, so it is carried through untouched rather than dropped
    by an edit to the boxes that do exist.
    """
    from review.core.inspector_pages import open_multi_select

    btn = ctx.runtime.cell_gating.fov_btn
    if btn is None:
        return
    fovs = available_fovs(ctx)
    current = selected_fovs(ctx)
    keys = None if current is None else {fov_match_key(f) for f in current}
    initial = {f for f in fovs if keys is None or fov_match_key(f) in keys}
    avail_keys = {fov_match_key(f) for f in fovs}
    absent = {f for f in (current or ()) if fov_match_key(f) not in avail_keys}
    applied = [set(initial)]

    def _apply(picked: list) -> None:
        chosen = set(picked)
        if chosen != applied[0]:
            applied[0] = set(chosen)
            on_apply(ctx, chosen | absent)

    open_multi_select(ctx, btn, key="cell_gating/fovs", title="Fields of view",
                      items=[(f, f"FOV {f}") for f in fovs],
                      checked=[f for f in fovs if f in initial], on_change=_apply,
                      settle_ms=APPLY_SETTLE_MS,
                      empty_text="No fields of view found in this dataset.")
