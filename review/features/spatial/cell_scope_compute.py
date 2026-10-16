"""What the Spatial tab is drawing, as a :class:`CellScope`.

Everything this tab computes — nearest-neighbour distances, colonies, radial
profiles, the summary matrix — starts from per-cell centroids, so the cells are
always well defined even where the statistic on top of them is not something a
CSV reader would reproduce by hand. What changes with the level is *scope*:

* **Summary** faces the whole plate — every well, every timepoint — because its
  panel is a wells × timepoints matrix.
* **Cell / Colony / Intra-colony** facet one panel per selected well, up to
  ``_MAX_FRAME_PANELS``, at one timepoint. The export takes every well the tab
  has a panel for — ``st.frames`` — not ``st.active_well``, which is merely
  ``shown[0]`` (``controller.py:310``) and would silently export one well of
  the eight on screen. Wells the panel cap dropped are counted in the scope
  block rather than left unmentioned.

The position columns the tab used are required — the well frame in µm on a
nanowell run, the field centroid otherwise — so a well whose run did not emit
them contributes nothing rather than contributing rows the tab could not have
placed. This tab also takes its own answer on the gate layer
(``spatial/collect.py:86``, ``:254``), which the scope carries rather than
overriding.
"""

from __future__ import annotations


from core.safe import swallowed

from review.features.export.cell_scope_compute import (
    CellScope, plotted_wells, scope_from_ctx,
)
from review.data.data_loading import resolved_timepoints
from review.features.spatial.collect import CENTROID_COL_COL, CENTROID_ROW_COL, position_columns
from review.features.spatial.state import LEVEL_SUMMARY


def cell_scope(ctx) -> CellScope | None:
    st = ctx.state.feature_slice("spatial")
    if st is None:
        return None

    level = str(getattr(st, "level", "") or "")
    # The tab computes with the gate layer on (controller.py:332); the cell-count
    # readout at tab_view.py:516 is the one place that deliberately does not, and
    # it is a label rather than a plotted statistic.
    include_gate = True

    notes: list[tuple[str, str]] = [("level", level or "cell")]

    if level == LEVEL_SUMMARY:
        wells = plotted_wells(ctx, fallback_to_all=True)
        timepoints: tuple[float, ...] = ()
    else:
        # Every panel on screen, in the order the tab drew them.
        groups = dict((w, g) for g, w in plotted_wells(ctx, fallback_to_all=True))
        panels = [str(f.get("well") or "") for f in (getattr(st, "frames", None) or [])]
        panels = [w for w in panels if w]
        if not panels:
            panels = [str(getattr(st, "active_well", "") or "")]
            panels = [w for w in panels if w]
        wells = tuple((groups.get(w, w), w) for w in panels)
        dropped = int(getattr(st, "frames_truncated", 0) or 0)
        if dropped:
            notes.append(("wells not shown",
                          f"{dropped} more selected well(s) exceeded the panel "
                          "cap and are not in this file"))

        # ``timepoint = None`` means "the first available" to the collector
        # (``collect.collect_spatial_frame``), not "every timepoint" — reading
        # it as the latter exports a file several times wider than the figure
        # while the scope block claims it holds all of them.
        tp = getattr(st, "timepoint", None)
        if tp is None:
            tp = _first_timepoint(ctx, [w for _g, w in wells])
        timepoints = (float(tp),) if tp is not None else ()

    if not wells:
        return None

    x_col, y_col, unit = _positions(ctx, [w for _g, w in wells])
    return scope_from_ctx(
        ctx,
        plot=f"spatial_{level or 'cell'}",
        timepoints=timepoints,
        include_gate=include_gate,
        # The tab reads a row's hours through the loader's resolver (``collect._hours``): a nanowell run's cell
        # table has only the ``timepoint`` label, so without the fallback every one of its rows dropped out.
        tp_fallback=True,
        wells=tuple(wells),
        required_columns=(x_col, y_col),
        notes=tuple(notes) + ((
            "geometry",
            f"positions are {x_col} / {y_col} ({unit}); every "
            "spatial statistic on this tab is computed from those"),),
    )


def _positions(ctx, wells) -> tuple[str, str, str]:
    """The position columns the tab placed these wells' cells with (``collect.position_columns``): the well
    frame in µm on a nanowell run, the field centroid in pixels otherwise."""
    from review.data.data_loading import get_rows

    for well in wells:
        try:
            df = get_rows(ctx, well)
        except Exception:
            swallowed(f"spatial scope: rows for {well!r}")
            continue
        found = position_columns(df) if df is not None and not df.empty else None
        if found is not None:
            return found
    return CENTROID_COL_COL, CENTROID_ROW_COL, "px"


def _first_timepoint(ctx, wells) -> float | None:
    """The timepoint an unset picker resolves to — the collector's own rule.

    ``collect_spatial_frame`` takes the smallest finite hour of the frame it is
    drawing — ``timepoint_hours``, else the ``timepoint`` label read as hours — so the export has to ask the same question of the same rows
    rather than falling back to "all", which is a different figure.
    """
    import numpy as np

    from review.data.data_loading import get_rows

    best: float | None = None
    for well in wells:
        try:
            df = get_rows(ctx, well)
        except Exception:
            swallowed(f"spatial scope: rows for {well!r}")
            continue
        if df is None or df.empty:
            continue
        values = np.asarray(resolved_timepoints(df), dtype=float)
        finite = values[np.isfinite(values)]
        if finite.size:
            candidate = float(finite.min())
            best = candidate if best is None else min(best, candidate)
    return best
