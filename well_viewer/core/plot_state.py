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

from well_viewer.domain.control_ref import KIND_SET, ControlRef


def _default_legend() -> dict[str, bool]:
    return {"mean": True, "frac": True, "cdf": True, "n": True}


@dataclass
class PlotState:
    """Cross-tab plot options + fold-change normalization state.

    The fold-change fields are read by the collection pipeline
    (``collect_bar_items`` / ``collect_line_series``) through
    ``domain.fold_change.fold_change_state``, which packages them into a
    ``FoldChangeState``. :meth:`set_fold_change` is the single place they are
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

    # Time-axis label multiplier (Properties → Time → "Interval per timepoint").
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
    #: What the control axis divides by — a ``ControlRef``, or None.
    #: Not a string: a string cannot say whether ``A01`` means the well
    #: or a replicate set someone named ``A01``.
    fc_control: object | None = None
    #: Whether the control axis divides everything by one reference
    #: (``"single"``) or each member by its own (``"per_member"``). A mode
    #: field rather than an absence: emptying ``fc_control`` to mean
    #: "per member" would make every untaught caller draw **raw** values under
    #: a fold-change axis label, because the bar collector's force-NaN guard
    #: ANDs with the control and ``_apply_axis`` returns its input unchanged
    #: when the denominator is None.
    fc_control_mode: str = "single"
    #: ``member key -> ControlRef`` for ``per_member`` mode. The keys are the
    #: plot's own bar/trace keys — replicate-set names when sets are pooled,
    #: well tokens otherwise — which is what ``domain/plot_members.py``
    #: enumerates. Entries for members that are not currently plotted are kept,
    #: not dropped: a user who hides a group and shows it again gets their
    #: pairing back rather than a silently emptied table.
    fc_control_map: dict = field(default_factory=dict)
    #: What a member that is itself somebody's control draws as — ``"self"``
    #: (normalized against itself, so a flat 1.0 or 0.0) or ``"hidden"``.
    #: There is no "raw" option: a raw control beside ratio bars puts two
    #: units on one axis.
    fc_control_render: str = "self"
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
    def fold_change_active(self) -> bool:
        """True when either fold-change mode is on."""
        return self.fc_vs_control_on or self.fc_vs_t0_on

    def set_fold_change(self, *, vs_control_on: bool | None = None,
                        control=None, clear_control: bool = False,
                        control_mode: str | None = None,
                        control_map: dict | None = None,
                        control_render: str | None = None,
                        vs_t0_on: bool | None = None,
                        baseline_tp: str | None = None,
                        op: str | None = None,
                        op_ctrl: str | None = None,
                        op_t0: str | None = None,
                        order: str | None = None) -> None:
        """Apply a fold-change state change. A ``None`` argument leaves that
        field unchanged — except ``control``, where "unchanged" and "clear it"
        cannot both be ``None``, so clearing is the explicit ``clear_control``
        flag. A bare string is accepted and read as a legacy reference.
        ``baseline_tp`` is the dataset's timepoint string the user picked as
        the per-member baseline; empty means "use the earliest finite point"
        (the original behaviour). ``op_ctrl`` / ``op_t0`` are the per-axis
        operators (``"ratio"`` / ``"subtract"``). ``op`` is the legacy
        single-op kwarg — sets both axes for back-compat. ``control_map``
        replaces the mapping wholesale and is **copied**, because the editor
        holds a working copy it mutates as the user types; sharing the dict
        would apply half-finished edits to the plot."""
        if vs_control_on is not None:
            self.fc_vs_control_on = bool(vs_control_on)
        if control is not None:
            self.fc_control = ControlRef.coerce(control)
        elif clear_control:
            self.fc_control = None
        if control_mode is not None and str(control_mode) in ("single", "per_member"):
            self.fc_control_mode = str(control_mode)
        if control_map is not None:
            self.fc_control_map = {
                str(k): ControlRef.coerce(v)
                for k, v in dict(control_map).items()
                if ControlRef.coerce(v) is not None
            }
        if control_render is not None and str(control_render) in ("self", "hidden"):
            self.fc_control_render = str(control_render)
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

    def rename_member(self, old: str, new: str) -> bool:
        """Follow a replicate-set rename through the fold-change state.

        Three things name a group, and rewriting fewer than all three is worse
        than rewriting none: the map's **keys** (which member a pairing is
        for), the map's **values** (which member is the control), and
        ``fc_control`` (the single global control). Rewrite only the keys and
        the map holds one entry pointing at a control that no longer exists
        while the group sits on the plate under its new name; leave
        ``fc_control`` alone and renaming the group used as the single control
        silently unresolves the whole plot.

        Only references that name a *replicate set* move. A well token is not
        renameable, and a legacy reference is ambiguous by construction — the
        caller resolves those to a concrete kind first (see
        ``core/selection_controller.sel_rename``), so anything still legacy
        here named nothing that could be resolved and is left as it is rather
        than guessed at.

        The map's *keys* are plain member keys, so they cannot say whether they
        mean the set or a well of the same token. Within one plot they never
        both appear — pooled mode keys on set names, unpooled on well tokens —
        but a retained entry from the other mode can collide with *new*. The
        **renamed** entry wins that collision: it belongs to the group the user
        just acted on, while the entry already sitting on *new* is a leftover
        from the other pooling mode. Keeping the leftover instead would discard
        a live pairing to preserve a dormant one, and the user would have no
        way to tell it had happened.

        Returns True when something changed, so a caller can decide whether a
        redraw is owed.
        """
        old, new = str(old), str(new)
        if not old or not new or old == new:
            return False

        def _moved(ref) -> ControlRef | None:
            if ref is None or getattr(ref, "kind", "") != KIND_SET:
                return None
            if ref.name != old:
                return None
            return ControlRef.for_replicate_set(new)

        changed = False
        moved_single = _moved(self.fc_control)
        if moved_single is not None:
            self.fc_control = moved_single
            changed = True

        remapped: dict = {}
        renamed: list = []          # 0 or 1 entries — the one keyed on *old*
        for key, ref in (self.fc_control_map or {}).items():
            moved_value = _moved(ref)
            if moved_value is not None:
                ref = moved_value
                changed = True
            if key == old:
                renamed.append(ref)
                changed = True
                continue
            remapped[key] = ref
        if renamed:
            # Written last so it overwrites any entry already keyed on *new*
            # rather than being overwritten by one. See the collision note above.
            remapped[new] = renamed[0]
        if changed:
            self.fc_control_map = remapped
        return changed

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

    def reset_for_new_dataset(self) -> None:
        """Drop the settings that name *this* dataset's wells and timepoints.

        Everything here refers to something only the previous plate had. A
        fold-change control names a well or a replicate set; a baseline names a
        timepoint; a line order names wells by token; a bar-group mode is about
        a layout of those wells. Carrying them into the next plate is not a
        preference the user set — it is a description of a plate that is no
        longer open, and the loudest case is fold change: plate B came up
        normalised against a group of plate A's wells, which resolves to
        nothing, so every bar was NaN under an axis still labelled as a fold
        change.

        Presentation-level preferences (SEM vs SD, log axes, show-threshold)
        are deliberately *not* reset — those are about how the user likes to
        look at data, not about which data it was.
        """
        self.fc_vs_control_on = False
        self.fc_control = None
        self.fc_control_mode = "single"
        self.fc_control_map = {}
        self.fc_vs_t0_on = False
        self.fc_baseline_tp = ""
        self.focus_timepoint = None
        self.line_order_rsets = []
        self.line_order_wells = []
        self.bar_group_mode = "well"
        self.bar_order = None
