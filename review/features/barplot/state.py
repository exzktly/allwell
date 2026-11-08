"""Bar-plot tab domain state.

Per-feature state for the Bar Plots tab — the multi-timepoint selection
dict backing the *Select Timepoints* popup, mirroring the
``features/stats/state.py`` / ``features/review_image/state.py``
pattern.

Cross-tab bar options (``use_sem``, ``use_fov_replicates``, ``bar_order``,
``bar_group_mode``, ``bar_pool_replicates``) continue to live on
``PlotState`` in ``review/core/plot_state.py`` because they are
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

    #: What the last decoration pass had to give up, or ``""``. Written by
    #: ``_decorate.decorate_bar_items`` and drawn on the figure by the renderer.
    #:
    #: Carried rather than recomputed: the notice is about the bands the split
    #: *replaced*, and by the time the renderer runs those are gone. It lives on
    #: the figure and not on the status line for the reason the significance
    #: note does — the persistent bar keeps a sentence after the redraw that
    #: resolved it, and a note on the axes is cleared by that same redraw.
    split_notice: str = ""

    # Per-cell overlay mode (A8 state migration: moved off the god-object
    # ``app._bar_swarm`` / ``app._bar_violin`` attributes). Mutually exclusive —
    # the controller's toggles clear the other. Bar-tab-local (only the bar
    # renderer's per-cell path reads them), so they belong here, not on core
    # PlotState.
    swarm: bool = False
    violin: bool = False

    # *Then relative to* — the second fold-change stage (``_baseline_ratio``).
    # Each bar is divided by its baseline bar: the bar that agrees with it on
    # every ``rel_within`` variable and holds ``rel_value_id`` on
    # ``rel_variable_id``. Off while either id is empty. Bar-only on purpose:
    # a field on the shared ``FoldChangeState`` would reach the Line and
    # Response collectors, which do not apply it, and those tabs would then
    # draw stage 1 under a setting that reads as applied. Stored as the rule
    # (stable ids), not as a bar-to-bar map, so it is recomputed on every
    # redraw and cannot go stale when the plate is re-annotated.
    rel_variable_id: str = ""
    rel_value_id: str = ""
    rel_within: list[str] = field(default_factory=list)
    #: What the last stage-2 pass had to say — the test it ran, and every bar it
    #: could not compute and why. Written by ``_baseline_ratio`` and drawn on the
    #: figure, never the status line (see ``split_notice``).
    rel_notice: str = ""

    def relative_to_on(self) -> bool:
        return bool(self.rel_variable_id and self.rel_value_id)
