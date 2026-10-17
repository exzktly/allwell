"""QC parameter model + the pure debris-gating verdict (§B / QC editor).

The QC "layer" is intentionally **non-destructive and composable**: it never
deletes rows or mutates the canonical ``Included`` flag (which manual curation
and cell-gating own). Instead it computes a separate per-cell ``QC_Pass`` series
from the persisted shape metrics, and the single inclusion chokepoint
(:func:`review.data.data_loading.df_included_mask`) ANDs the two together —
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
from typing import Any

# CSV columns the gate reads. ``area`` is always present as the base ``area_px``
# measurement (``shape_area`` too when shape features are selected); ``solidity``
# is emitted unconditionally by the pipeline so the gate is editable post-hoc.
AREA_COLS = ("shape_area", "area_px")
SOLIDITY_COL = "shape_solidity"
# Circularity (4π·area/perimeter², 1.0 = perfect circle) is emitted
# unconditionally by the pipeline, so this gate is always editable post-hoc.
CIRCULARITY_COL = "shape_circularity"
# Area of the connected bright *region* a cell sits in (always emitted by the
# pipeline). Rejecting on this catches debris StarDist shattered into many small
# cells — the fragments share one large region the per-cell size gate can't see.
REGION_AREA_COL = "qc_region_area"


def _first_col(df, names) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def _pos(v: Any) -> float | None:
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


def is_nanowell_run(info: dict[str, Any] | None) -> bool:
    """Whether ``pipeline_info.json`` describes a nanowell-mode run.

    The pipeline writes a ``nanowell`` block for every nanowell run
    (``pipeline.nanowell_task.record_nanowell_info``) and never otherwise.
    """
    return isinstance(info, dict) and isinstance(info.get("nanowell"), dict)


def default_exclude_border_cells(info: dict[str, Any] | None) -> bool:
    """The border switch's default for a dataset: on, except for nanowell runs."""
    return not is_nanowell_run(info)


