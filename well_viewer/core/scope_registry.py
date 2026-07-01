"""Wire each feature's fold-change scope to its redraw callable.

The scope registry in ``domain/fold_change_scopes.py`` holds the dirty-set
+ dispatch plumbing but, as a domain module, must not import from
``features/`` itself. This module does that wiring during app
construction: it imports each feature's redraw entry-point and registers
the scope with it.

Called once from ``core/bus_wiring.wire_event_bus``.
"""

from __future__ import annotations


def register_default_scopes(app=None) -> None:
    """Register the fold-change redraw scopes.

    The *feature-owned* scopes (currently just ``bar``) are sourced from the
    feature registry's ``fold_change_scope_factory`` descriptors — so a feature
    removed from ``features.registry.FEATURES`` drops its scope automatically and
    this module imports no feature. The remaining scopes (line / distribution /
    heat map) use the core ``redraw_app`` default and stay inline.

    Idempotent — re-registering replaces the prior descriptor, so calling this
    twice (e.g. in tests) is safe.
    """
    from well_viewer.domain.fold_change_scopes import (
        FoldChangeScope, register_fold_change_scope,
    )
    from well_viewer.core.plot_orchestrator import redraw_app

    # Feature-contributed scopes (e.g. bar). Resolve from the app's registry
    # when available; otherwise build a transient one so direct callers (tests)
    # still get the feature scopes registered.
    feats = getattr(app, "_features", None) if app is not None else None
    if feats is None:
        from well_viewer.features.registry import build_feature_registry
        feats = build_feature_registry()
    for _desc in feats.all():
        if _desc.fold_change_scope_factory is not None:
            register_fold_change_scope(_desc.fold_change_scope_factory())

    register_fold_change_scope(FoldChangeScope(
        name="line",
        ctrl_combo_attr="_fc_ctrl_combo_line",
        baseline_combo_attr="_fc_baseline_combo_line",
        redraw=redraw_app,
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
        redraw=redraw_app,
        tab_names=("Distribution",),
    ))
    register_fold_change_scope(FoldChangeScope(
        name="heatmap",
        ctrl_combo_attr="_fc_ctrl_combo_heatmap",
        baseline_combo_attr="_fc_baseline_combo_heatmap",
        redraw=redraw_app,
        tab_names=("Heat Map",),
    ))
