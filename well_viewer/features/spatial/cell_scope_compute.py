"""What the Spatial tab is drawing, as a :class:`CellScope`.

Everything this tab computes — nearest-neighbour distances, colonies, radial
profiles, the summary matrix — starts from per-cell centroids, so the cells are
always well defined even where the statistic on top of them is not something a
CSV reader would reproduce by hand. What changes with the level is *scope*:

* **Summary** faces the whole plate — every well, every timepoint — because its
  panel is a wells × timepoints matrix.
* **Cell / Colony / Intra-colony** are per-frame: one well and one timepoint,
  the pair the tab is displaying.

The centroid columns are required, so a well whose pipeline run did not emit
them contributes nothing rather than contributing rows the tab could not have
placed. This tab also takes its own answer on the gate layer
(``spatial/collect.py:86``, ``:254``), which the scope carries rather than
overriding.
"""

from __future__ import annotations

from well_viewer.features.export.cell_scope_compute import (
    CellScope, plotted_wells, scope_from_ctx,
)
from well_viewer.features.spatial.collect import CENTROID_COL_COL, CENTROID_ROW_COL
from well_viewer.features.spatial.state import LEVEL_SUMMARY


def cell_scope(ctx) -> CellScope | None:
    st = ctx.state.feature_slice("spatial")
    if st is None:
        return None

    level = str(getattr(st, "level", "") or "")
    # The tab computes with the gate layer on (controller.py:332); the cell-count
    # readout at tab_view.py:516 is the one place that deliberately does not, and
    # it is a label rather than a plotted statistic.
    include_gate = True

    if level == LEVEL_SUMMARY:
        wells = plotted_wells(ctx, fallback_to_all=True)
        timepoints: tuple[float, ...] = ()
    else:
        active = str(getattr(st, "active_well", "") or "")
        if not active:
            wells = plotted_wells(ctx)
            wells = wells[:1]
        else:
            group = next((g for g, w in plotted_wells(ctx, fallback_to_all=True)
                          if w == active), active)
            wells = ((group, active),)
        tp = getattr(st, "timepoint", None)
        timepoints = (float(tp),) if tp is not None else ()

    if not wells:
        return None

    return scope_from_ctx(
        ctx,
        plot=f"spatial_{level or 'cell'}",
        timepoints=timepoints,
        include_gate=include_gate,
        tp_fallback=False,
        wells=tuple(wells),
        required_columns=(CENTROID_ROW_COL, CENTROID_COL_COL),
        notes=(
            ("level", level or "cell"),
            ("geometry",
             f"positions are {CENTROID_ROW_COL} / {CENTROID_COL_COL}; every "
             "spatial statistic on this tab is computed from those"),
        ),
    )
