"""Spatial-analysis renderer — pure matplotlib.

Reads the controller's cached snapshot (``last_result``) and draws whichever
viz is selected. Six MVP vizs: spatial map, NND histogram, Ripley L,
colony size histogram, radial profile, boundary-vs-interior split.
"""

from __future__ import annotations

import logging

import numpy as np

from well_viewer.core.plot_style import tokens_for
from well_viewer.features.spatial import state as sp_state

_logger = logging.getLogger("well_viewer")

_COLONY_CMAP = "tab20"


def render_spatial(ctx) -> None:
    st = ctx.state.feature_slice("spatial")
    fig = ctx.widgets.get_or_none("_sp_fig")
    if st is None or fig is None:
        return
    fig.clear()
    try:
        if st.last_error:
            _draw_message(fig, st.last_error)
        elif not st.last_result:
            _draw_message(fig, "Select a well to explore its spatial structure.")
        else:
            viz = st.viz
            if viz == sp_state.VIZ_SPATIAL_MAP:
                _render_spatial_map(fig, st)
            elif viz == sp_state.VIZ_NND_HISTOGRAM:
                _render_nnd_histogram(fig, st)
            elif viz == sp_state.VIZ_RIPLEY:
                _render_ripley(fig, st)
            elif viz == sp_state.VIZ_COLONY_SIZE:
                _render_colony_size(fig, st)
            elif viz == sp_state.VIZ_COLONY_METRIC_DIST:
                _render_colony_metric_dist(fig, st)
            elif viz == sp_state.VIZ_RADIAL_PROFILE:
                _render_radial_profile(fig, st)
            elif viz == sp_state.VIZ_BOUNDARY_INTERIOR:
                _render_boundary_interior(fig, st)
            else:  # pragma: no cover
                _draw_message(fig, f"Unknown visualization: {viz!r}")
    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Spatial render failed")
        _draw_message(fig, f"Render failed: {exc}")
    canvas = ctx.widgets.get_or_none("_sp_canvas")
    if canvas is not None:
        canvas.draw_idle()


# ── shared helpers ────────────────────────────────────────────────────────────

def _tokens(ax):
    return tokens_for(ax)


def _draw_message(fig, text: str) -> None:
    ax = fig.add_subplot(111)
    bg, _title, muted, _grid, _spine = _tokens(ax)
    fig.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True,
            transform=ax.transAxes, color=muted, fontsize=10)


def _style_axes(ax, title: str = "", xlabel: str = "", ylabel: str = "") -> None:
    bg, title_fg, muted, grid, spine = _tokens(ax)
    ax.set_facecolor(bg)
    ax.figure.set_facecolor(bg)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(spine)
    ax.tick_params(colors=muted, labelsize=8)
    ax.grid(True, linewidth=0.4, alpha=0.5, color=grid)
    if title:
        ax.set_title(title, color=title_fg, fontsize=10, pad=6)
    if xlabel:
        ax.set_xlabel(xlabel, color=muted, fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=muted, fontsize=8)


# ── viz #1: spatial map ──────────────────────────────────────────────────────

def _render_spatial_map(fig, st) -> None:
    payload = st.last_result
    pts = payload.get("points")
    if pts is None or pts.shape[0] == 0:
        _draw_message(fig, "No cells at the selected timepoint.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax, title=f"{payload.get('well', '')} · t={payload.get('timepoint', 0):.2g} h",
                xlabel="x (px)", ylabel="y (px)")

    level = st.level
    if level == sp_state.LEVEL_COLONY:
        labels = payload.get("colony_labels")
        _scatter_by_colony(ax, pts, labels)
        if st.show_hulls:
            _overlay_hulls(ax, pts, labels)
    elif level == sp_state.LEVEL_INTRA:
        colours = payload.get("intra_colour")
        if colours is None or colours.shape[0] != pts.shape[0]:
            _scatter_by_colony(ax, pts, payload.get("colony_labels"))
        else:
            sc = ax.scatter(pts[:, 0], pts[:, 1], c=colours, s=8,
                            cmap="viridis", edgecolors="none")
            cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.8, pad=0.02)
            cbar.set_label(payload.get("intra_colour_label", "r/R"),
                           color=_tokens(ax)[2], fontsize=8)
        if st.show_hulls:
            _overlay_hulls(ax, pts, payload.get("colony_labels"))
    else:
        metric = payload.get("metric")
        if metric is not None and metric.shape[0] == pts.shape[0]:
            sc = ax.scatter(pts[:, 0], pts[:, 1], c=metric, s=8,
                            cmap="viridis", edgecolors="none")
            cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.8, pad=0.02)
            cbar.set_label(payload.get("metric_col", ""), color=_tokens(ax)[2],
                           fontsize=8)
        else:
            ax.scatter(pts[:, 0], pts[:, 1], s=8, color="#4aa3ff", edgecolors="none")

    ax.invert_yaxis()  # image-space y grows downward
    ax.set_aspect("equal", adjustable="datalim")


