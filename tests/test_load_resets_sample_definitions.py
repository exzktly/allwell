"""A dataset swap must clear the previous dataset's sample-definition state
(well labels, replicate sets / groups, notes, solo wells, ratios, gate values)
and decoded-image caches — else, because the per-dataset loaders early-return /
merge additively when the new dataset has no sidecar block, the old dataset's
values silently leak into the new one (the reported "well labels persist" bug).

Same X1 class as ``test_load_resets_gating_state``. The fast unit tests drive the
reset helpers directly; the ``viewer_app`` end-to-end tests (auto-marked ``slow``)
drive the real load path.
"""
from __future__ import annotations

import types

from well_viewer.core.selection_state import SelectionState
from well_viewer.core.load_controller import _reset_sample_definitions_state


class _FakeNotesEdit:
    def __init__(self) -> None:
        self.cleared = False

    def blockSignals(self, _b: bool) -> bool:
        return False

    def clear(self) -> None:
        self.cleared = True


def _ctx_with_selection(sel: SelectionState, notes_edit=None):
    widgets = types.SimpleNamespace(
        get_or_none=lambda name: notes_edit if name == "_notes_edit" else None
    )
    return types.SimpleNamespace(
        state=types.SimpleNamespace(selection=sel), widgets=widgets
    )


def test_reset_sample_definitions_clears_slice_and_syncs_notes():
    sel = SelectionState()
    sel.selections = [{"id": "s1", "name": "R", "wells": ["A01"]}]
    sel.current_selection_id = "s1"
    sel.notes_text = "old notes"
    sel.active_solo_wells = {"A01", "B02"}
    edit = _FakeNotesEdit()

    _reset_sample_definitions_state(_ctx_with_selection(sel, edit))

    assert sel.selections == []
    assert sel.current_selection_id is None
    assert sel.notes_text == ""
    assert sel.active_solo_wells == set()
    assert edit.cleared is True  # the Notes editor was cleared too


def test_reset_sample_definitions_no_notes_widget_is_safe():
    # Before the Notes tab is built, get_or_none returns None — must not raise.
    sel = SelectionState()
    sel.selections = [{"id": "s1"}]
    sel.notes_text = "x"
    _reset_sample_definitions_state(_ctx_with_selection(sel, None))
    assert sel.selections == [] and sel.notes_text == ""


def test_preview_and_review_image_clear_caches():
    from well_viewer.features.image_browser.review_image.state import (
        PreviewState,
        ReviewImageState,
    )

    pv = PreviewState(
        fluor={"k": 1}, overlay={"k": 1}, mask={"k": 1}, tophat_fluor={"k": 1}
    )
    pv.clear_caches()
    assert pv.fluor == {} and pv.overlay == {} and pv.mask == {}
    assert pv.tophat_fluor == {}

    ri = ReviewImageState(
        frame_cache={"k": 1},
        threshold_map_cache={"k": 1},
        flatfield_field_cache={"k": 1},
    )
    ri.include_cache["k"] = 1
    ri.clear_caches()
    assert ri.frame_cache is None
    assert ri.include_cache == {}
    assert ri.threshold_map_cache == {}
    assert ri.flatfield_field_cache == {}


# ── End-to-end: real load path (slow — auto-marked via the viewer_app fixture) ──


def _poison_dataset_state(app) -> None:
    """Set every leaky field to a non-default value, as if the just-loaded
    dataset had defined them."""
    from well_viewer.domain.ratio_models import RatioMetric

    st = app._app_state
    st.dataset.well_labels["A01"] = "CTRL"
    st.selection.selections = [{"id": "s1", "name": "R", "wells": ["A01"]}]
    st.selection.current_selection_id = "s1"
    st.selection.notes_text = "old notes"
    st.selection.active_solo_wells = {"A01"}
    st.cell_gating.fluor_gates["gfp"] = 10.0
    st.cell_gating.thresh_frac_on["gfp"] = 80.0
    st.channel.set_ratio_metrics([
        RatioMetric(
            name="R", numerator_channel="gfp", numerator_metric="mean_intensity",
            denominator_channel="mcherry", denominator_metric="mean_intensity",
        )
    ])


def _assert_all_cleared(app) -> None:
    st = app._app_state
    assert st.dataset.well_labels == {}
    assert st.selection.selections == []
    assert st.selection.current_selection_id is None
    assert st.selection.notes_text == ""
    assert st.selection.active_solo_wells == set()
    assert st.cell_gating.fluor_gates == {}
    assert st.cell_gating.thresh_frac_on == {}
    assert st.channel.ratio_metrics == []


def test_dataset_swap_clears_in_memory_carryover(viewer_app, tmp_path):
    """Load A, poison every leaky field, load a fresh B with no sidecar blocks —
    all fields reset (the loaders would otherwise early-return and keep them)."""
    from tests.fixtures.mini_dataset import build_mini_dataset

    app = viewer_app
    a = build_mini_dataset(tmp_path / "a")
    b = build_mini_dataset(tmp_path / "b")
    app._load_path(a)
    _poison_dataset_state(app)
    app._load_path(b)
    _assert_all_cleared(app)


def test_well_labels_hydrate_then_do_not_leak(viewer_app, tmp_path):
    """The genuine early-return path: A has a saved sample_definitions block with
    a label; B has none. A's label hydrates, then must be gone after loading B."""
    from tests.fixtures.mini_dataset import build_mini_dataset
    from well_viewer.domain.sample_definitions import (
        build_sample_definitions_v2,
        save_to_pipeline_info,
    )

    app = viewer_app
    a = build_mini_dataset(tmp_path / "a")
    b = build_mini_dataset(tmp_path / "b")
    block = build_sample_definitions_v2(
        {"A01": "CTRL"}, [], None, notes="a-notes", plot_groups=[]
    )
    save_to_pipeline_info(a, block)

    app._load_path(a)
    assert app._app_state.dataset.well_labels.get("A01") == "CTRL"  # hydrated
    assert app._app_state.selection.notes_text == "a-notes"

    app._load_path(b)  # B has no sample_definitions block
    assert app._app_state.dataset.well_labels == {}
    assert app._app_state.selection.notes_text == ""
