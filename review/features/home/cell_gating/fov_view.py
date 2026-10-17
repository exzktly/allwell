"""The Cell Gating tab's field-of-view picker (Qt).

One row in the tab's *Cell inclusion* section: a button naming which fields of
view count, opening a checkbox menu of every FOV the dataset has, and a muted
line under it saying what the selection keeps and where it could not apply.

The selection itself is ``ctx.state.cell_gating.fov_selection``; what it does
to the data is :mod:`review.data.fov_selection`'s ``FOV_Pass`` layer, which the
inclusion chokepoint ANDs with every other layer, so every plot, statistic and
export honours it. This module only edits the slice and asks for the
re-projection, persistence and redraw that a changed layer needs.

* :func:`build_fov_selection_row` — the row, registered on ``ctx.widgets``.
* :func:`set_fov_selection` — the one write path: state, disk, cache, redraw.
* :func:`refresh_fov_selection_widgets` — repaint the button and the status line.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.safe import swallow
from review.persistence.cell_gating import save_to_pipeline_info
from review.data.fov_selection import (
    fov_button_text,
    fov_match_key,
    fov_selection_status,
    normalise_selection,
    selected_fovs,
)
from widgets import menu_exec

#: Minimum width of the picker button, named for ARCHITECTURE §8.
_FOV_BTN_MIN_W = 150

_TOOLTIP = (
    "Restrict every plot, statistic and export to the chosen fields of view "
    "(FOV numbers). Cells in the other fields are excluded in every well. "
    "Saved with the dataset.")


def available_fovs(ctx) -> list[str]:
    """The dataset's FOV labels, numerically ordered."""
    from review.data.dataset_dims import fov_options
    from util.fov import sort_key

    return sorted(fov_options(ctx), key=sort_key)


def build_fov_selection_row(ctx, parent: QWidget, *, indent: int) -> QWidget:
    """The picker row: ``Fields of view: [All FOVs]`` over its status line."""
    box = QWidget(parent)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(indent, 2, 0, 2)
    outer.setSpacing(2)

    row = QWidget(box)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel("Fields of view:", row)
    label.setToolTip(_TOOLTIP)
    layout.addWidget(label)
    btn = QPushButton("All FOVs", row)
    btn.setMinimumWidth(_FOV_BTN_MIN_W)
    btn.setToolTip(_TOOLTIP)
    btn.clicked.connect(lambda _=False: open_fov_menu(ctx))
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
    """Repaint the button text and the status line from the live slice."""
    from review.data.field_quality_cache import untrusted_wells

    btn = ctx.runtime.cell_gating.fov_btn
    status = ctx.runtime.cell_gating.fov_status
    if btn is None and status is None:
        return
    selection = selected_fovs(ctx)
    if btn is not None:
        btn.setText(fov_button_text(selection))
    if status is not None:
        text = fov_selection_status(
            selection, available_fovs(ctx),
            untrusted_wells(ctx) if selection is not None else [])
        status.setText(text)
        status.setVisible(bool(text))


def set_fov_selection(ctx, selection) -> None:
    """Make *selection* the fields of view every calculation uses.

    ``None`` (or every field the dataset has) means all of them. Saves the
    gating block, re-projects every verdict layer onto the cached wells —
    which also clears the statistics cache and repaints the tab's inclusion
    readout — and redraws the app.
    """
    new = normalise_selection(selection, available_fovs(ctx))
    if new == selected_fovs(ctx):
        return
    ctx.state.cell_gating.fov_selection = new
    with swallow("save the FOV selection to pipeline_info.json"):
        save_to_pipeline_info(ctx)
    with swallow("re-project the inclusion layers after an FOV selection change"):
        ctx.features.call("qc.recompute_all", ctx)
    refresh_fov_selection_widgets(ctx)
    # The tab's own CDFs are read from the first kept field, so they move too.
    with swallow("re-read the Cell Gating CDFs after an FOV selection change"):
        ctx.features.call("cell_gating.load_cell_areas", ctx)
    with swallow("redraw after an FOV selection change"):
        from review.core.plot_orchestrator import redraw_app
        redraw_app(ctx)


def open_fov_menu(ctx) -> None:
    """Pop up one checkbox per FOV plus All / None, and apply on close.

    The ticks are gathered while the menu is open and applied once when it
    closes: each application re-projects every layer over every cached well and
    redraws, which is not something to do per click.
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction

    btn = ctx.runtime.cell_gating.fov_btn
    if btn is None:
        return
    fovs = available_fovs(ctx)
    current = selected_fovs(ctx)
    keys = None if current is None else {fov_match_key(f) for f in current}
    chosen = {f for f in fovs if keys is None or fov_match_key(f) in keys}

    menu = QMenu(btn)
    if not fovs:
        empty = QAction("No fields of view found in this dataset", menu)
        empty.setEnabled(False)
        menu.addAction(empty)
    boxes: list = []
    for fov in fovs:
        cb = QCheckBox(f"FOV {fov}", menu)
        cb.setChecked(fov in chosen)

        def _on(checked: bool, _fov=fov) -> None:
            chosen.add(_fov) if checked else chosen.discard(_fov)

        cb.toggled.connect(_on)
        boxes.append(cb)
        action = QWidgetAction(menu)
        action.setDefaultWidget(cb)
        menu.addAction(action)
    if fovs:
        menu.addSeparator()

        def _tick_all(state: bool) -> None:
            for box in boxes:
                box.setChecked(state)

        all_a = QAction("All", menu)
        all_a.triggered.connect(lambda _=False: _tick_all(True))
        menu.addAction(all_a)
        none_a = QAction("None", menu)
        none_a.triggered.connect(lambda _=False: _tick_all(False))
        menu.addAction(none_a)
    menu_exec.exec_transient_menu(menu, btn.mapToGlobal(btn.rect().bottomLeft()))
    if fovs:
        set_fov_selection(ctx, chosen)
