"""Which Properties controls the Bar Plots figure can honour (``review/domain/style_facets.py``).

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
        "_bar_fig": FigureFacets(
            has=UNIVERSAL | {"axis_limits", "error_kinds", "colour_mode", "bar_geometry", "beeswarm", "group_labels", "error_bars", "bar_show_mode", "legend", "threshold_line", "value_labels", "time_axis", "line_marks", "point_marks", "log_axes", "draw_order"},
            lacks={
                "cell_values": (
                    "The cell number format is the heat map's; this figure prints no cell values."
                ),
                "colour_limits": (
                    "No colour bar here: the series are keyed by a legend, so a colour range would move nothing."
                ),
                "scatter_density": (
                    "Density colouring and gates belong to the per-cell scatter alone."
                ),
            },
        ),
    }
