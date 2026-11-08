"""Which Properties controls the Scatter Plot figures can honour (``review/domain/style_facets.py``).

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
        "_scatter_fig": FigureFacets(
            has=UNIVERSAL | {"axis_limits", "scatter_density", "legend", "value_labels", "point_marks", "log_axes", "draw_order"},
            lacks={
                "error_kinds": (
                    "No Error bars row here: nothing on this figure draws a spread."
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
                "error_bars": (
                    "One point is one cell: ``collect_scatter_data`` reduces nothing, so there is no spread for the "
                    "Statistics rows to choose (only the aggregate collector reads ``use_sem``)."
                ),
                "group_labels": (
                    "``group_label_size`` sizes the bar plot's timepoint group labels (``barplot/renderer.py``); "
                    "nothing here reads it."
                ),
                "line_marks": (
                    "Every point is a markers-only line tagged ``_scatter_only_artist`` (``scatter/gating.py``), which "
                    "the style pass never gives a line style, and a markers-only line has no line width to draw."
                ),
                "threshold_line": (
                    "``show_threshold_line`` is read by the Line (CDF), Bar (Fraction) and Distribution renderers; not "
                    "by this one."
                ),
                "time_axis": (
                    "This feature never reads the time rescale (``review.domain.time_rescale``), so Interval / Offset "
                    "change nothing here."
                ),
            },
        ),
        "_scatter_agg_fig": FigureFacets(
            has=UNIVERSAL | {"axis_limits", "legend", "error_bars", "value_labels", "point_marks", "log_axes", "draw_order"},
            lacks={
                "error_kinds": (
                    "This figure's spread is read off ``use_sem`` alone, so it draws SEM or SD: offered None or 95% CI "
                    "it would draw SEM under a label saying otherwise."
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
                "group_labels": (
                    "``group_label_size`` sizes the bar plot's timepoint group labels (``barplot/renderer.py``); "
                    "nothing here reads it."
                ),
                "line_marks": (
                    "Each well is one ``errorbar`` drawn ``linestyle='none'``; its caps are skipped by the style pass "
                    "and its whiskers are a LineCollection the line pass never reaches, so Line style and Line width "
                    "change nothing."
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
            },
        ),
    }
