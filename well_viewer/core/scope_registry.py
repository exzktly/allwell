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
