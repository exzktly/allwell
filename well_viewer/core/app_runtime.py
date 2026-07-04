"""Per-feature runtime namespaces (A8 teardown, workstream T2).

Features stash *live, non-persisted* objects on the app during a session —
worker bridges, decoded image arrays, popup figures/canvases, extractors, cached
geometry. These are not typed persistent state (they are never serialized), so
they don't belong in ``AppState``; but leaving them as raw god-object
reach-throughs keeps the coupling alive.

``AppRuntime`` gives each feature a small namespace (``ctx.runtime.<feature>``)
for those objects. Assigning through it **stamps the value onto the app** as the
legacy ``app._<feature>_<name>`` attribute, exactly like ``WidgetRegistry`` does
for widgets — so the many existing ``app._<feature>_<name>`` readers keep
resolving unchanged while the *builders* stop naming the god-object. As readers
migrate onto ``ctx.runtime`` in later steps, the stamp is eventually dropped.

Deliberately NOT serialized: ``persistence/`` must never touch ``AppRuntime``
(enforced by ``tests/test_app_runtime.py``).
"""

from __future__ import annotations


class _FeatureRuntime:
    """One feature's live-object namespace, backed by ``app._<prefix><name>``."""

    __slots__ = ("_app", "_prefix")

    def __init__(self, app, prefix: str) -> None:
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "_prefix", prefix)

    def __setattr__(self, name: str, value) -> None:
        # Stamp onto the app so legacy ``app._<prefix><name>`` readers resolve.
        setattr(self._app, f"{self._prefix}{name}", value)

    def __getattr__(self, name: str):
        # Only reached for names not in __slots__ — i.e. the runtime fields.
        return getattr(self._app, f"{self._prefix}{name}", None)


