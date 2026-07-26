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
