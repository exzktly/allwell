"""Bar Plots' Reset order is enabled exactly while a custom order is in force — however the order got there.

It became a quiet command enabled only while there is an order to reset (design language D13). The drag and the
button keep it in step, but an order also arrives by a restored view or saved plot (``view_state._apply_bar``) and
leaves by a reset of the plot state; those reach the tab through ``refresh_bar_layout_btn_state``, which must sync
it too, or a restored order stands with Reset order greyed out and no way back but another drag.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout


def test_reset_order_follows_an_order_set_without_the_drag(shell_ctx, qtbot):
    from review.features.barplot.controller import refresh_bar_layout_btn_state
    from review.features.barplot.tab_view import build_bar_plots_tab

    ctx, _window = shell_ctx
    parent = QWidget()
    QVBoxLayout(parent)
    qtbot.addWidget(parent)
    build_bar_plots_tab(ctx, parent)
    button = ctx.widgets.get("_bar_reset_order_btn")
    assert ctx.state.plot.bar_order is None and not button.isEnabled()

    ctx.state.plot.bar_order = ["A02", "A01"]          # what a restored view writes
    refresh_bar_layout_btn_state(ctx)
    assert button.isEnabled()

    ctx.state.plot.bar_order = None                    # what a reset of the plot state leaves
    refresh_bar_layout_btn_state(ctx)
    assert not button.isEnabled()
