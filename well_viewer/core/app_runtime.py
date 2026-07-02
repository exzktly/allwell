"""Per-feature runtime namespaces (A8 teardown, workstream T2).

Features stash *live, non-persisted* objects on the app during a session —
worker bridges, decoded image arrays, popup figures/canvases, extractors, cached
geometry. These are not typed persistent state (they are never serialized), so
they don't belong in ``AppState``; but leaving them as raw ``ctx.app._<x>``
reach-throughs keeps the god-object coupling alive.

``AppRuntime`` gives each feature a small namespace (``ctx.runtime.<feature>``)
for those objects. Assigning through it **stamps the value onto the app** as the
legacy ``app._<feature>_<name>`` attribute, exactly like ``WidgetRegistry`` does
for widgets — so the many existing ``app._<feature>_<name>`` readers keep
resolving unchanged while the *builders* stop naming ``ctx.app``. As readers
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
    through ``ctx.runtime.<feature>`` instead of ``ctx.app._<feature>_*``.
    """

    __slots__ = ("_app", "smfish", "scatter")

    def __init__(self, app) -> None:
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "smfish", _FeatureRuntime(app, "_smfish_"))
        # Scatter gate-interaction live objects (RectangleSelector, in-progress
        # edit, per-panel render context, redraw callback, draw-arm flag). Gate
        # *geometry* (``_scatter_gates``) is persisted and stays typed state.
        object.__setattr__(self, "scatter", _FeatureRuntime(app, "_scatter_"))
