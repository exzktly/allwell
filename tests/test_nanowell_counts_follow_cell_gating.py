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


def test_save_cell_data_exports_exactly_the_cells_the_page_s_counts_count(qtbot, tmp_path):
    """The page's *Save cell data…* scope used ``include_gate=False``, so a cell a scatter gate leaves out was in
    the exported rows while ``gate_tally`` took it out of its nanowell's count (cell population audit P2). Through
    the real page and the row cache both read: one cell fails a scatter gate, and the file holds every other cell."""
    from review.data.data_loading import get_rows
    from review.features.export import cell_scope_compute as csc
    from review.features.nanowell import controller as nwc
    from review.features.nanowell.cell_scope_compute import cell_scope
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _dataset_with_areas(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    frame = get_rows(ctx, "A01")
    frame["Gate_Pass"] = 1
    frame.loc[frame.index[0], "Gate_Pass"] = 0                  # one cell outside the scatter gates
    gate = nwc.gate_tally(ctx, st.data)
    assert (gate.cells, gate.excluded) == (7, 1)
    exported = csc.population_mask(get_rows(ctx, "A01"), cell_scope(ctx))
    assert int(exported.sum()) == gate.cells - gate.excluded == 6


def test_the_nanowell_csv_says_its_counts_are_gated_and_by_how_much(qtbot, tmp_path):
    """*Save nanowell data…* wrote the gated count as ``n_cells`` with nothing in the file saying so, beside the
    run's area estimate and disagreement flag, which compare against the run's own count (cell population audit P3).
    Through the real page and the real QC size gate: each row carries the run's count and the cells left out, and the
    file names the gate."""
    from dataclasses import replace

    from review.features.nanowell import controller as nwc
    from review.features.qc.controller import get_qc_params, recompute_qc_all
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _dataset_with_areas(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    st = _loaded(qtbot, ctx)
    ctx.state.qc.params = replace(get_qc_params(ctx), enabled=True, min_area=30.0)
    recompute_qc_all(ctx)
    nwc.nw_on_activate(ctx)
    qtbot.waitUntil(lambda: not st.computing and st.gated_for == nwc.gate_version(ctx), timeout=20000)
    path = tmp_path / "nanowells.csv"
    nwc.export_long_csv(st, path, ["A01"])
    table = pd.read_csv(path).set_index(["timepoint", "nw_q"])
    assert table.loc[("t_c", 0), ["n_cells", "n_cells_run", "cells_left_out"]].tolist() == [1.0, 2.0, 1.0]
    assert table.loc[("t_c", 1), ["n_cells", "n_cells_run", "cells_left_out"]].tolist() == [1.0, 1.0, 0.0]
    columns = list(table.reset_index().columns)
    assert columns.index("n_cells_run") == columns.index("n_cells") + 1
    assert columns.index("area_est_cells") == columns.index("cells_left_out") + 1
    assert set(table["cell_gating"]) == {
        "Cell Gating leaves out 1 of 7 cells in these wells' nanowells: counts and brightness exclude them; "
        "confluence and nanowell fluorescence are measured from pixels and do not."}
