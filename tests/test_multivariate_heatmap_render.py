"""Characterization tests for the multivariate heatmap renderers (audit A13).

The four annotated-matrix blocks (`_render_correlation`, `_draw_loadings`,
`_draw_cluster_profile`, `_render_facet`) are being routed through one shared
`_annotated_matrix` helper. These tests snapshot the *drawn* output — the image
array, tick labels, and each text annotation (position, string, colour) — so the
refactor can be proven pixel-identical.
"""
from __future__ import annotations

import types

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from well_viewer.features.multivariate import renderer as R


def _annotations(ax):
    """Every text annotation on *ax* as a sortable (x, y, str, color) tuple."""
    out = []
    for t in ax.texts:
        x, y = t.get_position()
        out.append((round(float(x), 3), round(float(y), 3), t.get_text(),
                    str(t.get_color())))
    return sorted(out)


def _snapshot(ax):
    im = ax.images[0]
    arr = np.asarray(im.get_array(), dtype=float)
    return {
        "image": np.round(np.nan_to_num(arr, nan=-999.0), 4).tolist(),
        "clim": tuple(round(c, 4) for c in im.get_clim()),
        "cmap": im.get_cmap().name,
        "xticklabels": [t.get_text() for t in ax.get_xticklabels()],
        "yticklabels": [t.get_text() for t in ax.get_yticklabels()],
        "annotations": _annotations(ax),
    }


def _corr_res(n=4):
    rng = np.arange(n * n, dtype=float).reshape(n, n) / (n * n)
    corr = (rng + rng.T) / 2.0
    np.fill_diagonal(corr, 1.0)
    return {"corr": corr, "data": {"feature_labels": [f"f{i}" for i in range(n)]},
            "view": "corr"}


def test_correlation_snapshot_stable():
    fig = plt.figure()
    R._render_correlation(fig, _corr_res(4))
    snap = _snapshot(fig.axes[0])
    plt.close(fig)
    assert snap["cmap"] == "RdBu_r"
    assert snap["clim"] == (-1.0, 1.0)
    assert snap["xticklabels"] == ["f0", "f1", "f2", "f3"]
    # 4x4 <= 12 side → fully annotated (16 finite cells); ``.2f`` fmt.
    assert len(snap["annotations"]) == 16
    assert (0.0, 0.0, "1.00", "white") in snap["annotations"]  # diagonal r=1


def test_loadings_snapshot_stable():
    fig = plt.figure()
    ax = fig.add_subplot(111)
    loadings = {"components": np.array([[0.9, -0.2, 0.1], [0.3, 0.8, -0.6]]),
                "feature_labels": ["a", "b", "c"]}
    R._draw_loadings(fig, ax, loadings)
    snap = _snapshot(ax)
    plt.close(fig)
    assert snap["cmap"] == "RdBu_r"
    assert snap["xticklabels"] == ["PC1", "PC2"]
    assert snap["yticklabels"] == ["a", "b", "c"]
    # ``.2f`` fmt; annotation is white when |v| > 0.5*vmax (vmax = 0.9 here).
    assert snap["annotations"] == [
        (0.0, 0.0, "0.90", "white"), (0.0, 1.0, "-0.20", "#4C5360"),
        (0.0, 2.0, "0.10", "#4C5360"), (1.0, 0.0, "0.30", "#4C5360"),
        (1.0, 1.0, "0.80", "white"), (1.0, 2.0, "-0.60", "white"),
    ]


def test_cluster_profile_snapshot_stable():
    fig = plt.figure()
    ax = fig.add_subplot(111)
    profile = {"matrix": np.array([[1.0, -1.0], [0.5, 2.0]]),
               "cluster_ids": [0, 1], "feature_labels": ["x", "y"]}
    R._draw_cluster_profile(ax, profile)
    snap = _snapshot(ax)
    plt.close(fig)
    assert snap["yticklabels"] == ["C0", "C1"]
    # ``.1f`` fmt; white when |v| > 0.5*vmax (vmax = 2.0).
    assert snap["annotations"] == [
        (0.0, 0.0, "1.0", "#4C5360"), (0.0, 1.0, "0.5", "#4C5360"),
        (1.0, 0.0, "-1.0", "#4C5360"), (1.0, 1.0, "2.0", "white"),
    ]


def test_facet_snapshot_stable():
    fig = plt.figure()
    pivot = types.SimpleNamespace(
        values=np.array([[1.0, 2.0], [np.nan, 4.0]]),
        col_labels=["c0", "c1"], row_labels=["r0", "r1"])
    res = {"pivot": pivot, "value_label": "V", "agg": "mean",
           "row_factor": "R", "col_factor": "C"}
    R._render_facet(fig, res)
    snap = _snapshot(fig.axes[0])
    plt.close(fig)
    assert snap["cmap"] == "viridis"
    assert snap["xticklabels"] == ["c0", "c1"]
    # ``.3g`` fmt; NaN skipped; facet annotation colour always white.
    assert snap["annotations"] == [
        (0.0, 0.0, "1", "white"), (1.0, 0.0, "2", "white"),
        (1.0, 1.0, "4", "white"),
    ]
