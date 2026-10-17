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


def test_a_running_cell_export_notices_the_selection_change(tmp_path, monkeypatch):
    """The export re-checks this signature around every well it slices; a
    selection changed mid-export rewrites ``FOV_Pass`` without bumping
    ``rows_version``, so the signature itself has to carry it."""
    from review.domain import stats_compute
    from review.features.export.service import _dataset_signature

    monkeypatch.setattr(stats_compute, "_gate_cache_sig", lambda _ctx: None)
    ctx = _ctx(tmp_path)
    ctx.state.qc = SimpleNamespace(params=None)
    before = _dataset_signature(ctx)
    ctx.state.cell_gating.fov_selection = {"1"}
    assert _dataset_signature(ctx) != before


# ── review findings on the first version ────────────────────────────────────

def test_labels_carrying_two_numbers_are_never_merged(tmp_path):
    """``parse_fov`` reads a label's trailing digits for sorting, which made
    ``r1c1`` / ``r2c1`` (and ``s1_1`` / ``s2_1``, ``A-1`` / ``B-1``) one field:
    ticking ``r1c1`` alone kept ``r2c1`` too, and was even stored as "all"."""
    ctx = _ctx(tmp_path, selection={"r1c1"})
    ctx.state.dataset.cache["A01"] = _frame(["r1c1", "r2c1", "s1_1", "s2_1", "A-1", "B-1"])
    fs.apply_fov_selection_to_cache(ctx)
    assert ctx.state.dataset.cache["A01"][fs.FOV_PASS_COLUMN].tolist() == [1, 0, 0, 0, 0, 0]
    assert fs.normalise_selection({"r1c1"}, ["r1c1", "r2c1"]) == {"r1c1"}
    # A single number behind a letter prefix still matches by number.
    assert fs.fov_match_key("f01") == fs.fov_match_key("1") == fs.fov_match_key(1.0)
    assert fs.fov_match_key("fov3") == 3.0
    # This decides identity, so a digit from another alphabet is not a 1.
    assert fs.fov_match_key("\u0661") != fs.fov_match_key("1")


def test_the_verdict_decides_each_label_once_not_each_row(tmp_path, monkeypatch):
    """A well holds a few dozen labels and up to millions of cells; the label
    parse ran once per row, on the UI thread, at every load and re-projection."""
    calls = []
    real = fs.fov_match_key
    monkeypatch.setattr(fs, "fov_match_key", lambda v: calls.append(v) or real(v))
    ctx = _ctx(tmp_path, selection={"2"})
    ctx.state.dataset.cache["A01"] = _frame(["1", "2", "3"] * 2000)
    calls.clear()
    fs.apply_fov_selection_to_cache(ctx)
    assert ctx.state.dataset.cache["A01"][fs.FOV_PASS_COLUMN].sum() == 2000
    # One for the selected label, one per distinct row label.
    assert len(calls) <= 4, len(calls)


def test_the_fov_list_is_read_once_per_dataset(tmp_path, monkeypatch):
    """The status line asks on every inclusion-count repaint; without a list in
    ``pipeline_info.json`` each ask was a walk of every well's summary."""
    from review.core.app_runtime import AppRuntime
    from review.data import dataset_dims

    reads = []
    monkeypatch.setattr(dataset_dims, "fov_options",
                        lambda _ctx: reads.append(1) or ["2", "10", "1"])
    ctx = _ctx(tmp_path)
    ctx.runtime = AppRuntime(store={})
    ctx.state.dataset.well_paths["A01"] = tmp_path / "A01.csv"
    assert fs.available_fovs(ctx) == ["1", "2", "10"]
    assert fs.available_fovs(ctx) == ["1", "2", "10"]
    assert len(reads) == 1
    ctx.state.dataset.well_paths["B02"] = tmp_path / "B02.csv"
    fs.available_fovs(ctx)
    assert len(reads) == 2, "a dataset with different wells must be read again"


def _restore_ctx(tmp_path, monkeypatch, selection):
    """A context for the persistence restore path, with the tab-building step
    stubbed (it needs a built Cell Gating tab) and every feature call recorded."""
    from review.persistence import cell_gating as pcg

    monkeypatch.setattr(pcg, "_mirror_gating_to_widgets", lambda *_a, **_k: True)
    ctx = _ctx(tmp_path, selection=selection)
    calls = []
    ctx.features = SimpleNamespace(call=lambda name, *_a: calls.append(name))
    ctx.state.dataset.cache["A01"] = _frame(["1", "2", "3"])
    return ctx, calls


