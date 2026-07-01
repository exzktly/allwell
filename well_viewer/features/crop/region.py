"""Crop-region model + the pure inside-the-box verdict.

The crop "layer" is a non-destructive, dataset-wide sibling of the QC layer
(:mod:`well_viewer.features.qc.params`): it never deletes rows or mutates the
canonical ``Included`` flag. Instead it computes a separate per-cell
``Crop_Pass`` series from each cell's centroid, and the single inclusion
chokepoint (:func:`well_viewer.data.data_loading.df_included_mask`) ANDs it with
``Included`` and ``QC_Pass`` — effective inclusion is
``manual-keep AND passes-QC AND inside-crop``.

The crop is one rectangle in mask-pixel (row, col) space. Because every FOV /
well shares image dimensions, a single rectangle applies uniformly across the
whole dataset; the cell's centroid is a property of its CSV row, so the verdict
is keyed by the cell, not by the displayed FOV's mask.

This module is Qt-free and dependency-light (pandas only) so the verdict is
unit-tested without the app.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Always-on centroid columns the pipeline emits for every cell
# (``shape_features_core.ALWAYS_ON_KEYS``). ``centroid_col`` is the x pixel,
# ``centroid_row`` is the y pixel.
CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"

# (row0, col0, row1, col1) in source-mask pixels, normalized so row0<=row1,
# col0<=col1.
CropRegion = Tuple[int, int, int, int]


def normalize_region(
    row0: float, col0: float, row1: float, col1: float,
) -> Optional[CropRegion]:
    """Return a sorted integer ``(row0, col0, row1, col1)`` region, or ``None``
    when the box is degenerate (zero area) so callers can treat it as "no crop".
    """
    r0, r1 = sorted((int(round(row0)), int(round(row1))))
    c0, c1 = sorted((int(round(col0)), int(round(col1))))
    if r1 <= r0 or c1 <= c0:
        return None
    return (r0, c0, r1, c1)


def compute_crop_pass(df, region: Optional[CropRegion]):
    """Return an int Series (1 = centroid inside the crop, 0 = outside) for ``df``.

    Pure function of the frame's centroid columns and ``region``. Fail-open: when
    ``region`` is ``None``, the frame is empty, or either centroid column is
    absent (legacy CSVs without shape features), every row passes — we never
    exclude a cell we cannot place. Cells with a NaN centroid also pass. Bounds
    are inclusive.
    """
    import pandas as pd

    passed = pd.Series(1, index=df.index, dtype=int)
    if (
        region is None
        or df is None
        or len(df) == 0
        or CENTROID_ROW_COL not in df.columns
        or CENTROID_COL_COL not in df.columns
    ):
        return passed

    r0, c0, r1, c1 = region
    row = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce")
    col = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce")
    inside = (
        (row >= r0) & (row <= r1) & (col >= c0) & (col <= c1)
    )
    # NaN centroid → unplaceable → keep (fail-open), matching compute_qc_pass.
    inside = inside | row.isna() | col.isna()
    return inside.astype(int)


def crop_effect_for_df(df, region):
    """Summarise a crop's effect on one frame.

    Returns ``(removed, otherwise_included, has_centroids)``:
    - ``otherwise_included`` — cells that would count toward analysis ignoring
      the crop (``Included==1`` AND ``QC_Pass==1`` when those columns exist).
    - ``removed`` — of those, how many the crop now drops (centroid outside the
      box). This is the crop's marginal effect on every plot/stat.
    - ``has_centroids`` — whether the frame carries the ``shape_centroid_*``
      columns the crop needs; when False the crop fails open and ``removed`` is 0.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return 0, 0, False
    has_centroids = (
        CENTROID_ROW_COL in df.columns and CENTROID_COL_COL in df.columns
    )
    incl = pd.Series(True, index=df.index)
    if "Included" in df.columns:
        incl &= pd.to_numeric(df["Included"], errors="coerce").fillna(0).eq(1)
    if "QC_Pass" in df.columns:
        incl &= pd.to_numeric(df["QC_Pass"], errors="coerce").fillna(1).eq(1)
    otherwise_included = int(incl.sum())
    crop = compute_crop_pass(df, region).eq(1)
    removed = int((incl & ~crop).sum())
    return removed, otherwise_included, has_centroids

