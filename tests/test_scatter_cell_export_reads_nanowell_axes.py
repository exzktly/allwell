"""The per-cell Scatter export on a Nanowell ▸ axis exports the cells the plot draws (tracker G3).

The plot joins each cell's own nanowell's value onto its rows before drawing
(``with_nanowell_columns``). The export scope read the rows without that join,
so a ``nw::`` axis resolved to NaN, ``population_mask`` required a finite value,
and the file held no rows at all — and a gate drawn on those axes could mark
none of them ``plot_in_gate``. The scope now carries the nanowell rows and
``build_chunk`` joins them, the way the plot does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.fixtures.nanowell_dataset import COUNTS, SITES  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import scatter_ctx  # noqa: E402,F401

#: Nanowells holding at most two cells; any confluence. The fixture's counts put
#: some cells inside and some outside, so a marker that is all-0 or all-1 fails.
_GATE = {"x0": -0.5, "x1": 2.5, "y0": -1.0, "y1": 10.0, "x_col": "nw::n", "y_col": "nw::c", "name": "Few"}


def _chunk(ctx, well):
    from review.data.data_loading import get_rows
    from review.features.export.cell_scope_compute import build_chunk
    from review.features.scatter.cell_scope_compute import cell_scope

    ctx.state.cell_gating.scatter_gates = [dict(_GATE)]
    hours = sorted({float(h) for h in get_rows(ctx, well)["timepoint_hours"]})
    scope = cell_scope(ctx, entry_x="GFP", entry_y="GFP", col_x="nw::n", col_y="nw::c",
                       timepoints=tuple(hours))
    return build_chunk(get_rows(ctx, well), scope, group=well, well_label=well, well_name=well)


def test_every_cell_the_plot_draws_is_exported_with_its_nanowell_values(scatter_ctx):
    chunk = _chunk(scatter_ctx, "A01")
    n_cells = sum(sum(counts) for counts in COUNTS["A01"].values())
    assert len(chunk) == n_cells
    want = sorted((tp, q, r, n) for tp, counts in COUNTS["A01"].items()
                  for (q, r), n in zip(SITES, counts) for _ in range(n))
    got = sorted(zip(chunk["timepoint"].astype(str), chunk["nw_q"].astype(int),
                     chunk["nw_r"].astype(int), chunk["nw::n"].astype(int)))
    assert got == want


def test_the_gate_marker_reads_the_joined_nanowell_values(scatter_ctx):
    chunk = _chunk(scatter_ctx, "A01")
    marker = chunk["plot_in_gate"].astype(int).tolist()
    expected = [int(n <= 2) for n in chunk["nw::n"].astype(int)]
    assert marker == expected
    assert 0 < sum(marker) < len(marker)
