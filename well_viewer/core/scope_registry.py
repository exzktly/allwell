"""Wire each feature's fold-change scope to its redraw callable.

The scope registry in ``domain/fold_change_scopes.py`` holds the dirty-set
+ dispatch plumbing but, as a domain module, must not import from
``features/`` itself. This module does that wiring during app
construction: it imports each feature's redraw entry-point and registers
the scope with it.

Called once from ``core/bus_wiring.wire_event_bus``.
"""

from __future__ import annotations


def register_default_scopes() -> None:
    """Register the bar + line fold-change scopes.

    Idempotent — re-registering replaces the prior descriptor, so calling
    this twice (e.g. in tests) is safe.
    """
    from well_viewer.domain.fold_change_scopes import (
        FoldChangeScope, register_fold_change_scope,
    )
    from well_viewer.features.barplot.renderer import redraw_bars
    from well_viewer.core.plot_orchestrator import redraw_app

    register_fold_change_scope(FoldChangeScope(
        name="bar",
        ctrl_combo_attr="_fc_ctrl_combo_bar",
        baseline_combo_attr="_fc_baseline_combo_bar",
        redraw=redraw_bars,
        tab_names=("Bar Plots",),
    ))
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