@dataclass(frozen=True)
class QCParams:
    """Editable debris-gating thresholds. ``enabled`` mirrors the Analyze
    *Remove debris* toggle; the bounds are ``None`` when unset (no gate)."""

    enabled: bool = False
    #: Whether the run's own per-field quality verdicts exclude rows.
    #:
    #: **Off by default, and deliberately.** The field-quality subsystem
    #: reports; it does not decide. Turning it on filters out every row whose
    #: field the run flagged, which changes every number in the app — so it is
    #: a thing the user chooses after reading what was flagged, not a default
    #: they discover by finding their counts have moved.
    #:
    #: It is here rather than in its own block because it is the same kind of
    #: thing as the debris gate, edited on the same panel, persisted in the
    #: same section, and ANDed at the same chokepoint.
    exclude_flagged_fields: bool = False
    #: Whether cells whose own outline read as soft are excluded.
    #:
    #: **Off by default for the same reason as the field verdict above**, and a
    #: separate switch from it because the two answer different questions and
    #: neither subsumes the other. The field verdict is the run's six-metric
    #: judgement about a *whole* field — it sees a field that is clipped or
    #: unlike the rest of the plate, and it removes all of it or none of it.
    #: This one is an absolute cut on each cell's own boundary sharpness, so it
    #: removes the soft *part* of a field: a focus gradient across one FOV, or
    #: a coverslip tilt, neither of which is a whole-field fact and neither of
    #: which is a rectangle.
    exclude_soft_cells: bool = False
    #: Whether cells the field of view cuts are excluded (MATLAB's
    #: ``imclearborder`` rule, read from the pipeline's ``qc_touches_border``
    #: flag — see :mod:`review.data.border_gate`).
    #:
    #: **On by default for a dataset, off for a nanowell one** — but the
    #: dataclass default is ``False`` and the dataset default is decided in
    #: :meth:`from_pipeline_info` (:func:`default_exclude_border_cells`),
    #: because only the sidecar can say which kind of run it was. A partial
    #: cell's area and totals describe a fragment, so for an ordinary plate the
    #: honest default is to drop it. A nanowell run is different: its image is
    #: tiled across a lattice of wells, the tile edge is not a well edge, and
    #: its cells are assigned to wells by the nanowell measurement itself, so
    #: dropping everything a tile edge touches would remove cells for a reason
    #: that has nothing to do with them.
    #:
    #: Independent of ``enabled``: it is not a debris bound and writes its own
    #: ``Border_Pass`` column, so it needs no debris bound set to take effect.
    exclude_border_cells: bool = False
    #: The per-cell focus value below which a cell counts as out of focus.
    #:
    #: **Absolute, never relative to the frame.** A score relative to its own
    #: field cannot see a field that is uniformly soft, because every cell in
    #: it deviates from its neighbours by nothing — measured, the relative
    #: median is exactly 1.000 at three very different focus states. The
    #: estimator behind the column is a transition *width* in pixels, which is
    #: what lets this be one number: gain and pedestal do not move it, and
    #: brightness, nucleus size and crowding move it under 5 %. Clipping does
    #: move it — a flat-topped nucleus reads sharper — so a cut chosen on an
    #: unsaturated plate is too aggressive on a saturated one.
    #:
    #: Seeded from the run's own sidecar when it stated one, because the value
    #: depends mostly on the microscope rather than on the plate. ``None`` means no
    #: cut, which is what a dataset whose run stated none carries.
    focus_min: float | None = None
    min_area: float | None = None
    max_area: float | None = None
    min_solidity: float | None = None
    max_region_area: float | None = None
    min_circularity: float | None = None
    max_circularity: float | None = None

    # ── (de)serialization ────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "exclude_flagged_fields": bool(self.exclude_flagged_fields),
            "exclude_soft_cells": bool(self.exclude_soft_cells),
            "exclude_border_cells": bool(self.exclude_border_cells),
            "focus_min": self.focus_min,
            "min_area": self.min_area,
            "max_area": self.max_area,
            "min_solidity": self.min_solidity,
            "max_region_area": self.max_region_area,
            "min_circularity": self.min_circularity,
            "max_circularity": self.max_circularity,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> QCParams:
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            exclude_flagged_fields=bool(d.get("exclude_flagged_fields", False)),
            exclude_soft_cells=bool(d.get("exclude_soft_cells", False)),
            exclude_border_cells=bool(d.get("exclude_border_cells", False)),
            focus_min=_pos(d.get("focus_min")),
            min_area=_pos(d.get("min_area")),
            max_area=_pos(d.get("max_area")),
            min_solidity=_pos(d.get("min_solidity")),
            max_region_area=_pos(d.get("max_region_area")),
            min_circularity=_pos(d.get("min_circularity")),
            max_circularity=_pos(d.get("max_circularity")),
        )

    @classmethod
    def from_pipeline_info(cls, info: dict[str, Any] | None) -> QCParams:
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
            # The run states the cut and never applies it, so this is where it
            # first does anything. ``exclude_soft_cells`` is deliberately NOT
            # seeded on with it: a recorded threshold is the user saying "this
            # is my microscope's number", not "drop those cells from every
            # plot without telling me".
            focus_min=_pos(qc.get("focus_min")),
            exclude_border_cells=default_exclude_border_cells(info),
            min_area=min_area,
            max_area=_pos(qc.get("max_area")),
            min_solidity=_pos(qc.get("min_solidity")),
            max_region_area=_pos(qc.get("max_region_area")),
            min_circularity=_pos(qc.get("min_circularity")),
            max_circularity=_pos(qc.get("max_circularity")),
        )

    def with_updates(self, **changes: Any) -> QCParams:
        # normalize numeric bounds through _pos so 0/"" become None
        for k in ("min_area", "max_area", "min_solidity", "max_region_area",
                  "min_circularity", "max_circularity", "focus_min"):
            if k in changes:
                changes[k] = _pos(changes[k])
        if "enabled" in changes:
            changes["enabled"] = bool(changes["enabled"])
        return replace(self, **changes)

    @property
    def focus_gate_is_active(self) -> bool:
        """True when the focus cut would actually exclude anything.

        Both halves are required, and the asymmetry is the point: a cut with
        the switch off is a number the user has recorded, and the switch on
        with no cut is a request this app cannot honour — §D.2d measured why
        nothing may supply a default here. Either alone must filter nothing.
        """
        return bool(self.exclude_soft_cells) and self.focus_min is not None

    @property
    def is_active(self) -> bool:
        """True when the gate would actually exclude anything (enabled with at
        least one bound set)."""
        return self.enabled and any(
            b is not None for b in
            (self.min_area, self.max_area, self.min_solidity, self.max_region_area,
             self.min_circularity, self.max_circularity)
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
    if REGION_AREA_COL in df.columns and params.max_region_area is not None:
        region = pd.to_numeric(df[REGION_AREA_COL], errors="coerce")
        fail |= (region > params.max_region_area).fillna(False)
    if CIRCULARITY_COL in df.columns and (
        params.min_circularity is not None or params.max_circularity is not None
    ):
        circ = pd.to_numeric(df[CIRCULARITY_COL], errors="coerce")
        if params.min_circularity is not None:
            fail |= (circ < params.min_circularity).fillna(False)
        if params.max_circularity is not None:
            fail |= (circ > params.max_circularity).fillna(False)

    passed[fail] = 0
    return passed
