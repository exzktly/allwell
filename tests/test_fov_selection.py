"""The Cell Gating tab's FOV selection — the ``FOV_Pass`` inclusion layer.

The user picks which fields of view count, and every plot, statistic and export
then sees only those fields, because the selection is a verdict column the
inclusion chokepoint (``df_included_mask``) ANDs with the others. These tests
hold the layer (``review/data/fov_selection.py``), its persistence in the
``cell_gating`` block of ``pipeline_info.json``, the Cell Gating CDF / optimizer
population, and — through a production ``ViewerContext`` — the picker button
itself, so a control that is offered but does nothing cannot pass here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.dataset_state import DatasetState
from review.data import fov_selection as fs
from review.data.data_loading import VERDICT_LAYERS, df_included_mask


def _ctx(tmp_path, *, selection=None, integrity=None):
    ds = DatasetState()
    ds.begin_load(tmp_path)
    ds.fov_integrity.update(integrity or {})
    cg = CellGatingState(fov_selection=selection)
    return SimpleNamespace(state=SimpleNamespace(dataset=ds, cell_gating=cg))


def _frame(fovs):
    return pd.DataFrame({"fov": list(fovs), "timepoint": "0", "Included": 1,
                         "nucleus_id": list(range(len(fovs)))})


# ── the layer ───────────────────────────────────────────────────────────────

def test_cells_outside_the_selected_fields_leave_the_chokepoint(tmp_path):
    ctx = _ctx(tmp_path, selection={"1", "3"})
    ctx.state.dataset.cache["B02"] = _frame(["1", "2", "3", "2"])
    assert fs.apply_fov_selection_to_cache(ctx) == []
    df = ctx.state.dataset.cache["B02"]
    assert df[fs.FOV_PASS_COLUMN].tolist() == [1, 0, 1, 0]
    assert df_included_mask(df).tolist() == [True, False, True, False]


def test_fields_match_by_number_whatever_the_spelling(tmp_path):
    """``pipeline_info`` says ``"1"``, a float CSV column reads ``1.0``, a
    prefixed schema writes ``"f01"``: all are field 1, and field 10 is not."""
    ctx = _ctx(tmp_path, selection={"1"})
    ctx.state.dataset.cache["A01"] = _frame(["1.0", "f01", "01", "10", "2"])
    fs.apply_fov_selection_to_cache(ctx)
    assert ctx.state.dataset.cache["A01"][fs.FOV_PASS_COLUMN].tolist() == [1, 1, 1, 0, 0]


def test_clearing_the_selection_removes_the_column(tmp_path):
    ctx = _ctx(tmp_path, selection={"1"})
    ctx.state.dataset.cache["B02"] = _frame(["1", "2"])
    fs.apply_fov_selection_to_cache(ctx)
    ctx.state.cell_gating.fov_selection = None
    fs.apply_fov_selection_to_cache(ctx)
    assert fs.FOV_PASS_COLUMN not in ctx.state.dataset.cache["B02"].columns


def test_an_empty_selection_keeps_no_cell(tmp_path):
    """"None" in the picker is a real choice, not "no selection"."""
    ctx = _ctx(tmp_path, selection=set())
    ctx.state.dataset.cache["B02"] = _frame(["1", "2"])
    fs.apply_fov_selection_to_cache(ctx)
    assert not df_included_mask(ctx.state.dataset.cache["B02"]).any()


def test_a_well_with_untrusted_fov_labels_is_not_filtered(tmp_path):
    ctx = _ctx(tmp_path, selection={"1"}, integrity={"B02": False, "A01": True})
    ctx.state.dataset.cache.update({"B02": _frame(["1", "2"]), "A01": _frame(["1", "2"])})
    fs.apply_fov_selection_to_cache(ctx)
    assert fs.FOV_PASS_COLUMN not in ctx.state.dataset.cache["B02"].columns
    assert ctx.state.dataset.cache["A01"][fs.FOV_PASS_COLUMN].tolist() == [1, 0]


def test_the_layer_is_one_the_chokepoint_ands():
    assert fs.FOV_PASS_COLUMN in VERDICT_LAYERS


def test_the_projector_is_registered_whatever_the_profile():
    from review.data.cache_projectors import iter_cache_projectors
    from review.features.registry import build_feature_registry
    build_feature_registry()
    names = [p.__name__ for p in iter_cache_projectors()]
    assert "apply_fov_selection_to_cache" in names


def test_a_selection_of_every_field_is_stored_as_no_selection():
    assert fs.normalise_selection({"1", "2", "3"}, ["1", "2", "3"]) is None
    assert fs.normalise_selection(["1", "3"], ["1", "2", "3"]) == {"1", "3"}
    assert fs.normalise_selection(set(), ["1", "2"]) == set()
    assert fs.normalise_selection(None, ["1"]) is None


def test_the_button_and_status_say_what_is_kept():
    assert fs.format_fov_list(["5", "1", "3", "2", "10"]) == "1–3, 5, 10"
    assert fs.fov_button_text(None) == "All FOVs"
    assert fs.fov_button_text(set()) == "No FOVs"
    assert fs.fov_button_text({"2", "1"}) == "FOVs 1, 2"
    assert fs.fov_selection_status(None, ["1", "2"], []) == ""
    status = fs.fov_selection_status({"1", "9"}, ["1", "2", "3"], ["C03"])
    assert "Using 1 of 3 fields of view" in status
    assert "Not in this dataset: 9." in status
    assert "Not applied to 1 well(s)" in status and "C03" in status


# ── the Cell Gating CDFs and optimizer ──────────────────────────────────────

def test_the_cdf_reads_the_first_field_the_selection_keeps():
    """Field 1 is excluded, so "first frame of first FOV" must be field 2 —
    not field 1's cells, and not nothing."""
    from review.features.home.cell_gating.gating_compute import (
        _cell_gating_first_frame_rows, _cell_gating_valid_mask,
    )

    df = pd.DataFrame({"fov": ["1", "1", "2", "2"], "timepoint": "0",
                       "timepoint_hours": 0.0, "area_px": [10, 11, 20, 21],
                       "FOV_Pass": [0, 0, 1, 1]})
    rows, desc = _cell_gating_first_frame_rows(df)
    assert rows["area_px"].tolist() == [20, 21]
    assert desc.startswith("fov=2")
    assert _cell_gating_valid_mask(df).tolist() == [False, False, True, True]


