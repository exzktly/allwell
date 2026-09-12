"""Heatmap tab rendering.

Renders an arbitrary R×C grid colored by a chosen metric (mean above
threshold, fraction above threshold, cell count, or ratio mean) at a
single timepoint. Supports click-to-select wells and modifier-aware
selection.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from well_viewer.core.ui_helpers import EmptyReason

import math
from collections.abc import Iterable

import numpy as np
from ui import theme
from matplotlib import patches as mpatches
from matplotlib import colormaps as mpl_colormaps


from well_viewer.features.heatmap.models import (
    HeatmapLayout,
    make_plate_layout,
)
from well_viewer.domain.ratio_models import is_ratio_key
from well_viewer.domain.stats_compute import (
    _gate_cache_sig,
    aggregate_group,
    group_replicate_stats,
    replicate_mean_and_spread,
)
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.core.plot_state import error_bar_settings, spread_kind_label
from well_viewer.core.channel_state_controller import (
    active_channel_label,
    get_all_fluor_gates,
    on_cutoff_for_column,
)
from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index
from well_viewer.domain.tp_match import tp_close


# The labels themselves live in ``domain/heatmap_metrics`` — they are
# vocabulary shared with ``core/channel_state_controller``, which enables and
# disables rows of this combo as the active Property changes, and core must be
# able to name them without importing this feature. Re-exported here so the
# feature's own callers are unchanged.
from well_viewer.domain.heatmap_metrics import (  # noqa: F401
    METRIC_COUNT,
    METRIC_FRACTION,
    METRIC_MEAN,
    METRIC_MEAN_ALL,
    METRIC_OPTIONS,
    METRIC_RATIO,
)

# Selection-rectangle outline. Deliberately its own colour, not
# ``theme.Colors.danger`` — ``danger`` already marks the drag-reorder
# drop-target label in this same axes (see the label-drag block below), and
# reusing it here would make a selected well indistinguishable from a drop
# target while dragging over it.
_SELECTION_OUTLINE_COLOR = theme.Colors.heatmap_selection

#: Tick text longer than this leans over and shrinks (see ``redraw_heatmap``).
#: Sized to the widest label the grid drew before sample-map names existed —
#: a plate column reads ``12``, a row ``H`` — plus the room a short typed name
#: like ``Ctrl`` needs, so annotating a plate is what changes the axis and
#: renaming one tick is not.
_TICK_WIDE_CHARS = 6

#: Title lift, in points, when the column ticks are rotated. The flat value is
#: 8; rotated 8pt text over twelve columns stands about twice that.
_TITLE_PAD_ROTATED = 18


def _contrast_ink(rgba: tuple[float, float, float, float]) -> str:
    """Pick readable ink for a cell's rendered colour (via ``im.to_rgba``).

    Standard relative-luminance threshold. Takes the already-composited RGBA
    a specific value maps to — including colormap ``set_bad`` masking — so it
    agrees with what's actually drawn instead of recomputing the norm by hand.
    Distinct from ``image_table/image_compose.py``'s "Auto (contrast)" toggle,
    which measures mean raw-pixel RGB on the source image rather than a
    colormap+norm-mapped scalar; the two operate on different inputs and
    aren't interchangeable.
    """
    r, g, b = rgba[0], rgba[1], rgba[2]
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return theme.Colors.ink_dark if luminance > 0.6 else theme.Colors.ink_light


def active_layout(ctx) -> HeatmapLayout:
    """Return the active layout, synthesizing the plate default when needed."""
    layouts = list(ctx.runtime.heatmap.layouts or [])
    name = ctx.state.heatmap.active_layout_name
    if name:
        for lay in layouts:
            if lay.name == name:
                return lay
    if layouts:
        return layouts[0]
    return make_plate_layout((ctx.state.dataset.well_paths or {}).keys())


def heatmap_empty_reason(ctx) -> EmptyReason | None:
    """The Heat Map tab's ``wrap_with_empty_state`` reason: an empty layout.

    This page is not selection-driven. ``redraw_heatmap`` fills its array from
    ``layout.cells`` alone; the plate selection only draws outlines around cells
    and turns clicks into selections. So the state it can have nothing to show
    for is a layout with no wells placed in it — and until now it drew that state
    as a full 8x12 grid of blank cells under a colourbar spanning a range no
    measurement produced, which reads as data rather than as absence.

    The empty layout is reached more often than "the user has not filled one in"
    suggests: ``_ensure_sidebar_layout`` creates an empty ``Sidebar`` layout while
    the sidebar is being built and leaves ``active_layout_name`` unset, so the
    branch above returns it and the populated ``make_plate_layout`` default is
    never reached. Whether that seeding should change is a separate question
    about this tab's default; either way the placeholder states the truth.
    """
    from well_viewer.core.ui_helpers import EmptyReason

    if active_layout(ctx).cells:
        return None
    return EmptyReason(
        "No wells on this heat map.",
        "Drag wells from the plate into the grid, "
        "or click Auto-populate from selection.",
    )


def _selected_metric(ctx) -> str:
    cb = ctx.widgets.get_or_none("_heatmap_metric_cb")
    if cb is None:
        return METRIC_MEAN
    return str(cb.currentText() or METRIC_MEAN)


def _selected_cmap(ctx) -> str:
    cb = ctx.widgets.get_or_none("_heatmap_cmap_cb")
    if cb is None:
        return "viridis"
    # v2 LutSelector exposes lut() + isReversed() rather than currentText().
    if hasattr(cb, "lut"):
        name = cb.lut() or "viridis"
        if hasattr(cb, "isReversed") and cb.isReversed():
            name = f"{name}_r"
        return name
    return str(cb.currentText() or "viridis")


def _selected_tp(ctx) -> float | None:
    cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    tps: list[float] = list(ctx.state.heatmap.tp_values or [])
    if cb is None or not tps:
        return None
    idx = max(0, min(cb.currentIndex(), len(tps) - 1))
    return tps[idx]


def sync_heatmap_timepoint_to_focus(ctx) -> None:
    """Move the combo to the shared focus timepoint (N5) when one is set and
    present in this tab's timepoints — so a timepoint chosen on another
    plotting tab (Distribution) is reflected here. Signals are blocked so this
    silent sync never re-fires ``_on_tp_changed``. No-op when no focus is set.

    ``setCurrentIndex`` alone repaints the combo's own "Timepoint: …" text —
    it's a LabeledComboBox reading its item text, not a separate label to
    update in step."""
    focus = current_focus_tp(ctx)
    if focus is None:
        return
    tps: list[float] = list(ctx.state.heatmap.tp_values or [])
    i = nearest_index(tps, focus)
    if i is None:
        return
    cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    if cb is not None and cb.currentIndex() != i:
        blocked = cb.blockSignals(True)
        try:
            cb.setCurrentIndex(i)
        finally:
            cb.blockSignals(blocked)


def _repsets_loaded(ctx) -> list:
    """Every loaded replicate set (incl. hidden) over ``ctx.state`` — the
    pool the heatmap's rep-set-average expansion walks. Derived from the
    domain resolver so no god-object / feature import is needed."""
    from well_viewer.domain.plot_members import plot_replicate_sets_loaded
    return plot_replicate_sets_loaded(ctx)


def _expand_repset_wells(ctx, wells: Iterable[str]) -> list[str]:
    """If the rep-set average toggle is on, expand each input well to every
    well in the rep-set it belongs to. Otherwise return wells as-is.
    """
    src = list(wells)
    if not bool(ctx.state.heatmap.repset_avg):
        return src
    rep_sets = _repsets_loaded(ctx)
    if not rep_sets:
        return src
    out: list[str] = []
    seen: set = set()
    for w in src:
        chosen: list[str] | None = None
        for rs in rep_sets:
            members = list(getattr(rs, "wells", []) or [])
            if w in members:
                chosen = members
                break
        if chosen is None:
            if w not in seen:
                seen.add(w)
                out.append(w)
        else:
            for m in chosen:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
    return out


def _repset_name_for_wells(ctx, wells: Iterable[str]) -> str | None:
    """Return the name of the first rep-set that contains any of *wells*, or None."""
    if not bool(ctx.state.heatmap.repset_avg):
        return None
    rep_sets = _repsets_loaded(ctx)
    if not rep_sets:
        return None
    src = list(wells)
    for w in src:
        for rs in rep_sets:
            members = list(getattr(rs, "wells", []) or [])
            if w in members:
                return str(getattr(rs, "name", "") or "")
    return None


#: Which :class:`ReplicateStat` field each averaging metric reduces. ``METRIC_COUNT``
#: is absent on purpose — it is a total, not an average (see ``_cell_stat``).
_METRIC_FIELD = {
    METRIC_MEAN: "mean",
    METRIC_MEAN_ALL: "mean_all",
    METRIC_FRACTION: "frac",
    METRIC_RATIO: "mean_all",
}


def _repset_mean_of_means(
    ctx,
    wells: list[str],
    target_t: float,
    metric: str,
    *,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
) -> tuple[float, float]:
    """``(mean, spread)`` across replicates of *metric*, from the canonical list.

    ``group_replicate_stats`` is the one producer Bar, Line and the Statistics
    test reduce, and ``replicate_mean_and_spread`` is the same reducer they use,
    so this cannot describe a different quantity from the bar drawn beside it.
    The replicate unit follows the app's per-FOV toggle for the same reason:
    when the toggle is on the plots average per-FOV means, and a heatmap
    averaging per-well means would disagree with them again in the other
    direction. The spread is the SD or the SEM of the same replicates,
    following the app-wide Properties → Statistics choice — all four of its
    kinds, through :func:`core.plot_state.error_bar_settings`, so the 95% CI
    option scales this the 1.96 it scales the whisker by. Reading
    ``PlotState.use_sem`` alone would have drawn a plain SEM under a panel set
    to 95% CI and labelled it ``SEM``: the same set, two intervals, neither
    figure saying so.

    Both halves are ``NaN`` for a set with no replicates, and the spread alone
    is ``NaN`` for a set with exactly one — an undefined spread, which is not
    the same fact as replicates that agreed exactly, and is what stops a
    single-well cell from drawing a ``± 0.00`` beside neighbours whose error
    was measured.
    """
    reps = group_replicate_stats(
        ctx, wells, target_t,
        use_fov=bool(getattr(ctx.state.plot, "use_fov_replicates", False)),
        val_col=val_col,
        threshold=threshold,
        fluor_gates=fluor_gates,
    )
    if not reps:
        return float("nan"), float("nan")
    _kind, ci_scale, _draw = error_bar_settings(ctx)
    mean, spread = replicate_mean_and_spread(
        reps, _METRIC_FIELD[metric],
        use_sem=bool(getattr(ctx.state.plot, "use_sem", True)),
    )
    return mean, spread * ci_scale


def _cell_stat(
    ctx,
    wells: Iterable[str],
    target_t: float,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
) -> tuple[float, float]:
    """``(value, spread)`` for the heatmap cell holding *wells* at *target_t*.

    **The averaging metrics are the mean of each replicate's mean**, which is
    what Bar, Line and Stats draw for the same set. They all reduce the one
    replicate list ``group_replicate_stats`` produces, so a heatmap cell and
    the bar beside it cannot describe different quantities.

    This used to pool the cells of every well and take a single mean over the
    pool, which weights a well by how many cells it segmented: a replicate that
    happened to be more confluent pulled the cell harder, and on a set whose
    wells differ in confluence the heatmap and the bar showed different numbers
    with nothing saying why. A cell holding one well is unaffected — the mean of
    one well's mean is that well's mean.

    ``METRIC_COUNT`` is deliberately **not** converted. It reports how many
    cells the set contains, which is a total rather than an average, and the
    mean of the per-well counts would be a different quantity rather than a
    more consistent one. It stays the pooled total and says so on the cell —
    and its spread is ``NaN``, because a total has none: the replicates were
    summed rather than averaged, so there is nothing for a ``±`` to be the
    spread *of*. A number invented there would be the silent substitution §7
    of ``CLAUDE.md`` forbids, in the one metric whose value is not a mean.
    """
    expanded = _expand_repset_wells(ctx, wells)
    valid_wells = [w for w in expanded if w in ctx.state.dataset.well_paths]
    if not valid_wells:
        return float("nan"), float("nan")

    if metric == METRIC_COUNT:
        # Use aggregate to apply gate logic consistently, then read n_total.
        pts = aggregate_group(ctx,
            valid_wells, threshold=threshold, use_sem=False,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if tp_close(pt[0], target_t)]
        # NaN, not 0.0. A well with no point at this timepoint has an *unknown*
        # cell count, and returning zero coloured it as a real, empty well —
        # indistinguishable from one the pipeline genuinely measured as having
        # no cells. The two sibling metrics below already return NaN here.
        total = float(matched[0][5]) if matched else float("nan")
        return total, float("nan")

    if metric == METRIC_RATIO and not is_ratio_key(val_col):
        return float("nan"), float("nan")

    # METRIC_FRACTION, METRIC_RATIO, METRIC_MEAN_ALL and METRIC_MEAN all reduce
    # the one replicate list; only the ``ReplicateStat`` field differs, and
    # ``_METRIC_FIELD`` holds that mapping. ``METRIC_MEAN_ALL`` reads the mean
    # across every included cell — no per-channel ThreshFracOn filter — which is
    # what makes it useful for the non-MFI properties (Total / Max / Min / Std)
    # where the MFI threshold is semantically irrelevant.
    return _repset_mean_of_means(
        ctx, valid_wells, target_t,
        metric if metric in _METRIC_FIELD else METRIC_MEAN,
        val_col=val_col, threshold=threshold, fluor_gates=fluor_gates,
    )


def _cell_value(
    ctx,
    wells: Iterable[str],
    target_t: float,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
) -> float:
    """The heatmap cell value alone — :func:`_cell_stat` without its spread.

    The colour-scale sweep in :func:`_compute_global_range` pools values over
    every timepoint and has no use for the error, and reading the pair's first
    element there would put the tuple's shape into a second module.
    """
    value, _spread = _cell_stat(
        ctx, wells, target_t, metric, val_col, threshold, fluor_gates,
    )
    return value


def _sample_map_axis_labels(ctx, layout, grid_row_labels,
                            grid_col_labels) -> tuple[list[str], list[str]]:
    """``(row labels, col labels)`` taken from the sample map, ``""`` per tick
    where it describes nothing.

    This is the one place the Heat Map reads the Variables layer of Home →
    Sample Map. Every other plotting surface already names a well by what the
    user said it is — ``selections_model.figure_names_map`` for the exported
    rows and ``well_display_label_and_source`` for a tick on Bar or Line — and
    this grid was the holdout, drawing ``A``–``H`` and ``1``–``12`` (or a bare
    ``1``, ``2``, ``3`` on a layout built by *Auto-populate from selection*)
    over a plate whose doses and cell lines were fully annotated.

    A line is described by :func:`sample_variables.labels_for_axis_lines`,
    which rolls the variables up over every well on that row or column and
    keeps only the variables that actually vary along the axis. The empty
    string it returns for an undescribable line is deliberate and is not the
    same as a missing entry: the caller falls back **per tick**, so one mixed
    column on an otherwise annotated axis keeps its grid label instead of
    blanking the whole axis or blanking that one tick.

    *grid_row_labels* / *grid_col_labels* are what each tick is called without
    the sample map — the typed name, the plate default, or the 1-based index —
    and they serve twice: as the per-tick fallback the caller applies, and,
    passed through to :func:`sample_variables.labels_for_axis_lines`, as the
    suffix that tells two ticks apart when the sample map composes one name
    for both of them.

    Both lists are blank when the toggle is off or the plate has no variables,
    which is what makes this change nothing for a plate nobody annotated.
    """
    blank = ([""] * int(layout.rows), [""] * int(layout.cols))
    if not bool(ctx.state.heatmap.label_from_sample_map):
        return blank
    vset = getattr(getattr(ctx.state, "selection", None), "variables", None)
    if vset is None or not getattr(vset, "variables", ()):
        return blank
    from well_viewer.domain.sample_variables import labels_for_axis_lines

    cells = layout.cells or {}
    row_wells = [
        [w for c in range(layout.cols) for w in cells.get((r, c), [])]
        for r in range(layout.rows)
    ]
    col_wells = [
        [w for r in range(layout.rows) for w in cells.get((r, c), [])]
        for c in range(layout.cols)
    ]
    # The grid's own labels go in as the disambiguator for two ticks that
    # compose one name — the plate whose rows are HeLa×4 then U2OS×4, where
    # the composed names alone would leave row 0 indistinguishable from row 1
    # and take ``A``–``H`` away in exchange.
    return (labels_for_axis_lines(vset, row_wells, fallbacks=grid_row_labels),
            labels_for_axis_lines(vset, col_wells, fallbacks=grid_col_labels))


def _fixed_limit(value) -> float | None:
    """The Fixed-scale limit the user typed, or ``None`` for a blank field.

    A blank vmin/vmax edit is stored as ``nan`` (and is ``None`` before the user
    has ever set one); both mean "no opinion, take this end from the data".
    Every other finite number is an opinion — **zero included**. Zero is the
    whole reason this is a function rather than an inline truthiness test:
    ``float(vmin or nan)`` reads a typed ``0`` as a blank field, so the most
    ordinary lower bound anyone types threw the entire Fixed range away and the
    grid went on drawing the automatic one.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _auto_color_scale(arr: np.ndarray, ctx) -> tuple[float | None, float | None]:
    """The automatic (data-derived) colour range.

    Prefers the range pooled across every timepoint (``ctx.runtime.heatmap.
    global_vmin`` / ``.global_vmax``) so the colormap stays put while the
    timepoint combo is scrubbed, and falls back to this timepoint's own finite
    values when no global range has been computed.
    """
    g_vmin = ctx.runtime.heatmap.global_vmin
    g_vmax = ctx.runtime.heatmap.global_vmax
    if (
        g_vmin is not None and g_vmax is not None
        and math.isfinite(g_vmin) and math.isfinite(g_vmax) and g_vmax > g_vmin
    ):
        return float(g_vmin), float(g_vmax)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None, None
    return float(finite.min()), float(finite.max())


