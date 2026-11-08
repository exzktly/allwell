"""The Properties sidebar's vocabulary of *style facets*, and a figure's declaration of which it has.

A facet is one thing a Properties control can change on a figure — a legend, the bar renderer's show mode, a time
axis the rescale reaches. The sidebar builds a control only where the figure it is bound to has the facet that
control changes, so a plot is offered what it can honour and nothing else.

Every figure the panel opens on declares itself **totally**: each facet in :data:`FACETS` is either in ``has`` or in
``lacks``, and every absence carries the reason it is absent. Totality is the point. A declaration that listed only
exceptions would show a newly added control on every figure by default, which is the condition this module exists to
end; a total one makes adding a facet fail the declaration guard on every figure until each has answered for it
(``tests/test_every_properties_figure_declares_its_facets.py``).

A figure that draws several views on one canvas (the Nanowells page) may also name facets it has in some views and
not others (``views``). The panel builds those controls and hides them while the figure shows a view without the
facet, so a setting made in one view is kept, not discarded, when the user moves to another.

The declarations live beside the renderers that honour them, in ``review/features/<feature>/_style_facets.py``, and
reach the panel through the owning ``FeatureDescriptor.style_facets`` — a feature dropped by a build profile takes
its declaration with it. Qt-free and import-free above the standard library, like the rest of ``review/domain``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: Every facet, with the Properties rows it gates. The sidebar names one of these for every row it builds, and the
#: guard holds both directions: no row outside the vocabulary, no word in it that gates nothing.
FACETS: dict[str, str] = {
    "profile": "Profile & Format — the export profile",
    "typography": "Axes — the axis target, label / tick / title sizes and the tick marks",
    "scatter_density": "Density and Gates — the per-cell scatter's density colouring and its gates",
    "cell_values": "Cell values — the number format printed in each heat-map cell",
    "colour_mode": "Colours — how the plot assigns its series colours (PlotState)",
    "bar_geometry": "Bars — bar width and group spacing (PlotState)",
    "beeswarm": "Beeswarm — the point spacing of a beeswarm (PlotState)",
    "group_labels": "Axes → Group label size — the bar plot's timepoint group labels",
    "error_bars": "Statistics → Error bars and Across — the spread drawn and what it is taken over (PlotState)",
    "error_kinds": "Statistics → Error bars' None and 95% CI choices, read through ``error_bar_settings`` (PlotState)",
    "bar_show_mode": "Statistics → Show — Mean / Mean+spread / All points, read by the bar renderer (PlotState)",
    "legend": "Legend — show, font size, location of an axes legend",
    "threshold_line": "Overlays → Show threshold line (PlotState)",
    "value_labels": "Overlays → Show value labels and their format (PlotState)",
    "time_axis": "Time — the interval per timepoint and the offset (PlotState)",
    "line_marks": "Lines & Markers → Line style and line width",
    "point_marks": "Lines & Markers → Marker style, size and edge",
    "grid": "Grid — show, opacity, line style",
    "axis_limits": "Limits & Scale → X / Y limits and Autoscale",
    "colour_limits": "Limits & Scale → Colour limits of a colour bar the style pass may set",
    "log_axes": "Limits & Scale → Log scale and log base",
    "layout_engine": "Layout → Tight / Constrained",
    "draw_order": "Layout → the replicate-set and well draw-order lists",
}

#: The facets whose controls write ``PlotState`` rather than this figure's own preferences: one value for the whole
#: app, read by every plot that reads the field. A section built for one of them says so in the panel, because a
#: change there reaches other plots than the one the panel is open on.
SHARED: frozenset[str] = frozenset({
    "colour_mode", "bar_geometry", "beeswarm", "error_bars", "error_kinds", "bar_show_mode",
    "threshold_line", "value_labels", "time_axis",
})

#: The facets every figure the panel opens on has (typed axis limits are not among them: the Heat Map's axes are
#: row and column positions, which a limit can only crop): they style the axes themselves, which every figure draws. A
#: declaration writes ``UNIVERSAL | {...}`` rather than repeating them, and a facet added to :data:`FACETS` is not
#: in here unless somebody decided it is — so it still has to be answered for on every figure.
UNIVERSAL: frozenset[str] = frozenset({
    "profile", "typography", "grid", "layout_engine",
})


@dataclass(frozen=True)
class FigureFacets:
    """What one figure has.

    ``has`` — the facets the figure has in at least one of its views. ``lacks`` — every other facet, each with the
    reason. ``views`` — for a figure that draws several views: ``{view: {facet: reason}}``, the facets of ``has``
    that the named view does not draw. A view absent from ``views`` has every facet of ``has``. ``default_view`` is
    the view assumed before the renderer has stamped one on the figure.
    """

    has: frozenset[str]
    lacks: Mapping[str, str] = field(default_factory=dict)
    views: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    default_view: str = ""

    def live(self, view: str | None = None) -> frozenset[str]:
        """The facets the figure draws in *view* (its ``default_view`` when *view* is ``None`` or empty)."""
        absent = self.views.get(view or self.default_view, {})
        return frozenset(self.has - set(absent))

    def problems(self) -> list[str]:
        """Every way this declaration breaks the contract, as sentences; empty when it holds."""
        out: list[str] = []
        unknown = (set(self.has) | set(self.lacks)) - set(FACETS)
        if unknown:
            out.append(f"names facets the vocabulary does not have: {sorted(unknown)}")
        both = set(self.has) & set(self.lacks)
        if both:
            out.append(f"both has and lacks {sorted(both)}")
        missing = set(FACETS) - set(self.has) - set(self.lacks)
        if missing:
            out.append(f"does not answer for {sorted(missing)}")
        for facet, reason in self.lacks.items():
            if not str(reason).strip():
                out.append(f"lacks {facet!r} without a reason")
        for view, absent in self.views.items():
            stray = set(absent) - set(self.has)
            if stray:
                out.append(f"view {view!r} withholds facets the figure never has: {sorted(stray)}")
            for facet, reason in absent.items():
                if not str(reason).strip():
                    out.append(f"view {view!r} withholds {facet!r} without a reason")
        return out
