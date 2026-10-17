"""pipeline.shape_features — shared, Qt-free catalog of per-cell shape features.

Single source of truth for the optional morphology features the pipeline can
emit per cell and that the Review GUI surfaces in the channel dropdowns.
Imported by BOTH ``process_microscopy.py`` (to compute / emit) and
``review`` (to detect / label) so the two sides cannot drift — the same
role ``pipeline.auto_threshold`` and ``util.well_token`` play (shared Qt-free
``core`` modules importable by the headless pipeline).

Design notes
------------
* Per-cell shape columns are emitted with a ``shape_`` prefix and are
  **channel-independent** (they describe the segmentation mask, not a
  fluorescence channel), so they do NOT follow the ``<channel>_<metric>``
  convention used by intensity / smFISH columns.
* ``centroid_row`` / ``centroid_col`` are in :data:`ALWAYS_ON_KEYS`: the pipeline
  emits them regardless of the user's selection because they are the per-cell
  positions the cell-tracking feature links across timepoints.
* This module is pure data + tiny helpers. It does **not** import skimage,
  numpy, pandas, or Qt at module load — the actual ``regionprops`` computation
  lives in the pipeline, keyed off :func:`regionprops_properties`.
"""

from __future__ import annotations

from dataclasses import dataclass

COLUMN_PREFIX = "shape_"


@dataclass(frozen=True)
class ShapeFeature:
    """One selectable morphology feature.

    key        selection token (Analyze chip / ``--shape_features`` value),
               e.g. ``"eccentricity"``.
    column     emitted CSV column, e.g. ``"shape_eccentricity"``.
    label      friendly dropdown label, e.g. ``"Eccentricity"``.
    regionprop ``skimage.measure.regionprops`` base property required to derive
               this feature (``""`` for purely-derived features).
    kind       value family — ``"ratio"`` / ``"length_px"`` / ``"area_px"`` /
               ``"angle_rad"`` / ``"coord"`` — for axis labelling and so the GUI
               knows a shape metric has no gating threshold.
    derived    True when computed from other props rather than read directly
               (e.g. circularity = 4*pi*area / perimeter**2).
    """

    key: str
    column: str
    label: str
    regionprop: str
    kind: str
    derived: bool = False


# Emitted unconditionally (not user-selectable).
#   centroids — required by cell tracking (cross-timepoint linking);
#   circularity — always available so the Segmentation tab's QC debris gate can
#                 filter on it without depending on a per-run feature selection.
ALWAYS_ON_KEYS: tuple[str, ...] = ("centroid_row", "centroid_col", "circularity")


_FEATURES: tuple[ShapeFeature, ...] = (
    ShapeFeature("area",              "shape_area",              "Area",              "area",              "area_px"),
    ShapeFeature("perimeter",         "shape_perimeter",         "Perimeter",         "perimeter",         "length_px"),
    ShapeFeature("eccentricity",      "shape_eccentricity",      "Eccentricity",      "eccentricity",      "ratio"),
    ShapeFeature("solidity",          "shape_solidity",          "Solidity",          "solidity",          "ratio"),
    ShapeFeature("extent",            "shape_extent",            "Extent",            "extent",            "ratio"),
    ShapeFeature("major_axis_length", "shape_major_axis_length", "Major Axis Length", "major_axis_length", "length_px"),
    ShapeFeature("minor_axis_length", "shape_minor_axis_length", "Minor Axis Length", "minor_axis_length", "length_px"),
    ShapeFeature("orientation",       "shape_orientation",       "Orientation",       "orientation",       "angle_rad"),
    # Derived: 4*pi*area / perimeter**2 (1.0 = perfect circle).
    ShapeFeature("circularity",       "shape_circularity",       "Circularity",       "",                  "ratio", True),
    # Always-on (centroid maps to two scalar columns).
    ShapeFeature("centroid_row",      "shape_centroid_row",      "Centroid (row)",    "centroid",          "coord"),
    ShapeFeature("centroid_col",      "shape_centroid_col",      "Centroid (col)",    "centroid",          "coord"),
)