def _resolve_color_scale(arr: np.ndarray, ctx) -> tuple[float | None, float | None]:
    """Pick (vmin, vmax) for the heatmap.

    Mode is read from ``ctx.state.heatmap.scale_mode`` (Auto/Fixed). Under Auto
    the range is entirely ``_auto_color_scale``'s.

    Under Fixed the two edits are read **independently**: an end carrying a
    finite number is used verbatim, and an end left blank falls back to the
    automatic one. Filling in only vmax to cap a saturating plate is a complete
    instruction rather than half of one, and it used to be discarded along with
    the blank vmin beside it.

    A pair that cannot describe a colour range — ``vmax`` at or below ``vmin``
    once both ends are known — is refused rather than handed to matplotlib,
    which would either raise or paint the grid one flat colour. The refusal
    says so on the status line: a Fixed range that is ignored draws exactly the
    same figure as a Fixed range that was never entered.
    """
    mode = str(ctx.state.heatmap.scale_mode or "Auto")
    auto_vmin, auto_vmax = _auto_color_scale(arr, ctx)
    if mode != "Fixed":
        return auto_vmin, auto_vmax

    fixed_vmin = _fixed_limit(ctx.state.heatmap.vmin)
    fixed_vmax = _fixed_limit(ctx.state.heatmap.vmax)
    vmin = fixed_vmin if fixed_vmin is not None else auto_vmin
    vmax = fixed_vmax if fixed_vmax is not None else auto_vmax
    if vmin is not None and vmax is not None and vmax <= vmin:
        ctx.notify.set_status(
            f"Heat Map: the fixed colour scale needs vmax above vmin "
            f"(got vmin {vmin:g}, vmax {vmax:g}) — showing the automatic range."
        )
        return auto_vmin, auto_vmax
    return vmin, vmax


