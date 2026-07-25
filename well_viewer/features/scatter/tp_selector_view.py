"""The scatter tabs' multi-timepoint picker — one implementation, two tabs.

Both scatter sub-tabs (per-cell and aggregate) drop a checkbox menu below their
"Timepoints" button, with All / None actions. The two were separate copies of
the same forty-five lines, differing only in which selection dict they mutate,
which button they hang off, and which pair of callbacks they fire — so a fix to
one (the signal-blocking in ``_set_all``, say) had to be remembered for the
other.

Qt builder, no business logic: it reads and writes a selection dict the caller
owns and calls back into the caller's controller.
"""

from __future__ import annotations

from typing import Callable, Dict

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QMenu, QWidgetAction


def open_timepoint_selector(
    ctx,
    *,
    selections: Dict[str, bool],
    button_name: str,
    refresh_display: Callable,
    redraw: Callable,
) -> None:
    """Pop up a checkbox menu of timepoints below *button_name*.

    *selections* is the live ``{tp_string: bool}`` dict the tab owns; it is
    mutated in place, so the caller's state is the single source of truth.
    *refresh_display* and *redraw* are the tab's own ``(ctx) -> None`` callbacks,
    run after every change — including the All / None bulk actions, which block
    each checkbox's signals while setting it so the per-box handler cannot fire
    once per box and trigger a redraw storm.
    """
    btn = ctx.widgets.get_or_none(button_name)
    menu = QMenu(btn)

    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(selections.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(selections[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str):
            def on_toggled(checked: bool) -> None:
                selections[tp] = bool(checked)
                refresh_display(ctx)
                redraw(ctx)
            return on_toggled

        cb.toggled.connect(_make_handler())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)

    menu.addSeparator()

    def _set_all(value: bool) -> None:
        for tp_str, cb in checkboxes.items():
            cb.blockSignals(True)
            try:
                cb.setChecked(value)
            finally:
                cb.blockSignals(False)
            selections[tp_str] = value
        refresh_display(ctx)
        redraw(ctx)

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
