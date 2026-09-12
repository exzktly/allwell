"""Wire each feature's fold-change scope to its redraw callable.

The scope registry in ``domain/fold_change_scopes.py`` holds the dirty-set
+ dispatch plumbing but, as a domain module, must not import from
``features/`` itself. This module does that wiring during app
construction: it imports each feature's redraw entry-point and registers
the scope with it.

Called once from ``core/bus_wiring.wire_event_bus``.
"""

from __future__ import annotations


def redraw_app_scope(a):
    """The redraw the line / distribution / heat-map scopes share.

    ``redraw_app`` is ctx-native (D-core), but the scope coalescer invokes
    ``scope.redraw(win)`` with the *win* (see ``fold_change_scopes._call_redraw``).
    This threads the win handle to its ctx — mirroring the bar scope's
    ``lambda a: redraw_bars(a._ctx)``. Passing the raw win made redraw_app read
    ``win.window`` (a Qt bound method, not the ctx window), which ``_call_redraw``
    then swallowed — silently dropping every line/distribution/heat-map redraw on
    channel-change, fold-change, and deferred tab-flush.

    Module-level rather than a closure inside :func:`register_default_scopes`,
    because a caller that has *already* run this redraw itself needs to be able
    to name it and skip the scopes that use it — see ``core/plot_toggles``.
    Identity is the whole point, so there is one function object, made once.

    The import stays function-local, which means the *module attribute* is
    resolved on every call rather than bound here at import. That is what
    ``tests/test_default_scopes`` pins: a monkeypatch of
    ``plot_orchestrator.redraw_app`` has to reach the scope, as it does for
    every other function-local import in this app. It also means this path and
    the module-attribute path are the same object, so a harness wrapping both
    counts one repaint twice — ``tests/test_redraw_fanout`` knows that and
    wraps only one of them.
    """
    from review.core.plot_orchestrator import redraw_app

    return redraw_app(a._ctx)


def register_default_scopes(win=None, features=None) -> None:
    """Register the fold-change redraw scopes.

    The *feature-owned* scopes (currently just ``bar``) are sourced from the
    feature registry's ``fold_change_scope_factory`` descriptors — so a feature
    removed from ``features.registry.FEATURES`` drops its scope automatically and
    this module imports no feature. The remaining scopes (line / distribution /
    heat map) use the core ``redraw_app`` default and stay inline.

    The registry comes from *win* (the app's own) or is passed as *features*; it
    is never built here. Building it would mean importing
    ``features.registry``, and a caller that has no registry to offer has no
    feature scopes to register either — an empty run is the honest answer, not a
    transient registry conjured to stand in for the app's.

    Idempotent — re-registering replaces the prior descriptor, so calling this
    twice (e.g. in tests) is safe.
    """
    from review.domain.fold_change_scopes import (
        FoldChangeScope, register_fold_change_scope,
    )

    feats = features
    if feats is None and win is not None:
        feats = getattr(win, "_features", None)
    for _desc in (feats.all() if feats is not None else ()):
        if _desc.fold_change_scope_factory is not None:
            register_fold_change_scope(_desc.fold_change_scope_factory())

    register_fold_change_scope(FoldChangeScope(
        name="line",
        ctrl_combo_attr="_fc_ctrl_combo_line",
        baseline_combo_attr="_fc_baseline_combo_line",
        redraw=redraw_app_scope,
        tab_names=("Line Graphs",),
    ))
    # Distribution and Heat Map aren't fold-change-aware (no Control/Baseline
    # combos — the attrs below resolve to None and the sync skips them), but
    # they ARE redraw scopes: without a registration a channel change with one
    # of these tabs visible would leave its data stale (the coalescer only
    # redraws registered scopes). They use ``redraw_app`` — the same default
    # redraw that already fans out across the line / distribution / heat-map
    # tabs — so no feature-specific import is needed here (keeps the deferred-
    # import budget intact). The dummy combo attrs keep them out of the
    # fold-change sync while wiring up the channel-change / tab-flush redraw.
    register_fold_change_scope(FoldChangeScope(
        name="distribution",
        ctrl_combo_attr="_fc_ctrl_combo_distribution",
        baseline_combo_attr="_fc_baseline_combo_distribution",
        redraw=redraw_app_scope,
        tab_names=("Distribution",),
    ))
    register_fold_change_scope(FoldChangeScope(
        name="heatmap",
        ctrl_combo_attr="_fc_ctrl_combo_heatmap",
        baseline_combo_attr="_fc_baseline_combo_heatmap",
        redraw=redraw_app_scope,
        tab_names=("Heat Map",),
    ))