def _compute_global_range(
    ctx,
    layout,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
    ratios,
) -> tuple[float | None, float | None]:
    """Pool ``_cell_value`` across every (timepoint, cell) and return (vmin, vmax).

    Result is cached on ``ctx.runtime.heatmap`` keyed on the inputs that
    influence it so timepoint-slider scrubbing does not retrigger the full sweep.
    """
    tps = list(ctx.state.heatmap.tp_values or [])
    if not tps:
        return None, None

    # Content-based cache key. ``id(layout)`` / ``id(ratios)`` changed
    # every time the runtime app rebuilt either object, missing the
    # cache on slider scrubs where the underlying data was identical.
    # Use the layout's structural shape and the ratios dict's sorted
    # key tuple — both are cheap to compute relative to the O(T·R·C)
    # sweep below.
    layout_fingerprint = (
        getattr(layout, "name", ""),
        int(getattr(layout, "rows", 0)),
        int(getattr(layout, "cols", 0)),
        tuple(
            sorted(
                ((int(r), int(c)), tuple(sorted(w or [])))
                for (r, c), w in (getattr(layout, "cells", {}) or {}).items()
            )
        ),
    )
    # The ratio *definitions*, not just their names: two ratios can share a name
    # and divide different channels, and the colour scale computed from one is
    # wrong for the other. (`stats_compute._ratio_cache_sig` is the same rule for
    # the statistics cache.)
    ratios_fingerprint = tuple(
        (
            name,
            getattr(r, "numerator_channel", ""),
            getattr(r, "numerator_metric", ""),
            getattr(r, "denominator_channel", ""),
            getattr(r, "denominator_metric", ""),
            repr(getattr(r, "epsilon", None)),
        )
        for name, r in sorted((ratios or {}).items())
    )
    cache_key = (
        layout_fingerprint,
        metric,
        val_col,
        float(threshold),
        tuple(sorted((fluor_gates or {}).items())),
        ratios_fingerprint,
        tuple(tps),
        bool(ctx.state.heatmap.repset_avg),
        bool(ctx.state.heatmap.log_scale),
        # The scatter-gate signature. The colour range is computed from gated
        # cell values, so editing or resizing a gate changes every cell in the
        # map without moving any of the fields above it — and the cache is
        # otherwise cleared only when the tab is rebuilt, so an Auto scale went
        # on describing the population from before the edit.
        _gate_cache_sig(ctx),
    )
    cached = ctx.runtime.heatmap.global_range_cache
    if cached is not None and cached.get("key") == cache_key:
        return cached["vmin"], cached["vmax"]

    vmin: float | None = None
    vmax: float | None = None
    for tp in tps:
        for (r, c), wells in layout.cells.items():
            if not (0 <= r < layout.rows and 0 <= c < layout.cols):
                continue
            try:
                v = _cell_value(
                    ctx, wells, tp, metric, val_col,
                    threshold, fluor_gates,
                )
            except Exception:
                v = float("nan")
            if math.isfinite(v):
                vmin = v if vmin is None else min(vmin, v)
                vmax = v if vmax is None else max(vmax, v)

    ctx.runtime.heatmap.global_range_cache = {"key": cache_key, "vmin": vmin, "vmax": vmax}
    return vmin, vmax


