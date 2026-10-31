"""The Array view's Cells layer: a dot at the centre of each cell the run counted (nanowell tracker V-cells).

Asked for: "Can boundaries/masks be drawn on the cells themselves in a nanowell? Are these masks saved? Can it be
toggled on and off like the other nanowell toggles?" The run keeps no cell outlines, so of the options offered the
user chose the one that needs no re-run: a dot per counted cell, from the cell table every nanowell run already
writes, switched on and off from the Layers menu like the other layers.

The scene is asserted exactly — which cells, at which places, in both frames — and the page through its real
Layers menu action, as a click triggers it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.test_nanowell_review_data import _dataset, _row

#: Three nanowells of A01 at t_c, measured at these centres (µm, well frame).
CENTRES = {0: (100.0, 100.0), 1: (300.0, 100.0), 2: (500.0, 120.0)}
#: Counted cells: (timepoint, q, offset from the nanowell's measured centre). One at t_a, so a timepoint is chosen.
CELLS = [("t_c", 0, (5.0, -3.0)), ("t_c", 0, (-12.0, 8.0)), ("t_c", 2, (20.0, 0.0)), ("t_a", 1, (0.0, 0.0))]


def _write(tmp_path, *, positions: bool = True, areas=None):
    rows = []
    for tp in ("t_c", "t_a"):
        for q, (x, y) in CENTRES.items():
            n = sum(1 for t, cq, _ in CELLS if t == tp and cq == q)
            row = _row(tp, q, 0, n=n, c=0.1 * n, gfp=10.0, state="measured" if n else "empty")
            row.update(well_x_um=x, well_y_um=y)
            rows.append(row)
    cells = []
    for k, (tp, q, (dx, dy)) in enumerate(CELLS):
        cell = {"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": k + 1, "area_px": (areas or {}).get(k, 40),
                "GFP_mean_intensity": 10.0, "nw_q": q, "nw_r": 0, "nw_ring": q, "nw_label": f"{q}_0"}
        if positions:
            cell.update(well_x_um=CENTRES[q][0] + dx, well_y_um=CENTRES[q][1] + dy)
        cells.append(cell)
    grids = {"A01": {tp: {"status": "ok", "pitch_um": 172.0, "lattice_kind": "hex"} for tp in ("t_c", "t_a")}}
    return _dataset(tmp_path, {"A01": rows}, cells=cells, grids=grids)


def _scene(out, positions="measured"):
    from review.features.nanowell import controller as nwc
    from review.features.nanowell.array_compute import array_scene
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    nwc.load_into(st, out)
    return array_scene(st.metrics, st.data, "A01", "t_c", "n_cells", positions=positions, cells=True)


def test_a_dot_is_drawn_where_the_run_measured_each_counted_cell(tmp_path):
    scene = _scene(_write(tmp_path))
    expected = sorted((CENTRES[q][0] + dx, CENTRES[q][1] + dy) for tp, q, (dx, dy) in CELLS if tp == "t_c")
    assert sorted(zip(*map(list, scene.cells))) == expected                  # t_c's three, not t_a's one
    assert "each of the 3 cells the run counted" in scene.cells_note and "no cell outlines" in scene.cells_note


def test_on_the_lattice_a_dot_keeps_its_place_in_its_nanowell(tmp_path):
    """On the lattice a nanowell moves to its address's place; its cells move with it, keeping their offsets from
    its measured centre — not left at their measured places, where the lattice has no nanowell."""
    scene = _scene(_write(tmp_path), positions="lattice")
    places = {q: (scene.sites.x[k], scene.sites.y[k]) for k, (_w, q, _r) in enumerate(scene.sites.sites)}
    assert places[1] != CENTRES[1]                                           # the case where the frames differ
    expected = sorted((places[q][0] + dx, places[q][1] + dy) for tp, q, (dx, dy) in CELLS if tp == "t_c")
    # Flattened: pytest 9.2's approx refuses nested sequences, and a list of (x, y) pairs is one.
    got = sorted(zip(*map(list, scene.cells)))
    assert [c for xy in got for c in xy] == pytest.approx([c for xy in expected for c in xy])


def test_a_well_without_a_cell_table_or_positions_says_so_and_draws_nothing(tmp_path):
    scene = _scene(_write(tmp_path / "a", positions=False))
    assert len(scene.cells[0]) == 0 and "holds no cell positions" in scene.cells_note
    out = _write(tmp_path / "b")
    (out / "measurements_A01.csv").unlink()
    scene = _scene(out)
    assert len(scene.cells[0]) == 0 and "has no cell table" in scene.cells_note


def test_a_well_measured_with_no_cell_says_the_run_counted_none_not_that_it_has_no_table(tmp_path):
    # The pipeline writes a header-only table for a well it measured and found no cell in
    # (pipeline/nanowell_task.write_empty_cells_csv); that is "counted zero", not "could not count".
    from pipeline.nanowell_task import cell_columns

    out = _write(tmp_path)
    pd.DataFrame(columns=cell_columns(["GFP"])).to_csv(out / "measurements_A01.csv", index=False)
    scene = _scene(out)
    assert len(scene.cells[0]) == 0
    assert scene.cells_note == f"Cells: the run counted no cell in A01 at {scene.timepoint}."


def test_the_cell_table_is_read_again_when_it_changes(tmp_path):
    out = _write(tmp_path)
    assert len(_scene(out).cells[0]) == 3
    frame = pd.read_csv(out / "measurements_A01.csv")
    frame[frame["nw_q"] != 2].to_csv(out / "measurements_A01.csv", index=False)
    assert len(_scene(out).cells[0]) == 2


def test_the_layer_is_off_until_switched_on_and_kept_with_the_page_s_settings():
    from review.features.nanowell.state import ArrayLayers

    assert ArrayLayers().cells is False and all(not ArrayLayers.preset(p).cells for p in ArrayLayers.PRESETS)
    on = ArrayLayers(cells=True)
    assert ArrayLayers.from_config(on.to_config()).cells is True
    assert ArrayLayers.from_config({"cells": "yes"}).cells is False          # not a bool: the default


def test_the_layers_menu_switches_the_dots_on_and_off(tmp_path, qtbot):
    """Through the real page: the Layers menu's Cells action, triggered as a click triggers it; and the Overlays
    button, which hides the coloured layers together and leaves each switch as it was, takes the dots with them."""
    pytest.importorskip("PySide6")
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _write(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    nwc.set_view(ctx, "array")
    nwc.set_timepoint(ctx, "t_c")
    st = ctx.state.feature_slice("nanowell")

    def dots():
        found = [c for c in st.drawn.ax.collections if c.get_gid() == "nw-array-cells"]
        return sum(len(c.get_offsets()) for c in found)

    assert dots() == 0 and not any("Cells:" in t for t in _texts(ctx, "nw-array-footer"))
    action = ctx.widgets.get("_nw_layer_cells")
    action.trigger()
    assert st.layers.cells and action.isChecked() and dots() == 3
    assert any("3 cells the run counted" in t for t in _texts(ctx, "nw-array-footer"))
    overlays = ctx.widgets.get("_nw_overlays_btn")
    overlays.click()                                                     # the coloured layers hidden together
    assert dots() == 0 and st.layers.cells
    overlays.click()                                                     # and back, as they were
    assert dots() == 3
    action.trigger()
    assert not st.layers.cells and dots() == 0


# ── the dots and the count describe the same cells ───────────────────────────────────────────────────────────
#: The second t_c cell of nanowell 0 (``CELLS[1]``) is debris to a QC size gate of 30 px; every other cell is 40 px.
DEBRIS = {1: 10}


def test_the_gate_tally_names_each_cell_it_leaves_out_in_the_form_the_dots_are_keyed_in(tmp_path):
    """The tally keys a left-out cell exactly as ``collect.cell_positions`` keys its dot, whatever spelling each side
    gives the nucleus id: the row cache may hold ``2.0`` where the CSV holds ``2``."""
    from review.features.nanowell import cell_gate_compute as cg
    from review.features.nanowell.collect import cell_positions
    from review.features.nanowell.state import NanowellState
    from review.features.nanowell import controller as nwc

    out = _write(tmp_path, areas=DEBRIS)
    cached = pd.read_csv(out / "measurements_A01.csv")
    cached["nucleus_id"] = cached["nucleus_id"].astype(float)              # the row cache's float spelling
    gate = cg.tally([("A01", cached, (cached["area_px"] >= 30).to_numpy())])
    st = NanowellState()
    nwc.load_into(st, out)
    dots = cell_positions(st.data, "A01", "t_c")
    assert gate.left_out == {("A01", "F0", "t_c", "2")}
    assert [key in gate.left_out for key in dots["key"]] == [False, True, False]


def test_crop_dots_are_the_nanowell_s_own_cells_with_the_left_out_ones_hollow(tmp_path):
    """A crop is a square reaching past its nanowell, so it can hold a neighbour's cells. Its dots are only the cells
    counted in its own nanowell — the ones under the count written beneath it — and a cell Cell Gating leaves out is
    a hollow ring, counted apart in the caption."""
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    from matplotlib.figure import Figure

    from review.features.nanowell import cell_gate_compute as cg
    from review.features.nanowell import controller as nwc
    from review.features.nanowell.collect import cell_positions
    from review.features.nanowell.image_compute import Crop, _cells_in
    from review.features.nanowell.renderer import render_crop_grid
    from review.features.nanowell.state import NanowellState

    out = _write(tmp_path, areas=DEBRIS)
    st = NanowellState()
    nwc.load_into(st, out)
    cells = cell_positions(st.data, "A01", "t_c")
    # A 1 µm-per-pixel crop centred on nanowell 0, wide enough to reach nanowell 2's cell at (520, 120).
    box = dict(left_um=100.0 - 450, top_um=100.0 - 450, um_per_px=1.0, shape=(901, 901))
    reached, _keys = _cells_in(cells, **box)
    own, keys = _cells_in(cells, **box, site=(0, 0))
    assert len(reached) == 3 and len(own) == 2                             # the neighbour's cell is not its own
    assert sorted(map(tuple, own)) == sorted((450.0 + dx, 450.0 + dy) for tp, q, (dx, dy) in CELLS
                                             if tp == "t_c" and q == 0)

    table = pd.read_csv(out / "measurements_A01.csv")
    gate = cg.tally([("A01", table, (table["area_px"] >= 30).to_numpy())])
    fig = Figure(figsize=(6, 4))
    crop = Crop(np.zeros((901, 901)), "tile", "F0, full resolution", own, keys=keys)
    render_crop_grid(fig, {"t_c": crop}, ("A01", 0, 0), cells=True, left_out=cg.left_out(gate))
    ax = fig.axes[0]
    filled = [tuple(xy) for c in ax.collections if c.get_gid() == "nw-crop-cells" for xy in c.get_offsets()]
    hollow = [tuple(xy) for c in ax.collections if c.get_gid() == "nw-crop-cells-out" for xy in c.get_offsets()]
    assert filled == [(450.0 + 5.0, 450.0 - 3.0)] and hollow == [(450.0 - 12.0, 450.0 + 8.0)]
    caption = " ".join(t.get_text() for t in fig.texts if t.get_gid() == "nw-crop-grid-caption")
    assert "dots: the 1 cells counted in the nanowell" in caption
    assert "hollow: 1 more the run counted that Cell Gating leaves out" in caption


def test_with_the_qc_size_gate_on_the_filled_dots_are_the_cells_the_counts_count(tmp_path, qtbot):
    """Reported: toggling Cells on drew more dots in a nanowell than the count under it, with QC area limits set.
    Through the real page, the real QC gate and the row cache it writes: the filled dots in each nanowell number its
    count, and the cell the gate drops is drawn hollow rather than hidden."""
    pytest.importorskip("PySide6")
    from dataclasses import replace

    from review.features.nanowell import controller as nwc
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _write(tmp_path, areas=DEBRIS))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)
    nwc.set_view(ctx, "array")
    nwc.set_timepoint(ctx, "t_c")
    st.layers.positions = "measured"
    ctx.widgets.get("_nw_layer_cells").trigger()

    def offsets(gid):
        return sorted(tuple(xy) for c in st.drawn.ax.collections if c.get_gid() == gid for xy in c.get_offsets())

    m = st.metrics[st.metrics["timepoint"] == "t_c"].set_index("nw_q")["n"]
    filled, hollow = offsets("nw-array-cells"), offsets("nw-array-cells-out")
    per_nanowell = {q: sum(1 for x, _y in filled if abs(x - CENTRES[q][0]) < 50) for q in CENTRES}
    assert per_nanowell == {q: int(m[q]) for q in CENTRES} == {0: 1, 1: 0, 2: 1}
    assert hollow == [(CENTRES[0][0] - 12.0, CENTRES[0][1] + 8.0)]
    footer = " ".join(_texts(ctx, "nw-array-footer"))
    assert "Filled: the 2 the analysis keeps" in footer and "hollow: the 1 Cell Gating leaves out" in footer
