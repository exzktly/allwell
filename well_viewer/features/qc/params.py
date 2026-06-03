"""QC parameter model + the pure debris-gating verdict (§B / QC editor).

The QC "layer" is intentionally **non-destructive and composable**: it never
deletes rows or mutates the canonical ``Included`` flag (which manual curation
and cell-gating own). Instead it computes a separate per-cell ``QC_Pass`` series
from the persisted shape metrics, and the single inclusion chokepoint
(:func:`well_viewer.data.data_loading.df_included_mask`) ANDs the two together —
so the effective inclusion is ``manual-keep AND passes-QC``.

Because the verdict is a pure function of (shape metrics, thresholds), it can be
recomputed live whenever the thresholds change in the QC sidebar view — no
re-segmentation, no re-run. Flat-field correction is *not* part of this layer:
it rescales pixel intensities at quantification time and can only be changed by
re-running the pipeline.

This module is Qt-free and dependency-light (pandas only) so the gating logic is
unit-tested without the app.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

# CSV columns the gate reads. ``area`` is always present as the base ``area_px``
# measurement (``shape_area`` too when shape features are selected); ``solidity``
# is emitted unconditionally by the pipeline so the gate is editable post-hoc.
AREA_COLS = ("shape_area", "area_px")
SOLIDITY_COL = "shape_solidity"


def _first_col(df, names):
    for n in names:
        if n in df.columns:
            return n
    return None


def _pos(v: Any) -> Optional[float]:
    """Coerce to a positive float bound, or ``None`` when unset.

    A 0 / negative / blank value means "no bound" — matching the Analyze QC
    defaults (the entries default to ``0``), and avoiding the ``max_area == 0``
    foot-gun of excluding every cell.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


@dataclass(frozen=True)
class QCParams:
    """Editable debris-gating thresholds. ``enabled`` mirrors the Analyze
    *Remove debris* toggle; the bounds are ``None`` when unset (no gate)."""

    enabled: bool = False
    min_area: Optional[float] = None
    max_area: Optional[float] = None
    min_solidity: Optional[float] = None

    # ── (de)serialization ────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "min_area": self.min_area,
            "max_area": self.max_area,
            "min_solidity": self.min_solidity,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "QCParams":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            min_area=_pos(d.get("min_area")),
            max_area=_pos(d.get("max_area")),
            min_solidity=_pos(d.get("min_solidity")),
        )

    @classmethod
    def from_pipeline_info(cls, info: Optional[Dict[str, Any]]) -> "QCParams":
        """Seed from a dataset's ``pipeline_info.json`` ``qc_options`` block so
        the QC view opens pre-populated with the thresholds used at run time.

        Also migrates the retired legacy ``cell_gating.cell_area_threshold``
        into the QC layer's ``min_area`` (enabling the gate) so datasets created
        before the QC layer keep their cell-area filtering — now QC-owned.
        """
        info = info if isinstance(info, dict) else {}
        qc = info.get("qc_options", {}) or {}
        enabled = bool(qc.get("remove_debris", False))
        min_area = _pos(qc.get("min_area"))
        legacy = _pos((info.get("cell_gating", {}) or {}).get("cell_area_threshold"))
        if min_area is None and legacy is not None:
            min_area = legacy
            enabled = True
        return cls(
            enabled=enabled,
            min_area=min_area,
            max_area=_pos(qc.get("max_area")),
            min_solidity=_pos(qc.get("min_solidity")),
        )

    def with_updates(self, **changes: Any) -> "QCParams":
        # normalize numeric bounds through _pos so 0/"" become None
        for k in ("min_area", "max_area", "min_solidity"):
            if k in changes:
                changes[k] = _pos(changes[k])
        if "enabled" in changes:
            changes["enabled"] = bool(changes["enabled"])
        return replace(self, **changes)

    @property
    def is_active(self) -> bool:
        """True when the gate would actually exclude anything (enabled with at
        least one bound set)."""
        return self.enabled and any(
            b is not None for b in (self.min_area, self.max_area, self.min_solidity)
        )


def compute_qc_pass(df, params: QCParams):
    """Return an int Series (1 = passes QC, 0 = debris) aligned to ``df``.

    Pure function of the frame's shape metrics and ``params``. A cell fails when
    a present metric falls outside a set bound; cells whose metric is missing /
    NaN are treated as **passing** (we never exclude a cell we can't measure).
    When the gate is inactive, every row passes.
    """
    import pandas as pd

    passed = pd.Series(1, index=df.index, dtype=int)
    if not params.is_active or df is None or len(df) == 0:
        return passed

    fail = pd.Series(False, index=df.index)
    area_col = _first_col(df, AREA_COLS)
    if area_col is not None and (params.min_area is not None or params.max_area is not None):
        area = pd.to_numeric(df[area_col], errors="coerce")
        if params.min_area is not None:
            fail |= (area < params.min_area).fillna(False)
        if params.max_area is not None:
            fail |= (area > params.max_area).fillna(False)
    if SOLIDITY_COL in df.columns and params.min_solidity is not None:
        sol = pd.to_numeric(df[SOLIDITY_COL], errors="coerce")
        fail |= (sol < params.min_solidity).fillna(False)

    passed[fail] = 0
    return passed
