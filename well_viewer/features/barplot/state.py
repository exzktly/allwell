"""Bar-plot tab domain state.

Per-feature state for the Bar Plots tab — the multi-timepoint selection
dict backing the *Select Timepoints* popup, mirroring the
``features/stats/state.py`` / ``features/review_image/state.py``
pattern.

Cross-tab bar options (``use_sem``, ``use_fov_replicates``, ``bar_order``,
``bar_group_mode``, ``bar_pool_replicates``) continue to live on
``PlotState`` in ``well_viewer/core/plot_state.py`` because they are
plot-options the line plot / batch export also read.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BarPlotState:
    """Bar-plot feature-local state.

    ``tp_selections`` — the dict backing the *Select Timepoints* popup
    checkbox menu. Keys are formatted timepoint strings (e.g. "0", "24")
    and values are booleans. The dict is the authoritative source for
    what the bar tab plots; an empty selection (no keys ``True``) means
    "show nothing" even if the hidden ``_bar_tp_cb`` combo still carries
    a stale value.
    """

    tp_selections: dict[str, bool] = field(default_factory=dict)

    # Per-cell overlay mode (A8 state migration: moved off the god-object
    # ``app._bar_swarm`` / ``app._bar_violin`` attributes). Mutually exclusive —
    # the controller's toggles clear the other. Bar-tab-local (only the bar
    # renderer's per-cell path reads them), so they belong here, not on core
    # PlotState.
    swarm: bool = False
    violin: bool = False
