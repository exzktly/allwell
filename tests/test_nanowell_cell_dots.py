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


def _write(tmp_path, *, positions: bool = True):
    rows = []
    for tp in ("t_c", "t_a"):
        for q, (x, y) in CENTRES.items():
            n = sum(1 for t, cq, _ in CELLS if t == tp and cq == q)
            row = _row(tp, q, 0, n=n, c=0.1 * n, gfp=10.0, state="measured" if n else "empty")
            row.update(well_x_um=x, well_y_um=y)
            rows.append(row)
    cells = []
    for k, (tp, q, (dx, dy)) in enumerate(CELLS):
        cell = {"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": k + 1, "area_px": 40,
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
    assert sorted(zip(*map(list, scene.cells))) == pytest.approx(expected)


def test_a_well_without_a_cell_table_or_positions_says_so_and_draws_nothing(tmp_path):
    scene = _scene(_write(tmp_path / "a", positions=False))
    assert len(scene.cells[0]) == 0 and "holds no cell positions" in scene.cells_note
    out = _write(tmp_path / "b")
    (out / "measurements_A01.csv").unlink()
    scene = _scene(out)
    assert len(scene.cells[0]) == 0 and "has no cell table" in scene.cells_note


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
    """Through the real page: the Layers menu's Cells action, triggered as a click triggers it."""
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
    action.trigger()
    assert not st.layers.cells and dots() == 0
