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
    from review.features.home.cell_gating.controller import (
        cell_gating_load_cell_areas,
    )
    from review.features.home.cell_gating.tab_view import build_cell_gating_tab

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
    from review.features.home.cell_gating.controller import (
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


def test_a_section_lead_in_never_outdents_past_its_own_heading():
    """Excludes the lead-in that reads as outranking the section it belongs to.

    ``build_section_header`` lays its title out inside a block with a left
    margin; a lead-in added straight to the section's layout starts at 0. So
    "Always applied:" and "Then include cells by (choose one):" sat measurably
    **left of the "Cell inclusion" heading that owns them** — 364 and 365
    against the title's 373, measured off the rendered panel.

    Asserted against the header's own margin rather than against a number, so
    the two cannot drift apart again: the wrong implementation this excludes
    is a second copy of "8" written down here.
    """
    from review.core.ui_helpers import SECTION_HEADER_MARGINS
    from review.features.home.cell_gating.tab_view import _LEAD_IN_INDENT

    assert _LEAD_IN_INDENT == SECTION_HEADER_MARGINS[0]


def test_the_lead_in_carries_that_indent_onto_the_widget(qtbot):
    """And it reaches the label, not just a constant. Asserted at the boundary
    (CLAUDE.md §7.4 #1): a named indent nothing applies is a number in a file.
    """
    from PySide6.QtWidgets import QWidget

    from review.core.ui_helpers import SECTION_HEADER_MARGINS
    from review.features.home.cell_gating.tab_view import _lead_in

    parent = QWidget()
    qtbot.addWidget(parent)
    lbl = _lead_in(parent, "Always applied:")
    assert lbl.contentsMargins().left() == SECTION_HEADER_MARGINS[0]

def test_pressing_optimize_writes_the_cutoff_the_scan_chose(loaded_app, qtbot,
                                                            monkeypatch):
    """The button, pressed, end to end — not the helper underneath it.

    Eighteen files exercise the optimizer's compute layer and none of them
    presses this button, which is the gap a callback bug lives in: the scan can
    be perfect while the value never reaches the field the gating pass reads.
    So this drives the real widgets — set the combos, type the range, click —
    and asserts the ThreshFracOn edit afterwards.

    The two wells' rows are substituted so the fixture's own data cannot decide
    whether the assertion is possible. They are built to make the right and the
    wrong answers differ: Well A is 70% responders at 1500 against a background
    mode at 200, Well B is all background, so the cut belongs between the modes
    and a scan that silently did nothing would leave the field at its default.
    """
    import numpy as np
    import pandas as pd

    from review.features.home.cell_gating import controller as ctl

    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    rt = ctx.runtime.cell_gating
    # From the combo, not from a sorted channel list: the scan reads the
    # combo's ``currentData``, and a fixture whose alphabetically-first channel
    # is not the combo's first one would leave this measuring nothing but its
    # own mismatch.
    channel = str(rt.opt_channel_combo.currentData()
                  or rt.opt_channel_combo.currentText()).strip()
    wells = [rt.opt_well_a_combo.itemText(i)
             for i in range(rt.opt_well_a_combo.count())]
    if len(wells) < 2:
        pytest.skip("fixture has one well; the optimizer needs two")

    rng = np.random.default_rng(2)
    hot = np.concatenate([rng.lognormal(np.log(1500), 0.25, 700),
                          rng.lognormal(np.log(200), 0.3, 300)])
    cold = rng.lognormal(np.log(200), 0.3, 1000)

    def _rows(_ctx, well):
        values = hot if well == wells[0] else cold
        return pd.DataFrame({
            "area_px": [100] * len(values),
            "Included": [1] * len(values),
            f"{channel}_mean_intensity": values,
        })

    monkeypatch.setattr(ctl, "get_rows", _rows)

    rt.opt_well_a_combo.setCurrentIndex(0)
    rt.opt_well_b_combo.setCurrentIndex(1)
    rt.opt_min_edit.setText("0")
    rt.opt_max_edit.setText("5000")

    edit = rt.thresh_frac_edits[channel]
    edit.setText("0")
    ctx.widgets.get("_cell_gating_opt_btn").click()

    applied = float(edit.text())
    assert 400.0 < applied < 1200.0, (
        f"the button left the ON cutoff at {edit.text()!r}; the status line "
        f"says {rt.opt_result_label.text()!r}"
    )
    # The readout has to carry the N, since the N is what the cut rests on.
    status = rt.opt_result_label.text()
    assert "/1,000 ON" in status
    assert "95% confidence" in status


def test_the_specificity_weight_field_reaches_the_scan(loaded_app, qtbot,
                                                       monkeypatch):
    """A field nothing reads is a field that lies, so press the button twice.

    Raising the weight makes a cell counted ON in Well B more expensive, which
    moves the cut up. Asserting the field exists would pass with it wired to
    nothing; asserting that the two clicks disagree cannot.
    """
    import numpy as np
    import pandas as pd

    from review.features.home.cell_gating import controller as ctl

    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    rt = ctx.runtime.cell_gating
    # From the combo, not from a sorted channel list: the scan reads the
    # combo's ``currentData``, and a fixture whose alphabetically-first channel
    # is not the combo's first one would leave this measuring nothing but its
    # own mismatch.
    channel = str(rt.opt_channel_combo.currentData()
                  or rt.opt_channel_combo.currentText()).strip()
    wells = [rt.opt_well_a_combo.itemText(i)
             for i in range(rt.opt_well_a_combo.count())]
    if len(wells) < 2:
        pytest.skip("fixture has one well; the optimizer needs two")

    rng = np.random.default_rng(8)
    hot = np.concatenate([rng.normal(1500, 200, 700), rng.normal(300, 80, 300)])
    cold = np.concatenate([rng.normal(300, 80, 900), rng.normal(900, 150, 100)])

    def _rows(_ctx, well):
        values = hot if well == wells[0] else cold
        return pd.DataFrame({
            "area_px": [100] * len(values),
            "Included": [1] * len(values),
            f"{channel}_mean_intensity": values,
        })

    monkeypatch.setattr(ctl, "get_rows", _rows)
    rt.opt_well_a_combo.setCurrentIndex(0)
    rt.opt_well_b_combo.setCurrentIndex(1)
    rt.opt_min_edit.setText("0")
    rt.opt_max_edit.setText("3000")
    edit = rt.thresh_frac_edits[channel]

    rt.opt_weight_edit.setText("1")
    ctx.widgets.get("_cell_gating_opt_btn").click()
    plain = float(edit.text())

    rt.opt_weight_edit.setText("10")
    ctx.widgets.get("_cell_gating_opt_btn").click()
    strict = float(edit.text())

    assert strict > plain, (
        f"the Spec. weight field changed nothing: {plain} then {strict}")


def test_the_optimizer_curve_popup_draws_without_raising(loaded_app, qtbot,
                                                         monkeypatch, caplog):
    """The popup is built inside a ``swallowed``, so a crash in it is silent.

    Every line the confidence-bounded objective added to ``opt_curve_view`` —
    the re-derived candidate grid, the counts, the score curve — sits behind
    that handler, and a ``raise`` as the function's first statement left the
    suite green. So this presses the button and then reads the log the handler
    writes to, which is the only place the failure surfaces.
    """
    import logging

    import numpy as np
    import pandas as pd

    from review.features.home.cell_gating import controller as ctl

    ctx = loaded_app._ctx
    _build(ctx, qtbot)

    rt = ctx.runtime.cell_gating
    channel = str(rt.opt_channel_combo.currentData()
                  or rt.opt_channel_combo.currentText()).strip()
    wells = [rt.opt_well_a_combo.itemText(i)
             for i in range(rt.opt_well_a_combo.count())]
    if len(wells) < 2:
        pytest.skip("fixture has one well; the optimizer needs two")

    rng = np.random.default_rng(2)
    hot = np.concatenate([rng.lognormal(np.log(1500), 0.25, 700),
                          rng.lognormal(np.log(200), 0.3, 300)])
    cold = rng.lognormal(np.log(200), 0.3, 1000)

    def _rows(_ctx, well):
        values = hot if well == wells[0] else cold
        return pd.DataFrame({"area_px": [100] * len(values),
                             "Included": [1] * len(values),
                             f"{channel}_mean_intensity": values})

    monkeypatch.setattr(ctl, "get_rows", _rows)
    rt.opt_well_a_combo.setCurrentIndex(0)
    rt.opt_well_b_combo.setCurrentIndex(1)
    rt.opt_min_edit.setText("0")
    rt.opt_max_edit.setText("5000")

    with caplog.at_level(logging.ERROR):
        ctx.widgets.get("_cell_gating_opt_btn").click()

    failures = [r.getMessage() for r in caplog.records
                if "optimizer curve popup failed" in r.getMessage()]
    assert not failures, failures
    popup = ctx.runtime.cell_gating.opt_curve_popup
    assert popup is not None, "the validation graph was never opened"
    # And it drew the objective that actually ran, not the other one.
    labels = [ln.get_label() for ln in popup._fig.axes[0].lines]
    assert "separation, 95% lower bound" in labels