def redraw_heatmap(ctx) -> None:
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    fig = ctx.widgets.get_or_none("_heatmap_fig")
    if ax is None or canvas is None or fig is None:
        return

    layout = active_layout(ctx)
    sync_heatmap_timepoint_to_focus(ctx)
    tp = _selected_tp(ctx)
    if tp is None:
        # Try to derive from the data; if still none, leave the canvas blank.
        ax.clear()
        # Themed muted token, not a hard-coded "gray" — see the same sweep in
        # distribution/controller._empty_msg.
        from well_viewer.core.plot_style import tokens_for as _tf
        _bg, _title_fg, _muted_fg, _grid, _spine = _tf(ax)
        ax.text(
            0.5, 0.5, "No timepoints loaded.",
            ha="center", va="center", transform=ax.transAxes,
            fontsize=10, color=_muted_fg,
        )
        ax.set_axis_off()
        canvas.draw_idle()
        return

    metric = _selected_metric(ctx)
    val_col = ctx.state.channel.active_val_col
    # The ON cutoff follows the value column: shapes keep every cell, spot
    # counts read ThreshCountOn, intensities read ThreshFracOn.
    threshold = on_cutoff_for_column(
        ctx, val_col, channel=ctx.state.channel.active_channel)
    fluor_gates = get_all_fluor_gates(ctx)
    ratios = ctx.state.channel.ratio_index
    cmap_name = _selected_cmap(ctx)

    arr = np.full((layout.rows, layout.cols), np.nan, dtype=float)
    err = np.full((layout.rows, layout.cols), np.nan, dtype=float)
    cell_well_index: dict[tuple[int, int], list[str]] = {}

    for (r, c), wells in layout.cells.items():
        if not (0 <= r < layout.rows and 0 <= c < layout.cols):
            continue
        cell_well_index[(r, c)] = list(wells)
        try:
            arr[r, c], err[r, c] = _cell_stat(
                ctx, wells, tp, metric, val_col,
                threshold, fluor_gates,
            )
        except Exception:
            arr[r, c] = float("nan")
            err[r, c] = float("nan")

    # Cache for click/hover handlers (stamped onto ``app._heatmap_*`` by the
    # runtime namespace so the app-native mouse handlers keep resolving).
    # ``spread`` rides along for the CSV export, which writes the same ``±``
    # the figure draws rather than recomputing it from a second reduction.
    ctx.runtime.heatmap.cell_well_index = cell_well_index
    ctx.runtime.heatmap.array = arr
    ctx.runtime.heatmap.spread = err
    _err_kind, _ci_scale, _draw_err = error_bar_settings(ctx)
    ctx.runtime.heatmap.spread_label = spread_kind_label(
        _err_kind, bool(getattr(ctx.state.plot, "use_sem", True)))
    ctx.runtime.heatmap.layout_active = layout

    # Pool min/max across every timepoint so the colormap stays consistent
    # while the user scrubs the timepoint slider. Cached so this only
    # recomputes when an input changes.
    g_vmin, g_vmax = _compute_global_range(
        ctx, layout, metric, val_col,
        threshold, fluor_gates, ratios,
    )
    ctx.runtime.heatmap.global_vmin = g_vmin
    ctx.runtime.heatmap.global_vmax = g_vmax

    # Render. Resolve the active plot-theme tokens up front so the empty-cell
    # fill, figure background, and axes background all track the theme — the
    # heatmap deliberately skips PlotCard.setPlotTheme on redraw, so without
    # this the figure rendered on matplotlib's default white inside the dark UI.
    from well_viewer.core.plot_style import tokens_for as _tokens_for
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for(ax)
    fig.patch.set_facecolor(_bg)

    vmin, vmax = _resolve_color_scale(arr, ctx)
    cmap = mpl_colormaps.get_cmap(cmap_name).copy()
    cmap.set_bad(color=_bg)  # empty cells blend into the themed background

    masked = np.ma.masked_invalid(arr)

    # Log-scale toggle: route values through a LogNorm so the colorbar
    # spans orders of magnitude. Values ≤ 0 become invalid (masked).
    norm = None
    log_scale = bool(ctx.state.heatmap.log_scale)
    if log_scale:
        from matplotlib.colors import LogNorm
        positive = masked.filled(np.nan)
        positive = positive[np.isfinite(positive) & (positive > 0)]
        log_vmin = float(np.min(positive)) if positive.size else None
        log_vmax = float(np.max(positive)) if positive.size else None
        # User-fixed vmin/vmax win when they're positive.
        if (vmin is not None and vmin > 0) and (vmax is not None and vmax > vmin):
            log_vmin, log_vmax = float(vmin), float(vmax)
        elif str(ctx.state.heatmap.scale_mode or "Auto") == "Fixed" and (
            _fixed_limit(ctx.state.heatmap.vmin) is not None
            or _fixed_limit(ctx.state.heatmap.vmax) is not None
        ):
            # A Fixed range a log scale cannot use: a limit at or below zero.
            # The grid still draws — on the data's own positive range — so
            # without this the fixed numbers are ignored and nothing says so.
            ctx.notify.set_status(
                "Heat Map: a log colour scale needs both fixed limits above "
                "zero — showing the automatic log range instead."
            )
        if log_vmin is not None and log_vmax is not None and log_vmax > log_vmin > 0:
            norm = LogNorm(vmin=log_vmin, vmax=log_vmax)
            # Mask non-positive entries so LogNorm doesn't choke.
            masked = np.ma.masked_where(~np.isfinite(masked.filled(np.nan)) | (masked.filled(np.nan) <= 0), masked)
        else:
            # No usable positive range — a blank grid, one distinct value, or a
            # Fixed vmin at or below zero. Say so: rendering linear in silence
            # looks identical to the toggle having no effect at all.
            ctx.notify.set_status(
                "Heat Map: log scale needs two or more distinct positive values — "
                "showing the linear colour scale."
            )

    ax.clear()
    ax.set_facecolor(_bg)  # ax.clear() resets to white; re-apply the theme bg
    if norm is not None:
        im = ax.imshow(masked, aspect="equal", cmap=cmap, norm=norm,
                       origin="upper", interpolation="nearest")
    else:
        im = ax.imshow(masked, aspect="equal", cmap=cmap, vmin=vmin, vmax=vmax,
                       origin="upper", interpolation="nearest")

    # Always materialise label lists of the right length. Persisted layouts
    # can carry shorter ``row_labels`` / ``col_labels`` (e.g. one entry left
    # over from a resize) — ``layout.col_labels or [...]`` would short-
    # circuit to the truthy short list and produce a labels/ticks-count
    # mismatch ValueError when matplotlib's FixedLocator validates them.
    raw_row_labels = list(layout.row_labels or [])
    raw_col_labels = list(layout.col_labels or [])
    # What each tick is called without the sample map: the layout's own label,
    # or the 1-based index for a layout that carries none.
    grid_row_labels = [
        str(raw_row_labels[i]) if i < len(raw_row_labels) and raw_row_labels[i]
        else str(i + 1)
        for i in range(layout.rows)
    ]
    grid_col_labels = [
        str(raw_col_labels[i]) if i < len(raw_col_labels) and raw_col_labels[i]
        else str(i + 1)
        for i in range(layout.cols)
    ]
    # Three rungs, in this order: a name the **user typed** for this tick, then
    # what the sample map composes for the line, then the grid's own label. The
    # typed name is first because a rename that redraws as something else is a
    # control that does not work — and the sample-map rung is on by default, so
    # nobody opted into having their labels overwritten.
    sm_row_labels, sm_col_labels = _sample_map_axis_labels(
        ctx, layout, grid_row_labels, grid_col_labels)
    row_labels = [
        grid_row_labels[i] if layout.label_typed("row", i)
        else str(sm_row_labels[i]) if i < len(sm_row_labels) and sm_row_labels[i]
        else grid_row_labels[i]
        for i in range(layout.rows)
    ]
    col_labels = [
        grid_col_labels[i] if layout.label_typed("col", i)
        else str(sm_col_labels[i]) if i < len(sm_col_labels) and sm_col_labels[i]
        else grid_col_labels[i]
        for i in range(layout.cols)
    ]
    # Tick text used to be one or two characters (``A``–``H``, ``1``–``12``);
    # a sample-map name is a composed string like ``HeLa · 10 µM``, and there
    # can be 32 of them across. Horizontal at 8pt they overlap and then run off
    # the axis, which is a figure that has silently stopped being readable. So
    # a long label leans over and shrinks a little — chosen over eliding it,
    # because a truncated condition name is a figure that states the wrong
    # condition, and over leaving it horizontal, because overlapping ticks are
    # unreadable in a way a reader cannot repair.
    long_cols = max((len(t) for t in col_labels), default=0) > _TICK_WIDE_CHARS
    long_rows = max((len(t) for t in row_labels), default=0) > _TICK_WIDE_CHARS
    col_fs = 7 if long_cols else 8
    row_fs = 7 if long_rows else 8
    ax.set_xticks(range(layout.cols))
    if long_cols:
        # ``ha="left"`` because these ticks sit above the grid: a right-anchored
        # rotation would walk the text into the cells it labels.
        ax.set_xticklabels(col_labels, fontsize=col_fs, rotation=30, ha="left",
                           rotation_mode="anchor")
    else:
        ax.set_xticklabels(col_labels, fontsize=col_fs)
    ax.xaxis.tick_top()
    ax.set_yticks(range(layout.rows))
    ax.set_yticklabels(row_labels, fontsize=row_fs)
    setattr(ax, "_categorical_xaxis", True)
    setattr(ax, "_categorical_yaxis", True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)

    # Row/col drag-reorder visual: colour source blue, drop-target red.
    drag = ctx.runtime.heatmap.label_drag
    if drag is not None:
        _kind = drag["kind"]
        _src = drag["src"]
        _over = drag.get("over")
        _drag_labels = ax.get_xticklabels() if _kind == "col" else ax.get_yticklabels()
        for _i, _lbl in enumerate(_drag_labels):
            if _i == _src:
                _lbl.set_color(theme.Colors.accent_pressed)
                _lbl.set_fontweight("bold")
            elif _over is not None and _i == _over and _over != _src:
                _lbl.set_color(theme.Colors.danger)
                _lbl.set_fontweight("bold")

    # Cell text annotations. Resolve the user's number format once, not per cell.
    value_fmt = _cell_value_format(ctx, fig)
    # ``draw_err`` is the same gate the bars use, so *Error bars: None* silences
    # the cell annotation as well: a panel set to draw no error, drawing one.
    show_error = bool(ctx.state.heatmap.show_error) and _draw_err
    for (r, c), wells in cell_well_index.items():
        v = arr[r, c]
        if not math.isfinite(v):
            continue
        rs_name = _repset_name_for_wells(ctx, wells)
        if rs_name:
            text = rs_name
        elif len(wells) == 1:
            text = wells[0]
        else:
            text = f"{wells[0]}+{len(wells) - 1}"
        ink = _contrast_ink(im.to_rgba(v))
        e = err[r, c]
        # A cell with an error to draw gives up a little of its height to it,
        # so the three lines stay inside one square. A cell without one is laid
        # out exactly as it was — the whole grid must not shift because one
        # cell in it happens to hold two replicates.
        draw_error = show_error and math.isfinite(e)
        value_y = r - 0.20 if draw_error else r - 0.05
        name_y = r + 0.34 if draw_error else r + 0.32
        ax.text(
            c, name_y, text,
            ha="center", va="center",
            fontsize=6, color=ink, alpha=0.8,
        )
        ax.text(
            c, value_y, _format_cell_value(v, metric, value_fmt),
            ha="center", va="center",
            fontsize=8, color=ink,
        )
        if draw_error:
            ax.text(
                c, r + 0.06,
                f"± {_format_cell_error(e, metric, value_fmt)}",
                ha="center", va="center",
                fontsize=6.5, color=ink, alpha=0.85,
            )

    # Selection rectangles.
    selected = set(ctx.state.selection.selected_wells or set())
    for (r, c), wells in cell_well_index.items():
        if any(w in selected for w in wells):
            rect = mpatches.Rectangle(
                (c - 0.5, r - 0.5), 1.0, 1.0,
                fill=False, edgecolor=_SELECTION_OUTLINE_COLOR, linewidth=2.0,
            )
            ax.add_patch(rect)

    # Color bar — refill the persistent ``cax`` reserved at build time so
    # the main axes geometry stays put across redraws. Calling
    # ``fig.colorbar(im, ax=ax, ...)`` would silently shrink ``ax`` on
    # each redraw, which is what made the plot creep left and shrink.
    # Theme-aware text colors so the colorbar/title invert with the PlotCard's
    # publication↔screen toggle instead of staying mpl-default black.
    # Theme tokens (_bg/_title_fg/_muted_fg/_spine) were resolved up front in
    # the render section above and the figure/axes backgrounds already painted.
    cax = ctx.widgets.get_or_none("_heatmap_cax")
    if cax is not None:
        try:
            cax.cla()
            ctx.runtime.heatmap.colorbar = fig.colorbar(im, cax=cax)
            # Opt the bar out of the export-style pass's axis handling: its scale
            # and ticks come from ``im``'s norm, so under Log scale that pass
            # would otherwise reset the log ticks it just installed. ``cla()``
            # drops the attribute, so re-stamp it on every redraw.
            setattr(cax, "_aw_colorbar", True)
            ctx.runtime.heatmap.colorbar.set_label(_metric_axis_label(metric, ctx),
                                                   fontsize=8, color=_muted_fg)
            cax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in cax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            ctx.runtime.heatmap.colorbar = None
    else:
        if ctx.runtime.heatmap.colorbar is not None:
            try:
                ctx.runtime.heatmap.colorbar.remove()
            except Exception:
                swallowed()  # was: pass (silent except)
        try:
            ctx.runtime.heatmap.colorbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
            setattr(ctx.runtime.heatmap.colorbar.ax, "_aw_colorbar", True)
            ctx.runtime.heatmap.colorbar.set_label(_metric_axis_label(metric, ctx),
                                                   fontsize=8, color=_muted_fg)
            ctx.runtime.heatmap.colorbar.ax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in ctx.runtime.heatmap.colorbar.ax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            ctx.runtime.heatmap.colorbar = None

    # The timepoint through the app-wide time correction (Properties → Time),
    # not the raw filename hour. ``figure_timepoint_label`` is the helper for a
    # figure whose title is the only place a timepoint appears — this grid has
    # no time axis to hang the marker on — so the correction is stated here or
    # nowhere, and it carries its own "filename value × N" marker so a corrected
    # title cannot read like real hours. Drawing the raw number left this tab
    # naming a different hour from the Bar, Line, Spatial and batch-export
    # figures of the same plate, with nothing on either figure to say which.
    from well_viewer.domain.time_rescale import (
        figure_timepoint_label as _tp_label,
        rescale_for as _rescale_for,
    )
    title = (
        f"{active_channel_label(ctx)}"
        f" — {metric} — {_tp_label(tp, _rescale_for(ctx.state.plot))}"
    )
    if show_error and np.isfinite(err).any():
        # Which spread the ``±`` under each value is, in the same two words
        # every other error-bearing figure in the app uses. A grid whose cells
        # carry an unlabelled ``±`` is the ambiguity ``spread_kind_label``
        # exists to remove — SD, SEM and a 95% CI are three different intervals
        # over one set of replicates and look identical on the cell.
        title = f"{title} (± {ctx.runtime.heatmap.spread_label})"
    # ``pad`` lifts the title away from the heatmap rows so the descenders
    # never overlap the top row of cells; the figure's top=0.88 margin
    # reserves enough room for the lifted title.
    # Rotated column ticks stand taller than flat ones, so the title lifts off
    # them by the same amount rather than landing in the middle of the text.
    ax.set_title(title, fontsize=9, color=_title_fg,
                 pad=_TITLE_PAD_ROTATED if long_cols else 8)

    # Apply the Export Style sidebar prefs (font sizes, grid, log scale, …)
    # so toggling those in the configurator survives a redraw.
    try:
        apply_export_style_to_current(ctx, fig, canvas)
    except Exception:
        swallowed()  # was: pass (silent except)

    canvas.draw_idle()
    # Re-evaluate this tab's empty-state reason, which reads ``layout.cells``.
    # Nothing outside this feature touches the layout, so none of the app-wide
    # refresh triggers fire when the user fills it — and the placeholder's own
    # instruction is "click Auto-populate from selection". Without this, doing
    # exactly what it says left the placeholder standing over a populated grid.
    try:
        from well_viewer.core.ui_helpers import refresh_empty_state_pages
        refresh_empty_state_pages(ctx.window)
    except Exception:
        swallowed()


