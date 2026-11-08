"""Which Properties controls the Nanowells figure can honour (``review/domain/style_facets.py``).

Read by the Properties sidebar through this feature's ``FeatureDescriptor.style_facets``. Every facet is answered:
``has`` for those the renderer reads or the style pass acts on here, ``lacks`` with the reason for the rest.
``tests/test_every_properties_figure_declares_its_facets.py`` holds the declaration to the vocabulary and, for the
facets a PlotState field carries, to which feature packages read that field.
"""

from __future__ import annotations

from review.domain.style_facets import UNIVERSAL, FigureFacets
from review.features.nanowell.state import VIEWS

#: The page's eight views share one figure. Only the Distribution view draws a beeswarm
#: (``nanowell/controller.py`` reads ``swarm_spacing_of`` there alone), so Beeswarm is shown on that view and kept,
#: hidden, on the rest.
_BEESWARM_ONLY_ON_DISTRIBUTION = {
    view: {"beeswarm": "Only the Distribution view draws a beeswarm; this view never reads the point spacing."}
    for view in VIEWS if view != "distribution"
}


def style_facets() -> dict[str, FigureFacets]:
    """``{figure attribute: what it has}`` for every figure this feature draws that Properties opens on."""
    return {
        "_nw_fig": FigureFacets(
            has=UNIVERSAL | {"legend", "beeswarm", "line_marks", "point_marks", "colour_limits", "log_axes"},
            lacks={
                "bar_geometry": (
                    "Bar width and group spacing are read by the bar renderer alone (``barplot/collect.py``)."
                ),
                "bar_show_mode": (
                    "``bar_show_mode`` is read by the bar renderer alone — setting it here would change only the Bar "
                    "tab."
                ),
                "cell_values": (
                    "The cell number format is the heat map's; this figure prints no cell values."
                ),
                "colour_mode": (
                    "The Colours modes are the Line, Bar and Response renderers'; this figure takes its colours "
                    "elsewhere."
                ),
                "draw_order": (
                    "No replicate-set / well draw order to change: this figure does not draw one series per well in an "
                    "order the panel can reorder."
                ),
                "error_bars": (
                    "Nothing in this feature reads ``use_sem`` / ``bar_err_kind``: it draws no spread the Statistics "
                    "rows choose."
                ),
                "group_labels": (
                    "``group_label_size`` sizes the bar plot's timepoint group labels (``barplot/renderer.py``); "
                    "nothing here reads it."
                ),
                "scatter_density": (
                    "Density colouring and gates belong to the per-cell scatter alone."
                ),
                "threshold_line": (
                    "``show_threshold_line`` is read by the Line (CDF), Bar (Fraction) and Distribution renderers; not "
                    "by this one."
                ),
                "time_axis": (
                    "This feature never reads the time rescale (``review.domain.time_rescale``), so Interval / Offset "
                    "change nothing here."
                ),
                "value_labels": (
                    "Value labels are drawn by the Bar, Line and Scatter renderers (``review.core.plot_annotations``); "
                    "not by this one."
                ),
            },
            views=_BEESWARM_ONLY_ON_DISTRIBUTION,
            default_view="distribution",
        ),
    }
