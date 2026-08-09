"""The Cell Gating tab's structure.

Asserts what a layout change can quietly break: that both per-channel columns
are present for every channel and live in one table, that the table is not
buried in a height-capped scroll area, that each layer's two sources are
exclusive, that the scatter option becomes selectable when a gate is finally
drawn on the *other* tab, and that the optimizer is built but not shown.

Nothing new is imported at module scope on purpose, so this file can be run
against the pre-layout code (``scripts/prove_guard.sh``) and fail by *measuring*
rather than by failing to import.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas")

pytestmark = pytest.mark.qt


def _build(ctx, qtbot):
    """Build the tab and populate its rows, as the sub-tab entry point does.

    ``build_cell_gating_tab`` frames the table; the rows come from
    ``cell_gating_load_cell_areas``, which the real path
    (``build_cell_gating_subtab``) calls straight afterwards. Building without
    it gives an empty table and would make a row assertion here measure the
    test's own shortcut rather than the tab.
    """
    from PySide6.QtWidgets import QWidget
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_load_cell_areas,
    )
    from well_viewer.features.grouping.cell_gating.tab_view import build_cell_gating_tab

    parent = QWidget()
    qtbot.addWidget(parent)
    build_cell_gating_tab(ctx, parent)
    cell_gating_load_cell_areas(ctx)
    return parent


def test_each_decision_gets_its_own_per_channel_table(loaded_app, qtbot):
    """Every channel has both fields, and each lives under the section that owns
    the decision it feeds.

    The inclusion floor and the ON cutoff are per-channel numbers of the same
    shape answering different questions — one decides whether a cell is in the
    dataset at all, the other only how the cells in it are counted. A table
    serving both needs a heading that names neither, which is the original
    defect (both columns under the ThreshFracOn header) with a politer label.
    """
    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    fluor = ctx.runtime.cell_gating.fluor_gate_edits
    thresh = ctx.runtime.cell_gating.thresh_frac_edits
    channels = set(ctx.state.channel.fluor_channels)
    assert channels, "fixture should load channels"
    assert channels <= set(fluor), "a channel is missing its inclusion floor"
    assert channels <= set(thresh), "a channel is missing its ON cutoff"

    ch = sorted(channels)[0]
    assert fluor[ch].parent() is not thresh[ch].parent(), (
        "both decisions are sharing one table again")

    def _hosted_by(widget, host):
        node = widget.parentWidget()
        while node is not None:
            if node is host:
                return True
            node = node.parentWidget()
        return False

    assert _hosted_by(fluor[ch], ctx.widgets.get("_cell_gating_inner"))
    assert _hosted_by(thresh[ch], ctx.widgets.get("_cell_gating_thresh_inner"))


def test_the_table_is_not_inside_its_own_scroll_area(loaded_app, qtbot):
    """The primary input surface must not scroll inside the scrolling page.

    It used to sit in a QScrollArea pinned to 120px, so a plate with a few
    channels and a ratio metric scrolled a nested box on a window with room to
    spare.
    """
    from PySide6.QtWidgets import QScrollArea

    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    inner = ctx.widgets.get("_cell_gating_inner")
    node = inner.parentWidget()
    while node is not None:
        assert not isinstance(node, QScrollArea) or node.maximumHeight() > 120, (
            "the channel table is inside a height-capped scroll area again")
        node = node.parentWidget()


def test_each_layer_offers_its_two_sources_exclusively(loaded_app, qtbot):
    """Selecting the scatter source deselects the per-channel one, and drives
    the same flag the checkbox it replaced drove."""
    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    scatter = ctx.widgets.get("_cell_gating_use_scatter_gates_cb")
    group = scatter.group()
    assert group is not None and group.exclusive()
    per_channel = next(b for b in group.buttons() if b is not scatter)

    assert per_channel.isChecked() and not scatter.isChecked()

    scatter.setEnabled(True)          # a gate exists in the real flow
    scatter.setChecked(True)
    assert not per_channel.isChecked()
    assert ctx.state.cell_gating.use_scatter_gates is True

    per_channel.setChecked(True)
    assert ctx.state.cell_gating.use_scatter_gates is False


def test_the_two_gate_pickers_belong_to_visibly_different_layers(loaded_app, qtbot):
    """Each picker sits in its own layer's row, so the two are not two
    identically-labelled buttons 37 lines apart."""
    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    gating_btn = ctx.widgets.get("_cell_gating_gating_gate_btn")
    thresh_btn = ctx.widgets.get("_cell_gating_thresh_gate_btn")
    assert gating_btn.parent() is not thresh_btn.parent()

    gating_radio = ctx.widgets.get("_cell_gating_use_scatter_gates_cb")
    thresh_radio = ctx.widgets.get("_cell_gating_use_scatter_thresh_cb")
    assert gating_btn.parent() is gating_radio.parent()
    assert thresh_btn.parent() is thresh_radio.parent()


def test_the_scatter_source_wakes_up_when_a_gate_is_drawn(loaded_app, qtbot):
    """Gates are drawn on another tab, so this cannot be decided at build time.

    Deciding it once would leave the option permanently dead for anyone who
    opened Cell Gating before drawing their first gate.
    """
    from well_viewer.features.grouping.cell_gating.controller import (
        _cell_gating_refresh_gate_filter,
    )

    ctx = loaded_app._ctx
    ctx.state.cell_gating.scatter_gates = []
    _build(ctx, qtbot)

    scatter = ctx.widgets.get("_cell_gating_use_scatter_gates_cb")
    hint = ctx.runtime.cell_gating.gating_no_gates_hint
    assert not scatter.isEnabled()
    assert hint is not None and hint.isVisibleTo(hint.parentWidget())

    ctx.state.cell_gating.scatter_gates = [
        {"name": "Gate 1", "x_col": "a", "y_col": "b",
         "x0": 0, "y0": 0, "x1": 1, "y1": 1},
    ]
    _cell_gating_refresh_gate_filter(ctx)

    assert scatter.isEnabled()
    assert not hint.isVisibleTo(hint.parentWidget())


def test_the_optimizer_is_built_but_not_shown(loaded_app, qtbot):
    """It is a tool, not tab state: off the page, but built so its handles
    register and the data-change refreshers need no build-state guard."""
    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    win = ctx.runtime.cell_gating.optimizer_window
    assert win is not None
    assert win.isHidden(), "the optimizer must not occupy the tab"
    assert not win.isModal(), "it is read against the CDFs behind it"
    assert ctx.widgets.get("_cell_gating_opt_channel_combo") is not None
    assert ctx.widgets.get("_cell_gating_opt_btn").parent() is not None


def test_the_funnel_label_reports_real_numbers_after_a_build(loaded_app, qtbot):
    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    text = ctx.widgets.get("_cell_gating_funnel_label").text()
    assert "segmented cells" in text
    assert "QC debris gate" in text
