"""Per-feature runtime namespaces (A8 teardown, workstream T2 / capstone T6).

Features stash *live, non-persisted* objects during a session — worker bridges,
decoded image arrays, popup figures/canvases, extractors, cached geometry. These
are not typed persistent state (they are never serialized), so they don't belong
in ``AppState``; but leaving them as raw god-object reach-throughs keeps the
coupling alive.

``AppRuntime`` gives each feature a small namespace (``ctx.runtime.<feature>``)
for those objects, backed by a plain ``name -> object`` **attribute store** dict.
Assigning ``ctx.runtime.<feature>.<name> = v`` writes the key
``_<feature>_<name>`` into that dict; reading resolves it back. No god-object
reference is held (A8 T6 step 2): earlier phases stamped the value onto the app
as ``app._<feature>_<name>`` so legacy readers resolved, but every runtime reader
has since migrated onto ``ctx.runtime`` and the app stamp is **dropped** — the
holder now owns only its dict.

**Shared store with ``WidgetRegistry``.** In the real app the same dict backs the
widget registry (``WidgetRegistry(app, store=...)``), so a widget registered as
``_<feature>_<name>`` remains readable through ``ctx.runtime.<feature>.<name>``
(and vice-versa) exactly as when both stamped one ``app.__dict__``. The dict is
plain session storage, not a handle back to ``WellViewerApp`` — the coupling is
removed, not relocated. ``WidgetRegistry`` still stamps its handles onto the app
for the residual direct ``app._<widget>`` readers; that is its own concern.

Deliberately NOT serialized: ``persistence/`` must never touch ``AppRuntime``
(enforced by ``tests/test_app_runtime.py``).
"""

from __future__ import annotations


class _AppAttrStore:
    """A dict-like *view* over an object's attributes (``get`` → ``getattr``,
    ``[k]=v`` → ``setattr``).

    Backs ``AppRuntime`` only when it is constructed with an app and **no**
    explicit store — the legacy / test call path. It reproduces the pre-T6
    ``app._<feature>_<name>`` stamp semantics exactly, including class-level
    methods and ``@property`` delegates that a plain ``app.__dict__`` would miss
    (e.g. a duck-typed ``MockApp``'s ``_aggregate_well`` method). The real app
    never uses this — it injects a standalone dict shared with the
    ``WidgetRegistry``, so no god-object attribute is reachable through the
    runtime in production.
    """

    __slots__ = ("_app",)

    def __init__(self, app) -> None:
        self._app = app

    def get(self, key, default=None):
        return getattr(self._app, key, default)

    def __setitem__(self, key, value) -> None:
        setattr(self._app, key, value)


class _FeatureRuntime:
    """One feature's live-object namespace, backed by a shared attribute store.

    Reads/writes route through ``store[f"{prefix}{name}"]`` — the same key
    convention ``WidgetRegistry`` uses — so runtime fields and same-named widget
    handles resolve through one dict.
    """

    __slots__ = ("_store", "_prefix")

    def __init__(self, store: dict, prefix: str) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_prefix", prefix)

    def __setattr__(self, name: str, value) -> None:
        self._store[f"{self._prefix}{name}"] = value

    def __getattr__(self, name: str):
        # Only reached for names not in __slots__ — i.e. the runtime fields.
        return self._store.get(f"{self._prefix}{name}")