# ── persistence ─────────────────────────────────────────────────────────────

def test_the_block_carries_the_selection_only_when_set():
    from review.domain.gating_state import build_gating_block

    assert "fov_selection" not in build_gating_block({}, {})
    assert build_gating_block({}, {}, fov_selection=["10", "2", "1"]) == {
        "fov_selection": ["1", "2", "10"]}
    assert build_gating_block({}, {}, fov_selection=[]) == {"fov_selection": []}


def test_the_block_round_trips_onto_the_slice_and_a_restore_clears_it():
    from review.persistence.cell_gating import _write_gating_block_to_slice

    cg = CellGatingState()
    assert _write_gating_block_to_slice(cg, {"fov_selection": ["1", "3"]}) is True
    assert cg.fov_selection == {"1", "3"}
    _write_gating_block_to_slice(cg, {"fluor_gates": {"gfp": 5.0}})
    assert cg.fov_selection is None, "a block without a selection means every field"


def test_auto_threshold_keeps_the_saved_selection(tmp_path, monkeypatch):
    """That pass rebuilds the block from the keys it names; one it forgets is
    deleted from disk the moment the user clicks Auto-threshold."""
    from review.data import auto_threshold

    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"fluor_tokens": ["gfp"],
                                "cell_gating": {"fov_selection": ["2"]}}))
    monkeypatch.setattr(auto_threshold, "compute_auto_thresholds",
                        lambda *_a, **_k: {"gfp": 123.0})
    auto_threshold.apply_auto_thresholds_to_pipeline_info(tmp_path, fluor_channels=["gfp"])
    block = json.loads(info.read_text())["cell_gating"]
    assert block["thresh_frac_on"] == {"gfp": 123.0}
    assert block["fov_selection"] == ["2"]


# ── the picker, driven through a production context ─────────────────────────

@pytest.mark.qt
def test_unticking_a_field_in_the_picker_removes_its_cells_everywhere(
        qtbot, tmp_path, monkeypatch):
    """Press the real button, untick field 2 in the real menu, and the cells of
    field 2 are gone from the chokepoint, the button says so, and the choice is
    on disk for the next session."""
    from PySide6.QtWidgets import QCheckBox, QWidget

    from review.data.data_loading import get_rows
    from review.features.home.cell_gating.tab_view import build_cell_gating_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset
    from widgets import menu_exec

    dataset = build_mini_dataset(tmp_path / "multifov", n_fovs=3)
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, dataset)
    parent = QWidget()
    qtbot.addWidget(parent)
    build_cell_gating_tab(ctx, parent)

    btn = ctx.widgets.get("_cell_gating_fov_btn")
    assert btn.text() == "All FOVs"
    offered: list[str] = []

    def _untick_field_2(menu, _pos):
        for box in menu.findChildren(QCheckBox):
            offered.append(box.text())
            if box.text() == "FOV 2":
                box.setChecked(False)
        return None

    monkeypatch.setattr(menu_exec, "exec_menu", _untick_field_2)
    btn.click()

    assert offered == ["FOV 1", "FOV 2", "FOV 3"]
    assert ctx.state.cell_gating.fov_selection == {"1", "3"}
    assert btn.text() == "FOVs 1, 3"
    well = sorted(ctx.state.dataset.well_paths)[0]
    df = get_rows(ctx, well)
    kept = df.loc[df_included_mask(df), "fov"].astype(str)
    assert set(kept) == {"1", "3"}
    assert (df["fov"].astype(str) == "2").any(), "the fixture must hold field 2"
    saved = json.loads((dataset / "pipeline_info.json").read_text())
    assert saved["cell_gating"]["fov_selection"] == ["1", "3"]

    # Ticking it back is every field again: no column, nothing on disk.
    def _tick_all(menu, _pos):
        for box in menu.findChildren(QCheckBox):
            box.setChecked(True)
        return None

    monkeypatch.setattr(menu_exec, "exec_menu", _tick_all)
    btn.click()
    assert ctx.state.cell_gating.fov_selection is None
    assert fs.FOV_PASS_COLUMN not in get_rows(ctx, well).columns
    assert "cell_gating" not in json.loads((dataset / "pipeline_info.json").read_text())