def _format_cell_value(v: float, metric: str, fmt: str = "") -> str:
    """The number drawn in (and reported under the cursor for) one grid cell.

    *fmt* is the user's printf-style format from the Properties panel — ``"%.2f"``,
    ``"%.1e"``, ``"%d"``. An empty *fmt* keeps the automatic choice, which reads the
    metric and the magnitude: counts are whole numbers, fractions get two places,
    and intensities scale their precision to their size. A format that can't render
    the value (a bad conversion, or ``%d`` against something non-finite) falls back
    to that automatic choice rather than dropping the number.
    """
    if not math.isfinite(v):
        return ""
    if fmt:
        try:
            return fmt % v
        except (TypeError, ValueError):
            pass  # unusable format → the automatic choice below
    if metric == METRIC_COUNT:
        return f"{int(round(v))}"
    if metric == METRIC_FRACTION:
        return f"{v:.2f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def _format_cell_error(v: float, metric: str, fmt: str = "") -> str:
    """The ``± err`` a cell prints, which may never read as a zero it is not.

    The spread goes through the same format as the value it sits under, so a
    grid the user has set to two decimals does not print its value at two and
    its error at five. But a *format* chosen for the value can be too coarse
    for the error beside it — a fraction's automatic ``%.2f`` renders a real
    spread of ``0.004`` as ``0.00``, and a user's ``%d`` renders it as ``0`` —
    and a spread printed as zero is indistinguishable from replicates that
    agreed exactly. That is the measured-zero-versus-undefined conflation §7 of
    ``CLAUDE.md`` is about, re-created inside the feature added to remove it:
    a cell with no measurable spread already prints nothing at all, so a ``±
    0.00`` on the grid is a claim that the replicates agreed.

    So a non-zero spread that the chosen format flattens to zero falls back to
    two significant figures, which is the smallest rendering that cannot. The
    fallback is per cell rather than per grid: only the cells that would have
    lied change, and a grid whose errors are all comfortably above its
    precision is formatted exactly like its values.
    """
    if not math.isfinite(v):
        return ""
    text = _format_cell_value(v, metric, fmt)
    if v == 0.0:
        return text
    try:
        if float(text) != 0.0:
            return text
    except ValueError:
        # A format that produced something unparseable (a suffix, a unit) is
        # not one this can second-guess — it is also not one that can have
        # silently rounded to a bare zero.
        return text
    return f"{v:.2g}"


