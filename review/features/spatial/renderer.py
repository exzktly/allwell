"""Spatial-analysis renderer — pure matplotlib.

Reads the controller's cached snapshot and draws whichever viz is selected.
Six MVP vizs: spatial map, NND histogram, Ripley L, colony size histogram,
radial profile, boundary-vs-interior split.

Per-frame levels (Cell / Colony / Intra-colony) facet one panel per selected
well: ``st.frames`` holds a per-well payload and each per-viz drawer paints a
single axes, so the same viz repeats across a grid. Summary level draws a
single collapsed matrix from ``st.last_result``.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from ui import theme
from review.core.plot_style import apply_time_rescale, style_legend, tokens_for
from review.domain.time_rescale import axis_label as time_axis_label
from review.domain.time_rescale import format_scaled as format_scaled_tp
from review.domain.time_rescale import OFF as tr_OFF
from review.domain.time_rescale import rescale_for as time_rescale_for
from review.domain.selections_model import figure_names_map
from review.features.spatial import state as sp_state

_logger = logging.getLogger("review")

_COLONY_CMAP = "tab20"

# The observed/reference pairing used across every viz below (a point-pattern-
# analysis convention: the sample/observed series reads cool, the reference/
# summary series reads warm) — named once here rather than repeated as magic
# hex at ~8 call sites. Deliberately not sourced from theme.Colors: these are
# plot-data series, not chrome, and this renderer also renders in Publication
# mode via _tokens(ax), where a screen-only chrome token would look wrong.
_OBSERVED_COLOR = theme.Colors.spatial_observed
_REFERENCE_COLOR = theme.Colors.spatial_reference


def render_spatial(ctx) -> None:
    st = ctx.state.feature_slice("spatial")
    fig = ctx.widgets.get_or_none("_sp_fig")
    if st is None or fig is None:
        return
    fig.clear()
    try:
        if st.last_error:
            _draw_message(fig, st.last_error)
        elif st.viz == sp_state.VIZ_SUMMARY_PANEL:
            if not st.last_result:
                # Summary is deliberately *not* placeholder-gated — it falls back
                # to every loaded well rather than to one arbitrary one — so this
                # is the genuine "nothing computed yet" state, and the button is
                # what resolves it.
                _draw_message(fig, "Press Recompute to build the summary.")
            else:
                # The names, not the ctx: the renderer stays Qt- and
                # app-free, and the one figure in this tab that names wells
                # names them the way every other figure does.
                _render_summary_panel(fig, st, time_rescale_for(ctx.state.plot),
                                      names=figure_names_map(ctx))
        elif not st.frames:
            # The per-frame levels are placeholder-gated, so an empty selection
            # never reaches here: this is wells chosen whose frames would not
            # resolve.
            _draw_message(fig, "No spatial data for the chosen wells and timepoint.")
        else:
            _render_frames_grid(fig, st)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Spatial render failed")
        _draw_message(fig, f"Render failed: {exc}")
    canvas = ctx.widgets.get_or_none("_sp_canvas")
    # Re-apply the Export-Style / Properties sidebar prefs (title, font sizes,
    # grid, legend, …) so toggling them in the panel survives a recompute.
    try:
        from review.core.plot_style import apply_export_style_to_current
        apply_export_style_to_current(ctx, fig, canvas)
    except Exception:  # pragma: no cover - defensive
        from core.safe import swallowed
        swallowed()
    if canvas is not None:
        canvas.draw_idle()


# ── faceted grid (one panel per selected well) ───────────────────────────────

def _grid_shape(n: int) -> tuple[int, int]:
    """Rows × cols for *n* panels, kept roughly square so maps and
    distributions both stay legible."""
    if n <= 1:
        return 1, 1
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    return rows, cols


def _render_frames_grid(fig, st) -> None:
    frames = st.frames
    n = len(frames)
    rows, cols = _grid_shape(n)
    for i, payload in enumerate(frames):
        ax = fig.add_subplot(rows, cols, i + 1)
        _render_one_panel(ax, st, payload)
    if getattr(st, "frames_truncated", 0):
        _bg, title_fg, _muted, _grid, _spine = _tokens(fig.axes[0])
        fig.suptitle(
            f"Showing {n} of {n + st.frames_truncated} selected wells "
            f"(narrow the selection to see the rest)",
            color=title_fg, fontsize=9,
        )
    try:
        fig.tight_layout()
    except Exception:  # pragma: no cover - defensive
        from core.safe import swallowed
        swallowed()


def _render_one_panel(ax, st, payload) -> None:
    """Draw a single well's payload for the active viz into *ax*, prefixing the
    well label onto the panel title so faceted grids stay identifiable."""
    err = payload.get("error")
    if err:
        _msg_ax(ax, f"{payload.get('well', '')}\n{err}")
        return
    viz = st.viz
    if viz == sp_state.VIZ_SPATIAL_MAP:
        _render_spatial_map(ax, payload, st)
    elif viz == sp_state.VIZ_NND_HISTOGRAM:
        _render_nnd_histogram(ax, payload)
    elif viz == sp_state.VIZ_RIPLEY:
        _render_ripley(ax, payload)
    elif viz == sp_state.VIZ_COLONY_SIZE:
        _render_colony_size(ax, payload)
    elif viz == sp_state.VIZ_COLONY_METRIC_DIST:
        _render_colony_metric_dist(ax, payload)
    elif viz == sp_state.VIZ_RADIAL_PROFILE:
        _render_radial_profile(ax, payload)
    elif viz == sp_state.VIZ_BOUNDARY_INTERIOR:
        _render_boundary_interior(ax, payload)
    else:  # pragma: no cover
        _msg_ax(ax, f"Unknown visualization: {viz!r}")
        return
    _prefix_well_title(ax, payload.get("well", ""))


def _prefix_well_title(ax, well: str) -> None:
    """Prepend ``<well> · `` to whatever title the viz drawer set, preserving
    its colour/size. No-op on message panels (axis off → no title)."""
    if not well or not ax.axison:
        return
    art = ax.title
    existing = ax.get_title()
    text = f"{well} · {existing}" if existing else well
    ax.set_title(text, color=art.get_color(), fontsize=art.get_fontsize(),
                 pad=6)


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


def _msg_ax(ax, text: str) -> None:
    """Per-panel message (empty / failed facet) — draws into a single axes so
    the rest of the grid still renders."""
    bg, _title, muted, _grid, _spine = _tokens(ax)
    ax.figure.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True,
            transform=ax.transAxes, color=muted, fontsize=9)


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

def _render_spatial_map(ax, payload, st) -> None:
    pts = payload.get("points")
    if pts is None or pts.shape[0] == 0:
        _msg_ax(ax, "No cells at the selected timepoint.")
        return
    _style_axes(ax, title=f"t={payload.get('timepoint', 0):.2g} h",
                xlabel="x (px)", ylabel="y (px)")

    level = st.level
    metric = payload.get("metric")
    has_metric = (
        metric is not None
        and hasattr(metric, "shape")
        and metric.shape == (pts.shape[0],)
    )
    labels = payload.get("colony_labels")

    # The spatial map expresses each level's *defining* quantity, so toggling
    # the level visibly re-colours the same points:
    #   • Intra-colony → normalised radial position r/R (centre → edge), always
    #     — even with a metric picked. The metric is analysed against r/R via
    #     the Radial-profile / Boundary-vs-interior vizs, not this map.
    #   • Cell / Colony → the picked metric when one is set (the user's signal
    #     in the well plane), else colony id (Colony) or a flat colour (Cell).
    if level == sp_state.LEVEL_INTRA:
        colours = payload.get("intra_colour")
        if colours is None or colours.shape[0] != pts.shape[0]:
            _scatter_by_colony(ax, pts, labels)  # colonies not detected yet
        else:
            sc = ax.scatter(pts[:, 0], pts[:, 1], c=colours, s=8,
                            cmap="viridis", edgecolors="none")
            cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.8, pad=0.02)
            setattr(cbar.ax, "_aw_colorbar", True)
            cbar.set_label(payload.get("intra_colour_label", "r / R"),
                           color=_tokens(ax)[2], fontsize=8)
        if st.show_hulls:
            _overlay_hulls(ax, pts, labels)
    elif has_metric:
        sc = ax.scatter(pts[:, 0], pts[:, 1], c=metric, s=8,
                        cmap="viridis", edgecolors="none")
        cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.8, pad=0.02)
        setattr(cbar.ax, "_aw_colorbar", True)
        cbar.set_label(payload.get("metric_col", ""), color=_tokens(ax)[2],
                       fontsize=8)
        if st.show_hulls and level == sp_state.LEVEL_COLONY:
            _overlay_hulls(ax, pts, labels)
    elif level == sp_state.LEVEL_COLONY:
        _scatter_by_colony(ax, pts, labels)
        if st.show_hulls:
            _overlay_hulls(ax, pts, labels)
    else:
        ax.scatter(pts[:, 0], pts[:, 1], s=8, color=_OBSERVED_COLOR,
                   edgecolors="none")

    ax.invert_yaxis()  # image-space y grows downward
    ax.set_aspect("equal", adjustable="datalim")


def _scatter_by_colony(ax, pts, labels) -> None:
    import matplotlib
    if labels is None or labels.shape[0] != pts.shape[0]:
        ax.scatter(pts[:, 0], pts[:, 1], s=8, color=_OBSERVED_COLOR, edgecolors="none")
        return
    noise = labels < 0
    if noise.any():
        _, _, _muted, _, _ = _tokens(ax)
        ax.scatter(pts[noise, 0], pts[noise, 1], s=8, color=_muted,
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
            from core.safe import swallowed
            swallowed()
            continue
        verts = block[hull.vertices]
        closed = np.vstack([verts, verts[0]])
        ax.plot(closed[:, 0], closed[:, 1], color=cmap(cid % cmap.N),
                lw=0.8, alpha=0.7)


# ── viz #2: NND histogram ────────────────────────────────────────────────────

def _render_nnd_histogram(ax, payload) -> None:
    nnd = payload.get("nnd")
    if nnd is None or nnd.size == 0:
        _msg_ax(ax, "Not enough cells for a nearest-neighbour distribution.")
        return
    finite = nnd[np.isfinite(nnd)]
    if finite.size == 0:
        _msg_ax(ax, "No finite nearest-neighbour distances.")
        return
    _style_axes(ax, title="Nearest-neighbour distance",
                xlabel="Distance (px)", ylabel="Cells")
    ax.hist(finite, bins=30, color=_OBSERVED_COLOR, alpha=0.8)
    ax.axvline(float(np.median(finite)), color=_REFERENCE_COLOR, lw=1.2,
               label=f"median = {np.median(finite):.1f} px")
    style_legend(ax.legend(fontsize=8, frameon=False), ax)


# ── viz #3: Ripley L ─────────────────────────────────────────────────────────

def _render_ripley(ax, payload) -> None:
    ripley = payload.get("ripley")
    # ``None`` is the controller declining to compute (it guards on a minimum
    # cell count); an all-NaN curve is ``ripley_l`` itself declining, which it
    # does below two points rather than returning the all-zero curve inside a
    # zero-width envelope that used to read as *perfect* randomness. Both mean
    # the same thing to a reader, so both get the same words — otherwise the
    # NaN case drew an empty axes carrying a legend and a zero line, which
    # looks like a measurement that came out flat.
    if ripley is None or not np.isfinite(ripley.L_minus_r).any():
        _msg_ax(ax, "Not enough cells for Ripley's L.")
        return
    _style_axes(ax,
                title=f"Ripley L(r) − r · n={ripley.n_points}",
                xlabel="r (px)", ylabel="L(r) − r")
    _, _, _muted, _, _spine = _tokens(ax)
    ax.fill_between(ripley.r, ripley.csr_lo, ripley.csr_hi,
                    color=_muted, alpha=0.25, label="CSR envelope")
    ax.plot(ripley.r, ripley.L_minus_r, color=_OBSERVED_COLOR, lw=1.4, label="sample")
    ax.axhline(0.0, color=_spine, lw=0.6)
    style_legend(ax.legend(fontsize=8, frameon=False), ax)


# ── viz #6: colony size histogram ────────────────────────────────────────────

def _render_colony_size(ax, payload) -> None:
    stats = payload.get("colony_stats")
    if stats is None or stats.size.size == 0:
        _msg_ax(ax, "No colonies detected — try increasing ε multiplier.")
        return
    _style_axes(ax,
                title=f"Colony size · {stats.ids.size} colonies",
                xlabel="Cells per colony", ylabel="Colonies")
    max_size = int(stats.size.max())
    bins = min(30, max(3, max_size))
    ax.hist(stats.size, bins=bins, color=_OBSERVED_COLOR, alpha=0.85)
    ax.axvline(float(np.median(stats.size)), color=_REFERENCE_COLOR, lw=1.2,
               label=f"median = {float(np.median(stats.size)):.1f}")
    style_legend(ax.legend(fontsize=8, frameon=False), ax)


# ── viz #8: per-colony metric distribution (inter- vs intra-variability) ─────

def _render_colony_metric_dist(ax, payload) -> None:
    metric = payload.get("metric")
    labels = payload.get("colony_labels")
    metric_col = payload.get("metric_col") or "metric"
    if metric is None:
        _msg_ax(ax, "Pick a per-cell metric to compare colonies.")
        return
    if labels is None or labels.shape[0] != metric.shape[0]:
        _msg_ax(ax, "No colonies detected — try increasing ε multiplier.")
        return

    # Group values by colony, dropping noise + non-finite.
    finite = np.isfinite(metric)
    per_colony: list[tuple[int, np.ndarray]] = []
    for cid in sorted({int(v) for v in labels if int(v) >= 0}):
        block = metric[(labels == cid) & finite]
        if block.size > 0:
            per_colony.append((cid, block))
    if not per_colony:
        _msg_ax(ax, "No finite metric values inside any colony.")
        return

    # Sort colonies by median so the plot reads left→right in signal order.
    per_colony.sort(key=lambda kv: float(np.median(kv[1])))
    ids = [cid for cid, _ in per_colony]
    data = [block for _, block in per_colony]
    positions = list(range(1, len(data) + 1))

    _style_axes(ax, title=f"{metric_col} · per-colony distribution",
                xlabel="Colony (ordered by median)", ylabel=metric_col)
    bg, title_fg, muted, _grid, _spine = _tokens(ax)

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
        parts["cmedians"].set_color(theme.Colors.ink_dark)
        parts["cmedians"].set_linewidth(0.8)

    # Grand median as a reference line.
    grand = float(np.median(np.concatenate(data)))
    ax.axhline(grand, color=muted, lw=0.6, linestyle="--",
               label=f"grand median = {grand:.3g}")

    # Sparse x labels — every ceil(n/12)th colony id — so wide colony sets
    # stay readable.
    step = max(1, len(ids) // 12)
    ax.set_xticks(positions[::step])
    ax.set_xticklabels([str(c) for c in ids[::step]])

    # Variance-decomposition callout at top-left of the axes.
    decomp = payload.get("variance_decomp")
    if decomp is not None and decomp.n_cells > 0:
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
    style_legend(ax.legend(fontsize=7, frameon=False, loc="upper right"), ax)


# ── viz #9: radial profile ───────────────────────────────────────────────────

def _render_radial_profile(ax, payload) -> None:
    prof = payload.get("radial_profile")
    metric_col = payload.get("metric_col") or "metric"
    if prof is None or not np.any(np.isfinite(prof.mean)):
        _msg_ax(ax, "Pick a metric and ensure colonies were detected.")
        return
    _style_axes(ax,
                title=f"Radial profile of {metric_col}",
                xlabel="r / R (colony centre → edge)", ylabel=metric_col)
    ax.errorbar(prof.bin_centres, prof.mean, yerr=prof.sem,
                fmt="-o", color=_OBSERVED_COLOR, ecolor=_OBSERVED_COLOR,
                markersize=4, lw=1.2, capsize=2)


# ── viz #10: boundary vs interior split ──────────────────────────────────────

def _render_boundary_interior(ax, payload) -> None:
    metric = payload.get("metric")
    geom = payload.get("intra_geom")
    metric_col = payload.get("metric_col") or "metric"
    if metric is None or geom is None or metric.shape[0] != geom.is_boundary.shape[0]:
        _msg_ax(ax, "Pick a per-cell metric to compare boundary vs interior.")
        return
    interior = metric[~geom.is_boundary & np.isfinite(metric)]
    boundary = metric[geom.is_boundary & np.isfinite(metric)]
    if interior.size == 0 and boundary.size == 0:
        _msg_ax(ax, "No finite metric values in either partition.")
        return
    _style_axes(ax, title=f"{metric_col} · boundary vs interior",
                xlabel="", ylabel=metric_col)
    # An empty partition used to be substituted with ``np.array([0.0])`` and
    # drawn as a violin: a shape sitting on zero, with a mean marker on it,
    # reading as "every cell in this partition measured zero" when in fact no
    # cell was in it. Only the populated partitions get a violin now, and the
    # empty one is named on the axis instead of silently invented.
    partitions = [("interior", interior, _OBSERVED_COLOR),
                  ("boundary", boundary, _REFERENCE_COLOR)]
    positions = [i for i, (_n, arr, _c) in enumerate(partitions, start=1) if arr.size]
    data = [arr for _n, arr, _c in partitions if arr.size]
    colours = [c for _n, arr, c in partitions if arr.size]
    for i, (name, arr, _c) in enumerate(partitions, start=1):
        if not arr.size:
            ax.annotate(f"no {name} cells", xy=(i, 0.5),
                        xycoords=("data", "axes fraction"),
                        ha="center", va="center", fontsize=8,
                        color=theme.Colors.text_muted, rotation=90)
    parts = ax.violinplot(data, positions=positions, showmeans=True,
                           showextrema=False)
    for body, colour in zip(parts["bodies"], colours):
        body.set_facecolor(colour)
        body.set_alpha(0.6)
        body.set_edgecolor(colour)
    # Both ticks, always, even when one partition drew no violin: the reader
    # has to be able to see *which* partition is the empty one, and an axis
    # with a single tick reads as a chart that only ever had one category.
    ax.set_xticks([1, 2])
    ax.set_xticklabels([f"Interior (n={interior.size})",
                        f"Boundary (n={boundary.size})"])
    ax.set_xlim(0.4, 2.6)


# ── viz: Summary panel (per-(well, timepoint) matrix) ────────────────────────


def _summary_matrix(payload, key: str) -> tuple[np.ndarray, list, list]:
    """Extract the (n_wells, n_timepoints) float matrix for the chosen summary
    field, plus parallel row / column label lists."""
    wells = payload.get("wells", [])
    tps = payload.get("timepoints", [])
    summaries = payload.get("summaries")
    n_w, n_t = len(wells), len(tps)
    if summaries is None or n_w == 0 or n_t == 0:
        return np.zeros((0, 0)), wells, tps
    matrix = np.full((n_w, n_t), np.nan, dtype=float)
    for i in range(n_w):
        for j in range(n_t):
            s = summaries[i, j]
            if s is None:
                continue
            val = getattr(s, key, None)
            if val is None:
                continue
            matrix[i, j] = float(val)
    return matrix, wells, tps


def _summary_metric_label(key: str) -> tuple[str, str]:
    """Return ``(label, units)`` for a summary field, or the key if unknown."""
    for k, lbl, units in sp_state_metric_catalog():
        if k == key:
            return lbl, units
    return key, ""


def sp_state_metric_catalog():
    """Local import to avoid a module-scope cycle."""
    from review.domain.spatial_compute import SUMMARY_METRICS
    return SUMMARY_METRICS


def _render_summary_panel(fig, st, rescale=tr_OFF, *, names=None) -> None:
    payload = st.last_result
    key = payload.get("summary_metric_key") or "nnd_median"
    display = payload.get("summary_display") or sp_state.SUMMARY_DISPLAY_HEATMAP
    matrix, wells, tps = _summary_matrix(payload, key)
    if matrix.size == 0:
        # The summary ran and produced an empty matrix — not a missing selection,
        # which the shared placeholder would have caught before this point.
        _draw_message(fig, "The summary produced no wells or timepoints.")
        return
    label, units = _summary_metric_label(key)
    ylabel = f"{label} ({units})" if units else label

    # One name per well for both displays, so the heatmap's y axis and the
    # line legend cannot call the same well two different things.
    shown = _shown_wells(wells, names)
    if display == sp_state.SUMMARY_DISPLAY_HEATMAP:
        _render_summary_heatmap(fig, matrix, shown, tps, label, units,
                                rescale)
    else:
        _render_summary_lines(fig, matrix, shown, tps, ylabel, rescale)


def _shown_wells(wells, names) -> list[str]:
    """*wells*, each said the way the rest of the app says it.

    *names* is ``features.export.service.figure_names_map``'s answer — a typed
    Well Labels entry, or the values the well holds on the Variables tab — and
    a well it does not name keeps its token. ``None`` for a caller with no ctx
    to build one from, which is every test of the pure renderer.
    """
    lookup = dict(names or {})
    return [str(lookup.get(w) or w) for w in wells]


def _render_summary_heatmap(fig, matrix, wells, tps, label, units,
                            rescale=tr_OFF) -> None:
    ax = fig.add_subplot(111)
    _style_axes(ax, title=f"{label} · well × timepoint",
                xlabel=time_axis_label("Timepoint (h)", rescale),
                ylabel="Well")
    # Both axes are category positions, not measurements: x is the index of a
    # timepoint and y the index of a well. Without these flags the shared
    # restyle pass calls ``set_xscale``, which resets the locator and formatter
    # and replaced the timepoint / well tick labels with the raw index
    # positions (-0.5, 0.0, 0.5 …) — a grid whose axes named nothing.
    setattr(ax, "_categorical_xaxis", True)
    setattr(ax, "_categorical_yaxis", True)
    finite = matrix[np.isfinite(matrix)]
    if finite.size:
        vmin, vmax = float(finite.min()), float(finite.max())
    else:
        vmin, vmax = 0.0, 1.0
    im = ax.imshow(matrix, aspect="auto", cmap="viridis",
                   vmin=vmin, vmax=vmax, origin="lower",
                   interpolation="nearest")
    ax.set_xticks(range(len(tps)))
    ax.set_xticklabels([format_scaled_tp(t, rescale) for t in tps])
    ax.set_yticks(range(len(wells)))
    ax.set_yticklabels(wells)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    setattr(cbar.ax, "_aw_colorbar", True)
    cbar_label = f"{label} ({units})" if units else label
    cbar.set_label(cbar_label, color=_tokens(ax)[2], fontsize=8)


def _render_summary_lines(fig, matrix, wells, tps, ylabel: str,
                          rescale=tr_OFF) -> None:
    ax = fig.add_subplot(111)
    _style_axes(ax, title="Summary vs timepoint · one line per well",
                xlabel=time_axis_label("Timepoint (h)", rescale),
                ylabel=ylabel)
    # Real timepoint values on this one, so the labels are rescaled by the tick
    # formatter rather than written out.
    apply_time_rescale(ax, rescale)
    import matplotlib
    cmap = matplotlib.colormaps["tab20"]
    for i, w in enumerate(wells):
        y = matrix[i]
        # Drop NaN gaps so lines don't dive to zero when a well is missing a tp.
        mask = np.isfinite(y)
        if not mask.any():
            continue
        ax.plot(np.asarray(tps)[mask], y[mask],
                marker="o", markersize=3, lw=1.1,
                color=cmap(i % cmap.N), label=w)
    if len(wells) <= 12:
        style_legend(ax.legend(fontsize=7, frameon=False,
                               loc="upper right", ncol=min(2, len(wells))), ax)
