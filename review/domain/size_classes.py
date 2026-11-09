"""Two labelled cell-size classes, judged on each cell's area: the rule Home → Cell Gating holds and the Nanowells page
counts by.

A user who has imaged no marker that tells two cell types apart — T cells from dendritic cells, say — can still
tell them apart by size when their size ranges barely overlap. The rule is two **classes**, each a label the user
types and an area range in pixels, **inclusive at both ends** and read from the same area column as the debris gate
(``qc_params.AREA_COLS``), so "120 px" means the same thing on both rows of the panel. A bound left blank is open:
``[350, —]`` is every cell of at least 350 px that the debris gate keeps.

**The ranges may leave a gap and may not overlap.** Two populations whose sizes overlap a little are best counted
with the doubtful sizes left out of both, so a cell in neither range is *unclassified* — counted and reported, never
dropped silently and never forced into a class. A cell in both would be counted twice, so overlapping ranges are
refused with the reason (:meth:`SizeClassRule.problem`).

**Nothing is classified until the rule is complete** (:meth:`SizeClassRule.active`): both labels typed and distinct,
each class bounded on at least one side, no range inverted, the two not overlapping. An incomplete rule is kept as
typed — the panel shows what is wrong with it — and every metric it would feed is undefined with the reason
"size classes not set", never computed from half a rule.

The classes are counted over the cells Cell Gating keeps: the debris gate and every other inclusion layer run first,
so a class bound outside the debris gate's own bounds simply finds no cells there (:func:`clipped_by_gate` says so).

Here rather than in the Nanowells feature because the rule is a QC parameter (``QCParams.size_classes``), persisted
and carried with the rest of the Cell Gating settings, and ``review/domain`` may not import a feature.

Qt-free.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: How a metric is named before the user labels the classes; :meth:`SizeClassRule.name` puts the labels in their place.
PLACEHOLDERS = ("Size class 1", "Size class 2")
_PLACEHOLDER_RE = re.compile("|".join(re.escape(p) for p in PLACEHOLDERS))
#: How many classes the rule has. The model is a tuple so it could hold more; the panel offers two.
N_CLASSES = 2
#: The decimals an area is compared at (:func:`classify`).
AREA_DECIMALS = 6
#: The class index of a cell in no class (:func:`classify`).
UNCLASSIFIED = -1


def _bound(value) -> float | None:
    """*value* as an area bound, or ``None`` (open) when blank, unparseable, not finite, or not above zero — the debris
    gate's own rule (``qc_params._pos``), so a typed 0 is "no bound" on both rows of the panel."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


@dataclass(frozen=True)
class SizeClass:
    """One class: its label and its inclusive area range in pixels (``None`` = open on that side)."""

    label: str = ""
    min_area: float | None = None
    max_area: float | None = None

    @property
    def low(self) -> float:
        return 0.0 if self.min_area is None else float(self.min_area)

    @property
    def high(self) -> float:
        return math.inf if self.max_area is None else float(self.max_area)

    def range_text(self) -> str:
        """``80–250 px``, ``≥ 350 px`` or ``≤ 60 px``."""
        if self.min_area is not None and self.max_area is not None:
            return f"{self.min_area:g}–{self.max_area:g} px"
        if self.min_area is not None:
            return f"≥ {self.min_area:g} px"
        if self.max_area is not None:
            return f"≤ {self.max_area:g} px"
        return "any size"


