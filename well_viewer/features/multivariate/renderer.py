"""Multivariate feature renderer — pure matplotlib drawing.

``render_multivariate(app)`` repaints ``ctx.runtime.multivariate.fig`` from the snapshot the
controller cached on the state slice (``last_result`` / ``last_error``). Each of
the four modes draws its own figure layout. No Qt beyond the final
``canvas.draw_idle()``; no compute — the controller has already run it.
"""

from __future__ import annotations

import logging

import numpy as np

import theme
from well_viewer.core.plot_style import apply_ax_style, style_legend, tokens_for

_logger = logging.getLogger("well_viewer")

_CLUSTER_CMAP = "tab10"


def render_multivariate(ctx) -> None:
    st = ctx.state.feature_slice("multivariate")
    fig = ctx.runtime.multivariate.fig
    if st is None or fig is None:
        return
    fig.clear()
    try:
        if st.last_error:
            _draw_message(fig, st.last_error)
        elif not st.last_result:
            _draw_message(fig, "Select wells and feature columns to begin.")
        else:
            kind = st.last_result.get("kind")
            if kind == "correlation":
                _render_correlation(fig, st.last_result)
            elif kind == "dimred":
                _render_dimred(fig, st.last_result)
            elif kind == "clustering":
                _render_clustering(fig, st.last_result)
            elif kind == "facet":
                _render_facet(fig, st.last_result)
            else:  # pragma: no cover - defensive
                _draw_message(fig, "Nothing to display.")
    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Multivariate render failed")
        _draw_message(fig, f"Render failed: {exc}")
    canvas = ctx.runtime.multivariate.canvas
    if canvas is not None:
        canvas.draw_idle()


# ── shared helpers ───────────────────────────────────────────────────────────

def _draw_message(fig, text: str) -> None:
    ax = fig.add_subplot(111)
    bg, title_fg, muted_fg, _grid, _spine = tokens_for(ax)
    fig.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True,
            transform=ax.transAxes, color=muted_fg, fontsize=10)


def _footnote(fig, text: str) -> None:
    ax = fig.axes[0] if fig.axes else None
    _bg, _t, muted_fg, _g, _s = tokens_for(ax) if ax is not None else (None,) * 5
    fig.text(0.5, 0.005, text, ha="center", va="bottom", fontsize=7,
             color=muted_fg or "#888")


def _point_colors(data, color_by: str, clusters=None):
    """Return (per-cell colour array, list of (label, colour) legend entries)."""
    if color_by == "cluster" and clusters is not None:
        import matplotlib
        cmap = matplotlib.colormaps[_CLUSTER_CMAP]
        labels = clusters.labels
        colors = [cmap(int(c) % cmap.N) for c in labels]
        legend = [(f"Cluster {c}", cmap(int(c) % cmap.N))
                  for c in sorted(set(int(v) for v in labels))]
        return colors, legend
    if color_by == "timepoint":
        import matplotlib
        tps = data["timepoint"]
        uniq = sorted(set(float(t) for t in tps.tolist()))
        cmap = matplotlib.colormaps["viridis"]
        pos = {t: cmap(v) for t, v in zip(uniq, np.linspace(0, 1, max(len(uniq), 1)))}
        colors = [pos[float(t)] for t in tps.tolist()]
        legend = [(f"{t:g} h", pos[t]) for t in uniq]
        return colors, legend
    # group or well: use the collected per-group / per-well colours.
    key = "well" if color_by == "well" else "group"
    labels = data[key]
    if key == "well":
        # Per-well colours are keyed by well label in BOTH selection modes; use
        # them so "colour by: well" also works when replicate-set groups are
        # active (where group_colors is keyed by group name, not well token).
        gcolors = data.get("well_colors") or data["group_colors"]
    else:
        gcolors = data["group_colors"]
    order = []
    seen = set()
    for lbl in labels.tolist():
        if lbl not in seen:
            seen.add(lbl)
            order.append(lbl)
    default = theme.Colors.accent
    colors = [gcolors.get(lbl, default) for lbl in labels.tolist()]
    legend = [(str(lbl), gcolors.get(lbl, default)) for lbl in order]
    return colors, legend