def _scatter_by_colony(ax, pts, labels) -> None:
    import matplotlib
    if labels is None or labels.shape[0] != pts.shape[0]:
        ax.scatter(pts[:, 0], pts[:, 1], s=8, color="#4aa3ff", edgecolors="none")
        return
    noise = labels < 0
    if noise.any():
        ax.scatter(pts[noise, 0], pts[noise, 1], s=8, color="#888888",
                   edgecolors="none", label="noise")
    cmap = matplotlib.colormaps[_COLONY_CMAP]
    unique = sorted({int(v) for v in labels if int(v) >= 0})
    for cid in unique:
        m = labels == cid
        ax.scatter(pts[m, 0], pts[m, 1], s=8, color=cmap(cid % cmap.N),
                   edgecolors="none")


def _overlay_hulls(ax, pts, labels) -> None:
    if labels is None or labels.shape[0] != pts.shape[0]:
        return
    try:
        from scipy.spatial import ConvexHull
    except ImportError:  # pragma: no cover
        return
    import matplotlib
    cmap = matplotlib.colormaps[_COLONY_CMAP]
    for cid in sorted({int(v) for v in labels if int(v) >= 0}):
        block = pts[labels == cid]
        if block.shape[0] < 3:
            continue
        try:
            hull = ConvexHull(block)
        except Exception:
            from well_viewer.core.safe import swallowed
            swallowed()
            continue
        verts = block[hull.vertices]
        closed = np.vstack([verts, verts[0]])
        ax.plot(closed[:, 0], closed[:, 1], color=cmap(cid % cmap.N),
                lw=0.8, alpha=0.7)


# ── viz #2: NND histogram ────────────────────────────────────────────────────

def _render_nnd_histogram(fig, st) -> None:
    nnd = st.last_result.get("nnd")
    if nnd is None or nnd.size == 0:
        _draw_message(fig, "Not enough cells for a nearest-neighbour distribution.")
        return
    finite = nnd[np.isfinite(nnd)]
    if finite.size == 0:
        _draw_message(fig, "No finite nearest-neighbour distances.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax, title="Nearest-neighbour distance",
                xlabel="Distance (px)", ylabel="Cells")
    ax.hist(finite, bins=30, color="#4aa3ff", alpha=0.8)
    ax.axvline(float(np.median(finite)), color="#ff9c4a", lw=1.2,
               label=f"median = {np.median(finite):.1f} px")
    ax.legend(fontsize=8, frameon=False)


# ── viz #3: Ripley L ─────────────────────────────────────────────────────────

def _render_ripley(fig, st) -> None:
    ripley = st.last_result.get("ripley")
    if ripley is None:
        _draw_message(fig, "Not enough cells for Ripley's L.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax,
                title=f"Ripley L(r) − r · n={ripley.n_points}",
                xlabel="r (px)", ylabel="L(r) − r")
    ax.fill_between(ripley.r, ripley.csr_lo, ripley.csr_hi,
                    color="#888888", alpha=0.25, label="CSR envelope")
    ax.plot(ripley.r, ripley.L_minus_r, color="#4aa3ff", lw=1.4, label="sample")
    ax.axhline(0.0, color="#666666", lw=0.6)
    ax.legend(fontsize=8, frameon=False)


# ── viz #6: colony size histogram ────────────────────────────────────────────

def _render_colony_size(fig, st) -> None:
    stats = st.last_result.get("colony_stats")
    if stats is None or stats.size.size == 0:
        _draw_message(fig, "No colonies detected — try increasing ε multiplier.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax,
                title=f"Colony size · {stats.ids.size} colonies",
                xlabel="Cells per colony", ylabel="Colonies")
    max_size = int(stats.size.max())
    bins = min(30, max(3, max_size))
    ax.hist(stats.size, bins=bins, color="#4aa3ff", alpha=0.85)
    ax.axvline(float(np.median(stats.size)), color="#ff9c4a", lw=1.2,
               label=f"median = {float(np.median(stats.size)):.1f}")
    ax.legend(fontsize=8, frameon=False)


# ── viz #8: per-colony metric distribution (inter- vs intra-variability) ─────