def _cell_value_format(ctx, fig) -> str:
    """The Properties panel's printf format for this heat map's cell numbers."""
    try:
        from well_viewer.core.plot_style import figure_prefs
        return str(figure_prefs(ctx, fig).get("heatmap_value_format", "") or "")
    except Exception:
        swallowed()  # a missing pref must never stop the grid drawing
        return ""


def _metric_axis_label(metric: str, ctx) -> str:
    label = active_channel_label(ctx)
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL as _MLB
    prop_label = _MLB.get(ctx.state.channel.active_metric, "Mean Intensity")
    if metric == METRIC_COUNT:
        return "Cell count"
    if metric == METRIC_FRACTION:
        # Every metric is cut by some ON cutoff — ThreshFracOn or
        # ThreshCountOn — so the generic "above threshold" wording holds.
        return f"{label} fraction above threshold"
    if metric == METRIC_RATIO:
        return f"{label} (mean ratio)"
    if metric == METRIC_MEAN_ALL:
        return f"{label} {prop_label} (mean of all cells)"
    return f"{label} {prop_label} (mean above threshold)"


# ── Click handlers ───────────────────────────────────────────────────────────

def _cell_at_event(ctx, event) -> tuple[int, int] | None:
    if event.inaxes is None or event.xdata is None or event.ydata is None:
        return None
    layout = ctx.runtime.heatmap.layout_active
    if layout is None:
        return None
    c = int(round(event.xdata))
    r = int(round(event.ydata))
    if not (0 <= r < layout.rows and 0 <= c < layout.cols):
        return None
    return r, c