def _legend(ax, legend_entries, muted_fg, title=""):
    if not legend_entries or len(legend_entries) > 24:
        return
    from matplotlib.lines import Line2D
    handles = [Line2D([0], [0], marker="o", linestyle="", markersize=6,
                      markerfacecolor=c, markeredgecolor="none", label=lbl)
               for lbl, c in legend_entries]
    # Transparent frame + primary-foreground text (via style_legend) so labels
    # stay legible on the dark screen theme — the old muted grey over the
    # semi-opaque frame read as grey-on-grey.
    leg = ax.legend(handles=handles, title=title, fontsize=7, framealpha=0.0,
                    facecolor="none", loc="best")
    if leg is not None:
        leg.get_title().set_fontsize(7)
    style_legend(leg, ax)


# ── shared annotated-heatmap helper (audit A13) ──────────────────────────────

def _annotated_matrix(
    ax, mat, *, cmap, vmin, vmax, row_labels, col_labels, muted_fg,
    fmt, annotate, annot_color, x_rotation=45, mask_invalid=False,
):
    """Draw a value-annotated heatmap onto *ax*; return the ``AxesImage``.

    The shared scaffolding behind the four multivariate heatmaps (correlation,
    PCA loadings, cluster profile, facet grid): ``imshow`` + tick labels + the
    per-cell value-annotation loop. Everything that differed between the four is
    a parameter — ``cmap`` / ``vmin`` / ``vmax``, the tick labels (+ optional
    ``x_rotation``), the ``annotate`` predicate, the ``str.format`` ``fmt``, and
    an ``annot_color(v) -> colour`` callback. ``mask_invalid`` routes NaNs
    through ``masked_invalid`` for the image (the facet grid) while the
    annotation loop still reads the raw values.
    """
    im = ax.imshow(np.ma.masked_invalid(mat) if mask_invalid else mat,
                   cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(col_labels)))
    ax.set_yticks(range(len(row_labels)))
    xkw = dict(fontsize=7, color=muted_fg)
    if x_rotation:
        xkw.update(rotation=x_rotation, ha="right")
    ax.set_xticklabels(col_labels, **xkw)
    ax.set_yticklabels(row_labels, fontsize=7, color=muted_fg)
    if annotate:
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                v = mat[i, j]
                if np.isfinite(v):
                    ax.text(j, i, fmt.format(v), ha="center", va="center",
                            fontsize=6, color=annot_color(v))
    return im


# ── correlation / pair-matrix ────────────────────────────────────────────────

def _render_correlation(fig, res) -> None:
    corr = res["corr"]
    labels = res["data"]["feature_labels"]
    if res.get("view") == "splom":
        _render_splom(fig, res)
        return
    ax = fig.add_subplot(111)
    apply_ax_style(ax, "Feature correlation", "")
    ax.grid(False)
    _bg, _t, muted_fg, _g, _s = tokens_for(ax)
    # Annotate r values when the matrix is small enough to stay legible.
    im = _annotated_matrix(
        ax, corr, cmap="RdBu_r", vmin=-1, vmax=1,
        row_labels=labels, col_labels=labels, muted_fg=muted_fg,
        fmt="{:.2f}", annotate=len(labels) <= 12,
        annot_color=lambda v: "white" if abs(v) > 0.5 else muted_fg,
    )
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    setattr(cbar.ax, "_aw_colorbar", True)
    cbar.ax.tick_params(labelsize=7, colors=muted_fg)
    fig.subplots_adjust(left=0.28, bottom=0.30, right=0.98, top=0.94)