#: Public, ordered view of the catalog (use instead of the private ``_FEATURES``).
FEATURES: tuple[ShapeFeature, ...] = _FEATURES
#: All emitted shape columns, in catalog order (incl. always-on centroids).
ALL_COLUMNS: tuple[str, ...] = tuple(f.column for f in _FEATURES)

BY_KEY: dict[str, ShapeFeature] = {f.key: f for f in _FEATURES}
BY_COLUMN: dict[str, ShapeFeature] = {f.column: f for f in _FEATURES}

# Keys offered in the Analyze "Shape Metrics" selector (excludes always-on
# centroids, which are emitted unconditionally).
SELECTABLE_KEYS: tuple[str, ...] = tuple(
    f.key for f in _FEATURES if f.key not in ALWAYS_ON_KEYS
)


def is_shape_column(col: str) -> bool:
    """True if *col* is a shape-feature column (``shape_`` prefix)."""
    return isinstance(col, str) and col.startswith(COLUMN_PREFIX)


def column_for(key: str) -> str | None:
    """CSV column for a selection *key* (``"eccentricity"`` → ``"shape_eccentricity"``)."""
    feat = BY_KEY.get(key)
    return feat.column if feat else None


def label_for(column: str) -> str | None:
    """Friendly dropdown label for a shape *column* (``None`` if not a shape column)."""
    feat = BY_COLUMN.get(column)
    return feat.label if feat else None


def has_threshold(column: str) -> bool:
    """Shape metrics have no cell-gating threshold (continuous morphology).

    Mirrors the smFISH-count rule: callers should aggregate every cell
    (``threshold=-1.0``) rather than gate on a fluorescence floor.
    """
    return not is_shape_column(column)


def normalize_selection(keys) -> list[str]:
    """Filter *keys* to known selectable feature keys, preserving catalog order."""
    requested = {str(k).strip().lower() for k in (keys or [])}
    return [k for k in SELECTABLE_KEYS if k in requested]


def emitted_columns(selected_keys) -> list[str]:
    """Columns the pipeline emits for *selected_keys* plus the always-on centroids."""
    cols = [column_for(k) for k in normalize_selection(selected_keys)]
    cols += [column_for(k) for k in ALWAYS_ON_KEYS]
    return [c for c in cols if c]


def regionprops_properties(selected_keys) -> tuple[str, ...]:
    """``skimage.measure.regionprops_table`` ``properties`` needed for the selection.

    Collects the base regionprop names for the selected features plus the
    always-on centroids, expanding derived features to their inputs
    (circularity → area + perimeter). Deduped, deterministic order.
    """
    keys = list(normalize_selection(selected_keys)) + list(ALWAYS_ON_KEYS)
    props: list[str] = []
    for k in keys:
        feat = BY_KEY.get(k)
        if feat is None:
            continue
        if feat.derived:
            if k == "circularity":
                props += ["area", "perimeter"]
            continue
        if feat.regionprop:
            props.append(feat.regionprop)
    # dedupe, preserve first-seen order
    seen: set[str] = set()
    ordered: list[str] = []
    for p in props:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return tuple(ordered)


#: Per-cell flag: 1 when the cell's mask reaches the image's outermost row or
#: column, else 0. Emitted unconditionally, like ``qc_region_area``, so the
#: Review QC gate can drop partial cells post-hoc without re-segmenting. Not a
#: ``shape_`` column: it is a fact about where the cell sits in the frame, not
#: about its morphology, and it has no business in the shape-metric dropdowns.
BORDER_COLUMN = "qc_touches_border"


def labels_touching_border(labels) -> set[int]:
    """Label ids with at least one pixel on the image's outermost row/column.

    The same rule as MATLAB's ``imclearborder`` (and skimage's
    ``clear_border`` with ``buffer_size=0``) applied to a label image: a cell
    cut by the field of view is partial, so its area, intensity totals and
    shape describe the visible fragment rather than the cell. Only the four
    edge strips are read, so the cost is O(perimeter), not O(image).

    Returns an empty set for an empty or degenerate (< 1 px on an axis)
    image. Background (label 0) is never reported.
    """
    import numpy as np

    lab = np.asarray(labels)
    if lab.ndim != 2 or lab.size == 0:
        return set()
    edges = np.concatenate((lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]))
    return {int(v) for v in np.unique(edges) if v != 0}
