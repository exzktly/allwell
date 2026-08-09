"""Reset scopes against a real loaded app — the parts that only disk can prove.

``tests/test_reset_scopes.py`` covers the scope table and the dispatch. What it
cannot cover is the reason this change exists: two of the old reset commands
cleared live state and left the value sitting in a file, so the reload put it
straight back. That failure is invisible to any test that stops at the in-memory
value, which is exactly why it survived — so each test here reads the file, or
reloads and reads the state again.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.qt

pytest.importorskip("pandas")


def test_curation_scope_clears_the_cell_overrides_file(loaded_app_shape):
    """Manual curation lives in its own ``cell_overrides.json``.

    The trash-can's dialog listed "cell overrides" among what it deleted, but it
    only ever unlinked ``persistence.json`` — a different file — so every manual
    include/exclude survived a reset that said it had removed them.
    """
    from well_viewer.features.crop.controller import set_crop_region
    from well_viewer.persistence.reset_scopes import scope_by_key

    app = loaded_app_shape
    ctx = app._ctx
    well = sorted(ctx.state.dataset.well_paths)[0]

    ctx.runtime.review.included_overrides[(well, "1", "0h", "1")] = "0"
    set_crop_region(ctx, (0, 0, 8, 12))
    ctx.persistence.save("cell_overrides")

    overrides_path = ctx.state.dataset.data_dir / "cell_overrides.json"
    assert json.loads(overrides_path.read_text())["overrides"], "setup failed"

    scope_by_key("curation").clear(ctx)

    assert not ctx.runtime.review.included_overrides
    assert json.loads(overrides_path.read_text())["overrides"] == [], (
        "the override survived on disk; a reload would restore it"
    )


def test_sample_definitions_scope_clears_both_copies_of_plot_groups(loaded_app_shape):
    """Plot groups are stored twice, and clearing one copy clears nothing.

    ``plot_groups.load_from_data_dir`` treats an absent ``persistence.json``
    section as "nothing to say" — deliberately, so a folder shared without the
    sidecar keeps its groups — and the ``sample_definitions`` block in
    ``pipeline_info.json`` carries a second copy that hydrates first. Deleting
    persistence.json therefore left the groups fully intact.
    """
    from well_viewer.persistence.reset_scopes import scope_by_key

    app = loaded_app_shape
    ctx = app._ctx
    wells = sorted(ctx.state.dataset.well_paths)[:2]

    ctx.state.selection.plot_groups = [{"name": "Plate 1", "wells": list(wells)}]
    ctx.persistence.save("plot_groups")
    ctx.persistence.save("sample_definitions")

    info = json.loads((ctx.state.dataset.data_dir / "pipeline_info.json").read_text())
    assert info.get("sample_definitions", {}).get("plot_groups"), "setup failed"

    scope_by_key("sample_definitions").clear(ctx)

    info = json.loads((ctx.state.dataset.data_dir / "pipeline_info.json").read_text())
    assert not info.get("sample_definitions", {}).get("plot_groups"), (
        "the pipeline_info copy survived; the reload would restore the groups"
    )
    # And the state a reload lands on is genuinely empty.
    from well_viewer.persistence import plot_groups
    plot_groups.load_from_data_dir(ctx)
    assert ctx.state.selection.plot_groups == []


def test_view_reset_leaves_screen_and_disk_agreeing(loaded_app_shape):
    """Everything the reset drops from disk is also reset live, and vice versa.

    The old reset cleared selected wells, the fold-change scopes and three tabs'
    combos, then deleted the entire ``view_state`` section — which also carried
    the time-axis scale, legend visibility, draw order and error-band mode.
    Those stayed on screen and vanished from disk. The section is now re-saved
    rather than deleted, so the two cannot disagree.
    """
    from well_viewer.persistence import _doc, view_state

    app = loaded_app_shape
    ctx = app._ctx

    ctx.state.plot.time_scale = 4.0
    ctx.state.plot.use_sem = False
    ctx.state.plot.show_significance = True
    ctx.state.plot.line_order_wells = sorted(ctx.state.dataset.well_paths)[:2]
    ctx.state.plot.fc_vs_control_on = True
    view_state.save_to_data_dir(ctx)

    view_state.reset(ctx)

    assert ctx.state.plot.time_scale == 1.0
    assert ctx.state.plot.use_sem is True
    assert ctx.state.plot.show_significance is False
    assert ctx.state.plot.line_order_wells == []
    assert ctx.state.plot.fc_vs_control_on is False

    saved = _doc.get_section(ctx, "view_state")
    assert saved is not None, "the section must be re-saved, not deleted"
    assert saved["time_scale"] == 1.0
    assert saved["use_sem"] is True
    assert saved["show_significance"] is False
    assert saved["line_order_wells"] == []
    assert saved["fold_change"]["vs_control_on"] is False
    assert saved["selected_wells"] == []


def test_gating_scope_returns_qc_to_the_run_settings(loaded_app_shape):
    """QC resets to ``None`` so the accessor re-seeds it from the run's own
    ``pipeline_info`` thresholds — blank thresholds would be a state this
    dataset was never in."""
    from well_viewer.features.qc.controller import get_qc_params
    from well_viewer.persistence import _doc
    from well_viewer.persistence.qc_params import _SECTION
    from well_viewer.persistence.reset_scopes import scope_by_key

    app = loaded_app_shape
    ctx = app._ctx

    edited = get_qc_params(ctx).with_updates(min_area=999.0, enabled=True)
    ctx.state.qc.params = edited
    ctx.persistence.save("qc_params")
    assert _doc.get_section(ctx, _SECTION) is not None, "setup failed"

    scope_by_key("gating").clear(ctx)

    assert _doc.get_section(ctx, _SECTION) is None
    assert get_qc_params(ctx).min_area != 999.0
