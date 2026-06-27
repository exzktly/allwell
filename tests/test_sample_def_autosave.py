"""Sample definitions should auto-persist on edit (debounced), like notes /
ratios / view-state — not only when the Save button is clicked.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.persistence import sample_definitions as sd  # noqa: E402
from well_viewer.persistence.registry import build_persistence_registry  # noqa: E402


def test_sample_definitions_registered_for_autosave():
    app = SimpleNamespace()
    reg = build_persistence_registry(app)
    # The domain is now on the shared debounced-save registry.
    assert "sample_definitions" in reg._save
    assert reg._save["sample_definitions"] is sd.autosave_to_pipeline_info


def test_autosave_noops_without_data_dir():
    # No data dir → silent no-op, no dialog, no exception.
    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=SimpleNamespace(data_dir=None, well_labels={}),
            selection=SimpleNamespace(selections=[], current_selection_id=None,
                                      v2_writes_disabled=False),
        )
    )
    sd.autosave_to_pipeline_info(app)  # must not raise


def _read_block(data_dir):
    info = json.loads((data_dir / "pipeline_info.json").read_text())
    return info.get("sample_definitions")


def test_edit_autosaves_without_clicking_save(loaded_app, mini_dataset):
    app = loaded_app
    # A structural edit (add a selection) routes through rebuild_all, which now
    # schedules a debounced sample-definitions write.
    sid = app._sel_add(name="AutoSaveProbe", wells=[], source="user")
    assert sid is not None
    assert "sample_definitions" in app._persistence._pending  # queued, not yet written

    # Flush the debounce immediately (the QTimer would do this after 500 ms).
    app._persistence.save("sample_definitions")

    block = _read_block(mini_dataset)
    assert block is not None, "sample_definitions block should be written on edit"
    names = {s.get("name") for s in (block.get("selections") or [])}
    assert "AutoSaveProbe" in names


def test_label_edit_schedules_autosave(loaded_app):
    app = loaded_app
    app._persistence._pending.discard("sample_definitions")
    # Mutating a well label through the affix helper should queue a save.
    app._app_state.selection.selected_wells = set(
        list(app._app_state.dataset.well_paths.keys())[:1]
    )
    from well_viewer.core.selection_controller import labels_apply_affix
    labels_apply_affix(app, where="prefix")
    # Either it queued a save, or (no wells selected) it didn't crash; assert the
    # common case where a well was available.
    if app._app_state.dataset.well_paths:
        assert "sample_definitions" in app._persistence._pending


def test_cell_gating_edit_persists_without_save_button(loaded_app, mini_dataset):
    """Cell-gating thresholds must autosave on edit — there is no Save button.

    Editing a ThreshFracOn value and firing its change handler writes the
    ``cell_gating`` block straight to pipeline_info.json (via
    ``_cell_gating_persist_params``), so removing the explicit Save button does
    not strand gating state. Guards the autosave coverage the Save removal
    relies on.
    """
    from well_viewer.domain.gating_state import read_gating_params
    from well_viewer.features.grouping.cell_gating_tab_view import (
        build_cell_gating_subtab,
        cell_gating_on_threshold_frac_on_change,
    )

    app = loaded_app
    if not hasattr(app, "_cell_gating_thresh_frac_edits"):
        build_cell_gating_subtab(app)
    edits = getattr(app, "_cell_gating_thresh_frac_edits", {}) or {}
    if not edits:
        pytest.skip("mini dataset exposes no gateable fluor channel")

    channel = next(iter(edits))
    edits[channel].setText("42.0")
    cell_gating_on_threshold_frac_on_change(app)

    block = read_gating_params(mini_dataset)
    assert block is not None, "cell_gating block should be written on edit"
    assert float(block.get("thresh_frac_on", {}).get(channel)) == 42.0
