"""The focused cell's status-bar chip, and what clearing or clicking it does.

The chip (``widgets.focus_chip.FocusChip``) names the focused cell
(``review/core/cell_focus.py``) wherever the user is, so a ring drawn on a tab
they have not looked at yet can still be found. Clicking its text opens the cell
in Image Browser → Segmentation (the ``image_browser.show_cell`` service, so a
build without the Image Browser offers nothing rather than failing); ✕ clears
the focus. Opening a dataset clears it too: the cell belonged to the last one.
"""

from __future__ import annotations


def build_focus_chip(win, parent):
    """Build the chip into *parent*, register it as ``_focus_chip`` and wire it."""
    from widgets.focus_chip import FocusChip

    from review.core.cell_focus import focused_cell, set_focused_cell

    ctx = win._ctx
    chip = FocusChip(parent, tooltip="The focused cell — click to open it in Segmentation")
    win._widget_registry.register("_focus_chip", chip)

    def _refresh(*_args) -> None:
        cell = focused_cell(ctx)
        chip.setItem(cell.caption if cell is not None else None)

    def _open() -> None:
        cell = focused_cell(ctx)
        if cell is None:
            return
        shown = ctx.features.query("image_browser.show_cell", ctx, cell.well, cell.fov,
                                   cell.tp, str(cell.nucleus_id), default=False)
        if not shown:
            ctx.notify.set_status("This build has no Segmentation page to open the cell in.")

    chip.activated.connect(_open)
    chip.cleared.connect(lambda: set_focused_cell(ctx, None))
    ctx.bus.focused_cell_changed.connect(_refresh)
    ctx.bus.dataset_loaded.connect(lambda *_a: set_focused_cell(ctx, None))
    _refresh()
    return chip
