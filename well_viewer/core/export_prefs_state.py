"""Export/figure-style preferences — typed state.

The publication-style prefs (fonts, line/marker, grid, legend, …) and the
per-figure *view* prefs (axis limits, log scaling, per-axis bookkeeping) used to
sit on the god-object as the loose ``WellViewerApp._export_style_prefs`` and
``_export_axis_prefs`` dicts, lazily created on first access and persisted by
``persistence/view_state.py``.

Moving them into a typed slice (A8 teardown) removes two loose god-object
attributes and lets the export-style application path read prefs from
``ctx.state`` — a prerequisite for the plot renderers to drop their
``apply_export_style_to_current`` god-object residual.

``style_prefs`` stays ``None`` until first seeded (by
``plot_style._ensure_export_style_prefs``, which fills it from
``DEFAULT_EXPORT_STYLE_PREFS``) so the save path can tell "never touched" from
"explicitly emptied" exactly as the old ``getattr(app, …, None)`` did.
``axis_prefs`` is a ``{fig_key: {pref: value}}`` per-figure store. It holds the
mark styling (line width/style, marker size/style/edge width) as well as the
axis view — see ``plot_style.FIGURE_SCOPED_PREF_KEYS``; the name predates the
mark keys joining it.

Qt-free: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExportPrefsState:
    """Global publication-style prefs + the per-figure axis-view store."""

    style_prefs: dict | None = None
    axis_prefs: dict = field(default_factory=dict)

    def reset_for_new_dataset(self) -> None:
        """Drop the per-figure axis *limits*, keep everything else.

        The dividing rule is the same one the load path applies everywhere:
        *which data* versus *how I like to look at it*. An explicit
        ``y_lim_max`` is in the units of the plate that was open, and
        ``apply_export_style_prefs`` re-applies it on every redraw — so it
        follows the next plate around looking exactly like a plot that has
        decided to clip itself.

        A log scale and the mark styling (line width, marker size and their
        kin) are the other half: they are how the user likes a figure to read,
        they carry no plate-specific units, and `plot_style`'s own comment
        calls mark size a property of what the plot *draws*. Clearing the whole
        of ``axis_prefs`` took those with it and silently reset a user's mark
        styling on every dataset switch. ``style_prefs`` — the publication look
        — was never in scope.
        """
        from well_viewer.core.plot_style import AXIS_LIMIT_PREF_KEYS

        for fig_prefs in self.axis_prefs.values():
            if isinstance(fig_prefs, dict):
                for key in AXIS_LIMIT_PREF_KEYS:
                    fig_prefs.pop(key, None)
        # Drop any figure whose entry is now empty, so the store does not grow
        # a husk per figure per dataset.
        self.axis_prefs = {k: v for k, v in self.axis_prefs.items() if v}
