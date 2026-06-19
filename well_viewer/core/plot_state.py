"""Plot-options domain state.

The cross-tab plotting options: the SEM/SD error-band toggle, the per-FOV
replicate toggle, per-panel legend visibility, the user's drag-reorder for the
Bar and Line tabs, and the fold-change normalization state.

``fc_dirty_scopes`` is the dirty-set that ``well_viewer/fold_change_scopes.py``
manages — holding it here means that "redraw the visible tab now, defer the
rest" machinery composes PlotState rather than stashing a loose attribute on
the app.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_legend() -> dict[str, bool]:
    return {"mean": True, "frac": True, "cdf": True}


@dataclass
class PlotState:
    """Cross-tab plot options + fold-change normalization state.

    ``fc_state`` is the ``(vs_control, control_label, vs_t0)`` tuple the
    collection pipeline (``collect_bar_items`` / ``collect_line_series``)
    consumes — :meth:`set_fold_change` is the single place that triple is
    mutated.
    """

    use_sem: bool = True
    use_fov_replicates: bool = False
    legend_visible: dict[str, bool] = field(default_factory=_default_legend)

    bar_order: list | None = None
    line_order_rsets: list[str] = field(default_factory=list)
    line_order_wells: list[str] = field(default_factory=list)

    # Bar tab multi-timepoint layout. ``bar_group_mode`` controls whether the
    # x-axis clusters by well ("well": one cluster per well/rset, timepoints
    # inside) or by timepoint ("timepoint": one cluster per tp, wells inside).
    # ``bar_pool_replicates`` controls whether wells inside a ReplicateSet are
    # collapsed into a single column (legacy grouped-mode) or expanded into
    # one column per well (still coloured by the rset's rank colour).
    bar_group_mode: str = "well"
    bar_pool_replicates: bool = True
    # Violin / beeswarm only. When True (and ≥1 replicate set is active),
    # each rset's wells are pooled into one per-cell column instead of one
    # column per well. Independent of ``use_fov_replicates`` (which controls
    # the SD/SEM source on the bar / n_above panels).
    bar_pool_rset_cells: bool = False

    fc_vs_control_on: bool = False
    fc_control_label: str = ""
    fc_vs_t0_on: bool = False
    # User-chosen baseline timepoint (the dataset's tp string, e.g. ``"24"``).
    # Empty when the user wants the **earliest** finite-mean point per member
    # (the original ``vs t0`` behaviour), so backward-compat with saved state
    # that only carries ``fc_vs_t0_on=True`` is preserved.
    fc_baseline_tp: str = ""
    fc_dirty_scopes: set[str] = field(default_factory=set)

    # ── fold change ──────────────────────────────────────────────────────────

    @property
    def fc_state(self) -> tuple[bool, str, bool]:
        """The fold-change tuple consumed by the bar / line collection pipeline."""
        return (self.fc_vs_control_on, self.fc_control_label, self.fc_vs_t0_on)

    @property
    def fold_change_active(self) -> bool:
        """True when either fold-change mode is on."""
        return self.fc_vs_control_on or self.fc_vs_t0_on

    def set_fold_change(self, *, vs_control_on: bool | None = None,
                        control_label: str | None = None,
                        vs_t0_on: bool | None = None,
                        baseline_tp: str | None = None) -> None:
        """Apply a fold-change state change. A ``None`` argument leaves that
        field unchanged. ``control_label`` is stored verbatim — the UI layer
        strips its display-only disambiguation suffix before calling.
        ``baseline_tp`` is the dataset's timepoint string the user picked as
        the per-member baseline; empty means "use the earliest finite point"
        (the original behaviour)."""
        if vs_control_on is not None:
            self.fc_vs_control_on = bool(vs_control_on)
        if control_label is not None:
            self.fc_control_label = control_label
        if vs_t0_on is not None:
            self.fc_vs_t0_on = bool(vs_t0_on)
        if baseline_tp is not None:
            self.fc_baseline_tp = str(baseline_tp)

    # ── toggles ──────────────────────────────────────────────────────────────

    def toggle_sem(self) -> bool:
        """Flip the SEM/SD error-band mode; returns the new ``use_sem``."""
        self.use_sem = not self.use_sem
        return self.use_sem

    def toggle_fov_replicates(self) -> bool:
        """Flip the per-FOV replicate mode; returns the new state."""
        self.use_fov_replicates = not self.use_fov_replicates
        return self.use_fov_replicates

    def toggle_bar_group_mode(self) -> str:
        """Flip the bar-tab x-axis grouping (well ↔ timepoint); returns the new value."""
        self.bar_group_mode = "timepoint" if self.bar_group_mode == "well" else "well"
        return self.bar_group_mode

    def toggle_bar_pool_replicates(self) -> bool:
        """Flip whether replicate-set wells pool to a single column."""
        self.bar_pool_replicates = not self.bar_pool_replicates
        return self.bar_pool_replicates

    def toggle_bar_pool_rset_cells(self) -> bool:
        """Flip the per-cell-view rset-pooling mode; returns the new state."""
        self.bar_pool_rset_cells = not self.bar_pool_rset_cells
        return self.bar_pool_rset_cells
