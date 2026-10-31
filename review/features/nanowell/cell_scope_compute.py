"""What the Nanowells page is drawing, as a :class:`CellScope` for *Save cell data…*.

The page's numbers come from the nanowell tables, but each nanowell's count is a count of rows of the
per-cell CSV that carry its address, so the cells behind the figure are well defined: every cell, at every
timepoint, of the wells the page draws, with a nanowell address. The export takes all timepoints even on
*Along x: Wells*, and says so in the scope block, because the figure's timepoint is a label of the
acquisition and the per-cell CSV's hours need not equal it.

The rows are the cells the page's counts count: ``df_included_mask`` with the scatter gates applied
(``include_gate=True``), exactly as ``controller.gate_tally`` takes them out of each nanowell's count. With the gates
left out, a cell a scatter gate excludes was in the file and not in the count beside it.
"""

from __future__ import annotations

from review.features.export.cell_scope_compute import CellScope, scope_from_ctx
from review.features.nanowell.controller import page_groups


def cell_scope(ctx) -> CellScope | None:
    st = ctx.state.feature_slice("nanowell")
    if st is None or st.metrics is None:
        return None
    groups = page_groups(ctx)
    wells = tuple((name, well) for name, members in groups.items() for well in members)
    if not wells:
        return None
    return scope_from_ctx(
        ctx, plot="nanowells", timepoints=(), include_gate=True, tp_fallback=False, wells=wells,
        required_columns=("nw_q", "nw_r"),
        notes=(("view", f"{st.view}, {st.metric}, along {st.axis}"),
               ("timepoints", "every timepoint of these wells, whichever the figure shows"),
               ("nanowells", "cells with a nanowell address (nw_q, nw_r); cells outside every nanowell are "
                             "not in the nanowell tables either")),
    )