def test_restoring_a_different_selection_re_reads_the_cdfs(tmp_path, monkeypatch):
    """The CDFs are drawn from the first kept field. A saved-plot restore that
    changed the selection left them showing the previous one's field."""
    from review.persistence.cell_gating import restore_gating_block

    ctx, calls = _restore_ctx(tmp_path, monkeypatch, {"3"})
    restore_gating_block(ctx, {})
    assert ctx.state.cell_gating.fov_selection is None
    assert fs.FOV_PASS_COLUMN not in ctx.state.dataset.cache["A01"].columns
    assert calls == ["cell_gating.load_cell_areas"]


def test_restoring_the_same_selection_re_projects_nothing(tmp_path, monkeypatch):
    """A block whose selection is the live one — which is every dataset load
    without a selection — threw away every memoised statistic for nothing."""
    from review.core import selection_controller
    from review.persistence.cell_gating import apply_gating_block, restore_gating_block

    invalidated = []
    monkeypatch.setattr(selection_controller, "invalidate_stats_cache",
                        lambda *_a, **_k: invalidated.append(1))
    ctx, calls = _restore_ctx(tmp_path, monkeypatch, {"3"})
    ctx.state.stats_cache = SimpleNamespace()
    restore_gating_block(ctx, {"fov_selection": ["3"]})
    ctx.state.cell_gating.fov_selection = None
    apply_gating_block(ctx, {"fluor_gates": {"gfp": 5.0}})
    assert invalidated == [] and calls == []
    # …and a real change still does both.
    apply_gating_block(ctx, {"fov_selection": ["2"]})
    assert invalidated == [1] and calls == ["cell_gating.load_cell_areas"]


def test_the_write_path_lives_in_the_controller_and_the_view_builds_only():
    """CLAUDE.md §3: ``*_view.py`` holds Qt builders, ``controller.py`` the
    orchestration. The save / re-project / redraw path sat in the view."""
    import ast

    from review.features.home.cell_gating import controller
    from tests._repo_scan import REPO_ROOT, parse_source

    assert callable(controller.set_fov_selection)
    tree = parse_source(REPO_ROOT / "review/features/home/cell_gating/fov_view.py")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {"set_fov_selection", "save_to_pipeline_info", "redraw_app"} & (names | attrs)
    assert "features" not in attrs, "the view reaches a feature service"
    # And it writes no pixel literal into a layout call (CLAUDE.md §5).
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("setContentsMargins", "setSpacing")):
            literals = [a.value for a in call.args if isinstance(a, ast.Constant)]
            assert all(v == 0 for v in literals), (call.func.attr, literals)


@pytest.mark.qt
def test_closing_the_picker_unchanged_writes_nothing_and_keeps_absent_fields(
        qtbot, tmp_path, monkeypatch):
    """Opening the picker to look and closing it rewrote the saved selection
    from the boxes alone, deleting a selected field the dataset does not have,
    and re-projected every well. Only an edit applies, and an edit keeps it."""
    from PySide6.QtWidgets import QCheckBox, QWidget

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
    # "01" is field 1 spelled as an older run wrote it: a close that rebuilds
    # the selection from the boxes would respell it, so it measures the no-op.
    ctx.state.cell_gating.fov_selection = {"01", "2", "9"}
    info = dataset / "pipeline_info.json"
    before = info.read_text()
    recomputes = []
    real_call = ctx.features.call
    monkeypatch.setattr(ctx.features, "call", lambda name, *a, **k: (
        recomputes.append(name) if name == "qc.recompute_all" else None) or real_call(name, *a, **k))

    monkeypatch.setattr(menu_exec, "exec_menu", lambda _menu, _pos: None)
    ctx.widgets.get("_cell_gating_fov_btn").click()
    assert ctx.state.cell_gating.fov_selection == {"01", "2", "9"}
    assert info.read_text() == before and recomputes == []

    def _untick_2(menu, _pos):
        for box in menu.findChildren(QCheckBox):
            if box.text() == "FOV 2":
                box.setChecked(False)
        return None

    monkeypatch.setattr(menu_exec, "exec_menu", _untick_2)
    ctx.widgets.get("_cell_gating_fov_btn").click()
    assert ctx.state.cell_gating.fov_selection == {"1", "9"}
    assert json.loads(info.read_text())["cell_gating"]["fov_selection"] == ["1", "9"]
    assert recomputes == ["qc.recompute_all"]