def _render_splom(fig, res) -> None:
    data = res["data"]
    X = data["X"]
    labels = data["feature_labels"]
    n = min(X.shape[1], 6)  # cap grid size for legibility / performance
    colors, _legend_entries = _point_colors(data, "group")
    axes = fig.subplots(n, n, sharex="col")
    if n == 1:
        axes = np.array([[axes]])
    _bg, _t, muted_fg, _g, _s = tokens_for(axes.flat[0])
    for i in range(n):
        for j in range(n):
            ax = axes[i, j]
            ax.tick_params(labelsize=6, colors=muted_fg)
            for sp in ax.spines.values():
                sp.set_color(_s)
            if i == j:
                ax.hist(X[:, i], bins=30, color=theme.Colors.hist_fill, alpha=0.8)
            else:
                ax.scatter(X[:, j], X[:, i], s=3, c=colors, alpha=0.4,
                           edgecolors="none", rasterized=True)
            if i == n - 1:
                ax.set_xlabel(labels[j], fontsize=6, color=muted_fg)
            if j == 0:
                ax.set_ylabel(labels[i], fontsize=6, color=muted_fg)
    fig.suptitle("Feature pair matrix (SPLOM)", fontsize=9, color=_t,
                 fontweight="bold")
    fig.subplots_adjust(left=0.10, bottom=0.10, right=0.98, top=0.94,
                        hspace=0.15, wspace=0.15)


# ── dimensionality reduction ─────────────────────────────────────────────────

def _render_dimred(fig, res) -> None:
    coords = res["coords"]
    axis_labels = res["axis_labels"]
    data = res["data"]
    loadings = res.get("loadings")
    # PCA carries loadings → split the panel: scatter left, loading heatmap right.
    if loadings is not None:
        ax, ax_load = fig.subplots(1, 2, gridspec_kw={"width_ratios": [2.2, 1.0]})
    else:
        ax = fig.add_subplot(111)
        ax_load = None
    apply_ax_style(ax, "2-D projection", axis_labels[1])
    ax.set_xlabel(axis_labels[0], fontsize=8)
    colors, legend = _point_colors(data, res.get("color_by", "group"),
                                   res.get("clusters"))
    _bg, _t, muted_fg, _g, _s = tokens_for(ax)
    ax.scatter(coords[:, 0], coords[:, 1], s=8, c=colors, alpha=0.6,
               edgecolors="none", rasterized=True)
    _legend(ax, legend, muted_fg,
            title={"group": "Group", "well": "Well", "timepoint": "Time",
                   "cluster": "Cluster"}.get(res.get("color_by", "group"), ""))
    if ax_load is not None:
        _draw_loadings(fig, ax_load, loadings)
        fig.subplots_adjust(left=0.09, bottom=0.11, right=0.98, top=0.94, wspace=0.55)
    else:
        fig.subplots_adjust(left=0.12, bottom=0.11, right=0.97, top=0.94)
    note = res.get("sampled_note")
    if note:
        _footnote(fig, note)


def _draw_loadings(fig, ax, loadings) -> None:
    """Heatmap of feature loadings (rows) on PC1 / PC2 (cols), value-annotated —
    reads which features drive each principal component."""
    import numpy as np

    comps = np.asarray(loadings["components"], dtype=float)   # n_comp x n_feat
    labels = list(loadings["feature_labels"])
    mat = comps.T                                            # n_feat x n_comp
    n_pc = mat.shape[1]
    apply_ax_style(ax, "PCA loadings", "")
    ax.grid(False)
    _bg, _t, muted_fg, _g, _s = tokens_for(ax)
    vmax = float(np.nanmax(np.abs(mat))) if mat.size else 1.0
    vmax = vmax if vmax > 0 else 1.0
    _annotated_matrix(
        ax, mat, cmap="RdBu_r", vmin=-vmax, vmax=vmax,
        row_labels=labels, col_labels=[f"PC{i + 1}" for i in range(n_pc)],
        muted_fg=muted_fg, x_rotation=0, fmt="{:.2f}", annotate=len(labels) <= 20,
        annot_color=lambda v: "white" if abs(v) > 0.5 * vmax else muted_fg,
    )


# ── clustering / phenotyping ─────────────────────────────────────────────────

