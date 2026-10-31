"""The nanowell focus filter reaches every tab (``NW_Focus_Pass``, ``review.data.nanowell_focus_gate``).

A nanowell whose rim was out of focus at a timepoint is a gap on the Nanowells page; its cells at that timepoint
must leave every other surface too — the inclusion chokepoint every per-cell tab reads — under the one switch the
QC layer owns, on by default for a nanowell run.

The fixture is built so the wrong answers differ from the right ones (CLAUDE.md §7.8): the soft nanowell's cell is
far brighter than every other, so a well mean that kept it is a different number; a second nanowell at the same
timepoint has no rim focus at all (its rim was not fitted), so a rule that failed unjudged cells would drop it; and
an unaddressed cell sits beside them, which no nanowell verdict may touch.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

pytest.importorskip("PySide6")

from review.data.data_loading import df_included_mask, get_rows  # noqa: E402
from review.domain.nanowell_rim_focus import NW_FOCUS_PASS_COLUMN  # noqa: E402

STARTS = {"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00"}
SITES = 12
SHARP, SOFT = 0.24, 0.15        # 0.15 / 0.24 = 0.625, under the default 0.75


def _dataset(tmp_path):
    """Well A01: twelve nanowells at t_c and t_a, one cell each, plus one cell outside every nanowell. At t_a,
    (0, 0)'s rim is soft and its cell reads GFP 900; (1, 0) has no rim focus. Every other cell reads 10."""
    from tests.test_nanowell_review_data import _dataset as write, _row

    rows, cells = [], []
    for tp in STARTS:
        for k in range(SITES):
            q, r = k % 4, k // 4
            row = _row(tp, q, r, n=1, c=0.1, gfp=10.0)
            row["focus"] = SHARP * (1.0 - 0.01 * k)
            if tp == "t_a" and (q, r) == (0, 0):
                row["focus"] = SOFT
            if tp == "t_a" and (q, r) == (1, 0):
                row["focus"] = None
            rows.append(row)
            gfp = 900.0 if (tp, q, r) == ("t_a", 0, 0) else 10.0
            cells.append({"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": len(cells) + 1,
                          "area_px": 50, "GFP_mean_intensity": gfp, "nw_q": q, "nw_r": r})
        cells.append({"well": "A01", "fov": "F0", "timepoint": tp, "nucleus_id": len(cells) + 1, "area_px": 50,
                      "GFP_mean_intensity": 10.0, "nw_q": None, "nw_r": None})
    out = write(tmp_path, {"A01": rows}, starts=STARTS)
    pd.DataFrame(cells).to_csv(out / "measurements_A01.csv", index=False)
    return out


@pytest.fixture
def loaded(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, _dataset(tmp_path))
    return ctx, window


def _soft_cells(frame):
    return frame["timepoint"].astype(str).eq("t_a") & frame["nw_q"].eq(0) & frame["nw_r"].eq(0)


def test_the_soft_nanowell_s_cells_leave_the_analysis_and_nothing_else_does(loaded):
    ctx, _window = loaded
    assert ctx.state.qc.params is None or ctx.state.qc.params.exclude_soft_nanowells
    frame = get_rows(ctx, "A01")
    assert NW_FOCUS_PASS_COLUMN in frame.columns
    verdict = frame[NW_FOCUS_PASS_COLUMN]
    assert verdict[_soft_cells(frame)].tolist() == [0]
    assert verdict[~_soft_cells(frame)].eq(1).all()          # the unfitted rim's cell and the unaddressed one pass
    kept = frame[df_included_mask(frame)]
    at_a = kept[kept["timepoint"].astype(str) == "t_a"]
    gfp = next(c for c in frame.columns if c.lower() == "gfp_mean_intensity")
    assert at_a[gfp].mean() == 10.0                           # with the soft cell it would be (900 + 12 * 10) / 13
    assert len(at_a) == SITES                                 # 12 nanowells less the soft one, plus the unaddressed


def test_switched_off_the_column_is_removed_and_every_cell_counts_again(loaded):
    from review.features.qc.controller import get_qc_params, recompute_qc_all

    ctx, _window = loaded
    get_rows(ctx, "A01")
    ctx.state.qc.params = replace(get_qc_params(ctx), exclude_soft_nanowells=False)
    recompute_qc_all(ctx)
    frame = get_rows(ctx, "A01")
    assert NW_FOCUS_PASS_COLUMN not in frame.columns
    assert df_included_mask(frame).all()
    # A lower fraction judges the 0.625 rim in focus: nothing is left out, by the number rather than the switch.
    ctx.state.qc.params = replace(get_qc_params(ctx), exclude_soft_nanowells=True, nanowell_focus_fraction=0.6)
    recompute_qc_all(ctx)
    assert get_rows(ctx, "A01")[NW_FOCUS_PASS_COLUMN].eq(1).all()


def test_the_page_and_the_cells_leave_out_the_same_nanowell_timepoints(qtbot, loaded):
    """One verdict, judged on the same rows: the page's ``out_of_focus`` gaps are exactly the nanowell-timepoints
    whose cells ``NW_Focus_Pass`` leaves out, and the page's Cell Gating note does not claim them."""
    from review.features.nanowell import controller as nwc
    from review.features.nanowell.collect import OUT_OF_FOCUS

    ctx, _window = loaded
    ctx.state.selection.selected_wells = {"A01"}
    st = ctx.state.feature_slice("nanowell")
    nwc.nw_on_activate(ctx) if ctx.widgets.get_or_none("_nw_fig") else nwc.ensure_loaded(ctx, force=True)
    qtbot.waitUntil(lambda: st.metrics is not None and not st.computing, timeout=20000)
    m = st.metrics
    page = {(tp, int(q), int(r)) for tp, q, r in m.loc[m["state"] == OUT_OF_FOCUS, ["timepoint", "nw_q", "nw_r"]]
            .itertuples(index=False)}
    frame = get_rows(ctx, "A01")
    cells = {(str(tp), int(q), int(r)) for tp, q, r in
             frame.loc[frame[NW_FOCUS_PASS_COLUMN].eq(0), ["timepoint", "nw_q", "nw_r"]].itertuples(index=False)}
    assert page == cells == {("t_a", 0, 0)}
    assert st.gate is not None and st.gate.excluded == 0      # not Cell Gating's doing, and the note must not say so


def test_the_cell_gating_panel_shows_the_nanowell_row_and_hides_the_per_cell_one(qtbot, loaded):
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import build_qc_gate_controls

    ctx, _window = loaded
    get_rows(ctx, "A01")
    host = QWidget()
    qtbot.addWidget(host)
    build_qc_gate_controls(ctx, host)
    host.show()
    w = ctx.widgets.get("_qc_gate_widgets")
    assert w["nanowell_focus_row"].isVisible() and not w["focus_row"].isVisible()
    assert w["exclude_soft_nanowells"].isChecked()
    assert w["nanowell_focus_status"].text() == (
        "1 nanowell-timepoint (1 nanowell) out of focus across the plate — excluded, 1 cell. "
        "Kept unchecked: 1 with no rim focus (rim not fitted).")


def test_a_qc_section_saved_before_the_switch_existed_opens_with_it_on(loaded):
    from review.persistence import _doc
    from review.persistence.qc_params import load_from_data_dir

    ctx, _window = loaded
    _doc.set_section(ctx, "qc_params", {"enabled": False, "exclude_border_cells": False})
    load_from_data_dir(ctx)
    assert ctx.state.qc.params.exclude_soft_nanowells