def _event_modifiers(event) -> tuple[bool, bool]:
    """Return ``(shift, ctrl)`` for a matplotlib mouse *event*.

    Qt's ``QMouseEvent`` carries the authoritative modifier state, but PySide6
    exposes it as a ``KeyboardModifier`` flag object that ``int()`` refuses to
    convert — the value has to be read through ``.value``. Matplotlib's own
    ``event.key`` is the fallback for events with no GUI event attached (it only
    tracks modifiers while the canvas holds keyboard focus, so it is not enough
    on its own). ``cmd`` counts as ctrl because that is what macOS reports for
    the Command key; Qt already folds Command into ``ControlModifier`` there.
    """
    gui_event = getattr(event, "guiEvent", None)
    modifiers = getattr(gui_event, "modifiers", None)
    if callable(modifiers):
        try:
            flags = modifiers()
            value = int(getattr(flags, "value", flags))
            from PySide6.QtCore import Qt as _Qt
            shift_bit = int(getattr(_Qt.ShiftModifier, "value", _Qt.ShiftModifier))
            ctrl_bit = int(getattr(_Qt.ControlModifier, "value", _Qt.ControlModifier))
            return bool(value & shift_bit), bool(value & ctrl_bit)
        except Exception:
            swallowed()  # fall through to the matplotlib key state
    keys = set((getattr(event, "key", None) or "").lower().split("+"))
    return "shift" in keys, bool(keys & {"control", "ctrl", "cmd"})


def on_heatmap_click(ctx, event) -> None:
    rc = _cell_at_event(ctx, event)
    if rc is None:
        return
    cell_index = ctx.runtime.heatmap.cell_well_index or {}
    wells_here = list(cell_index.get(rc, []))
    if not wells_here:
        return

    selected = set(ctx.state.selection.selected_wells or set())
    shift, ctrl = _event_modifiers(event)

    if shift:
        selected.update(wells_here)
    elif ctrl:
        for w in wells_here:
            if w in selected:
                selected.discard(w)
            else:
                selected.add(w)
    else:
        selected = set(wells_here)

    # ``set_selected_wells`` is the single mutation point (prev-sel tracking +
    # redraw fan-out); it takes ctx, so thread the context.
    from well_viewer.core.selection_controller import set_selected_wells
    set_selected_wells(ctx, selected)


