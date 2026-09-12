"""Characterization for the ``ctx.app`` decomposition of the Data-group tab
builders and the sample-definitions load path.

Guards that:
  * the feature-layer registry builds both surfaces without naming the
    god-object — Review CSV goes direct to ``build_review_csv_tab(ctx, ...)``,
    and the sample-definition editors are tabs of Home, built by Home's own
    controller through ``core.lazy`` proxies rather than through a core
    god-object seam;
  * Home's Notes editor is registered on ``ctx.widgets`` so the ctx-native
    loader can write migrated notes back through it; and
  * ``persistence.sample_definitions._apply_unified_state`` — ctx-native —
    applies a saved block onto the typed ``ctx.state.selection`` slice, lands the
    notes text in ``SelectionState.notes_text``, and mirrors it into the widget.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_data_tabs_build_and_register_notes_widget(loaded_app):
    """``loaded_app`` drains every non-lazy centre builder, so both surfaces
    build through the ctx-native registry paths."""
    app = loaded_app
    ctx = app._ctx

    # Review CSV built via the direct ctx-native builder (registers its widgets).
    assert ctx.widgets.has("_review_well_lbl"), "Review CSV tab did not build"
    assert getattr(app, "_review_well_lbl", None) is ctx.widgets.get("_review_well_lbl")

    # The definition editors built as tabs of Home, off its own drain.
    assert ctx.runtime.home.subtabs is not None
    assert ctx.widgets.has("_rep_list")

    # The Notes editor is registered so the ctx-native loader can reach it.
    assert ctx.widgets.has("_home_notes_edit"), "_home_notes_edit not registered"


def test_load_applies_block_ctx_native(loaded_app, mini_dataset):
    """The ctx-native loader applies a saved block onto typed state + the widget."""
    from review.domain.sample_definitions import (
        build_sample_definitions_v2,
        save_to_pipeline_info as _write_block,
    )
    from review.persistence.sample_definitions import load_from_pipeline_info

    app = loaded_app
    ctx = app._ctx
    notes_edit = ctx.widgets.get("_home_notes_edit")

    wells = list(ctx.state.dataset.well_paths.keys())[:2]
    selections = [{"id": "sel-d6", "name": "D6Probe", "wells": wells}]
    block = build_sample_definitions_v2(
        {}, selections, "sel-d6", notes="hello from D6",
    )
    _write_block(ctx.state.dataset.data_dir, block)

    # Wipe the in-memory state so the load has to repopulate it.
    ctx.state.selection.selections = []
    ctx.state.selection.current_selection_id = None
    ctx.state.selection.notes_text = ""
    notes_edit.setPlainText("")

    assert load_from_pipeline_info(ctx) is True

    names = {s.get("name") for s in ctx.state.selection.selections}
    assert "D6Probe" in names, "selections were not applied onto ctx.state"
    assert ctx.state.selection.notes_text == "hello from D6"
    # The registered widget is mirrored from the loaded block.
    assert notes_edit.toPlainText() == "hello from D6"
