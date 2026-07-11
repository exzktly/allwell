"""Multivariate feature controller — behaviour + state mutation.

Glues the three pure layers together: read :class:`MultivariateState`, collect
the feature matrix (``collect.collect_feature_matrix``), run the requested
analysis (``domain.multivariate_compute``), stash the result on the state slice,
and repaint (``renderer.render_multivariate``). Also owns the small setters the
tab's controls call, the optional-method availability probes, dataset/channel/
selection reactions, and CSV export. Functions take ``app`` (the
``WellViewerApp``) — no logic lives on the app object itself.
"""

from __future__ import annotations

import importlib.util
import logging

from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import refresh_plot_empty_states
from well_viewer.domain import multivariate_compute as mvc
from well_viewer.features.multivariate import state as mv_state
from well_viewer.features.multivariate.collect import (
    collect_feature_matrix, column_display_label,
)
from well_viewer.features.multivariate.renderer import render_multivariate

_logger = logging.getLogger("well_viewer")

_MIN_CELLS = 3

# t-SNE / UMAP are O(n^2)-ish and run on the UI thread, so on a large selection
# they freeze the app for a long time. Cap the cells fed to them (a seeded random
# subsample) so the projection stays interactive; PCA is cheap and never capped.
# Mirrors the subsample the hierarchical clusterer already uses.
_MAX_PROJECTION_CELLS = 2500


def _subsample_for_projection(X, data, method: str):
    """For the slow projections (t-SNE / UMAP), return a seeded random subsample
    of at most ``_MAX_PROJECTION_CELLS`` cells — ``X`` and every parallel per-cell
    array in ``data`` sliced by the same indices — plus the original cell count
    (or 0 when no subsampling happened). PCA and small selections pass through."""
    import numpy as np

    n = X.shape[0]
    if method not in ("tsne", "umap") or n <= _MAX_PROJECTION_CELLS:
        return X, data, 0
    rng = np.random.default_rng(0)  # deterministic so the view is reproducible
    idx = np.sort(rng.choice(n, size=_MAX_PROJECTION_CELLS, replace=False))
    sub = dict(data)
    sub["X"] = X[idx]
    for key in ("group", "well", "timepoint"):
        arr = data.get(key)
        if arr is not None:
            sub[key] = np.asarray(arr)[idx]
    sub["n_cells"] = int(len(idx))
    return sub["X"], sub, int(n)


# ── optional-method availability ─────────────────────────────────────────────