class AppRuntime:
    """Container of per-feature runtime namespaces, exposed as ``ctx.runtime``.

    Add a feature namespace here when its builder starts writing live objects
    through ``ctx.runtime.<feature>`` instead of the god-object ``_<feature>_*``.
    """

    __slots__ = ("_app", "smfish", "scatter", "cell_browser", "image_browser",
                 "image_table", "heatmap", "barplot", "cell_gating", "multivariate",
                 "batch_export", "saved_plots", "split", "fov", "sem", "aggregate",
                 "review_image", "review", "stats", "plot_groups", "export",
                 "centre")

    def __init__(self, app) -> None:
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "smfish", _FeatureRuntime(app, "_smfish_"))
        # Scatter gate-interaction live objects (RectangleSelector, in-progress
        # edit, per-panel render context, redraw callback, draw-arm flag). Gate
        # *geometry* (``_scatter_gates``) is persisted and stays typed state.
        object.__setattr__(self, "scatter", _FeatureRuntime(app, "_scatter_"))
        # Live objects only — persisted fields (edits dicts, choices, selections)
        # stay on the app / typed state; see each feature's migration notes.
        object.__setattr__(self, "cell_browser", _FeatureRuntime(app, "_cell_browser_"))
        object.__setattr__(self, "image_browser", _FeatureRuntime(app, "_image_browser_"))
        # Image-table grid: live cell list, per-row LUTs, crop tool, grid widgets.
        object.__setattr__(self, "image_table", _FeatureRuntime(app, "_image_table_"))
        object.__setattr__(self, "heatmap", _FeatureRuntime(app, "_heatmap_"))
        object.__setattr__(self, "barplot", _FeatureRuntime(app, "_bar_"))
        object.__setattr__(self, "cell_gating", _FeatureRuntime(app, "_cell_gating_"))
        object.__setattr__(self, "multivariate", _FeatureRuntime(app, "_mv_"))
        # Live callback closures a builder stamps for other layers to invoke
        # (set-mode, repopulate, refreshers). Persistence calls some of these
        # through the stamp — the closures are live objects, not serialized data.
        object.__setattr__(self, "batch_export", _FeatureRuntime(app, "_batch_export_"))
        object.__setattr__(self, "saved_plots", _FeatureRuntime(app, "_saved_plots_"))
        # App-global split-view mode flag (transient session state, never
        # persisted): ``ctx.runtime.split.active`` <-> ``app._split_active``.
        # split_view.py still writes ``app._split_active`` directly; ctx-based
        # plot handlers read the flag here instead of naming the god-object.
        object.__setattr__(self, "split", _FeatureRuntime(app, "_split_"))
        # App-global per-FOV toggle button bank + observers (shared by the plot
        # tabs and batch-export panels): ``ctx.runtime.fov.btns`` <-> the
        # in-place-mutated ``app._fov_btns`` list, ``ctx.runtime.fov.observers``
        # <-> ``app._fov_observers``. Transient live widgets, never persisted.
        object.__setattr__(self, "fov", _FeatureRuntime(app, "_fov_"))
        # App-global SEM/SD error-band toggle bank + observers (shared by every
        # plot tab's Error Band control): ``ctx.runtime.sem.btns`` <->
        # ``app._sem_btns`` and ``ctx.runtime.sem.observers`` <->
        # ``app._sem_observers``. Live widgets, never persisted.
        object.__setattr__(self, "sem", _FeatureRuntime(app, "_sem_"))
        # Per-well / per-group aggregation delegates. Production ``WellViewerApp``
        # exposes neither (the DataFrame path is used); the pure-logic test apps
        # stamp ``app._aggregate_well`` / ``app._aggregate_group`` to serve
        # precomputed AggPoints. ``ctx.runtime.aggregate.well`` / ``.group``
        # resolve those callables (or ``None``) so the stats leaf threads the
        # delegate through ``ctx`` instead of naming the god-object.
        object.__setattr__(self, "aggregate", _FeatureRuntime(app, "_aggregate_"))
        # Review-image pixmap scratch: the decoded base RGB buffer, the QLabel
        # host + QScrollArea canvas widgets, and the cached display scale — live
        # objects the pixmap-service leaves (``render_review_image_display`` /
        # ``zoom_review_image_to_selected_nucleus``) read/write. ``prefix``
        # ``_review_image_`` maps ``ctx.runtime.review_image.base_rgb`` <->
        # ``app._review_image_base_rgb`` (and ``.label`` / ``.canvas`` / ``.scale``)
        # so the ctx-native leaves reach them without the god-object handle.
        object.__setattr__(self, "review_image", _FeatureRuntime(app, "_review_image_"))
        # Review-CSV live scratch (prefix ``_review_``): the table nav-index
        # (``ctx.runtime.review.csv_row_index`` <-> ``app._review_csv_row_index``),
        # the render signature, the last lookup context, and the manual
        # cell-inclusion override map (``.included_overrides`` <->
        # ``app._review_included_overrides``). These are read/written as raw app
        # scratch by the CSV populate + cell-click navigation; the runtime alias
        # lets the ctx-native table-build leaf reach them without the god-object
        # handle. (The override map is still autosaved by
        # ``persistence.cell_overrides`` via the stamped app attr, not through
        # this namespace — persistence never imports AppRuntime.)
        object.__setattr__(self, "review", _FeatureRuntime(app, "_review_"))
        # Stats-tab live callbacks the results-panel builder wires for other
        # layers to invoke: ``ctx.runtime.stats.run`` <-> ``app._stats_run`` and
        # ``ctx.runtime.stats.property_change`` <-> ``app._stats_property_change``.
        # Bound-method handles (live objects), never serialized — resolved through
        # the ``_stats_`` prefix so the ctx-native builder names no god-object.
        object.__setattr__(self, "stats", _FeatureRuntime(app, "_stats_"))
        # Plot Groups quick-load drawers (a shared *core* sidebar widget, not a
        # removable feature): ``ctx.runtime.plot_groups.drawers`` <->
        # ``app._plot_groups_drawers`` — the list of registered drawer dicts that
        # ``refresh_plot_groups_sidebar`` repaints in lock-step. Lets the ctx-native
        # drawer builder + refresh reach the registry list without the god-object
        # handle. The per-drawer Qt widgets go through ``ctx.widgets``.
        object.__setattr__(self, "plot_groups", _FeatureRuntime(app, "_plot_groups_"))
        # Export-style session scratch (prefix ``_export_style_``): the custom
        # export-profile dict (``ctx.runtime.export.custom_profiles`` <->
        # ``app._export_style_custom_profiles``). Session-only, never persisted —
        # lets the export + batch-export panels list/apply profiles ctx-natively.
        object.__setattr__(self, "export", _FeatureRuntime(app, "_export_style_"))
        # Centre-notebook lazy build callback (prefix ``_centre_``): the
        # ``ctx.runtime.centre.build_pending`` closure <-> ``app._centre_build_pending``,
        # stamped once by ``centre_view`` to force-flip the centre stack to a
        # named tab's page and build it on demand. A live callback (like the
        # other stamped closures here), never persisted — lets the lazy-tab
        # force-build (persistence load + the ThreshFracOn quick-edit both need
        # it) resolve through ctx instead of the god-object handle.
        object.__setattr__(self, "centre", _FeatureRuntime(app, "_centre_"))