def _render_clustering(fig, res) -> None:
    data = res["data"]
    clusters = res["clusters"]
    coords = res["coords"]
    axis_labels = res["axis_labels"]
    profile = res.get("profile")
    if profile is not None:
        ax1, ax2, ax3 = fig.subplots(1, 3, gridspec_kw={"width_ratios": [1.4, 1.1, 1.2]})
    else:
        ax1, ax2 = fig.subplots(1, 2)
        ax3 = None

    # Left: PCA scatter coloured by cluster.
    apply_ax_style(ax1, "Clusters (PCA projection)", axis_labels[1])
    ax1.set_xlabel(axis_labels[0], fontsize=8)
    colors, legend = _point_colors(data, "cluster", clusters)
    _bg, _t, muted_fg, _g, _s = tokens_for(ax1)
    ax1.scatter(coords[:, 0], coords[:, 1], s=8, c=colors, alpha=0.6,
                edgecolors="none", rasterized=True)
    _legend(ax1, legend, muted_fg, title="Cluster")

    # Right: stacked bar of cluster fractions per group.
    apply_ax_style(ax2, "Phenotype composition", "Fraction of cells")
    ax2.grid(False)
    import matplotlib
    cmap = matplotlib.colormaps[_CLUSTER_CMAP]
    names = res["group_names"]
    cids = res["cluster_ids"]
    fractions = res["fractions"]
    x = np.arange(len(names))
    bottom = np.zeros(len(names))
    for ci, cid in enumerate(cids):
        vals = fractions[:, ci]
        ax2.bar(x, vals, bottom=bottom, color=cmap(int(cid) % cmap.N),
                width=0.8, label=f"Cluster {cid}")
        bottom += vals
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, rotation=45, ha="right", fontsize=7, color=muted_fg)
    ax2.set_ylim(0, 1)

    # Far right (optional): cluster feature-profile heatmap — what each cluster
    # IS, in feature space (the interpretable substitute for t-SNE/PCA axes).
    if ax3 is not None:
        _draw_cluster_profile(ax3, profile)

    if clusters.subsampled:
        _footnote(fig, f"Hierarchical clustering fit on {clusters.n_sampled} "
                       f"sampled cells (of {data['n_cells']}).")
    fig.subplots_adjust(left=0.06, bottom=0.22, right=0.98, top=0.92, wspace=0.45)


def _draw_cluster_profile(ax, profile) -> None:
    """Heatmap of each cluster's mean (z-scored) feature value — rows = clusters,
    cols = features, red = above-population-mean, blue = below."""
    mat = np.asarray(profile["matrix"], dtype=float)          # n_clusters x n_feat
    cids = list(profile["cluster_ids"])
    labels = list(profile["feature_labels"])
    apply_ax_style(ax, "Cluster feature profile", "")
    ax.grid(False)
    _bg, _t, muted_fg, _g, _s = tokens_for(ax)
    vmax = float(np.nanmax(np.abs(mat))) if mat.size else 1.0
    vmax = vmax if vmax > 0 else 1.0
    _annotated_matrix(
        ax, mat, cmap="RdBu_r", vmin=-vmax, vmax=vmax,
        row_labels=[f"C{c}" for c in cids], col_labels=labels, muted_fg=muted_fg,
        fmt="{:.1f}", annotate=mat.shape[0] * mat.shape[1] <= 120,
        annot_color=lambda v: "white" if abs(v) > 0.5 * vmax else muted_fg,
    )


# ── multi-factor faceting ────────────────────────────────────────────────────

def _render_facet(fig, res) -> None:
    pivot = res["pivot"]
    values = pivot.values
    ax = fig.add_subplot(111)
    apply_ax_style(ax, f"{res['value_label']} — {res['agg']} "
                       f"by {res['row_factor']} x {res['col_factor']}", "")
    ax.grid(False)
    _bg, _t, muted_fg, _g, _s = tokens_for(ax)
    im = _annotated_matrix(
        ax, values, cmap="viridis", vmin=None, vmax=None,
        row_labels=pivot.row_labels, col_labels=pivot.col_labels,
        muted_fg=muted_fg, fmt="{:.3g}", annotate=values.size <= 200,
        annot_color=lambda v: "white", mask_invalid=True,
    )
    ax.set_xlabel(res["col_factor"], fontsize=8, color=muted_fg)
    ax.set_ylabel(res["row_factor"], fontsize=8, color=muted_fg)
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    setattr(cbar.ax, "_aw_colorbar", True)
    cbar.ax.tick_params(labelsize=7, colors=muted_fg)
    fig.subplots_adjust(left=0.20, bottom=0.28, right=0.98, top=0.92)
