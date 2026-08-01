"""Every FOV control folds its label into the control itself.

Companion to ``test_timepoint_control_folding.py`` — the same
``widgets.labeled_combo.LabeledComboBox`` fold applied to the three FOV
combos across Review (Segmentation preview, smFISH, Review CSV), painting
their own "FOV: F03" prefix instead of sitting beside a separate "FOV"
``QLabel``.

Regression: before this, every one of these paired a plain ``QLabel``("FOV")
with its combo through ``ControlBand.add_scope("FOV", combo)`` — two widgets
per control, the pattern the Channel/Property/Timepoint folds already moved
away from.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402


def test_fov_combos_use_labeled_combo_with_no_sibling_label(viewer_app, qtbot):
    from widgets.labeled_combo import LabeledComboBox

    app = viewer_app
    checks = [
        ("well_viewer.features.smfish.tab_view", "build_smfish_tab", "_smfish_fov_cb"),
        ("well_viewer.features.review_csv.tab_view", "build_review_csv_tab", "_review_fov_cb"),
        ("well_viewer.features.preview.panel_view", "build_review_image_panel", "_review_image_fov_menu"),
    ]
    import importlib
    for module_name, fn_name, attr in checks:
        mod = importlib.import_module(module_name)
        builder = getattr(mod, fn_name)
        parent = QWidget()
        qtbot.addWidget(parent)
        builder(app._ctx, parent)
        cb = app._ctx.widgets.get_or_none(attr)
        assert cb is not None, f"{attr} was not registered by {fn_name}"
        assert isinstance(cb, LabeledComboBox), f"{attr} is a {type(cb)}, not LabeledComboBox"
        assert cb.label() == "FOV"
        found = {lbl.text() for lbl in parent.findChildren(QLabel)}
        assert "FOV" not in found, "found a stranded 'FOV' caption label beside the folded combo"


def test_review_csv_fov_combo_has_no_fixed_width(viewer_app, qtbot):
    """Regression: ``_review_fov_cb`` carried a stale ``setFixedWidth(120)``
    from before it was self-sizing — that floor would fight
    LabeledComboBox's shrink-to-painted-content sizing the same way the
    Channel/Property/Timepoint combos' old floors did."""
    from well_viewer.features.review_csv.tab_view import build_review_csv_tab

    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_review_csv_tab(app._ctx, parent)
    cb = app._ctx.widgets.get_or_none("_review_fov_cb")
    assert cb is not None
    assert cb.minimumWidth() == 0
    assert cb.maximumWidth() >= 16777215  # Qt's default QWIDGETSIZE_MAX
