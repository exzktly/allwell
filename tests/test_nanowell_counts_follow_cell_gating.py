"""The Nanowells page counts only the cells Home's Cell Gating includes (``cell_gate_compute``).

Asked for: a size filter on a nanowell's objects. Cell Gating's QC gate already filters cells by size for every
other figure, but the page drew the run's ``n_cells`` whatever was set there. A nanowell's count is now the run's
count less the cells the analysis leaves out of it, and its brightness is the mean over the cells kept.

The fixtures are built so the wrong answers differ from the right ones (CLAUDE.md §7.8): the cells a gate drops
have intensities far from the kept ones, so a brightness taken over every cell is a different number, and a
second well's rows sit in the same frame, so counting them against the first well shows.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import cell_gate_compute as cg


def _cells(rows):
    """Per-cell rows ``(well, timepoint, q, r, area_px, gfp)``."""
    return pd.DataFrame(rows, columns=["well", "timepoint", "nw_q", "nw_r", "area_px", "gfp_mean_intensity"])


def _sites():
    """The site metrics columns :func:`cg.apply` changes, for well A01 at one timepoint: (0,0) 3 cells, (1,0) 2
    cells, (2,0) occluded (no count)."""
    return pd.DataFrame({"well": ["A01", "A01", "A01"], "timepoint": ["t1", "t1", "t1"],
                         "nw_q": [0.0, 1.0, 2.0], "nw_r": [0.0, 0.0, 0.0],
                         "n": [3.0, 2.0, np.nan], "brightness_GFP": [160 / 3, 6.0, np.nan]})


CELLS = _cells([("A01", "t1", 0, 0, 10, 100.0), ("A01", "t1", 0, 0, 50, 20.0), ("A01", "t1", 0, 0, 60, 40.0),
                ("A01", "t1", 1, 0, 50, 5.0), ("A01", "t1", 1, 0, 70, 7.0),
                ("A01", "t1", 2, 0, 5, 9.0),                   # in an occluded nanowell: no count to change
                ("A01", "t1", None, None, 5, 1.0),            # outside every nanowell
                ("B01", "t1", 0, 0, 5, 1.0)])                 # another well's row in the same table


def _small_out(cells, min_area=30):
    return (cells["area_px"] >= min_area).to_numpy()


def test_a_nanowell_loses_exactly_the_cells_gated_out_of_it_and_its_brightness_is_the_kept_cells_mean():
    frame = _sites()
    gate = cg.tally([("A01", CELLS, _small_out(CELLS))])
    cg.apply(frame, gate, ["GFP"])
    assert frame["n"].tolist()[:2] == [2.0, 2.0]                   # (0,0) lost its 10 px cell; (1,0) lost none
    assert np.isnan(frame["n"].iloc[2])                            # occluded: still no count, never a number
    assert frame["brightness_GFP"].iloc[0] == 30.0                 # (20 + 40) / 2, not every cell's 53.3
    assert frame["brightness_GFP"].iloc[1] == 6.0                  # untouched: the run's own value
    # Every addressed A01 cell is counted; the unaddressed one and B01's are not. Of them, the 10 px cell and the
    # 5 px one in the occluded nanowell are out.
    assert (gate.cells, gate.excluded) == (6, 2)


def test_with_nothing_gated_out_every_count_is_the_run_s_own():
    frame = _sites()
    before = frame.copy()
    gate = cg.tally([("A01", CELLS, np.ones(len(CELLS), dtype=bool))])
    cg.apply(frame, gate, ["GFP"])
    pd.testing.assert_frame_equal(frame, before)
    assert cg.note(gate) == ""


def test_a_nanowell_whose_cells_are_all_gated_out_counts_zero_and_has_no_brightness():
    frame = _sites()
    gate = cg.tally([("A01", CELLS, _small_out(CELLS, min_area=1000))])
    cg.apply(frame, gate, ["GFP"])
    assert frame["n"].tolist()[:2] == [0.0, 0.0]
    assert frame["brightness_GFP"].iloc[:2].isna().all()


def test_the_note_says_what_follows_the_gate_and_names_the_wells_it_could_not_read():
    gate = cg.tally([("A01", CELLS, _small_out(CELLS))], unread=["B07"])
    assert cg.note(gate) == (
        "Cell Gating leaves out 2 of 6 cells in these wells' nanowells: counts and brightness exclude them; "
        "confluence and nanowell fluorescence are measured from pixels and do not. The cells of B07 could not be "
        "read, so their counts are the run's own.")


# ── through the page: the real QC size gate, the real worker, the figure's own text ──────────────────────────
pytest.importorskip("PySide6")


def _dataset_with_areas(tmp_path):
    """Well A01, nanowells (0,0) and (1,0) at t_c and t_a. At t_c, (0,0) holds a 10 px object and a 50 px cell
    and (1,0) one 50 px cell; at t_a both hold two 50 px cells."""
    from tests.test_nanowell_review_data import _dataset, _row

    rows = [_row("t_c", 0, 0, n=2, c=0.2, gfp=100.0), _row("t_c", 1, 0, n=1, c=0.1, gfp=50.0),
            _row("t_a", 0, 0, n=2, c=0.2, gfp=100.0), _row("t_a", 1, 0, n=2, c=0.2, gfp=100.0)]
    cells = [("t_c", 0, 10, 900.0), ("t_c", 0, 50, 30.0), ("t_c", 1, 50, 20.0),
             ("t_a", 0, 50, 10.0), ("t_a", 0, 50, 10.0), ("t_a", 1, 50, 10.0), ("t_a", 1, 50, 10.0)]
    out = _dataset(tmp_path, {"A01": rows}, starts={"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00"})
    pd.DataFrame([{"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": i + 1, "area_px": area,
                   "GFP_mean_intensity": gfp, "nw_q": q, "nw_r": 0} for i, (tp, q, area, gfp) in enumerate(cells)]
                 ).to_csv(out / "measurements_A01.csv", index=False)
    return out


def _site(st, tp, q):
    m = st.metrics
    return m[(m["well"] == "A01") & (m["timepoint"] == tp) & (m["nw_q"] == q) & (m["nw_r"] == 0)].iloc[0]


def test_moving_the_qc_size_gate_changes_the_page_s_counts_and_says_so_on_the_figure(qtbot, tmp_path):
    from dataclasses import replace

    from review.features.nanowell import controller as nwc
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _dataset_with_areas(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    assert (_site(st, "t_c", 0)["n"], _site(st, "t_c", 0)["brightness_GFP"]) == (2.0, 465.0)
    assert _site(st, "t_a", 0)["count_fc"] == 1.0

    # Cell Gating's size gate, as its panel sets it: the 10 px object is debris.
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    nwc.nw_on_activate(ctx)                                     # back to the page
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)

    assert (_site(st, "t_c", 0)["n"], _site(st, "t_c", 0)["brightness_GFP"]) == (1.0, 30.0)
    assert _site(st, "t_c", 0)["n0"] == 1.0                     # Starting cells follows the count
    assert _site(st, "t_a", 0)["count_fc"] == 2.0               # and so does every fold change from it
    assert _site(st, "t_c", 1)["n"] == 1.0                      # a nanowell the gate took nothing from
    assert _site(st, "t_c", 0)["c"] == 0.2                      # confluence is pixels: unchanged
    said = re.sub(r"\s+", " ", " ".join(_texts(ctx)))
    assert "Cell Gating leaves out 1 of 7 cells in these wells' nanowells" in said

    # The gate turned off again: the run's own counts, and no sentence.
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=False)
    recompute_qc_all(ctx)
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)
    assert _site(st, "t_c", 0)["n"] == 2.0
    assert "Cell Gating" not in " ".join(_texts(ctx))


def test_batch_export_takes_the_same_cells_out_as_the_page(qtbot, tmp_path):
    """Batch Export's Nanowells mode computes its own metrics, through its own ``_load``; it must not disagree
    with the page, and its figures carry the same sentence."""
    from dataclasses import replace

    from review.features.batch_export.nanowell_panel import NanowellBatchExportPanel
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, _dataset_with_areas(tmp_path))
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    panel = NanowellBatchExportPanel(ctx, window)
    _data, metrics, said = panel._load("earliest", ["A01"])
    row = metrics[(metrics["timepoint"] == "t_c") & (metrics["nw_q"] == 0)].iloc[0]
    assert (row["n"], row["brightness_GFP"]) == (1.0, 30.0)
    assert said.startswith("Cell Gating leaves out 1 of 7 cells")


# ── a whole field left out leaves its nanowells out (tracker H3) ─────────────

def _dataset_with_two_tiles(tmp_path):
    """Well A01 at t_c and t_a. Tile F0 owns (0,0), two 50 px cells, and (3,0), an empty nanowell; tile F1 owns
    (1,0), two 50 px cells. Every nanowell holds the same at both timepoints."""
    from tests.test_nanowell_review_data import _dataset, _row

    def owned(row, owner):
        return {**row, "owner": owner}

    rows = []
    for tp in ("t_c", "t_a"):
        rows += [owned(_row(tp, 0, 0, n=2, c=0.2, gfp=100.0), "F0"), owned(_row(tp, 3, 0, "empty", n=0, c=0.0,
                                                                                gfp=0.0), "F0"),
                 owned(_row(tp, 1, 0, n=2, c=0.2, gfp=100.0), "F1")]
    out = _dataset(tmp_path, {"A01": rows}, starts={"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00"})
    cells = [(tp, fov, q) for tp in ("t_c", "t_a") for fov, q in (("F0", 0), ("F0", 0), ("F1", 1), ("F1", 1))]
    pd.DataFrame([{"well": "A01", "fov": fov, "timepoint": tp, "nucleus_id": i + 1, "area_px": 50,
                   "GFP_mean_intensity": 10.0, "nw_q": q, "nw_r": 0} for i, (tp, fov, q) in enumerate(cells)]
                 ).to_csv(out / "measurements_A01.csv", index=False)
    return out


def _reloaded(qtbot, ctx, st):
    from review.features.nanowell import controller as nwc

    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)


def test_a_field_excluded_in_qc_triage_leaves_out_every_nanowell_it_holds(qtbot, tmp_path):
    """Reported by review: subtracting an excluded field's cells made its loaded nanowells read as measured and
    emptied (n 2 -> 0, fold change 0, a dot at zero) and left its empty ones counted as empty. Each nanowell the
    field holds is left out with the reason, the empty one too, and the other tile's are untouched."""
    from types import SimpleNamespace

    from review.features.home.triage.controller import exclude_fields
    from review.features.nanowell import series_compute as sc
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _dataset_with_two_tiles(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    assert (_site(st, "t_a", 0)["state"], _site(st, "t_a", 0)["count_fc"]) == ("measured", 1.0)

    field = SimpleNamespace(well="A01", fov="F0", timepoint="t_a", can_exclude=True)
    assert exclude_fields(ctx, [field], "soft tile") == 1
    _reloaded(qtbot, ctx, st)

    for q in (0, 3):                                             # loaded and empty, both in F0 at t_a
        row = _site(st, "t_a", q)
        assert row["state"] == "field_excluded", (q, row["state"])
        assert np.isnan(row["n"]) and np.isnan(row["c"]) and np.isnan(row["count_fc"])
    why = sc.exclusion_reason(st.metrics, "count_fc", ["GFP"])
    at = st.metrics.index[(st.metrics["timepoint"] == "t_a") & (st.metrics["nw_q"] == 0)][0]
    assert why[at] == "field_excluded"
    assert (_site(st, "t_c", 0)["state"], _site(st, "t_c", 0)["n"]) == ("measured", 2.0)   # F0 at t_c: kept
    assert (_site(st, "t_a", 1)["n"], _site(st, "t_a", 1)["count_fc"]) == (2.0, 1.0)        # F1: kept
    said = re.sub(r"\s+", " ", " ".join(_texts(ctx)))
    assert "1 excluded field (a tile at one timepoint) leaves out every nanowell it holds" in said
    assert "Cell Gating leaves out" not in said                  # no cell was subtracted from any count


def test_fovs_chosen_on_cell_gating_leave_out_the_other_tiles_nanowells(qtbot, tmp_path):
    from review.features.qc.controller import recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _dataset_with_two_tiles(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.state.cell_gating.fov_selection = {"F1"}
    recompute_qc_all(ctx)
    _reloaded(qtbot, ctx, st)
    assert [_site(st, tp, q)["state"] for tp in ("t_c", "t_a") for q in (0, 3)] == ["field_excluded"] * 4
    assert [_site(st, tp, 1)["n"] for tp in ("t_c", "t_a")] == [2.0, 2.0]


def test_the_field_keys_are_read_per_tile_and_never_in_an_untrusted_well():
    sites = pd.DataFrame({"well": ["A01", "A01", "A01", "B01"], "owner": ["F0", "F1", "F0", "F0"],
                          "timepoint": ["t1", "t1", "t2", "t1"]})
    assert cg.excluded_fields(sites, excluded=[("A01", "F0", "t1")]) == {("A01", "F0", "t1")}
    assert cg.excluded_fields(sites, flagged=[("B01", "F0", "t1")], untrusted=["B01"]) == frozenset()
    assert cg.excluded_fields(sites, chosen_fovs={"1"}) == {("A01", "F0", "t1"), ("A01", "F0", "t2"),
                                                           ("B01", "F0", "t1")}
    assert cg.excluded_fields(sites) == frozenset()


# ── the cell dots follow the same inclusion as the counts (tracker M11) ─────────

#: Well-frame centres (µm) of A01's nanowells; F0 owns (0,0), F1 owns (1,0), at both timepoints.
_CENTRES = {0: (100.0, 100.0), 1: (300.0, 100.0)}
#: A01's cells: (timepoint, q, area px, offset from the nanowell's centre). At t_c (0,0) holds a 10 px object, the
#: one the size gate drops, beside a 50 px cell.
_DOTS = [("t_c", 0, 10, (5.0, -3.0)), ("t_c", 0, 50, (-12.0, 8.0)), ("t_c", 1, 50, (20.0, 0.0)),
         ("t_a", 0, 50, (1.0, 1.0)), ("t_a", 1, 50, (-4.0, 2.0)), ("t_a", 1, 50, (6.0, -6.0))]


def _dot(tp, q, offset):
    return (_CENTRES[q][0] + offset[0], _CENTRES[q][1] + offset[1])


def _dataset_with_cell_positions(tmp_path):
    from tests.test_nanowell_review_data import _dataset, _row

    rows = []
    for tp in ("t_c", "t_a"):
        for q, (x, y) in _CENTRES.items():
            n = sum(1 for t, cq, _a, _o in _DOTS if t == tp and cq == q)
            rows.append({**_row(tp, q, 0, n=n, c=0.1 * n, gfp=10.0), "owner": f"F{q}", "well_x_um": x,
                         "well_y_um": y})
    grids = {"A01": {tp: {"status": "ok", "pitch_um": 172.0, "lattice_kind": "hex"} for tp in ("t_c", "t_a")}}
    out = _dataset(tmp_path, {"A01": rows}, grids=grids,
                   starts={"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00"})
    pd.DataFrame([{"well": "A01", "fov": f"F{q}", "timepoint": tp, "nucleus_id": i + 1, "area_px": area,
                   "GFP_mean_intensity": 10.0, "nw_q": q, "nw_r": 0, "well_x_um": _dot(tp, q, o)[0],
                   "well_y_um": _dot(tp, q, o)[1]} for i, (tp, q, area, o) in enumerate(_DOTS)]
                 ).to_csv(out / "measurements_A01.csv", index=False)
    return out


def test_the_array_s_cells_layer_draws_the_cells_the_counts_hold(qtbot, tmp_path):
    """Reported by review: the Cells layer drew every cell the run counted while the counts under it were gated, so a
    nanowell reading "0 cells" sat under drawn dots. Through the real Layers action: the size gate's 10 px object is
    no longer drawn, the footer counts it as left out, and a field excluded in QC triage draws none of its cells."""
    from dataclasses import replace
    from types import SimpleNamespace

    from review.features.home.triage.controller import exclude_fields
    from review.features.nanowell import controller as nwc
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page, _texts

    ctx = _page(qtbot, _dataset_with_cell_positions(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "array")
    nwc.set_timepoint(ctx, "t_c")
    ctx.widgets.get("_nw_layer_cells").trigger()

    def dots():
        found = [c for c in st.drawn.ax.collections if c.get_gid() == "nw-array-cells"]
        return sorted(tuple(map(float, p)) for c in found for p in c.get_offsets())

    every = sorted(_dot(tp, q, o) for tp, q, _a, o in _DOTS if tp == "t_c")
    assert dots() == every

    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    _reloaded(qtbot, ctx, st)
    assert _site(st, "t_c", 0)["n"] == 1.0
    assert dots() == sorted(_dot(tp, q, o) for tp, q, area, o in _DOTS if tp == "t_c" and area >= 30)
    footer = re.sub(r"\s+", " ", " ".join(_texts(ctx, "nw-array-footer")))
    assert "Cell Gating leaves out 1 more here, which is not drawn." in footer, footer

    exclude_fields(ctx, [SimpleNamespace(well="A01", fov="F0", timepoint="t_a", can_exclude=True)], "soft tile")
    _reloaded(qtbot, ctx, st)
    nwc.set_timepoint(ctx, "t_a")
    assert _site(st, "t_a", 0)["state"] == "field_excluded"
    assert dots() == sorted(_dot(tp, q, o) for tp, q, _a, o in _DOTS if tp == "t_a" and q == 1)
    footer = re.sub(r"\s+", " ", " ".join(_texts(ctx, "nw-array-footer")))
    assert "1 more is in an excluded field, which is not drawn." in footer, footer


def test_a_cut_crop_records_the_rows_of_the_cells_it_draws(tmp_path):
    from review.features.nanowell import image_compute as imc
    from tests.test_nanowell_crop_cells import _tile_data

    data, metrics = _tile_data(tmp_path, [(100.0, 40.0), (98.0, 41.0), (150.0, 40.0)])
    crop = imc.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.cells.tolist() == [[9.0, 9.0], [5.0, 11.0]]
    assert crop.cell_rows[["well_x_um", "well_y_um"]].values.tolist() == [[100.0, 40.0], [98.0, 41.0]]


def test_the_crop_strip_draws_only_the_cells_the_counts_hold(qtbot, tmp_path, monkeypatch):
    """The crop overlay goes through the same rule: the strip's dots leave out the cell the size gate drops, and the
    caption says so."""
    from dataclasses import replace

    from PySide6.QtCore import Qt

    from review.features.nanowell import controller as nwc
    from review.features.nanowell.image_compute import Crop
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _click_dot, _loaded, _page

    def crops(_data, _metrics, site, **_kw):
        """A cut crop of (0,0) at t_c as ``image_compute`` cuts one: each cell's pixel and its row, in one order."""
        rows = pd.DataFrame([{"well": "A01", "timepoint": tp, "nw_q": q, "nw_r": 0, "fov": f"F{q}",
                              "well_x_um": _dot(tp, q, o)[0], "well_y_um": _dot(tp, q, o)[1]}
                             for tp, q, _a, o in _DOTS if tp == "t_c" and q == 0])
        return {"t_c": Crop(np.zeros((40, 40)), "tile", "F0, full resolution", np.array([[3.0, 4.0], [8.0, 9.0]]),
                            cell_rows=rows)}

    monkeypatch.setattr(nwc, "site_crops", crops)
    ctx = _page(qtbot, _dataset_with_cell_positions(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    _reloaded(qtbot, ctx, st)
    _click_dot(qtbot, ctx, ("A01", 0, 0))
    box = ctx.widgets.get("_nw_strip_cells_box")
    qtbot.waitUntil(lambda: box.isVisible() and box.width() > 0, timeout=5000)
    qtbot.mouseClick(box, Qt.LeftButton)

    def strip(gid):
        fig = ctx.widgets.get("_nw_strip_fig")
        return fig, [t.get_text() for t in fig.texts if t.get_gid() == gid]

    qtbot.waitUntil(lambda: bool(strip("nw-crop-caption")[1]), timeout=10000)
    fig, caption = strip("nw-crop-caption")
    dots = [c.get_offsets().tolist() for ax in fig.axes for c in ax.collections if c.get_gid() == "nw-crop-cells"]
    assert dots == [[[8.0, 9.0]]], dots                                  # the 10 px object's dot is gone
    assert caption[0].endswith("; dots: the 1 cells the run counted in view; 1 more Cell Gating leaves out, not "
                               "drawn"), caption


def test_save_nanowell_data_records_the_gate_beside_the_gated_counts(qtbot, tmp_path, monkeypatch):
    """Tracker M10: *Save nanowell data…* writes the page's gated count under the pipeline's own ``n_cells`` name, so
    the file must say so — the run's own count beside it, how many cells the gate left out of each nanowell, and the
    figure's sentence about the gate on every row, as Batch Export's summary carries it. Through the real Save menu
    action, with the real QC size gate dropping (0,0)'s 10 px object at t_c."""
    from dataclasses import replace
    from pathlib import Path

    from PySide6.QtGui import QAction

    import review.core.csv_export as csv_export
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _dataset_with_areas(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    _reloaded(qtbot, ctx, st)
    out = str(Path(ctx.state.dataset.data_dir) / "nanowell_metrics.csv")
    monkeypatch.setattr(csv_export, "ask_save_csv", lambda *_a, **_k: out)
    (action,) = [a for a in ctx.window.findChildren(QAction) if a.text() == "Save nanowell data…"]
    action.trigger()
    written = pd.read_csv(out, dtype={"timepoint": str})
    assert {"n_cells_run", "cells_left_out", "cell_gating"} <= set(written.columns), list(written.columns)
    by = {(r.timepoint, int(r.nw_q)): r for r in written.itertuples()}
    assert (by["t_c", 0].n_cells, by["t_c", 0].n_cells_run, by["t_c", 0].cells_left_out) == (1.0, 2.0, 1)
    assert (by["t_c", 1].n_cells, by["t_c", 1].n_cells_run, by["t_c", 1].cells_left_out) == (1.0, 1.0, 0)
    assert (by["t_a", 0].n_cells, by["t_a", 0].n_cells_run, by["t_a", 0].cells_left_out) == (2.0, 2.0, 0)
    assert set(written["cell_gating"]) == {
        "Cell Gating leaves out 1 of 7 cells in these wells' nanowells: counts and brightness exclude them; "
        "confluence and nanowell fluorescence are measured from pixels and do not."}
    assert list(written.columns).index("n_cells_run") == list(written.columns).index("n_cells") + 1
