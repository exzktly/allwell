"""Which Properties controls the Distribution figure can honour (``review/domain/style_facets.py``).

Read by the Properties sidebar through this feature's ``FeatureDescriptor.style_facets``. Every facet is answered:
``has`` for those the renderer reads or the style pass acts on here, ``lacks`` with the reason for the rest.
``tests/test_every_properties_figure_declares_its_facets.py`` holds the declaration to the vocabulary and, for the
facets a PlotState field carries, to which feature packages read that field.
"""

from __future__ import annotations

from review.domain.style_facets import UNIVERSAL, FigureFacets


def style_facets() -> dict[str, FigureFacets]:
    """``{figure attribute: what it has}`` for every figure this feature draws that Properties opens on."""
    return {
        "_distribution_fig": FigureFacets(
            has=UNIVERSAL | {"axis_limits", "legend", "threshold_line", "time_axis", "line_marks", "log_axes"},
            lacks={
                "error_kinds": (
                    "No Error bars row here: nothing on this figure draws a spread."
                ),
                "point_marks": (
                    "No line carries a marker: the histogram is patches, the violins are collections, and the KDE "
                    "and CDF curves are drawn without markers, which the style pass keeps off a curve "
                    "(``plot_style._drawn_without_marker``)."
                ),
                "bar_geometry": (
                    "Bar width and group spacing are read by the bar renderer alone (``barplot/collect.py``)."
                ),
                "bar_show_mode": (
                    "``bar_show_mode`` is read by the bar renderer alone — setting it here would change only the Bar "
                    "tab."
                ),
                "beeswarm": (
                    "No beeswarm is drawn here; ``swarm_spacing`` is read by the bar and Nanowells renderers only."
                ),
                "cell_values": (
                    "The cell number format is the heat map's; this figure prints no cell values."
                ),
                "colour_limits": (
                    "No colour bar here: the series are keyed by a legend, so a colour range would move nothing."
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
                    "``redraw_distribution`` reads neither ``use_sem`` nor ``bar_err_kind`` "
                    "(``distribution/tab_view.py`` says so): a distribution draws the cells, not a spread of their "
                    "mean."
                ),
                "group_labels": (
                    "``group_label_size`` sizes the bar plot's timepoint group labels (``barplot/renderer.py``); "
                    "nothing here reads it."
                ),
                "scatter_density": (
                    "Density colouring and gates belong to the per-cell scatter alone."
                ),
                "value_labels": (
                    "Value labels are drawn by the Bar, Line and Scatter renderers (``review.core.plot_annotations``); "
                    "not by this one."
                ),
            },
        ),
    }
