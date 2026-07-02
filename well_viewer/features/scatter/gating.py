"""Scatter density-coloring + rectangular gating (no Qt; matplotlib lazy).

Two flow-cytometry-style helpers for the per-cell scatter:

* **Density coloring** — ``compute_point_density`` returns a per-point local
  2D density (0..1) so each well's cloud can be shaded by its own density
  (FACS-style), and ``density_cmap_for`` builds a per-well sequential colormap
  from the well's identity color so wells stay distinguishable.
* **Gates** — interactive rectangular boxes the user drags on the plot, each
  labeled with the percentage of points inside it (broken down per well, then
  per timepoint when several are selected).

The pure numeric half (``compute_point_density`` / ``compute_gate_stats``) is
matplotlib-free so it can be unit-tested with just numpy. The rendering /
selector half imports matplotlib lazily inside each function, mirroring the
``collect.py`` ↔ ``controller.py`` split.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from well_viewer.core.safe import swallowed
from well_viewer.domain.tp_match import tp_close_mask
from well_viewer.features.scatter.collect import timepoint_color_map

# Accent used for gate outlines / selector rubber-band (matches the app accent).
GATE_COLOR = "#e24a4a"


# ── density ──────────────────────────────────────────────────────────────────

def _box_smooth2d(H: np.ndarray, k: int = 3) -> np.ndarray:
    """Separable box-blur of a 2D histogram via ``np.convolve`` (no scipy)."""
    if k <= 1:
        return H
    kern = np.ones(k, dtype=float) / float(k)
    H = np.apply_along_axis(lambda m: np.convolve(m, kern, mode="same"), 0, H)
    H = np.apply_along_axis(lambda m: np.convolve(m, kern, mode="same"), 1, H)
    return H


def _hist_density(vx: np.ndarray, vy: np.ndarray, bins: int = 256) -> np.ndarray:
    """O(N) density estimate: bin counts of each point's 2D histogram cell.

    Used for large point clouds and whenever the KDE path is unavailable
    (scipy missing) or degenerate (singular covariance).
    """
    H, xe, ye = np.histogram2d(vx, vy, bins=bins)
    H = _box_smooth2d(H, k=3)
    ix = np.clip(np.digitize(vx, xe) - 1, 0, H.shape[0] - 1)
    iy = np.clip(np.digitize(vy, ye) - 1, 0, H.shape[1] - 1)
    return H[ix, iy]


def _density_values(vx: np.ndarray, vy: np.ndarray, max_kde_n: int) -> np.ndarray:
    """Raw (un-normalized) density at each (vx, vy). KDE when small, hist else."""
    n = vx.size
    # Zero variance in a dimension makes the KDE covariance singular.
    if np.ptp(vx) == 0.0 or np.ptp(vy) == 0.0:
        return _hist_density(vx, vy)
    if n <= max_kde_n:
        try:
            from scipy.stats import gaussian_kde
            kde = gaussian_kde(np.vstack([vx, vy]))
            return np.asarray(kde(np.vstack([vx, vy])), dtype=float)
        except Exception:
            return _hist_density(vx, vy)
    return _hist_density(vx, vy)


def compute_point_density(
    x,
    y,
    *,
    x_log: bool = False,
    y_log: bool = False,
    max_kde_n: int = 20000,
) -> np.ndarray:
    """Per-point local 2D density, normalized to ``0..1`` (aligned to *x*/*y*).

    ``x_log`` / ``y_log`` compute the density in log10 space (FACS plots are
    usually log; estimating in linear space mis-colors a log-displayed cloud).
    Non-finite / non-positive-under-log points are assigned the minimum density
    so they never raise and sort to the back.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out
    if n < 3:
        return np.full(n, 0.5)

    xs = x.astype(float, copy=True)
    ys = y.astype(float, copy=True)
    valid = np.isfinite(xs) & np.isfinite(ys)
    # Match the axis' log10(x+1) display transform so the density shading lines
    # up with where the points are drawn (and zero-valued cells still count).
    if x_log:
        xs = np.log10(np.clip(xs, 0.0, None) + 1.0)
    if y_log:
        ys = np.log10(np.clip(ys, 0.0, None) + 1.0)

    if int(valid.sum()) < 3:
        return np.full(n, 0.5)

    dens = _density_values(xs[valid], ys[valid], max_kde_n)
    dmin = float(np.nanmin(dens))
    dmax = float(np.nanmax(dens))
    if dmax > dmin:
        dens = (dens - dmin) / (dmax - dmin)
    else:
        dens = np.full(dens.shape, 0.5)
    # Masked-out points (non-finite / non-positive under log) keep 0.0.
    out[valid] = dens
    return out


def density_cmap_for(base_color):
    """Sequential colormap for a well's density, keyed on its identity color.

    Runs from a clearly-tinted low end (≈45% toward white — saturated enough to
    read, not the near-white that made the old ramp look faint), through the
    full identity color at mid density, to a darkened core at the top. Keeping
    each well's hue lets overlapping wells stay distinguishable while the
    light→saturated→dark span gives the cloud real visible contrast.
    """
    from matplotlib.colors import LinearSegmentedColormap, to_rgb
    rgb = np.asarray(to_rgb(base_color), dtype=float)
    light = rgb + (1.0 - rgb) * 0.45  # low density: visible tint, not near-white
    dark = rgb * 0.55                 # high density: darkened, saturated core
    return LinearSegmentedColormap.from_list("aw_density", [light, rgb, dark])


# Perceptual stretch applied to normalized density before colormapping. KDE
# density is right-skewed (most points low), so a <1 gamma lifts the bulk into
# the visible part of the ramp instead of leaving it pinned near the light end.
DENSITY_GAMMA = 0.6


# ── point rendering ──────────────────────────────────────────────────────────

def render_scatter_points(
    app,
    ax,
    scatter_data: Dict[str, Dict[str, Any]],
    *,
    density_on: bool,
    x_log: bool,
    y_log: bool,
    marker_size: float,
    color_by_tp: bool = False,
):
    """Plot one artist per well and return ``(interaction_points, legend_handles)``.

    Solid mode draws a markers-only ``Line2D`` per well (so each well is an
    independent ``<g>`` in SVG export and picks up the marker-style prefs).
    Density mode draws a per-point ``PathCollection`` shaded by each well's own
    local 2D density via a colormap derived from the well's identity color, plus
    one proxy ``Line2D`` per group so the legend still maps group → color.

    ``color_by_tp`` (the "Color by time" toggle) colours each point by its
    timepoint instead of by well identity, so a well's cloud shows its evolution
    over time on one axis. It draws one markers-only ``Line2D`` per
    ``(well, timepoint)`` in a shared viridis colour (see
    ``collect.timepoint_color_map``) and returns one legend proxy per timepoint.
    It takes precedence over density shading, whose colormap is keyed to well
    identity and would be meaningless per-timepoint.
    """
    from matplotlib.lines import Line2D

    # Scatter axes default to log/log and must stay that way regardless of the
    # shared x_log/y_log export pref (which other plot types reset). Tag the
    # axis with the scatter-specific choice; apply_export_style_prefs honors
    # ``_aw_force_xlog`` / ``_aw_force_ylog`` over the shared pref. Also set
    # the scale directly NOW so the first-paint after a cold open isn't
    # left at the matplotlib default (linear) until the user toggles the
    # checkbox off + on — apply_export_style_prefs runs later in the
    # redraw and would normally pick this up, but only if data is in
    # the axis at that point; setting it eagerly avoids the
    # "checkbox is checked but plot is linear until I toggle" reload bug.
    try:
        from well_viewer.core.plot_style import apply_axis_log_scale
        ax._aw_force_xlog = bool(x_log)
        ax._aw_force_ylog = bool(y_log)
        # Scatter log axes use a log10(x+1) display transform so zero-valued
        # cells stay visible; the data itself is untouched (gates/clicks/exports
        # remain in raw units). See plot_style.apply_axis_log_scale.
        ax._aw_log_plus1 = True
        apply_axis_log_scale(ax, "x", x_log)
        apply_axis_log_scale(ax, "y", y_log)
    except Exception:
        swallowed()

    interaction_points: List[Tuple[float, float, Tuple[str, str, str, int]]] = []
    legend_handles: List[Any] = []

    # decision: colour by timepoint (viridis), overrides identity colour. Build
    # the map once from every tp present so all wells/panels share one scale.
    tp_color: Dict[float, Any] = {}
    if color_by_tp:
        all_tps: List[float] = []
        for _d in scatter_data.values():
            all_tps.extend(_d.get("tp", []) or [])
        tp_color = timepoint_color_map(all_tps)
        legend_handles = [
            Line2D([], [], marker="o", linestyle="none", color=tp_color[tp],
                   label=f"t={tp:g}h")
            for tp in sorted(tp_color)
        ]

    for label, data in scatter_data.items():
        # Legend text prefers the sample-definition display label; the dict key
        # (`label`) stays the raw identity used for gids / metadata lookup.
        legend_label = data.get("label") or label
        wells_breakdown = data.get("wells") or {label: {
            "x": data["x"], "y": data["y"],
            "tp": data.get("tp", []),
            "metadata": data.get("metadata", []),
        }}
        first_well_in_group = True
        for well_label, well_data in wells_breakdown.items():
            # ``gid`` becomes the SVG <g> id so Illustrator can target each well.
            gid = (
                f"well_{well_label}__{label}"
                if label != well_label else f"well_{well_label}"
            )
            if color_by_tp:
                wx = np.asarray(well_data["x"], dtype=float)
                wy = np.asarray(well_data["y"], dtype=float)
                wtp = np.asarray(well_data.get("tp", []), dtype=float)
                if wx.size and wtp.size == wx.size:
                    for tp in sorted(tp_color):
                        m = tp_close_mask(wtp, tp)
                        if not m.any():
                            continue
                        (line,) = ax.plot(
                            wx[m], wy[m], marker="o", linestyle="none",
                            label="_nolegend_", color=tp_color[tp],
                            alpha=0.6, markersize=6, markeredgecolor="none",
                        )
                        # Tag scatter-only so export-style never adds a line.
                        line._scatter_only_artist = True
                        try:
                            line.set_gid(f"{gid}__t{tp:g}")
                        except Exception:
                            swallowed()
            elif density_on:
                wx = np.asarray(well_data["x"], dtype=float)
                wy = np.asarray(well_data["y"], dtype=float)
                if wx.size:
                    dens = compute_point_density(wx, wy, x_log=x_log, y_log=y_log)
                    order = np.argsort(dens)  # dense points drawn on top
                    coll = ax.scatter(
                        wx[order], wy[order],
                        c=np.power(dens[order], DENSITY_GAMMA),
                        cmap=density_cmap_for(data["color"]),
                        s=marker_size * marker_size, edgecolors="none",
                        alpha=0.85, zorder=5,
                    )
                    # Tagged so apply_export_style sizes it but never collapses
                    # the per-point colormap to one color (see plot_style).
                    coll._aw_density_collection = True
                    try:
                        coll.set_gid(gid)
                    except Exception:
                        swallowed()
                if first_well_in_group:
                    legend_handles.append(Line2D(
                        [], [], marker="o", linestyle="none",
                        color=data["color"], label=legend_label,
                    ))
            else:
                (line,) = ax.plot(
                    well_data["x"], well_data["y"], marker="o", linestyle="none",
                    label=legend_label if first_well_in_group else "_nolegend_",
                    color=data["color"], alpha=0.6, markersize=6,
                    markeredgecolor="none",
                )
                # Tag scatter-only so export-style never adds a connecting line.
                line._scatter_only_artist = True
                try:
                    line.set_gid(gid)
                except Exception:
                    swallowed()
            first_well_in_group = False

        if not hasattr(app, "_scatter_metadata"):
            app._scatter_metadata = {}
        app._scatter_metadata[label] = data["metadata"]
        interaction_points.extend(
            (x, y, meta)
            for x, y, meta in zip(data["x"], data["y"], data["metadata"])
        )

    return interaction_points, legend_handles


# ── gate statistics ──────────────────────────────────────────────────────────

def _gate_on_current_axes(app, gate) -> bool:
    """True when *gate* was drawn on the axes currently displayed.

    A gate's box is in the data space of its own ``x_col``/``y_col``; on any
    other axis pair those coordinates are meaningless, so the gate must not be
    drawn or hit-tested there. ``app._scatter_axes_cols`` is set each redraw
    (scatter.controller). Absent (no redraw yet) ⇒ don't hide."""
    cols = getattr(app, "_scatter_axes_cols", None)
    if not cols:
        return True
    gx, gy = gate.get("x_col"), gate.get("y_col")
    if gx is None or gy is None:
        return True  # unstamped gate (no recorded axes) — can't match, don't hide
    return gx == cols[0] and gy == cols[1]


def _norm_gate(g: Dict[str, float]) -> Dict[str, float]:
    """Return a copy of *g* with ``x0<=x1`` and ``y0<=y1`` (drag may flip them)."""
    x0, x1 = sorted((g["x0"], g["x1"]))
    y0, y1 = sorted((g["y0"], g["y1"]))
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _in_gate(xs: np.ndarray, ys: np.ndarray, gate: Dict[str, float]) -> np.ndarray:
    return (
        (xs >= gate["x0"]) & (xs <= gate["x1"])
        & (ys >= gate["y0"]) & (ys <= gate["y1"])
    )


def _pct(xs: np.ndarray, ys: np.ndarray, gate: Dict[str, float]):
    """Return ``(pct, n_inside, total)`` or ``(None, 0, 0)`` when empty."""
    tot = int(xs.size)
    if tot == 0:
        return None, 0, 0
    nin = int(_in_gate(xs, ys, gate).sum())
    return 100.0 * nin / tot, nin, tot


def compute_gate_inclusion(df, gates, ratios=None) -> np.ndarray:
    """Boolean array over *df*'s rows: ``True`` for any cell inside ANY gate.

    The Cell Gating tab uses this to turn the interactive scatter gates into the
    ``Included`` mask (the union — a cell in *any* gated population is kept).
    Each gate carries the metric columns it was drawn on (``x_col`` / ``y_col``,
    stamped in :func:`_on_gate_drawn`); values are resolved through
    :func:`resolve_value_series` so ratio / shape columns work exactly as on the
    plot. A gate missing its columns, or whose columns are absent from *df*,
    resolves to all-NaN and so contributes nothing (NaN comparisons are False).

    With no usable gates the mask is all-``False`` — the caller decides what an
    empty gate set means (the worker treats "mode on, no gates" as no
    restriction before reaching here).
    """
    from well_viewer.data.data_loading import resolve_value_series

    n = len(df)
    inside = np.zeros(n, dtype=bool)
    for gate in gates or []:
        x_col = gate.get("x_col")
        y_col = gate.get("y_col")
        if not x_col or not y_col:
            continue
        xs = resolve_value_series(df, x_col, ratios).to_numpy()
        ys = resolve_value_series(df, y_col, ratios).to_numpy()
        inside |= _in_gate(xs, ys, _norm_gate(gate))
    return inside


def compute_gate_stats(
    scatter_data: Dict[str, Dict[str, Any]],
    gate: Dict[str, float],
    tps_list: List[float],
) -> List[Tuple[str, Optional[str], int]]:
    """Label rows ``(text, color_or_None, indent)`` for one gate.

    Percentages are denominated by each group's own total (the FACS reading:
    "what fraction of well A fell in this gate"). Layout:

    * one group & one tp → a single ``"42.3%"`` line;
    * multiple groups → one ``"<group>: %"`` line each, colored by the group's
      base color, plus indented per-tp sub-lines when >1 tp is selected;
    * one group & multiple tps → one ``"t=Xh: %"`` line per timepoint.
    """
    groups = list(scatter_data.items())
    multi_groups = len(groups) > 1
    multi_tps = len(tps_list) > 1
    rows: List[Tuple[str, Optional[str], int]] = []

    if not multi_groups and not multi_tps:
        if groups:
            _, data = groups[0]
            pct, _, _ = _pct(np.asarray(data["x"], float),
                             np.asarray(data["y"], float), gate)
            if pct is not None:
                rows.append((f"{pct:.1f}%", None, 0))
        return rows

    for gname, data in groups:
        xs = np.asarray(data["x"], float)
        ys = np.asarray(data["y"], float)
        tps = np.asarray(data.get("tp", []), float)
        color = data.get("color")

        def _tp_rows(indent: int) -> None:
            if not (multi_tps and tps.size == xs.size):
                return
            for t in sorted(tps_list):
                m = tp_close_mask(tps, t)
                pct_t, _, tot_t = _pct(xs[m], ys[m], gate)
                if pct_t is not None and tot_t > 0:
                    rows.append((f"t={float(t):g}h: {pct_t:.1f}%", color, indent))

        if multi_groups:
            pct, _, _ = _pct(xs, ys, gate)
            if pct is not None:
                rows.append((f"{gname}: {pct:.1f}%", color, 0))
            _tp_rows(1)
        else:
            # Single group, multiple timepoints.
            _tp_rows(0)

    return rows


# ── gate rendering ───────────────────────────────────────────────────────────

def _notify_gates_changed(app) -> None:
    """Tell listeners the gate set changed so their gate-count UI stays accurate
    after a gate is drawn / cleared / deleted.

    Three independent listeners may register: the Properties sidebar's
    ``_scatter_gate_count_cb`` (the "N gate(s)" label), the Cell Gating tab's
    ``_scatter_gates_changed_cb`` (its scatter-gate summary), and the Cell
    Browser's ``_cell_browser_gates_changed_cb`` (the live gate-sourced gallery,
    N1). All are optional.
    """
    # A gate change alters the scatter-gate on-population, so cached stats keyed
    # off it are stale — drop the cache (audit S4). The cache key also folds in
    # the gate signature; invalidating here keeps the cache from accumulating
    # dead entries per edit.
    inv = getattr(app, "_invalidate_stats_cache", None)
    if callable(inv):
        try:
            inv()
        except Exception:
            swallowed()
    for attr in ("_scatter_gate_count_cb", "_scatter_gates_changed_cb",
                 "_cell_browser_gates_changed_cb"):
        cb = getattr(app, attr, None)
        if callable(cb):
            try:
                cb()
            except Exception:
                swallowed()


def draw_gates(app, ax, scatter_data, tps_list, ink_color: str,
               *, handles: bool = True) -> None:
    """Draw every stored gate on *ax* as a dashed rectangle + its percentage label.

    ``handles`` draws the draggable corner squares (shown only on the
    interactive panel — the overlay axis, or the top panel of the
    "Separate axes" layout).
    """
    # "Show gates" toggle off → keep the gates in memory but don't render them.
    if getattr(app, "_scatter_gates_hidden", False):
        return
    from matplotlib.patches import Rectangle
    gates = getattr(app, "_scatter_gates", None) or []
    # Edit handles are only meaningful when not drawing a new gate (the
    # RectangleSelector owns the drag in draw mode) and on an interactive panel.
    editable = handles and not getattr(app, "_scatter_gate_draw_mode", False)
    for gate in gates:
        # Only draw gates belonging to the axes currently on screen.
        if not _gate_on_current_axes(app, gate):
            continue
        gn = _norm_gate(gate)  # drag may transiently flip a corner past another
        w = gn["x1"] - gn["x0"]
        h = gn["y1"] - gn["y0"]
        rect = Rectangle(
            (gn["x0"], gn["y0"]), w, h,
            fill=False, edgecolor=GATE_COLOR, linewidth=1.2,
            linestyle="--", zorder=20,
        )
        rect._scatter_gate_artist = True  # noqa: SLF001 (tag for our own use)
        ax.add_patch(rect)

        # Corner handles advertise that the box is editable by dragging its
        # edges/corners. Drawn as a PathCollection that is NOT tagged
        # ``_aw_marker_collection`` / ``_aw_density_collection``, so the
        # export-style hook leaves it untouched.
        if editable:
            handles = ax.scatter(
                [gn["x0"], gn["x1"], gn["x0"], gn["x1"]],
                [gn["y0"], gn["y0"], gn["y1"], gn["y1"]],
                marker="s", s=28, facecolors="white", edgecolors=GATE_COLOR,
                linewidths=1.0, zorder=22,
            )
            handles._scatter_gate_artist = True  # noqa: SLF001

        rows = compute_gate_stats(scatter_data, gn, tps_list)
        # Lead the label with the gate's name so it matches the Cell Gating
        # filtering-gate dropdown.
        gname = str(gate.get("name") or "")
        if gname:
            rows = [(gname, ink_color, 0), *rows]
        if not rows:
            continue
        # Stack the label lines from the gate's top-left corner downward, using
        # point offsets so spacing is constant regardless of data scale. Each
        # group line carries its own color so it matches the legend swatch.
        line_h = 12.0  # points between rows
        for i, (text, color, indent) in enumerate(rows):
            ann = ax.annotate(
                text,
                xy=(gn["x0"], gn["y1"]),
                xycoords="data",
                xytext=(4.0 + indent * 10.0, -4.0 - i * line_h),
                textcoords="offset points",
                ha="left", va="top",
                fontsize=7,
                color=(color or ink_color),
                zorder=21,
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                          edgecolor="none", alpha=0.7),
            )
            ann._scatter_gate_artist = True  # noqa: SLF001
            ann.set_clip_on(False)


# ── gate interaction (RectangleSelector) ─────────────────────────────────────

def _make_selector(app, ax):
    """Construct a RectangleSelector across matplotlib versions (props vs rectprops)."""
    from matplotlib.widgets import RectangleSelector

    def _onselect(eclick, erelease):
        _on_gate_drawn(app, eclick, erelease)

    style = dict(facecolor="none", edgecolor=GATE_COLOR,
                 linewidth=1.2, linestyle="--")
    try:  # matplotlib >= 3.5
        return RectangleSelector(
            ax, _onselect, useblit=True, button=[1],
            interactive=False, props=style,
        )
    except TypeError:  # older API
        return RectangleSelector(
            ax, _onselect, useblit=True, button=[1],
            drawtype="box", rectprops=style,
        )


def set_gate_draw_mode(app, on: bool) -> None:
    """Arm / disarm rectangular-gate drawing on the per-cell scatter axes."""
    app._scatter_gate_draw_mode = bool(on)
    ax = getattr(app, "_ax_scatter", None)
    if ax is None:
        return
    sel = getattr(app, "_scatter_gate_selector", None)
    if on:
        # Always rebuild the selector against the CURRENT axes. A cached selector
        # is bound to whatever ``_ax_scatter`` was when it was made, and an
        # intervening redraw (``ax.clear()`` on overlay, or a full ``fig.clear()``
        # rebuild in "Separate axes" mode) leaves it stale — useblit keeps a
        # background snapshot of the old axes and its drag artists were removed.
        # Reusing it captured drag coordinates against the dead axes, so the
        # FIRST gate (fresh selector) was correct but every later gate (and any
        # gate in faceted mode, where the figure is rebuilt) came out wrong.
        # Discarding + recreating each arm guarantees a clean selector on the
        # live axes.
        if sel is not None:
            try:
                sel.set_active(False)
            except Exception:
                swallowed()
            try:
                sel.disconnect_events()
            except Exception:
                swallowed()
        try:
            sel = _make_selector(app, ax)
        except Exception:
            swallowed()
            app._scatter_gate_selector = None
            return
        app._scatter_gate_selector = sel
        try:
            sel.set_active(True)
        except Exception:
            swallowed()
    elif sel is not None:
        try:
            sel.set_active(False)
        except Exception:
            swallowed()


def _next_gate_name(app) -> str:
    """Default ``Gate N`` name, N = first integer not already taken."""
    existing = {
        str(g.get("name") or "")
        for g in (getattr(app, "_scatter_gates", None) or [])
    }
    n = 1
    while f"Gate {n}" in existing:
        n += 1
    return f"Gate {n}"


def rename_gate(app, idx: int, name: str) -> bool:
    """Rename gate *idx* to *name*, keeping names non-empty and unique.

    Returns ``True`` when the rename took effect (and notifies listeners so the
    Cell Gating dropdown + inclusion recompute follow). A blank or duplicate
    name is rejected (returns ``False``) so the caller can revert its field.
    """
    gates = getattr(app, "_scatter_gates", None) or []
    if not (0 <= idx < len(gates)):
        return False
    new = str(name).strip()
    old = str(gates[idx].get("name") or "")
    if not new or new == old:
        return False
    if any(i != idx and str(g.get("name") or "") == new for i, g in enumerate(gates)):
        return False  # name already taken by another gate
    gates[idx]["name"] = new
    # Follow the rename in both Cell Gating selection sets (gating + thresh) so
    # the selection isn't lost (None means "all", nothing to update).
    for attr in ("_cell_gating_gating_choices", "_cell_gating_thresh_choices"):
        choices = getattr(app, attr, None)
        if isinstance(choices, set) and old in choices:
            choices.discard(old)
            choices.add(new)
    _notify_gates_changed(app)
    _redraw(app)
    return True


def _on_gate_drawn(app, eclick, erelease) -> None:
    """RectangleSelector callback: store the dragged box and redraw."""
    coords = (eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata)
    if any(c is None for c in coords):
        return
    x0, x1 = sorted((float(eclick.xdata), float(erelease.xdata)))
    y0, y1 = sorted((float(eclick.ydata), float(erelease.ydata)))
    if x1 - x0 <= 0 or y1 - y0 <= 0:  # ignore zero-area click-drags
        return
    if not getattr(app, "_scatter_gates", None):
        app._scatter_gates = []
    # Stamp the gate with the metric columns of the axes it was drawn on so it
    # can be re-applied to a dataframe out of context (the Cell Gating tab uses
    # this to turn a gate into an inclusion mask). The active columns are already
    # cached on the last redraw — see scatter.controller.redraw_scatter.
    cache = getattr(app, "_scatter_interaction_cache", None) or {}
    # Snapshot the wells the scatter was showing when the gate was drawn, so a
    # downstream consumer (the Cell Browser gate-source gallery) can honour the
    # gate's well context instead of an independent selection. Empty ⇒ "all".
    try:
        drawn_wells = sorted(app._app_state.selection.selected_wells)
    except Exception:
        drawn_wells = []
    # Auto-name silently (Gate 1, Gate 2, …); rename via the sidebar gate list.
    app._scatter_gates.append({
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "x_col": cache.get("col_x"), "y_col": cache.get("col_y"),
        "name": _next_gate_name(app),
        "wells": drawn_wells,
    })
    _notify_gates_changed(app)
    # One gate per arming: exit create mode so the next press edits/clicks
    # rather than drawing another box. Sync the sidebar's "Draw gate" toggle.
    set_gate_draw_mode(app, False)
    sync = getattr(app, "_scatter_gate_draw_sync", None)
    if callable(sync):
        sync(False)
    _redraw(app)


def clear_gates(app) -> None:
    """Drop every gate and redraw."""
    app._scatter_gates = []
    _notify_gates_changed(app)
    _redraw(app)


def refresh_gate_selector(app) -> None:
    """Rebuild the selector after a redraw (``ax.clear()`` voids the old one)."""
    if not getattr(app, "_scatter_gate_draw_mode", False):
        return
    app._scatter_gate_selector = None
    set_gate_draw_mode(app, True)


def _redraw(app) -> None:
    """Trigger a scatter redraw via the callback the tab wires at build time.

    The callback (``redraw_scatter_orchestrate``) is stashed on the app by
    ``build_scatter_cells_tab`` so this module never imports the controller —
    keeping the scatter package free of an internal import cycle.
    """
    cb = getattr(app, "_scatter_gate_redraw_cb", None)
    if callable(cb):
        cb(app)


# ── gate editing (drag the edges / corners of an existing gate) ───────────────

def gate_edge_at(app, event, tol_px: float = 8.0):
    """Hit-test: return ``(gate_idx, handle)`` for the edge/corner under the
    pointer, else ``None``. ``handle`` is one of
    ``l/r/t/b/tl/tr/bl/br``. Works in display (pixel) space so the tolerance is
    uniform regardless of axis scale (incl. log). Hidden gates are not hittable.
    """
    if getattr(app, "_scatter_gates_hidden", False):
        return None
    ax = getattr(app, "_ax_scatter", None)
    if ax is None or getattr(event, "inaxes", None) is not ax:
        return None
    if event.x is None or event.y is None:
        return None
    px, py = float(event.x), float(event.y)
    gates = getattr(app, "_scatter_gates", None) or []
    # Iterate top-most (last drawn) first so the gate visually on top wins.
    for idx in range(len(gates) - 1, -1, -1):
        # Gates from other axes aren't drawn here, so they're not hittable.
        if not _gate_on_current_axes(app, gates[idx]):
            continue
        gn = _norm_gate(gates[idx])
        x0d, y0d = ax.transData.transform((gn["x0"], gn["y0"]))
        x1d, y1d = ax.transData.transform((gn["x1"], gn["y1"]))
        left, right = min(x0d, x1d), max(x0d, x1d)
        bottom, top = min(y0d, y1d), max(y0d, y1d)
        if not (left - tol_px <= px <= right + tol_px
                and bottom - tol_px <= py <= top + tol_px):
            continue
        near_l = abs(px - left) <= tol_px
        near_r = abs(px - right) <= tol_px
        near_b = abs(py - bottom) <= tol_px
        near_t = abs(py - top) <= tol_px
        handle = None
        if near_l and near_b:
            handle = "bl"
        elif near_l and near_t:
            handle = "tl"
        elif near_r and near_b:
            handle = "br"
        elif near_r and near_t:
            handle = "tr"
        elif near_l:
            handle = "l"
        elif near_r:
            handle = "r"
        elif near_b:
            handle = "b"
        elif near_t:
            handle = "t"
        if handle is not None:
            return idx, handle
    return None


def on_gate_edit_press(app, event) -> None:
    """Begin editing if the press landed on an existing gate's edge/corner."""
    if getattr(event, "button", None) != 1:
        return
    if getattr(app, "_scatter_gate_draw_mode", False):
        return  # draw mode owns the drag (creating a new gate)
    hit = gate_edge_at(app, event)
    if hit is None:
        return
    idx, handle = hit
    app._scatter_gate_edit = {"idx": idx, "handle": handle}


def on_gate_edit_motion(app, event) -> None:
    """Resize the active gate while dragging; otherwise hint the cursor."""
    ed = getattr(app, "_scatter_gate_edit", None)
    if not ed:
        _update_gate_cursor(app, event)
        return
    if event.xdata is None or event.ydata is None:
        return
    gates = getattr(app, "_scatter_gates", None) or []
    if not (0 <= ed["idx"] < len(gates)):
        app._scatter_gate_edit = None
        return
    g = gates[ed["idx"]]
    handle = ed["handle"]
    if "l" in handle:
        g["x0"] = float(event.xdata)
    if "r" in handle:
        g["x1"] = float(event.xdata)
    if "b" in handle:
        g["y0"] = float(event.ydata)
    if "t" in handle:
        g["y1"] = float(event.ydata)
    redraw_gates_only(app)


def on_gate_edit_release(app, event) -> None:
    """Finalise an edit: normalise the box, dropping it if collapsed to zero area."""
    ed = getattr(app, "_scatter_gate_edit", None)
    if not ed:
        return
    app._scatter_gate_edit = None
    gates = getattr(app, "_scatter_gates", None) or []
    if not (0 <= ed["idx"] < len(gates)):
        return
    g = gates[ed["idx"]]
    nx0, nx1 = sorted((g["x0"], g["x1"]))
    ny0, ny1 = sorted((g["y0"], g["y1"]))
    if nx1 - nx0 <= 0 or ny1 - ny0 <= 0:
        del gates[ed["idx"]]
    else:
        g["x0"], g["x1"], g["y0"], g["y1"] = nx0, nx1, ny0, ny1
    # A resize changes gate membership too, so notify on every finalised edit
    # (not just deletes) — listeners recompute the gated inclusion.
    _notify_gates_changed(app)
    redraw_gates_only(app)


def paint_gates(app, entries, cols=None) -> None:
    """Draw gates across one or more panels and remember how, so an edit drag
    can repaint them all.

    *entries* is a list of ``(ax, scatter_data, tps_list, ink, handles)`` — one
    per panel. The overlay layout passes a single entry; "Separate axes" passes
    one per stacked panel (handles only on the interactive top panel). Stored on
    ``app._scatter_gate_render`` so :func:`redraw_gates_only` repaints every
    panel during a drag — fixing the case where resizing on the top panel left
    the panels below stale.

    *cols* is the ``(col_x, col_y)`` pair currently on the axes; stored so gate
    rendering / hit-testing can hide gates drawn on a different X/Y pair.
    """
    if cols is not None:
        app._scatter_axes_cols = cols
    app._scatter_gate_render = list(entries)
    for ax, scatter_data, tps_list, ink, handles in app._scatter_gate_render:
        draw_gates(app, ax, scatter_data, tps_list, ink, handles=handles)


def redraw_gates_only(app) -> None:
    """Repaint just the gate artists on every recorded panel (no point re-plot).

    Points don't move during an edit drag — only the gate does — so this clears
    and redraws the gate artists on each panel from the stored render entries.
    Falls back to a full redraw when nothing has been recorded yet.
    """
    entries = getattr(app, "_scatter_gate_render", None)
    if not entries:
        _redraw(app)
        return
    for ax, scatter_data, tps_list, ink, handles in entries:
        for group in (list(ax.collections), list(ax.patches), list(ax.texts)):
            for artist in group:
                if getattr(artist, "_scatter_gate_artist", False):
                    try:
                        artist.remove()
                    except Exception:
                        swallowed()
        draw_gates(app, ax, scatter_data, tps_list, ink, handles=handles)
    canvas = getattr(app, "_scatter_canvas", None)
    if canvas is not None:
        canvas.draw_idle()


def _update_gate_cursor(app, event) -> None:
    """Show a resize cursor when hovering a gate edge/corner (discoverability)."""
    canvas = getattr(app, "_scatter_canvas", None)
    if canvas is None or not hasattr(canvas, "setCursor"):
        return
    hit = gate_edge_at(app, event)
    try:
        from PySide6.QtCore import Qt as _Qt
    except Exception:
        return
    cursors = {
        "l": _Qt.SizeHorCursor, "r": _Qt.SizeHorCursor,
        "t": _Qt.SizeVerCursor, "b": _Qt.SizeVerCursor,
        "tl": _Qt.SizeFDiagCursor, "br": _Qt.SizeFDiagCursor,
        "tr": _Qt.SizeBDiagCursor, "bl": _Qt.SizeBDiagCursor,
    }
    if hit is not None:
        canvas.setCursor(cursors.get(hit[1], _Qt.ArrowCursor))
        app._scatter_gate_cursor_set = True
    elif getattr(app, "_scatter_gate_cursor_set", False):
        canvas.unsetCursor()
        app._scatter_gate_cursor_set = False