def _render_colony_metric_dist(fig, st) -> None:
    payload = st.last_result
    metric = payload.get("metric")
    labels = payload.get("colony_labels")
    metric_col = payload.get("metric_col") or "metric"
    if metric is None:
        _draw_message(fig, "Pick a per-cell metric to compare colonies.")
        return
    if labels is None or labels.shape[0] != metric.shape[0]:
        _draw_message(fig, "No colonies detected — try increasing ε multiplier.")
        return

    # Group values by colony, dropping noise + non-finite.
    finite = np.isfinite(metric)
    per_colony: list[tuple[int, np.ndarray]] = []
    for cid in sorted({int(v) for v in labels if int(v) >= 0}):
        block = metric[(labels == cid) & finite]
        if block.size > 0:
            per_colony.append((cid, block))
    if not per_colony:
        _draw_message(fig, "No finite metric values inside any colony.")
        return

    # Sort colonies by median so the plot reads left→right in signal order.
    per_colony.sort(key=lambda kv: float(np.median(kv[1])))
    ids = [cid for cid, _ in per_colony]
    data = [block for _, block in per_colony]
    positions = list(range(1, len(data) + 1))

    ax = fig.add_subplot(111)
    _style_axes(ax, title=f"{metric_col} · per-colony distribution",
                xlabel="Colony (ordered by median)", ylabel=metric_col)

    import matplotlib
    cmap = matplotlib.colormaps[_COLONY_CMAP]
    parts = ax.violinplot(data, positions=positions, showmeans=False,
                           showmedians=True, showextrema=False)
    for cid, body in zip(ids, parts["bodies"]):
        c = cmap(int(cid) % cmap.N)
        body.set_facecolor(c)
        body.set_alpha(0.65)
        body.set_edgecolor(c)
    if "cmedians" in parts:
        parts["cmedians"].set_color("#222222")
        parts["cmedians"].set_linewidth(0.8)

    # Grand median as a reference line.
    grand = float(np.median(np.concatenate(data)))
    ax.axhline(grand, color="#888888", lw=0.6, linestyle="--",
               label=f"grand median = {grand:.3g}")

    # Sparse x labels — every ceil(n/12)th colony id — so wide colony sets
    # stay readable.
    step = max(1, len(ids) // 12)
    ax.set_xticks(positions[::step])
    ax.set_xticklabels([str(c) for c in ids[::step]])

    # Variance-decomposition callout at top-left of the axes.
    decomp = payload.get("variance_decomp")
    if decomp is not None and decomp.n_cells > 0:
        bg, title_fg, muted, _grid, _spine = _tokens(ax)
        text = (
            f"ICC = {decomp.icc:.2f}  ·  "
            f"σ²_between = {decomp.sigma2_between:.3g}  ·  "
            f"σ²_within = {decomp.sigma2_within:.3g}  ·  "
            f"n = {decomp.n_cells} cells in {decomp.n_colonies} colonies"
        )
        ax.text(0.01, 0.98, text, transform=ax.transAxes, ha="left", va="top",
                fontsize=8, color=title_fg,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=bg,
                          edgecolor=muted, alpha=0.85))
    ax.legend(fontsize=7, frameon=False, loc="upper right")


# ── viz #9: radial profile ───────────────────────────────────────────────────

def _render_radial_profile(fig, st) -> None:
    prof = st.last_result.get("radial_profile")
    metric_col = st.last_result.get("metric_col") or "metric"
    if prof is None or not np.any(np.isfinite(prof.mean)):
        _draw_message(fig, "Pick a metric and ensure colonies were detected.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax,
                title=f"Radial profile of {metric_col}",
                xlabel="r / R (colony centre → edge)", ylabel=metric_col)
    ax.errorbar(prof.bin_centres, prof.mean, yerr=prof.sem,
                fmt="-o", color="#4aa3ff", ecolor="#4aa3ff",
                markersize=4, lw=1.2, capsize=2)


# ── viz #10: boundary vs interior split ──────────────────────────────────────

def _render_boundary_interior(fig, st) -> None:
    payload = st.last_result
    metric = payload.get("metric")
    geom = payload.get("intra_geom")
    metric_col = payload.get("metric_col") or "metric"
    if metric is None or geom is None or metric.shape[0] != geom.is_boundary.shape[0]:
        _draw_message(fig, "Pick a per-cell metric to compare boundary vs interior.")
        return
    interior = metric[~geom.is_boundary & np.isfinite(metric)]
    boundary = metric[geom.is_boundary & np.isfinite(metric)]
    if interior.size == 0 and boundary.size == 0:
        _draw_message(fig, "No finite metric values in either partition.")
        return
    ax = fig.add_subplot(111)
    _style_axes(ax, title=f"{metric_col} · boundary vs interior",
                xlabel="", ylabel=metric_col)
    positions = [1, 2]
    data = [interior if interior.size else np.array([0.0]),
            boundary if boundary.size else np.array([0.0])]
    parts = ax.violinplot(data, positions=positions, showmeans=True,
                           showextrema=False)
    colours = ["#4aa3ff", "#ff9c4a"]
    for body, colour in zip(parts["bodies"], colours):
        body.set_facecolor(colour)
        body.set_alpha(0.6)
        body.set_edgecolor(colour)
    ax.set_xticks(positions)
    ax.set_xticklabels([f"Interior (n={interior.size})",
                        f"Boundary (n={boundary.size})"])
