"""Cell Gating applies a scatter gate drawn on nanowell metrics to the cells in those nanowells.

A gate drawn on the nanowell scatter, or on the cell scatter's "Nanowell ▸" axes, names ``nw::<key>`` columns that
no cell table holds. Cell Gating used to read those columns straight off the cell rows, find nothing, and so include
no cell at all — with nothing to say why. It now joins each cell's own nanowell's value at its timepoint
(``observations.nanowell_gate_values`` → ``join_nanowell_columns``).

**The count it reads is the count before the scatter gates.** A nanowell's count is the cells the analysis
includes, and the scatter gates decide part of that, so judging a gate against the gated count makes the pass
flip-flop: every nanowell the first pass left out reads 0 cells on the second, and a box reaching down to 0
takes them back. The fixture is built so it does (CLAUDE.md §7.8 #2): the box holds counts 0–2 and the
pixel-measured confluence (``0.1 × count``, untouched by any cell gate) on its other axis.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.fixtures.nanowell_dataset import COUNTS, SITES  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import scatter_ctx  # noqa: E402,F401

#: Counts 0–2 inside (x), any confluence (y).
_GATE = {"x0": -0.5, "x1": 2.5, "y0": -1.0, "y1": 10.0, "x_col": "nw::n", "y_col": "nw::c", "name": "Few cells"}


def _want(well: str) -> list[tuple[str, int, int, int]]:
    """``(timepoint, q, r, passes)`` per cell, from the fixture's counts: in when its nanowell held ≤ 2 cells."""
    return sorted((tp, q, r, int(n <= 2)) for tp, counts in COUNTS[well].items()
                  for (q, r), n in zip(SITES, counts) for _ in range(n))


def _got(ctx, well: str):
    from review.data.data_loading import get_rows

    df = get_rows(ctx, well)
    return sorted(zip(df["timepoint"].astype(str), df["nw_q"].astype(int), df["nw_r"].astype(int),
                      df["Gate_Pass"].astype(int)))


def test_a_nanowell_gate_includes_the_cells_of_the_nanowells_inside_it(scatter_ctx):
    from review.data.data_loading import get_rows
    from review.features.home.cell_gating.controller import _recompute_now_on_this_thread

    ctx = scatter_ctx
    for well in COUNTS:
        get_rows(ctx, well)                         # held, so the pass gates them
    ctx.state.cell_gating.scatter_gates = [dict(_GATE)]
    ctx.state.cell_gating.use_scatter_gates = True
    assert _recompute_now_on_this_thread(ctx)
    for well in COUNTS:
        assert _got(ctx, well) == _want(well), well


def test_the_gate_reads_counts_from_before_the_scatter_gates_so_a_second_pass_agrees(scatter_ctx):
    from review.data.data_loading import get_rows
    from review.features.home.cell_gating.controller import _recompute_now_on_this_thread

    ctx = scatter_ctx
    for well in COUNTS:
        get_rows(ctx, well)
    ctx.state.cell_gating.scatter_gates = [dict(_GATE)]
    ctx.state.cell_gating.use_scatter_gates = True
    assert _recompute_now_on_this_thread(ctx)
    assert _recompute_now_on_this_thread(ctx)       # a gated count would now read 0 for every nanowell left out
    for well in COUNTS:
        assert _got(ctx, well) == _want(well), well


def test_the_projector_regates_a_reloaded_well_the_same_way(scatter_ctx):
    """A well evicted and read back is gated by the recorded configuration (``apply_gating_to_cache``), which must
    carry the nanowell values the pass read, or the reloaded well loses every cell."""
    from review.data.data_loading import get_rows
    from review.features.home.cell_gating.controller import _recompute_now_on_this_thread
    from review.features.home.cell_gating.gating_compute import apply_gating_to_cache

    ctx = scatter_ctx
    for well in COUNTS:
        get_rows(ctx, well)
    ctx.state.cell_gating.scatter_gates = [dict(_GATE)]
    ctx.state.cell_gating.use_scatter_gates = True
    assert _recompute_now_on_this_thread(ctx)
    df = get_rows(ctx, "A01")
    df["Gate_Pass"] = 0                              # as if read back from disk with no gating on it
    apply_gating_to_cache(ctx, "A01")
    assert _got(ctx, "A01") == _want("A01")
