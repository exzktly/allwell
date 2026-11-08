"""Which Properties controls the Heat Map figure can honour (``review/domain/style_facets.py``).

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
        "_heatmap_fig": FigureFacets(
            has=UNIVERSAL | {"error_kinds", "cell_values", "error_bars", "time_axis"},
            lacks={
                "axis_limits": (
                    "X and Y limits on a heat map are ranges of row and column *positions*: a typed limit only crops "
                    "the plate or pads it with empty space, which no one asks a plate map for. The axes are marked "
                    "``_aw_no_typed_limits``, so the style pass applies none — a limit typed before this row was hidden "
                    "cannot go on cropping the grid with nothing left to clear it. With Colour limits Display ▾'s and "
                    "no log axes, Limits & Scale has nothing left and is not built."
                ),
                "bar_geometry": (
                    "Bar width and group spacing are read by the bar renderer alone (``barplot/collect.py``)."
                ),
                "bar_show_mode": (
                    "``bar_show_mode`` is read by the bar renderer alone. A cell is one value; there is no Mean / "
                    "Mean+spread / All points to choose between. The Error bars and Across rows *are* live — they pick "
                    "the interval the per-cell ``±`` prints."
                ),
                "beeswarm": (
                    "No beeswarm is drawn here; ``swarm_spacing`` is read by the bar and Nanowells renderers only."
                ),
                "colour_limits": (
                    "Its colour range is Display ▾'s — Auto, Global or typed, and the log norm — and the image is "
                    "marked ``_aw_owns_clim``, so the style pass's Colour limits leave it alone rather than fight that "
                    "control."
                ),
                "colour_mode": (
                    "The Colours modes are the Line, Bar and Response renderers'; this figure takes its colours "
                    "elsewhere."
                ),
                "draw_order": (
                    "No replicate-set / well draw order to change: this figure does not draw one series per well in an "
                    "order the panel can reorder."
                ),
                "group_labels": (
                    "``group_label_size`` sizes the bar plot's group labels. A heat map names its rows and columns with "
                    "ordinary ticks, which Tick label size already governs."
                ),
                "legend": (
                    "A heat map draws no legend. Its colour bar is the key, and that is the mappable's, not an "
                    "``ax.legend()`` this section can reach."
                ),
                "line_marks": (
                    "There are no lines in the grid — ``ax.lines`` is empty, so Line style and Line width style artists "
                    "that do not exist."
                ),
                "log_axes": (
                    "Both axes are marked ``_categorical_xaxis`` / ``_categorical_yaxis``: they carry row and column "
                    "positions, not values, so the log pass skips them. Colouring on a log scale is Display ▾ → Log "
                    "scale, which acts on the norm."
                ),
                "point_marks": (
                    "There are no markers in the grid — no line carries one and no collection is tagged "
                    "``_aw_marker_collection``."
                ),
                "scatter_density": (
                    "Density colouring and gates belong to the per-cell scatter alone."
                ),
                "threshold_line": (
                    "The renderer never reads ``show_threshold_line``."
                ),
                "value_labels": (
                    "The renderer never reads ``show_value_labels``: the number in a cell is drawn by the heat map "
                    "itself and formatted by Cell values."
                ),
            },
        ),
    }
