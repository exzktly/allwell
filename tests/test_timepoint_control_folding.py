"""Every Timepoint control folds its label into the control itself.

Companion to ``test_thresh_frac_quick_edit.py::test_channel_and_property_combos_fold_their_own_label``,
which covers Plotting's page-level Channel/Property combos — this covers every
per-tab Timepoint control across Review, both the plain-combo kind
(``widgets.labeled_combo.LabeledComboBox``, painting its own "Timepoint: …"
prefix — Heat Map included, unified from a ``StyledSlider`` to a combo like
every sibling tab) and the checkbox-menu multi-select kind (Bar Plots /
Scatter Cells / Scatter Agg, whose picker button now carries its own
"Timepoints: … selected" readout via ``core.ui_helpers.multiselect_button_text``
instead of sitting beside a separate count label).

Regression: before this, every one of these paired a plain ``QLabel``
("Timepoint" / "Timepoints") with its combo, slider, or button through
``ControlBand.add_scope(label, widget)`` — two widgets per control, the
pattern the Channel/Property fold already moved away from.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ── multiselect_button_text: Qt-free, pure ───────────────────────────────────

def test_multiselect_button_text_all_selected():
    from review.core.ui_helpers import multiselect_button_text
    assert multiselect_button_text("Timepoints", {"1": True, "2": True}) == \
        "Timepoints: All 2 selected"


def test_multiselect_button_text_partial():
    from review.core.ui_helpers import multiselect_button_text
    assert multiselect_button_text("Timepoints", {"1": True, "2": False, "3": True}) == \
        "Timepoints: 2/3 selected"


def test_multiselect_button_text_empty():
    from review.core.ui_helpers import multiselect_button_text
    assert multiselect_button_text("Timepoints", {}) == "Timepoints: 0 selected"


def test_multiselect_button_text_none_selected():
    from review.core.ui_helpers import multiselect_button_text
    assert multiselect_button_text("Timepoints", {"1": False, "2": False}) == \
        "Timepoints: 0/2 selected"


# ── structural: every tab's Timepoint control folds its label ───────────────

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402


def _no_sibling_label(ctrl_widget, forbidden_texts) -> None:
    found = {lbl.text() for lbl in ctrl_widget.findChildren(QLabel)}
    hit = found & set(forbidden_texts)
    assert not hit, f"found stranded caption label(s) {hit} beside a folded control"


def test_combo_based_timepoint_controls_use_labeled_combo(viewer_app, qtbot):
    from widgets.labeled_combo import LabeledComboBox

    app = viewer_app
    checks = [
        ("review.features.stats.tab_view", "build_stats_tab", "_stats_tp_cb"),
        ("review.features.spatial.tab_view", "build_spatial_tab", "_sp_tp_cb"),
        ("review.features.multivariate.tab_view", "build_multivariate_tab", "_mv_tp_cb"),
        ("review.features.distribution.tab_view", "build_distribution_tab", "_distribution_tp_cb"),
        ("review.features.smfish.tab_view", "build_smfish_tab", "_smfish_tp_cb"),
        ("review.features.review_csv.tab_view", "build_review_csv_tab", "_review_tp_cb"),
        ("review.features.preview.panel_view", "build_review_image_panel", "_review_image_tp_menu"),
        ("review.features.heatmap.tab_view", "build_heatmap_tab", "_heatmap_tp_cb"),
    ]
    import importlib
    for module_name, fn_name, attr in checks:
        mod = importlib.import_module(module_name)
        builder = getattr(mod, fn_name)
        parent = QWidget()
        qtbot.addWidget(parent)
        builder(app._ctx, parent)
        if attr == "_heatmap_tp_cb":
            # The builder ends with a queued matplotlib draw_idle; let it
            # settle while the canvas is still alive (see
            # test_heatmap_widget_registry_a8.py) or pytest-qt's post-test
            # processEvents fires it against a deleted C++ object.
            qtbot.wait(1)
        cb = app._ctx.widgets.get_or_none(attr)
        assert cb is not None, f"{attr} was not registered by {fn_name}"
        assert isinstance(cb, LabeledComboBox), f"{attr} is a {type(cb)}, not LabeledComboBox"
        assert cb.label() == "Timepoint"
        forbidden = {"Timepoint", "Timepoints"}
        if attr == "_review_image_tp_menu":
            # The review-image band also hosts the Delete scope popover, whose
            # three CheckableDropdowns each print their own noun. Its Wells and
            # FOVs rows are the same shape as its Timepoints one, so they are
            # named here too — only the Timepoints caption was ever guarded,
            # which is how all three shipped with the word printed twice.
            forbidden |= {"Wells", "FOVs"}
        _no_sibling_label(parent, forbidden)


def test_multiselect_timepoint_buttons_have_no_sibling_label(viewer_app, qtbot):
    from review.features.barplot.tab_view import build_bar_plots_tab
    from review.features.scatter.cells_tab_view import build_scatter_cells_tab
    from review.features.scatter.agg_tab_view import build_scatter_agg_tab

    app = viewer_app
    for builder, btn_attr, label_attr in (
        (build_bar_plots_tab, "_bar_tp_button", "_bar_tp_label"),
        (build_scatter_cells_tab, "_scatter_tp_button", "_scatter_tp_label"),
        (build_scatter_agg_tab, "_scatter_agg_tp_button", "_scatter_agg_tp_label"),
    ):
        parent = QWidget()
        qtbot.addWidget(parent)
        builder(app._ctx, parent)
        btn = app._ctx.widgets.get_or_none(btn_attr)
        assert btn is not None, f"{btn_attr} was not registered"
        assert btn.text().startswith("Timepoints:"), btn.text()
        # The old count label is gone entirely, not just hidden.
        assert app._ctx.widgets.get_or_none(label_attr) is None
        _no_sibling_label(parent, {"Timepoints"})