def _probe(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except Exception:
        return False


def mv_dimred_methods_available() -> dict:
    """Which 2-D projection methods can run in this environment.

    PCA is always available (numpy SVD). UMAP / t-SNE depend on optional libs;
    the tab disables their radio options and shows an install hint when absent.
    """
    return {"pca": True, "umap": _probe("umap"), "tsne": _probe("sklearn")}


# ── state slice access ───────────────────────────────────────────────────────

def _state(ctx):
    return ctx.state.feature_slice("multivariate")


# ── lifecycle hooks ──────────────────────────────────────────────────────────

def mv_on_activate(ctx) -> None:
    """Tab shown: restore persisted config (once), repopulate pickers, recompute."""
    st = _state(ctx)
    if st is None:
        return
    if not ctx.runtime.multivariate.config_loaded:
        try:
            ctx.persistence.load("multivariate")
        except Exception:
            swallowed()
        ctx.runtime.multivariate.config_loaded = True
    mv_refresh_inputs(ctx)
    if ctx.runtime.multivariate.canvas is not None:
        mv_recompute(ctx)


def mv_on_channel_changed(ctx) -> None:
    """Channel list changed → the available feature columns may have changed."""
    if ctx.runtime.multivariate.canvas is not None:
        mv_refresh_inputs(ctx)


def mv_refresh_inputs(ctx) -> None:
    """Rebuild the feature picker + timepoint menu from the live dataset."""
    if ctx.runtime.multivariate.refresh_feature_picker is not None:
        try:
            ctx.runtime.multivariate.refresh_feature_picker()
        except Exception:
            swallowed()
    if ctx.runtime.multivariate.refresh_timepoints is not None:
        try:
            ctx.runtime.multivariate.refresh_timepoints()
        except Exception:
            swallowed()


# ── recompute pipeline ───────────────────────────────────────────────────────

def mv_recompute(ctx) -> None:
    """Collect → compute → render for the current state. The single redraw entry."""
    st = _state(ctx)
    if st is None or ctx.runtime.multivariate.canvas is None:
        return
    # Flip the empty-state stack off its placeholder now that this lazily-built
    # tab exists — the global refresh at load time ran before the tab was built,
    # so its stack would otherwise stay on the "load a directory" page.
    refresh_plot_empty_states(ctx.window)
    st.reset_result()
    data = collect_feature_matrix(
        ctx, feature_cols=st.feature_cols, timepoints=st.timepoints,
    )
    st.n_cells = data["n_cells"]
    X = data["X"]
    n_feat = X.shape[1]

    if n_feat == 0:
        st.last_error = "Select one or more feature columns to build the matrix."
    elif st.n_cells == 0:
        st.last_error = "No cells in the current selection (check wells / gating / timepoints)."
    else:
        # t-SNE / UMAP block the UI thread for a while; show a wait cursor +
        # status so the app reads as "working", not "frozen". Repaint once before
        # the blocking compute so both actually appear.
        slow = (st.mode == mv_state.MODE_DIMRED
                and st.dimred_method in ("tsne", "umap"))
        if slow:
            _begin_busy(ctx, f"Computing {st.dimred_method.upper()}…")
        try:
            st.last_result = _compute_for_mode(ctx, st, data)
        except mvc.MethodUnavailable as exc:
            st.last_error = (
                f"{exc.method} needs the '{exc.package}' package, which is not "
                f"installed. Use PCA, or install {exc.package}."
            )
        except ValueError as exc:
            st.last_error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.exception("Multivariate compute failed")
            st.last_error = f"Analysis failed: {exc}"
        finally:
            if slow:
                _end_busy(ctx)
    render_multivariate(ctx)


def _begin_busy(ctx, message: str) -> None:
    """Show a wait cursor + status and force one repaint so they appear before a
    blocking compute freezes the event loop."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        ctx.notify.set_status(message)
        QApplication.processEvents()  # paint the cursor + status before blocking
    except Exception:
        swallowed()


def _end_busy(ctx) -> None:
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
    except Exception:
        swallowed()


def _compute_for_mode(ctx, st, data) -> dict:
    """Run the analysis for ``st.mode`` and return a renderer-ready payload."""
    X = data["X"]
    mode = st.mode

    if mode == mv_state.MODE_CORRELATION:
        if X.shape[1] < 2:
            raise ValueError("Correlation needs at least two feature columns.")
        if X.shape[0] < _MIN_CELLS:
            raise ValueError("Not enough cells for a correlation (need at least 3).")
        return {
            "kind": mode,
            "corr": mvc.correlation_matrix(X),
            "view": st.corr_view,
            "data": data,
        }

    if mode == mv_state.MODE_DIMRED:
        if X.shape[0] < _MIN_CELLS:
            raise ValueError("Not enough cells to project (need at least 3).")
        result = {
            "kind": mode, "color_by": st.color_by, "data": data,
        }
        if st.dimred_method == "pca":
            # PCA is a linear projection, so we can surface the loadings (how each
            # feature weights onto each PC) — UMAP / t-SNE have no such mapping.
            pca = mvc.pca_svd(X, n_components=2, standardize_data=st.standardize)
            evr = pca.explained_variance_ratio
            result["coords"] = pca.scores[:, :2]
            result["axis_labels"] = [
                f"PC{i + 1} ({(evr[i] * 100 if i < len(evr) else 0):.1f}%)"
                for i in range(2)
            ]
            result["loadings"] = {
                "components": pca.components,        # n_components x n_features
                "evr": evr,
                "feature_labels": data["feature_labels"],
            }
        else:
            # t-SNE / UMAP: cap the cell count so the (main-thread) projection
            # doesn't freeze the app; subset the parallel arrays used for colour.
            Xp, data_p, n_total = _subsample_for_projection(X, data, st.dimred_method)
            coords, axis_labels = mvc.project_2d(
                Xp, method=st.dimred_method, standardize_data=st.standardize,
            )
            result["coords"] = coords
            result["axis_labels"] = axis_labels
            result["data"] = data_p
            if n_total:
                result["sampled_note"] = (
                    f"{st.dimred_method.upper()} on {Xp.shape[0]:,} sampled "
                    f"cells (of {n_total:,})")
            if st.color_by == "cluster":
                result["clusters"] = _run_clustering(st, Xp)
            return result
        if st.color_by == "cluster":
            result["clusters"] = _run_clustering(st, X)
        return result

    if mode == mv_state.MODE_CLUSTERING:
        if X.shape[0] < max(_MIN_CELLS, st.cluster_count):
            raise ValueError(
                f"Need at least {max(_MIN_CELLS, st.cluster_count)} cells for "
                f"{st.cluster_count} clusters."
            )
        clusters = _run_clustering(st, X)
        # PCA scatter for the left panel (always available, cheap).
        coords, axis_labels = mvc.project_2d(
            X, method="pca", standardize_data=st.standardize,
        )
        names, cids, fractions = mvc.cluster_fractions(
            clusters.labels, data["group"], n_clusters=clusters.n_clusters,
        )
        prof_ids, profile = mvc.cluster_feature_profiles(
            X, clusters.labels, n_clusters=clusters.n_clusters,
        )
        return {
            "kind": mode, "clusters": clusters, "coords": coords,
            "axis_labels": axis_labels, "group_names": names,
            "cluster_ids": cids, "fractions": fractions, "data": data,
            "profile": {"cluster_ids": prof_ids, "matrix": profile,
                        "feature_labels": data["feature_labels"]},
        }

    if mode == mv_state.MODE_FACET:
        col = st.facet_value_col or (st.feature_cols[0] if st.feature_cols else "")
        if not col:
            raise ValueError("Pick a feature column to aggregate for faceting.")
        # Re-collect just the facet value column so it need not be in the matrix.
        fdata = collect_feature_matrix(ctx, feature_cols=[col], timepoints=st.timepoints)
        if fdata["n_cells"] == 0:
            raise ValueError("No cells to facet in the current selection.")
        value = fdata["X"][:, 0]
        row_factor = _factor_array(fdata, st.facet_row)
        col_factor = _factor_array(fdata, st.facet_col)
        pivot = mvc.pivot_facets(value, row_factor, col_factor, agg=st.facet_agg)
        return {
            "kind": mode, "pivot": pivot, "value_label": column_display_label(ctx, col),
            "row_factor": st.facet_row, "col_factor": st.facet_col,
            "agg": st.facet_agg, "data": fdata,
        }

    raise ValueError(f"unknown multivariate mode: {mode!r}")


def _run_clustering(st, X):
    if st.cluster_method == "hierarchical":
        return mvc.hierarchical_cluster(
            X, k=st.cluster_count, method=st.linkage_method,
            standardize_data=st.standardize,
        )
    return mvc.kmeans_cluster(X, k=st.cluster_count, standardize_data=st.standardize)


def _factor_array(data, factor: str):
    """Map a facet factor name to the parallel per-cell label array."""
    if factor == "timepoint":
        return [f"{t:g} h" for t in data["timepoint"].tolist()]
    if factor == "group":
        return list(data["group"].tolist())
    return list(data["well"].tolist())  # default: well


# ── setters (called by the tab controls) ─────────────────────────────────────

def _apply(ctx, **changes) -> None:
    """Mutate the state slice, persist (debounced), sync controls, recompute."""
    st = _state(ctx)
    if st is None:
        return
    for key, val in changes.items():
        setattr(st, key, val)
    try:
        ctx.persistence.schedule_save("multivariate")
    except Exception:
        swallowed()
    if ctx.runtime.multivariate.sync_controls is not None:
        try:
            ctx.runtime.multivariate.sync_controls()
        except Exception:
            swallowed()
    mv_recompute(ctx)


def mv_set_mode(ctx, mode: str) -> None:
    _apply(ctx, mode=mode)


def mv_set_corr_view(ctx, view: str) -> None:
    _apply(ctx, corr_view=view)


def mv_set_dimred_method(ctx, method: str) -> None:
    _apply(ctx, dimred_method=method)


def mv_set_color_by(ctx, key: str) -> None:
    _apply(ctx, color_by=key)


def mv_set_cluster_method(ctx, method: str) -> None:
    _apply(ctx, cluster_method=method)


def mv_set_cluster_count(ctx, k: int) -> None:
    _apply(ctx, cluster_count=int(k))


def mv_set_standardize(ctx, on: bool) -> None:
    _apply(ctx, standardize=bool(on))


def mv_set_features(ctx, cols) -> None:
    _apply(ctx, feature_cols=list(cols))


def mv_set_facet_row(ctx, factor: str) -> None:
    _apply(ctx, facet_row=factor)


def mv_set_facet_col(ctx, factor: str) -> None:
    _apply(ctx, facet_col=factor)


def mv_set_facet_value(ctx, col: str) -> None:
    _apply(ctx, facet_value_col=col)


def mv_set_facet_agg(ctx, agg: str) -> None:
    _apply(ctx, facet_agg=agg)


def mv_set_linkage(ctx, method: str) -> None:
    _apply(ctx, linkage_method=method)


def mv_set_timepoints(ctx, tps) -> None:
    _apply(ctx, timepoints=[float(t) for t in (tps or [])])


# ── export ───────────────────────────────────────────────────────────────────

def mv_export_csv(ctx) -> None:
    """Write the current feature matrix (+ any cluster labels) to a CSV file."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    import numpy as np

    st = _state(ctx)
    if st is None:
        return
    # Prefer the currently displayed dim-red / clustering result so the exported
    # embedding coordinates + cluster labels match the plot exactly (including any
    # t-SNE/UMAP subsample). Other modes export a freshly collected matrix.
    res = st.last_result if isinstance(st.last_result, dict) else {}
    kind = res.get("kind")
    if kind in (mv_state.MODE_DIMRED, mv_state.MODE_CLUSTERING) and res.get("data"):
        data = res["data"]
    else:
        res = {}
        data = collect_feature_matrix(ctx, feature_cols=st.feature_cols,
                                      timepoints=st.timepoints)
    if data["n_cells"] == 0 or not data["feature_cols"]:
        QMessageBox.information(ctx.window, "Nothing to export",
                               "The current selection produced no feature matrix.")
        return
    init_dir = str(ctx.state.dataset.data_dir or "")
    path, _ = QFileDialog.getSaveFileName(
        ctx.window, "Export feature matrix", init_dir, "CSV files (*.csv)")
    if not path:
        return
    try:
        import pandas as pd

        X = np.asarray(data["X"], dtype=float)
        frame = pd.DataFrame(X, columns=data["feature_cols"])
        frame.insert(0, "well", data["well"])
        frame.insert(1, "group", data["group"])
        frame.insert(2, "timepoint_hours", data["timepoint"])

        # Embedding coordinates for the displayed projection (pca_1/2, tsne_1/2,
        # umap_1/2) — each cell's position travels with its features so the map is
        # quantifiable downstream.
        coords = res.get("coords")
        if coords is not None and len(coords) == len(frame):
            method = st.dimred_method if kind == mv_state.MODE_DIMRED else "pca"
            frame[f"{method}_1"] = np.asarray(coords)[:, 0]
            frame[f"{method}_2"] = np.asarray(coords)[:, 1]

        # Cluster label per cell — from the displayed result when it clustered,
        # else run the current clustering config on the exported matrix.
        clusters = res.get("clusters")
        if clusters is not None and len(clusters.labels) == len(frame):
            frame["cluster"] = clusters.labels
        elif st.mode == mv_state.MODE_CLUSTERING and st.feature_cols:
            try:
                frame["cluster"] = _run_clustering(st, X).labels
            except Exception:
                swallowed()

        frame.to_csv(path, index=False)
        msg = f"Exported {len(frame):,} cells → {path}"
        loadings_path = _write_pca_loadings_csv(ctx, st, data, path)
        if loadings_path:
            msg += f"  ·  PCA loadings → {loadings_path}"
        # Cluster feature-profile companion (what each cluster/region represents).
        prof_path = _write_cluster_profile_csv(ctx, st, res, path)
        if prof_path:
            msg += f"  ·  cluster profiles → {prof_path}"
        ctx.notify.set_status(msg)
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Export failed", f"Could not write CSV:\n{exc}")


def _write_cluster_profile_csv(ctx, st, res, matrix_path: str):
    """Write ``<stem>_cluster_profiles.csv`` — the mean (z-scored) value of each
    feature within each cluster, the interpretable "what does each cluster mean"
    table. Returns the path, or None when the displayed result carries no
    clustering."""
    import numpy as np
    import pandas as pd
    from pathlib import Path

    prof = res.get("profile") if isinstance(res, dict) else None
    if not prof:
        return None
    try:
        matrix = np.asarray(prof["matrix"], dtype=float)
        if matrix.size == 0:
            return None
        df = pd.DataFrame(matrix, columns=list(prof["feature_labels"]),
                          index=[f"cluster_{c}" for c in prof["cluster_ids"]])
        df.index.name = "cluster"
        out = str(Path(matrix_path).with_name(
            Path(matrix_path).stem + "_cluster_profiles.csv"))
        df.to_csv(out)
        return out
    except Exception:
        swallowed()
        return None


def _write_pca_loadings_csv(ctx, st, data, matrix_path: str):
    """Write a ``<stem>_pca_loadings.csv`` next to the feature matrix.

    Rows are features; columns are PC1..PCk (each cell = that feature's loading
    on that PC), followed by an ``explained_variance_ratio`` row. Skipped (returns
    ``None``) when the matrix can't support PCA (< 2 features or < 2 cells)."""
    import numpy as np
    import pandas as pd
    from pathlib import Path

    X = np.asarray(data["X"], dtype=float)
    if X.ndim != 2 or X.shape[1] < 2 or X.shape[0] < 2:
        return None
    try:
        n_comp = int(min(X.shape[0], X.shape[1]))
        pca = mvc.pca_svd(X, n_components=n_comp, standardize_data=st.standardize)
        comps = np.asarray(pca.components, dtype=float)   # k x n_features
        k = comps.shape[0]
        cols = [f"PC{i + 1}" for i in range(k)]
        loadings = pd.DataFrame(comps.T, index=data["feature_labels"], columns=cols)
        loadings.index.name = "feature"
        evr = np.asarray(pca.explained_variance_ratio, dtype=float)
        loadings.loc["explained_variance_ratio"] = list(evr[:k])
        out = str(Path(matrix_path).with_name(Path(matrix_path).stem + "_pca_loadings.csv"))
        loadings.to_csv(out)
        return out
    except Exception:
        swallowed()
        return None