# ── Row/col label drag-to-reorder ────────────────────────────────────────────

def _label_at_event(ctx, event) -> tuple[str, int] | None:
    """Return ("row", i) or ("col", i) if *event* is near a tick label, else None.

    Used only for the initial press that starts a drag, so the threshold is
    generous (12 px) but still requires the cursor to be close to a label.
    """
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if ax is None or canvas is None or event.x is None or event.y is None:
        return None
    try:
        renderer = canvas.get_renderer()
    except Exception:
        return None
    pad = 12
    for i, lbl in enumerate(ax.get_xticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            if (bb.x0 - pad <= event.x <= bb.x1 + pad and
                    bb.y0 - pad <= event.y <= bb.y1 + pad):
                return ("col", i)
        except Exception:
            swallowed()  # silent except
            continue
    for i, lbl in enumerate(ax.get_yticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            if (bb.x0 - pad <= event.x <= bb.x1 + pad and
                    bb.y0 - pad <= event.y <= bb.y1 + pad):
                return ("row", i)
        except Exception:
            swallowed()  # silent except
            continue
    return None


def _nearest_label_in_drag(ctx, event, kind: str) -> int | None:
    """Flexibly find the target row/col index during a drag.

    Prefers the data coordinate (when the cursor is inside the axes) so the
    user can release anywhere along a column strip or row strip.  Falls back
    to nearest-label-by-pixel when the cursor is outside the axes (e.g. over
    the label gutter itself).
    """
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if ax is None or canvas is None or event.x is None or event.y is None:
        return None

    layout = ctx.runtime.heatmap.layout_active
    if layout is None:
        layout = active_layout(ctx)

    # Inside the axes: data coords give us the exact row/col directly.
    if event.inaxes is ax:
        if kind == "col" and event.xdata is not None:
            col = int(round(event.xdata))
            if 0 <= col < layout.cols:
                return col
        if kind == "row" and event.ydata is not None:
            row = int(round(event.ydata))
            if 0 <= row < layout.rows:
                return row

    # Outside axes (e.g. label gutter): snap to nearest label by pixel centre.
    try:
        renderer = canvas.get_renderer()
    except Exception:
        return None
    labels = ax.get_xticklabels() if kind == "col" else ax.get_yticklabels()
    best: int | None = None
    best_dist = float("inf")
    for i, lbl in enumerate(labels):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            cx = (bb.x0 + bb.x1) / 2
            cy = (bb.y0 + bb.y1) / 2
            dist = abs(event.x - cx) if kind == "col" else abs(event.y - cy)
            if dist < best_dist:
                best_dist = dist
                best = i
        except Exception:
            swallowed()  # silent except
            continue
    return best


def _update_drag_label_colors(ctx) -> None:
    """Apply drag highlight colours to existing tick label artists and redraw."""
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if ax is None or canvas is None:
        return
    drag = ctx.runtime.heatmap.label_drag
    # Reset all labels to default.
    for lbl in ax.get_xticklabels():
        lbl.set_color("black")
        lbl.set_fontweight("normal")
    for lbl in ax.get_yticklabels():
        lbl.set_color("black")
        lbl.set_fontweight("normal")
    if drag is not None:
        kind = drag["kind"]
        src = drag["src"]
        over = drag.get("over")
        labels = ax.get_xticklabels() if kind == "col" else ax.get_yticklabels()
        for i, lbl in enumerate(labels):
            if i == src:
                lbl.set_color(theme.Colors.accent_pressed)
                lbl.set_fontweight("bold")
            elif over is not None and i == over and over != src:
                lbl.set_color(theme.Colors.danger)
                lbl.set_fontweight("bold")
    canvas.draw_idle()


def _set_canvas_cursor(ctx, cursor_shape) -> None:
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if canvas is None:
        return
    try:
        if cursor_shape is None:
            canvas.unsetCursor()
        else:
            canvas.setCursor(cursor_shape)
    except Exception:
        swallowed()  # was: pass (silent except)


def on_heatmap_label_drag_press(ctx, event) -> bool:
    """Start a label drag on left-press over a tick label. Returns True if consumed."""
    if getattr(event, "button", None) != 1:
        return False
    if getattr(event, "dblclick", False):
        return False
    target = _label_at_event(ctx, event)
    if target is None:
        return False
    kind, idx = target
    ctx.runtime.heatmap.label_drag = {"kind": kind, "src": idx, "over": idx}
    _update_drag_label_colors(ctx)
    try:
        from PySide6.QtCore import Qt as _Qt
        _set_canvas_cursor(ctx, _Qt.ClosedHandCursor)
    except Exception:
        swallowed()  # was: pass (silent except)
    return True


def on_heatmap_label_drag_motion(ctx, event) -> None:
    """Update drag highlight while the mouse moves."""
    drag = ctx.runtime.heatmap.label_drag
    if drag is None:
        # Show open-hand cursor when hovering over any label.
        target = _label_at_event(ctx, event)
        try:
            from PySide6.QtCore import Qt as _Qt
            if target is not None:
                _set_canvas_cursor(ctx, _Qt.OpenHandCursor)
            else:
                _set_canvas_cursor(ctx, None)
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    # Use the flexible finder so the highlight tracks the cursor everywhere.
    drag["over"] = _nearest_label_in_drag(ctx, event, drag["kind"])
    _update_drag_label_colors(ctx)


def on_heatmap_label_drag_release(ctx, event) -> None:
    """Finish drag: apply reorder if source ≠ target, then clean up."""
    drag = ctx.runtime.heatmap.label_drag
    if drag is None:
        return
    src = drag["src"]
    kind = drag["kind"]
    # Recompute target at the release position (motion may not have fired last).
    over = _nearest_label_in_drag(ctx, event, kind)
    if over is None:
        over = drag.get("over")
    ctx.runtime.heatmap.label_drag = None
    _set_canvas_cursor(ctx, None)
    if over is not None and over != src:
        _apply_label_reorder(ctx, kind, src, over)
    else:
        _update_drag_label_colors(ctx)  # just clear highlights


def _apply_label_reorder(ctx, kind: str, src: int, dst: int) -> None:
    from well_viewer.features.heatmap.sidebar_view import (
        _ensure_sidebar_layout,
        _persist_and_redraw,
        refresh_heatmap_layout_sidebar,
    )
    layout = _ensure_sidebar_layout(ctx)
    if kind == "row":
        layout.reorder_rows(src, dst)
    else:
        layout.reorder_cols(src, dst)
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_sidebar(ctx)