class AppRuntime:
    """Container of per-feature runtime namespaces, exposed as ``ctx.runtime``.

    Add a feature namespace here when its builder starts writing live objects
    through ``ctx.runtime.<feature>`` instead of the god-object ``_<feature>_*``.

    Backed by an attribute-store dict. **The real app passes ``store=``** — a
    standalone dict shared with the ``WidgetRegistry`` — so the holder owns only
    that dict and reaches no god-object attribute. When called with just an app
    and no ``store`` (legacy / test call sites), the store defaults to a
    ``_AppAttrStore`` view over the app so the pre-T6 stamp semantics survive
    unchanged: a runtime write lands as ``app._<feature>_<name>`` and a widget
    the registry stamped on the app (or a class-level delegate) resolves through
    the namespace. Production never takes that path.
    """

    __slots__ = ("_store", "smfish", "scatter", "cell_browser", "image_browser",
                 "image_table", "heatmap", "barplot", "cell_gating", "multivariate",
                 "batch_export", "saved_plots", "split", "fov", "sem", "aggregate",
                 "review_image", "review", "stats", "plot_groups", "export",
                 "centre", "view_state", "tab", "grouping", "playground")

    def __init__(self, app=None, store: dict | None = None) -> None:
        if store is None:
            # Legacy / test path: back the namespaces with a view over the app's
            # attributes so a runtime write is visible as ``app._<feature>_<name>``
            # and registry-stamped widgets (and class-level delegates) resolve
            # via ``getattr``. The real app injects a standalone ``store`` and
            # never lands here.
            store = _AppAttrStore(app) if app is not None else {}
        s = store
        object.__setattr__(self, "_store", s)
        object.__setattr__(self, "smfish", _FeatureRuntime(s, "_smfish_"))
        # Scatter gate-interaction live objects (RectangleSelector, in-progress
        # edit, per-panel render context, redraw callback, draw-arm flag). Gate
        # *geometry* (``_scatter_gates``) is persisted and stays typed state.
        object.__setattr__(self, "scatter", _FeatureRuntime(s, "_scatter_"))
        # Live objects only — persisted fields (edits dicts, choices, selections)
        # stay on the app / typed state; see each feature's migration notes.
        object.__setattr__(self, "cell_browser", _FeatureRuntime(s, "_cell_browser_"))
        object.__setattr__(self, "image_browser", _FeatureRuntime(s, "_image_browser_"))
        # Image-table grid: live cell list, per-row LUTs, crop tool, grid widgets.
        object.__setattr__(self, "image_table", _FeatureRuntime(s, "_image_table_"))
        object.__setattr__(self, "heatmap", _FeatureRuntime(s, "_heatmap_"))
        object.__setattr__(self, "barplot", _FeatureRuntime(s, "_bar_"))
        object.__setattr__(self, "cell_gating", _FeatureRuntime(s, "_cell_gating_"))
        object.__setattr__(self, "multivariate", _FeatureRuntime(s, "_mv_"))
        # Live callback closures a builder stamps for other layers to invoke
        # (set-mode, repopulate, refreshers). Persistence calls some of these
        # through the stamp — the closures are live objects, not serialized data.
        object.__setattr__(self, "batch_export", _FeatureRuntime(s, "_batch_export_"))
        object.__setattr__(self, "saved_plots", _FeatureRuntime(s, "_saved_plots_"))
        # App-global split-view mode flag (transient session state, never
        # persisted): ``ctx.runtime.split.active`` <-> ``app._split_active``.
        # Both sides go through this namespace now (A8 residual R5) — split_view
        # writes ``…runtime.split.active`` and ctx-based plot handlers read it —
        # so the flag's single home is the runtime, not a bare god-object attr.
        object.__setattr__(self, "split", _FeatureRuntime(s, "_split_"))
        # App-global per-FOV toggle button bank + observers (shared by the plot
        # tabs and batch-export panels): ``ctx.runtime.fov.btns`` <-> the
        # in-place-mutated ``app._fov_btns`` list, ``ctx.runtime.fov.observers``
        # <-> ``app._fov_observers``. Transient live widgets, never persisted.
        object.__setattr__(self, "fov", _FeatureRuntime(s, "_fov_"))
        # App-global SEM/SD error-band toggle bank + observers (shared by every
        # plot tab's Error Band control): ``ctx.runtime.sem.btns`` <->
        # ``app._sem_btns`` and ``ctx.runtime.sem.observers`` <->
        # ``app._sem_observers``. Live widgets, never persisted.
        object.__setattr__(self, "sem", _FeatureRuntime(s, "_sem_"))
        # Per-well / per-group aggregation delegates. Production ``WellViewerApp``
        # exposes neither (the DataFrame path is used); the pure-logic test apps
        # stamp ``app._aggregate_well`` / ``app._aggregate_group`` to serve
        # precomputed AggPoints. ``ctx.runtime.aggregate.well`` / ``.group``
        # resolve those callables (or ``None``) so the stats leaf threads the
        # delegate through ``ctx`` instead of naming the god-object.
        object.__setattr__(self, "aggregate", _FeatureRuntime(s, "_aggregate_"))
        # Review-image pixmap scratch: the decoded base RGB buffer, the QLabel
        # host + QScrollArea canvas widgets, and the cached display scale — live
        # objects the pixmap-service leaves (``render_review_image_display`` /
        # ``zoom_review_image_to_selected_nucleus``) read/write. ``prefix``
        # ``_review_image_`` maps ``ctx.runtime.review_image.base_rgb`` <->
        # ``app._review_image_base_rgb`` (and ``.label`` / ``.canvas`` / ``.scale``)
        # so the ctx-native leaves reach them without the god-object handle.
        object.__setattr__(self, "review_image", _FeatureRuntime(s, "_review_image_"))
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
        object.__setattr__(self, "review", _FeatureRuntime(s, "_review_"))
        # Stats-tab live callbacks the results-panel builder wires for other
        # layers to invoke: ``ctx.runtime.stats.run`` <-> ``app._stats_run`` and
        # ``ctx.runtime.stats.property_change`` <-> ``app._stats_property_change``.
        # Bound-method handles (live objects), never serialized — resolved through
        # the ``_stats_`` prefix so the ctx-native builder names no god-object.
        object.__setattr__(self, "stats", _FeatureRuntime(s, "_stats_"))
        # Plot Groups quick-load drawers (a shared *core* sidebar widget, not a
        # removable feature): ``ctx.runtime.plot_groups.drawers`` <->
        # ``app._plot_groups_drawers`` — the list of registered drawer dicts that
        # ``refresh_plot_groups_sidebar`` repaints in lock-step. Lets the ctx-native
        # drawer builder + refresh reach the registry list without the god-object
        # handle. The per-drawer Qt widgets go through ``ctx.widgets``.
        object.__setattr__(self, "plot_groups", _FeatureRuntime(s, "_plot_groups_"))
        # Export-style session scratch (prefix ``_export_style_``): the custom
        # export-profile dict (``ctx.runtime.export.custom_profiles`` <->
        # ``app._export_style_custom_profiles``). Session-only, never persisted —
        # lets the export + batch-export panels list/apply profiles ctx-natively.
        object.__setattr__(self, "export", _FeatureRuntime(s, "_export_style_"))
        # Centre-notebook lazy build callback (prefix ``_centre_``): the
        # ``ctx.runtime.centre.build_pending`` closure <-> ``app._centre_build_pending``,
        # stamped once by ``centre_view`` to force-flip the centre stack to a
        # named tab's page and build it on demand. A live callback (like the
        # other stamped closures here), never persisted — lets the lazy-tab
        # force-build (persistence load + the ThreshFracOn quick-edit both need
        # it) resolve through ctx instead of the god-object handle.
        object.__setattr__(self, "centre", _FeatureRuntime(s, "_centre_"))
        # View-state persistence scratch (prefix ``_view_state_``): the pending
        # per-tab restore payload (``ctx.runtime.view_state.pending`` <->
        # ``app._view_state_pending``) and the cross-dataset "absent" well/solo
        # sets stashed on restore so a subset-load-then-save doesn't prune wells
        # the current dataset simply doesn't contain (``.absent_wells`` /
        # ``.absent_solo``, audit X14). Session scratch, never itself serialized
        # through AppRuntime — snapshot/restore read/write it via this alias so
        # the persistence layer names no god-object.
        object.__setattr__(self, "view_state", _FeatureRuntime(s, "_view_state_"))
        # Tab-lifecycle transient scratch (prefix ``_tab_``): the on_tab_change
        # smoke-check's cross-switch selection-model identity cache
        # (``ctx.runtime.tab.selection_model_identity``). Debug-only, never
        # persisted; read exclusively by the tab controller.
        object.__setattr__(self, "tab", _FeatureRuntime(s, "_tab_"))
        # Sample-Definitions GROUPS-list transient scratch (prefix ``_grp_``): the
        # pending inline-rename row index (``ctx.runtime.grouping.inline_edit_idx``
        # <-> ``app._grp_inline_edit_idx``) the rename action sets and the panel
        # refresh consumes, and the Quick-Replicates dropdown state
        # (``ctx.runtime.grouping.pair_dir`` / ``.iter_order`` <-> ``app._grp_pair_dir``
        # / ``app._grp_iter_order``) the row/col-pair shims + the from-dropdowns
        # reader thread through. Session-only, never persisted.
        object.__setattr__(self, "grouping", _FeatureRuntime(s, "_grp_"))
        # Playground testbed transient scratch (prefix ``_playground_``): the
        # current frame identity key (``ctx.runtime.playground.frame_key`` <->
        # ``app._playground_frame_key``), the reference-field worker bridge
        # (``.ref_bridge``), and the pan-drag origin tuple (``.pan_drag``). Live
        # session objects the Playground controller reads/writes, never persisted.
        object.__setattr__(self, "playground", _FeatureRuntime(s, "_playground_"))