@dataclass(frozen=True)
class SizeClassRule:
    """The two classes as typed. ``classes`` always has :data:`N_CLASSES` entries; blank ones are unset."""

    classes: tuple = (SizeClass(), SizeClass())

    @classmethod
    def of(cls, *rows) -> SizeClassRule:
        """A rule from ``(label, min_area, max_area)`` rows, each bound normalised as the panel's boxes are."""
        built = [SizeClass(str(label or "").strip(), _bound(lo), _bound(hi)) for label, lo, hi in rows]
        built += [SizeClass()] * (N_CLASSES - len(built))
        return cls(tuple(built[:N_CLASSES]))

    def is_blank(self) -> bool:
        """Nothing typed at all: the rule is simply not in use."""
        return all(not c.label and c.min_area is None and c.max_area is None for c in self.classes)

    def problem(self) -> str:
        """Why the rule classifies nothing, in a sentence for the panel; ``""`` when it is complete."""
        if self.is_blank():
            return "No size classes are set."
        for k, c in enumerate(self.classes, start=1):
            if not c.label:
                return f"Class {k} needs a label."
            if c.min_area is None and c.max_area is None:
                return f"{c.label} needs a minimum or a maximum area."
            if c.low > c.high:
                return f"{c.label}'s minimum ({c.min_area:g} px) is above its maximum ({c.max_area:g} px)."
        a, b = self.classes
        if a.label.casefold() == b.label.casefold():
            return "The two classes need different labels."
        if a.low <= b.high and b.low <= a.high:
            return (f"{a.label} ({a.range_text()}) and {b.label} ({b.range_text()}) overlap, so a cell could be "
                    "counted in both; leave a gap between them, or let them meet without sharing a value.")
        return ""

    def active(self) -> bool:
        return not self.problem()

    @property
    def labels(self) -> tuple:
        return tuple(c.label for c in self.classes)

    def key(self) -> tuple:
        """What counts were computed for: equal keys give equal classes. An inactive rule's key is ``()``."""
        if not self.active():
            return ()
        return tuple((c.label, c.min_area, c.max_area) for c in self.classes)

    def text(self) -> str:
        """The rule in words for a figure's note: ``T cells 80–250 px, DCs 350–1200 px``."""
        if not self.active():
            return "not set"
        return ", ".join(f"{c.label} {c.range_text()}" for c in self.classes)

    def name(self, text: str) -> str:
        """*text* with the placeholder class names (:data:`PLACEHOLDERS`) replaced by the user's labels; unchanged
        while the rule is incomplete, so a metric still reads "Size class 1 count" until there is a class to name."""
        if not self.active():
            return text
        # Both at once: replaced one after the other, a first label containing the second placeholder ("Size class
        # 2") would be rewritten by the second replacement.
        labels = dict(zip(PLACEHOLDERS, self.labels))
        return _PLACEHOLDER_RE.sub(lambda m: labels[m.group(0)], text)

    # ── persistence (the ``size_classes`` entry of the QC parameters) ──
    def to_list(self) -> list:
        """The rule as saved: one ``{"label", "min_area", "max_area"}`` per class, an empty list when blank."""
        if self.is_blank():
            return []
        return [{"label": c.label, "min_area": c.min_area, "max_area": c.max_area} for c in self.classes]

    @classmethod
    def from_list(cls, rows) -> SizeClassRule:
        """The rule as saved; anything not a list of mappings reads as a blank rule."""
        if not isinstance(rows, (list, tuple)):
            return cls()
        parsed = [(r.get("label"), r.get("min_area"), r.get("max_area")) for r in rows if isinstance(r, dict)]
        return cls.of(*parsed)


def classify(areas, rule: SizeClassRule) -> np.ndarray:
    """Each area's class index (0, 1, …) or :data:`UNCLASSIFIED`; every cell is unclassified under an inactive rule,
    and so is a cell with no area. Inclusive at both ends."""
    # Rounded far below a pixel: a nanowell run writes ``area_px`` as ``area_um2 / pixel_um**2``, so a cell of
    # exactly 250 px can read 250.0000000001 and would fall outside an inclusive 250 by float noise alone.
    values = np.round(pd.to_numeric(pd.Series(areas), errors="coerce").to_numpy(dtype=float), AREA_DECIMALS)
    out = np.full(len(values), UNCLASSIFIED, dtype=int)
    if not rule.active():
        return out
    finite = np.isfinite(values)
    for k, c in enumerate(rule.classes):
        out[finite & (values >= c.low) & (values <= c.high) & (out == UNCLASSIFIED)] = k
    return out


def diameter_um(area_px: float, pixel_um: float) -> float:
    """The diameter in µm of a circle of *area_px* pixels at *pixel_um* µm per pixel."""
    return 2.0 * math.sqrt(float(area_px) / math.pi) * float(pixel_um)


def diameter_text(c: SizeClass, pixel_um: float | None) -> str:
    """A class's range as equivalent-circle diameters, ``≈ 10.1–12.4 µm``; ``""`` without a pixel size."""
    if pixel_um is None or not math.isfinite(pixel_um) or pixel_um <= 0:
        return ""
    if c.min_area is not None and c.max_area is not None:
        return f"≈ {diameter_um(c.min_area, pixel_um):.1f}–{diameter_um(c.max_area, pixel_um):.1f} µm"
    if c.min_area is not None:
        return f"≈ ≥ {diameter_um(c.min_area, pixel_um):.1f} µm"
    if c.max_area is not None:
        return f"≈ ≤ {diameter_um(c.max_area, pixel_um):.1f} µm"
    return ""


def clipped_by_gate(rule: SizeClassRule, gate_min: float | None, gate_max: float | None) -> str:
    """A sentence when the debris gate's area bounds cut into a class's range — the classes are counted over the
    cells the gate keeps, so the part of a range outside it can hold no cell — or ``""``."""
    if not rule.active():
        return ""
    cut = []
    for c in rule.classes:
        if (gate_min is not None and c.low < gate_min) or (gate_max is not None and c.high > gate_max):
            cut.append(c.label)
    if not cut:
        return ""
    bounds = f"{gate_min:g}" if gate_min is not None else "—"
    bounds += f"–{gate_max:g}" if gate_max is not None else "–—"
    return (f"The debris gate keeps areas {bounds} px, which cuts into {' and '.join(cut)}: the classes count only "
            "the cells the gate keeps.")
