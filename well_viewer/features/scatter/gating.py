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
    if x_log:
        valid &= xs > 0
        xs = np.where(xs > 0, np.log10(np.where(xs > 0, xs, 1.0)), np.nan)
    if y_log:
        valid &= ys > 0
        ys = np.where(ys > 0, np.log10(np.where(ys > 0, ys, 1.0)), np.nan)

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
    """Sequential colormap from a light tint of *base_color* to the full color.

    Encodes density by lightness/saturation while keeping each well's identity
    hue, so overlapping wells stay distinguishable under per-well density mode.
    """
    from matplotlib.colors import LinearSegmentedColormap, to_rgb
    rgb = np.asarray(to_rgb(base_color), dtype=float)
    light = rgb + (1.0 - rgb) * 0.82  # blend ~82% toward white
    return LinearSegmentedColormap.from_list("aw_density", [light, rgb])


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
):
    """Plot one artist per well and return ``(interaction_points, legend_handles)``.

    Solid mode draws a markers-only ``Line2D`` per well (so each well is an
    independent ``<g>`` in SVG export and picks up the marker-style prefs).
    Density mode draws a per-point ``PathCollection`` shaded by each well's own
    local 2D density via a colormap derived from the well's identity color, plus
    one proxy ``Line2D`` per group so the legend still maps group → color.
    """
    from matplotlib.lines import Line2D

    interaction_points: List[Tuple[float, float, Tuple[str, str, str, int]]] = []
    density_proxies: List[Any] = []

    for label, data in scatter_data.items():
        wells_breakdown = data.get("wells") or {label: {
            "x": data["x"], "y": data["y"],
            "metadata": data.get("metadata", []),
        }}
        first_well_in_group = True
        for well_label, well_data in wells_breakdown.items():
            # ``gid`` becomes the SVG <g> id so Illustrator can target each well.
            gid = (
                f"well_{well_label}__{label}"
                if label != well_label else f"well_{well_label}"
            )
            if density_on:
                wx = np.asarray(well_data["x"], dtype=float)
                wy = np.asarray(well_data["y"], dtype=float)
                if wx.size:
                    dens = compute_point_density(wx, wy, x_log=x_log, y_log=y_log)
                    order = np.argsort(dens)  # dense points drawn on top
                    coll = ax.scatter(
                        wx[order], wy[order], c=dens[order],
                        cmap=density_cmap_for(data["color"]),
                        s=marker_size * marker_size, edgecolors="none",
                        alpha=0.6, zorder=5,
                    )
                    # Tagged so apply_export_style sizes it but never collapses
                    # the per-point colormap to one color (see plot_style).
                    coll._aw_density_collection = True
                    try:
                        coll.set_gid(gid)
                    except Exception:
                        swallowed()
                if first_well_in_group:
                    density_proxies.append(Line2D(
                        [], [], marker="o", linestyle="none",
                        color=data["color"], label=label,
                    ))
            else:
                (line,) = ax.plot(
                    well_data["x"], well_data["y"], marker="o", linestyle="none",
                    label=label if first_well_in_group else "_nolegend_",
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

    return interaction_points, density_proxies


# ── gate statistics ──────────────────────────────────────────────────────────

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
                m = np.abs(tps - float(t)) <= 1e-6
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
    """Tell the Properties sidebar (if present) that the gate set changed, so its
    gate-count label stays accurate after a gate is drawn/cleared."""
    cb = getattr(app, "_scatter_gate_count_cb", None)
    if callable(cb):
        try:
            cb()
        except Exception:
            swallowed()


def draw_gates(app, ax, scatter_data, tps_list, ink_color: str) -> None:
    """Draw every stored gate as a dashed rectangle + its percentage label."""
    # "Show gates" toggle off → keep the gates in memory but don't render them.
    if getattr(app, "_scatter_gates_hidden", False):
        return
    from matplotlib.patches import Rectangle
    gates = getattr(app, "_scatter_gates", None) or []
    for gate in gates:
        w = gate["x1"] - gate["x0"]
        h = gate["y1"] - gate["y0"]
        rect = Rectangle(
            (gate["x0"], gate["y0"]), w, h,
            fill=False, edgecolor=GATE_COLOR, linewidth=1.2,
            linestyle="--", zorder=20,
        )
        rect._scatter_gate_artist = True  # noqa: SLF001 (tag for our own use)
        ax.add_patch(rect)

        rows = compute_gate_stats(scatter_data, gate, tps_list)
        if not rows:
            continue
        # Stack the label lines from the gate's top-left corner downward, using
        # point offsets so spacing is constant regardless of data scale. Each
        # group line carries its own color so it matches the legend swatch.
        line_h = 12.0  # points between rows
        for i, (text, color, indent) in enumerate(rows):
            ann = ax.annotate(
                text,
                xy=(gate["x0"], gate["y1"]),
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
        if sel is None:
            try:
                sel = _make_selector(app, ax)
            except Exception:
                swallowed()
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
    app._scatter_gates.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    _notify_gates_changed(app)
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
