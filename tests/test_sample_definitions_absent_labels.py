"""A subset load must not erase the well labels of the wells it cannot see.

``load_from_pipeline_info`` hydrates ``dataset.well_labels`` only for tokens in
``dataset.well_paths`` — right, because a label for a well that is not loaded
has nothing to attach to. The autosave then rebuilt the whole
``sample_definitions`` block from that pruned map, so opening a subset of a
plate and touching one label rewrote pipeline_info.json without every other
well's label. The file was the only place those labels existed.

The sequence driven here is the app's: hydrate from the sidecar, edit a label,
autosave, then read the file back. The assertion is on the bytes on disk, not
on the dict the writer was handed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from review.core.widget_registry import WidgetRegistry
from review.domain.sample_definitions import build_sample_definitions_v2
from review.persistence import sample_definitions


FULL_PLATE_LABELS = {
    "A01": "control",
    "A02": "drug",
    "A03": "drug 10uM",
    "A04": "drug 100uM",
}


@pytest.fixture()
def plate(tmp_path, monkeypatch):
    """A pipeline_info.json holding labels for four wells, and a context whose
    load sees only the first two — the subset case."""
    block = build_sample_definitions_v2(
        dict(FULL_PLATE_LABELS), [], None, notes="", plot_groups=[],
    )
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"fluor_tokens": ["gfp"],
                                "sample_definitions": block}, indent=2))

    dataset = SimpleNamespace(
        data_dir=tmp_path, well_paths={"A01": 1, "A02": 2},
        well_labels={}, tok_to_label={}, cache={},
    )
    selection = SimpleNamespace(
        selections=[], current_selection_id=None, plot_groups=[],
        notes_text="", v2_writes_disabled=False,
    )
    app = SimpleNamespace(
        _app_state=SimpleNamespace(dataset=dataset, selection=selection),
    )
    ctx = SimpleNamespace(
        window=app,
        widgets=WidgetRegistry(SimpleNamespace()),
        notify=SimpleNamespace(set_status=lambda message: None),
        state=SimpleNamespace(dataset=dataset, selection=selection),
    )
    app._ctx = ctx

    # The stats-cache invalidation reaches the plot toggles and the review-image
    # slice; neither is part of the label round-trip.
    import review.core.selection_controller as selection_controller
    monkeypatch.setattr(
        selection_controller, "invalidate_stats_cache", lambda c: None
    )
    return ctx, info


def _labels_on_disk(info: Path) -> dict:
    return json.loads(info.read_text())["sample_definitions"]["well_labels"]


def test_a_subset_load_that_autosaves_keeps_the_other_wells_labels(plate):
    ctx, info = plate

    sample_definitions.load_from_pipeline_info(ctx)
    assert ctx.state.dataset.well_labels == {"A01": "control", "A02": "drug"}, (
        "setup: only the loaded wells hydrate"
    )

    ctx.state.dataset.well_labels["A01"] = "vehicle"
    sample_definitions.autosave_to_pipeline_info(ctx)

    assert _labels_on_disk(info) == {
        "A01": "vehicle",          # the edit landed
        "A02": "drug",
        "A03": "drug 10uM",        # never loaded, must survive
        "A04": "drug 100uM",
    }, f"labels outside the subset were erased: {_labels_on_disk(info)}"


def test_clearing_a_loaded_wells_label_still_deletes_it(plate):
    """The merge must not resurrect a label the user removed."""
    ctx, info = plate
    sample_definitions.load_from_pipeline_info(ctx)

    del ctx.state.dataset.well_labels["A02"]
    sample_definitions.autosave_to_pipeline_info(ctx)

    assert "A02" not in _labels_on_disk(info), (
        "a label deleted for a loaded well came back from the file: "
        f"{_labels_on_disk(info)}"
    )
    assert _labels_on_disk(info)["A03"] == "drug 10uM"


def test_the_reset_scope_forgets_absent_labels_too(plate):
    """The "Sample definitions" reset means every label, including the ones the
    current load cannot see — otherwise the merge would undo the reset."""
    ctx, info = plate
    sample_definitions.load_from_pipeline_info(ctx)

    ctx.state.dataset.well_labels.clear()
    sample_definitions.autosave_forgetting_absent_labels(ctx)

    assert _labels_on_disk(info) == {}, (
        f"the reset left labels behind: {_labels_on_disk(info)}"
    )
