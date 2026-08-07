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
    return {"mean": True, "frac": True, "cdf": True, "n": True}


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
    # Show the ON/OFF threshold marker overlaid on plots (the vertical line /
    # shaded band at ThreshFracOn on the Distribution, line-plot CDF, and smFISH
    # plots, and the 0.5 reference + "threshold=" label on the bar Fraction
    # panel). Off by default — opt in via the Properties sidebar.
    show_threshold_line: bool = False

    # Time-axis label multiplier (Properties → Time → "Rescale time").
    # 1.0 = off. Applied by ``domain/time_rescale.py`` to what a time axis
    # *prints* and to nothing else: the plotted values, the timepoint menus, the
    # Statistics tab and every exported CSV keep the hours parsed out of the
    # filenames. It exists for plates whose filename cadence isn't the real one
    # (``T01…T12`` imaged every four hours), and every axis it touches carries
    # the factor in its label — the numbers disagree with the rest of the app on
    # purpose, so the figure has to say so.
    time_scale: float = 1.0

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
    # Bar plot Properties → Statistics controls.
    # ``bar_err_kind``: which error bar to draw — "none" | "sem" | "sd" |
    # "ci95" (95% CI = 1.96 × SEM, normal approx). Kept in sync with
    # ``use_sem`` (sem→True, sd→False) so the per-card band toggle agrees.
    # ``bar_show_mode``: "mean" (bars only) | "mean_spread" (bars + error
    # bars, the default) | "all_pts" (overlay per-cell points via the
    # beeswarm path).
    bar_err_kind: str = "sem"
    bar_show_mode: str = "mean_spread"
    # Draw significance brackets (from the Statistics tab's pairwise results)
    # between bars on the Bar plot. Off by default; toggled from the bar
    # Display popover. Only rendered when the plot is in grouped/pooled mode and
    # the bar keys match the compared group names. (N7)
    show_significance: bool = False

    # Shared "focus timepoint" across the plotting tabs (N5). The single
    # cross-tab timepoint the single-timepoint tabs (Heat Map slider,
    # Distribution combo) read/write, and the default the multi-select tabs
    # (Bar / Scatter) seed from. ``None`` means "not yet chosen" — each tab
    # falls back to its own default (earliest / first available). Stored as the
    # dataset's timepoint-hours float so it is comparable across tabs.
    focus_timepoint: float | None = None

    fc_vs_control_on: bool = False
    fc_control_label: str = ""
    fc_vs_t0_on: bool = False
    # User-chosen baseline timepoint (the dataset's tp string, e.g. ``"24"``).
    # Empty when the user wants the **earliest** finite-mean point per member
    # (the original ``vs t0`` behaviour), so backward-compat with saved state
    # that only carries ``fc_vs_t0_on=True`` is preserved.
    fc_baseline_tp: str = ""
    # Per-axis fold-change operator. **ratio** (x / d, the original behaviour
    # and v7.1.x default) or **subtract** (x - d). Independent per axis so a
    # user can e.g. subtract a background-control and then divide by t0.
    fc_op_ctrl: str = "ratio"   # vs-control axis; one of {"ratio", "subtract"}
    fc_op_t0: str = "ratio"     # vs-baseline-tp axis; one of {"ratio", "subtract"}
    # Which axis applies FIRST when both are active. Commutes when both
    # ops are the same; matters only with mixed ops. ``"ctrl_first"`` (default,
    # the v7.1.8 behaviour and ΔΔCt-style stacking): the t0 baseline is
    # post-control. ``"t0_first"``: the control denominator is post-t0
    # (each member self-normalises against its own baseline first, then
    # the control normalisation is applied).
    fc_order: str = "ctrl_first"  # one of {"ctrl_first", "t0_first"}
    fc_dirty_scopes: set[str] = field(default_factory=set)

    # ── fold change ──────────────────────────────────────────────────────────

    @property
    def fc_op(self) -> str:
        """Legacy single-op accessor: returns the control-axis op for compat
        with snapshot readers / older code. New callers should read
        ``fc_op_ctrl`` and ``fc_op_t0`` directly."""
        return self.fc_op_ctrl

    @fc_op.setter
    def fc_op(self, value: str) -> None:
        """Legacy single-op setter: fan out to both axes so old code paths
        (saved snapshots that only carry ``op``) keep their previous
        same-op-on-both-axes semantics."""
        v = str(value) if value is not None else "ratio"
        if v not in ("ratio", "subtract"):
            v = "ratio"
        self.fc_op_ctrl = v
        self.fc_op_t0 = v

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
                        baseline_tp: str | None = None,
                        op: str | None = None,
                        op_ctrl: str | None = None,
                        op_t0: str | None = None,
                        order: str | None = None) -> None:
        """Apply a fold-change state change. A ``None`` argument leaves that
        field unchanged. ``control_label`` is stored verbatim — the UI layer
        strips its display-only disambiguation suffix before calling.
        ``baseline_tp`` is the dataset's timepoint string the user picked as
        the per-member baseline; empty means "use the earliest finite point"
        (the original behaviour). ``op_ctrl`` / ``op_t0`` are the per-axis
        operators (``"ratio"`` / ``"subtract"``). ``op`` is the legacy
        single-op kwarg — sets both axes for back-compat."""
        if vs_control_on is not None:
            self.fc_vs_control_on = bool(vs_control_on)
        if control_label is not None:
            self.fc_control_label = control_label
        if vs_t0_on is not None:
            self.fc_vs_t0_on = bool(vs_t0_on)
        if baseline_tp is not None:
            self.fc_baseline_tp = str(baseline_tp)
        if op is not None and str(op) in ("ratio", "subtract"):
            self.fc_op = str(op)  # fans out to both via property setter
        if op_ctrl is not None and str(op_ctrl) in ("ratio", "subtract"):
            self.fc_op_ctrl = str(op_ctrl)
        if op_t0 is not None and str(op_t0) in ("ratio", "subtract"):
            self.fc_op_t0 = str(op_t0)
        if order is not None and str(order) in ("ctrl_first", "t0_first"):
            self.fc_order = str(order)

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
