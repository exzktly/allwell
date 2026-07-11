"""The viewer's dependency-injection context.

``ViewerContext`` is the single typed handle that tab builders and controllers
receive instead of reaching into the ``WellViewerApp`` god-object. It bundles
the typed domain state, the event bus, and the widget registry.

**No ``app`` escape hatch (A8 teardown, T6).** Earlier phases carried a transitional
``app`` field — a raw reference to the ``WellViewerApp`` god-object that any
builder could reach an arbitrary method through. That generic escape hatch is
**gone**: the burn-down of raw ``ctx``-to-god-object references reached 0 and
this field was deleted. The god-object instance is now held privately
(``_window``) and is reachable only through the *closed, frozen* surface below:

- ``ctx.state`` / ``ctx.bus`` / ``ctx.widgets`` / ``ctx.runtime`` — typed state,
  event bus, widget registry, per-feature live-object namespaces.
- ``ctx.window`` — the ``WellViewerApp`` **qua QMainWindow**, for the handful of
  operations that genuinely need the widget (dialog parenting, ``isinstance``
  guards, ``findChildren`` walks). A widget need, not behaviour coupling.
- A small set of **named app/window-service accessors** (``ctx.set_status`` /
  ``ctx.persistence`` / ``ctx.rebuild_all`` / …) — each wraps one genuine
  ``WellViewerApp`` service that has no typed home *yet*. The method list below
  *is* the enumerable spec for the remaining god-object teardown: extracting each
  of these services into a standalone object (the epic's separate
  "decompose the god-methods" roadmap) is what finally lets ``_window`` itself
  narrow to a pure ``QMainWindow``.

Anti-gaming rule: these are a *minimal, closed* set — never a ``__getattr__``
passthrough, and never a public ``app`` alias back onto the god-object. Every
accessor is frozen by ``tests/test_ctx_service_accessors.py`` so adding one is a deliberate,
reviewed act; if the list grows past a handful, that is the signal to decompose
``WellViewerApp`` itself, not to add more.

Frozen: a context is wired once at construction and never reassigned.
"""

from __future__ import annotations

from dataclasses import dataclass

from well_viewer.core.app_state import AppState
from well_viewer.core.event_bus import EventBus
from well_viewer.core.widget_registry import WidgetRegistry


@dataclass(frozen=True)
class ViewerContext:
    """Typed dependency bundle injected into tab builders and controllers.

    Consumers read ``ctx.state.<domain>`` for typed state, ``ctx.bus`` for the
    event bus, ``ctx.widgets`` for widget handles, ``ctx.runtime`` for per-feature
    live objects, and the frozen named accessors below for the residual
    app/window-level services. There is no ``app`` field — the god-object is
    private (``_window``) and reachable only through those accessors.
    """

    state: AppState
    bus: EventBus
    widgets: WidgetRegistry
    runtime: object = None   # AppRuntime — per-feature live-object namespaces (T2)
    # The ``WellViewerApp`` instance, held ONLY as the ``QMainWindow`` that the
    # ``window`` accessor exposes and as the transitional host of the named
    # services below. Private: no code names ``ctx._window`` — features use the
    # frozen accessors. When the god-service decompositions land, each service
    # gets its own home and this handle narrows to (or is replaced by) a pure
    # window widget.
    _window: object = None

    def __post_init__(self) -> None:
        # Attach the row-access facade (B2) onto the state so callers reach it
        # as ``ctx.state.repo``. Done here — the DI bundle is the natural wiring
        # point and it needs the assembled context. Mutates ``state`` (not the
        # frozen ctx), and only when unset so a caller-provided repo is kept.
        if getattr(self.state, "repo", None) is None:
            from well_viewer.data.well_repository import WellRepository
            self.state.repo = WellRepository(self)

    # ── Named app/window-service accessors (A8, design "C") ──────────────────
    # Minimal, closed set — see the module docstring and the freeze test. Each
    # wraps one genuine WellViewerApp service so feature code names it instead of
    # reaching a generic escape hatch (which no longer exists).

    @property
    def window(self):
        """The top-level ``QMainWindow`` (the ``WellViewerApp`` *qua widget*).

        Names the god-object purely as the **Qt widget** for the handful of
        operations that legitimately need the main window and can never be
        god-object-free: dialog parenting (``QFileDialog``/``QMessageBox``),
        ``isinstance(..., QWidget)`` guards, and ``findChildren`` tree walks.
        Routing those through ``window`` keeps the ctx-app burn-down honest — a
        ``ctx.window`` call is a widget-parenting need, not behaviour coupling."""
        return self._window

    @property
    def persistence(self):
        """The app's persistence registry (``schedule_save`` / ``load`` per slice).

        A genuine app-level *service* (view-state autosave/load), not lazy
        behaviour coupling — features name it here instead of reaching a raw app
        persistence handle. Kept in the frozen accessor set."""
        return self._window._persistence

    @property
    def features(self):
        """The feature-descriptor registry (``{tab_name: FeatureDescriptor}``).

        A genuine app-level *catalogue service* — the app's own record of which
        tabs exist and their metadata (``single_well_selection`` / ``per_well_
        selection`` / lifecycle hooks). Controllers reason about tab behaviour
        through it (is this tab a single-well selector?) without naming the
        god-object; exposing it here keeps that access enumerable and frozen.
        Kept in the frozen accessor set."""
        return getattr(self._window, "_features", None)

    def rebuild_all(self) -> None:
        """Run the app's master redraw fan-out after a data/selection change.

        A genuine app-level *orchestration service* (invalidate stats cache →
        refresh sidebar map + Sample-Definitions groups → redraw bars/lines/
        scatter → per-tab follow-ups). It has no ctx form — it fans out into a
        dozen god-object methods — so callers name it here. Kept in the frozen
        accessor set; one of the irreducible teardown-endgame services."""
        self._window._rebuild_all()

    def refresh_sidebar_map(self) -> None:
        """Repaint the line-graph well-picker's rep-colour map (debounced).

        A genuine app-level *orchestration service* — the debounced sidebar-map
        refresh every selection/group edit triggers. No ctx form (it schedules a
        QTimer that repaints registered plate widgets), so callers name it here.
        Kept in the frozen accessor set."""
        fn = getattr(self._window, "_refresh_sidebar_map", None)
        if callable(fn):
            fn()

    def set_status(self, message: str) -> None:
        """Show *message* in the main-window status bar (no-op if unavailable)."""
        fn = getattr(self._window, "_set_status", None)
        if callable(fn):
            fn(message)

    def toast(self, message: str, *, kind: str = "info", msec: int = 3500) -> None:
        """Show a transient notification toast (no-op if unavailable).

        A genuine window-shell *notification service*, distinct from the status
        bar (``set_status``): a self-dismissing popup announcing an action's
        outcome (import/export success, a failure). It has no typed-state home —
        it's a Qt overlay on the main window — so callers name it here. Kept in
        the frozen accessor set."""
        fn = getattr(self._window, "_toast", None)
        if callable(fn):
            fn(message, kind=kind, msec=msec)

    def save_figure_dialog(self, fig, default_name: str) -> None:
        """Open the app's Save-As dialog for *fig*, honouring the shared file-type
        filter and the active dataset directory."""
        self._window._save_matplotlib_fig(fig, default_name)

    def open_export_style_panel(self, plot_key: str) -> None:
        """Open the figure-properties (export-style) side panel for *plot_key*."""
        self._window._open_export_style_panel(plot_key)
