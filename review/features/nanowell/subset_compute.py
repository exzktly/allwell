"""The *Subset* the Nanowells page draws: any number of conditions on a nanowell, all of which (or any of which) it
must meet to be used.

Asked for: "Expand the starting cells tool to a subset builder. Allow specification of any combination and number
of fields, metric, position, ring/position distance, etc." The *Starting cells* filter this grew from is one kind of
condition here, and its presets are still one click away (:meth:`Subset.starting`).

**What a condition can test.** Each condition names a *field* (:func:`field_choices`):

* ``n0`` — *Starting cells*, the nanowell's count at the page's Baseline (the number the occupancy colours are
  drawn from, so the condition and the key always agree);
* any metric column of the metrics table (``n``, ``c``, ``brightness_<ch>``, ``signal_<ch>``, the fold changes,
  ``growth_rate``) — a per-timepoint metric is tested *at* a timepoint: the baseline (the count's), the well's
  earliest or latest, a named one, or at *any* or *every* timepoint of the well;
* the nanowell's place in its array: ``ring`` (lattice steps from the central nanowell), ``distance_um`` (µm from
  the central nanowell on the ideal lattice of the well's measured pitch), ``nw_q`` and ``nw_r`` (its address);
* ``owner`` — the imaging field (tile) the run cut the nanowell from, at a timepoint as a metric is;
* ``fused`` — whether the nanowell lay on a tile seam at a timepoint (no one tile held it with margin, so it was
  measured from a crop blended from the tiles covering it, ``pipeline.nanowell_seams``), so the nanowells on the
  seams can be kept alone or left out;
* ``identity`` — the identity check's verdict on the nanowell's timepoint (``pipeline.nanowell_identity``:
  confirmed, reference, partial, inconclusive, contradicted or not checked), at a timepoint as a metric is, tested
  with *is one of* / *is not one of*. It is the timepoint's verdict, shared by every nanowell of the well there; the
  *Identity contradicted* property below is the per-nanowell answer, which a ``partial`` verdict gives only to the
  nanowells it lists;
* the nanowell's per-timepoint properties (:data:`ROW_FLAGS`), each a yes/no read from its own column so that no
  one of them stands in for another: whether the run *found* the nanowell, whether the well's timepoint was not
  addressed at all, whether the nanowell was occluded or clipped, whether the identity check contradicted its
  address, whether a circular rim was fitted to the well's array at that timepoint (``series_compute``'s
  ``rim_fitted``), whether the rim-focus rule flagged its rim, whether its field is left out (Cell Gating, QC
  triage, the chosen FOVs), and whether the run measured it without a count. *Found by the run at every timepoint: yes* keeps
  only the nanowells tracked through the whole dataset; *at any timepoint: no* on a defect keeps those it never
  touched;
* ``window_sites`` — whether the nanowell is *inside* or *outside* the set of nanowells a Distribution window held
  when it was turned into a subset (**Use as Subset**). The set is frozen on the condition, with the name it was
  given (``Window 1``); a nanowell not on the list is decided against, never "no value";
* ``gate`` — whether the nanowell lies *inside* or *outside* a gate drawn on the Scatter Plot's nanowell scatter (or
  on its "Nanowell ▸" axes), at a timepoint as a metric is. A nanowell at a timepoint is one point on that scatter,
  so it is inside when its two metrics there fall in the box. The condition names the gate; its box and axes are
  read from the gates as they are now each time the subset is evaluated (:func:`bind_gates`), so editing the gate
  on the scatter moves the subset with it, and a gate deleted or renamed gives every nanowell no value for the
  condition and the condition says so. Only a gate whose two axes are both nanowell metrics can be named
  (:func:`nanowell_gates`): a box over a cell metric does not place a nanowell.

A numeric field is compared with ``≥``, ``≤``, ``=``, *between* two values or *not between* them (both bounds
inclusive); the imaging field *is one of* or *is not one of* a set of tiles, and the identity verdict of a set of
verdicts; *on a tile seam* is *yes* or *no*.

**Every nanowell is in, out, or undecided.** Each condition answers yes, no, or "no value" for a nanowell (a count
that was occluded at the baseline, a tile with no recorded pitch, a timepoint the nanowell was not seen at). A
nanowell is kept only when the subset answers yes — every condition under *all*, at least one under *any*. One that
is not kept is *outside* when a condition decided against it, and has *no value* when it fell short only for want
of one; the figure counts the two apart, as the Starting cells filter did. *At any timepoint* is yes when some
value passes and no when every value it has fails; *at every timepoint* is no when some value fails and yes only
when the nanowell has a passing value at every timepoint of its well.

**The subset comes first.** The nanowells outside it are taken out of what a view computes on before any exclusion
is worked out, and every figure carries one sentence that counts them (:func:`note`), so a nanowell is never counted
twice. A subset that is only a Starting cells condition says so in the words that filter always used.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from pipeline.nanowell_identity import (
    CONFIRMED, CONTRADICTED, INCONCLUSIVE, NOT_CHECKED, PARTIAL, REFERENCE,
)
from review.features.nanowell import starting_cells_compute as sc
from review.features.nanowell.collect import (
    CLIPPED, EMPTY, FIELD_EXCLUDED, MEASURABLE, MEASURED, NOT_LOCATED, OCCLUDED, UNADDRESSED,
)
from review.features.nanowell.distribution_compute import METRICS, PER_NANOWELL
from review.features.nanowell.series_compute import baseline_timepoint
from review.features.nanowell.views_compute import lattice_kind, lattice_ring, lattice_xy, well_pitch

SITE_KEY = ["well", "nw_q", "nw_r"]
FEW_BELOW = sc.FEW_BELOW

#: The field a Starting cells condition tests: the count at the baseline.
START = "n0"
#: The fields that place a nanowell in its array: field -> label.
POSITION_FIELDS = {"ring": "Ring (steps from the centre)", "distance_um": "Distance from the centre (µm)",
                   "nw_q": "Lattice q", "nw_r": "Lattice r"}
#: The imaging field (tile) a nanowell was cut from at a timepoint.
TILE = "owner"
#: Whether the nanowell lay on a tile seam at a timepoint: the site table's ``fused`` flag.
SEAM = "fused"
#: The identity check's verdict on the nanowell's timepoint: ``series_compute``'s ``identity`` column.
IDENTITY = "identity"
#: Every verdict an identity condition can name, in the editor's order: verdict -> what the editor and figure write.
IDENTITY_VERDICTS = {CONFIRMED: "confirmed", REFERENCE: "reference", PARTIAL: "partial",
                     INCONCLUSIVE: "inconclusive", CONTRADICTED: "contradicted", NOT_CHECKED: "not checked"}
#: Whether the nanowell's point on the nanowell scatter lies in a gate drawn there, at a timepoint.
GATE = "gate"
#: Whether the nanowell is one of the sites a Distribution window held when *Use as Subset* froze it. The sites are
#: kept on the condition (:attr:`Condition.sites`), not looked up: the Subset filters the figure the window is drawn
#: on, so a window bound to its own figure would depend on its own output.
WINDOW = "window_sites"
#: The most nanowells one window condition holds: they are saved with the page, so the block is bounded.
MAX_WINDOW_SITES = 20_000
#: The prefix a scatter gate's axis carries when it is a nanowell metric (``review.domain.observation``).
_NW_PREFIX = "nw::"
#: The nanowell's per-timepoint properties, each tested *yes* or *no* at a timepoint: field -> label. Each is read
#: from its own column (:func:`_row_flag`), so they are independent: a nanowell in a field Cell Gating leaves out is
#: still *found* when the run found it, and one whose identity was contradicted is still *found* too.
ROW_FLAGS = {
    "found": "Found by the run",
    "tp_unaddressed": "Timepoint not addressed",
    "occluded": "Occluded or clipped",
    "identity_out": "Identity contradicted",
    "rim_fitted": "Array rim fitted",
    "rim_out_of_focus": "Rim out of focus",
    "field_left_out": "In a field left out",
    "no_count": "Measured without a count",
}
#: Which group of the editor each property is listed under.
_ROW_FLAG_GROUPS = {"found": "Tracking", "tp_unaddressed": "Tracking", "identity_out": "Tracking",
                    "rim_fitted": "Tracking"}
#: The states in which the run recorded a row for the nanowell, i.e. found it (``collect``'s pipeline states).
_RECORDED = (MEASURED, EMPTY, OCCLUDED, CLIPPED)
#: The fields whose values are whole numbers: their bounds are typed without decimals.
INTEGER_FIELDS = frozenset({START, "n", "ring", "nw_q", "nw_r"})
#: Fold changes are measured from the baseline, so a condition on one depends on the Baseline control.
_FOLD_CHANGES = ("count_fc", "log2_count_fc", "confluence_fc", "brightness_fc", "signal_fc")

#: How a numeric field is compared, and how the imaging field is: op -> what the editor and the figure write.
NUMERIC_OPS = {"ge": "≥", "le": "≤", "eq": "=", "between": "between", "outside": "not between"}
CATEGORY_OPS = {"in": "is one of", "not_in": "is not one of"}
FLAG_OPS = {"yes": "yes", "no": "no"}
GATE_OPS = {"in": "inside", "not_in": "outside"}
#: Which timepoint a per-timepoint field is read at; a named timepoint is ``tp:<label>``.
ATS = {"baseline": "at baseline", "earliest": "at the earliest timepoint", "latest": "at the latest timepoint",
       "any": "at any timepoint", "every": "at every timepoint"}
TP_PREFIX = "tp:"
MATCHES = ("all", "any")
#: The most conditions a subset holds: far beyond any question asked of a plate, and a bound on a saved block.
MAX_CONDITIONS = 24


# ── fields ───────────────────────────────────────────────────────────────────
def metric_of(column: str) -> tuple[str, str | None] | None:
    """``(metric, channel)`` for a metrics column a condition may test, or ``None``: ``brightness_fc_GFP`` is
    ``("brightness_fc", "GFP")``. The occupancy class is not one: Starting cells tests the count it is drawn from."""
    if column in METRICS and not METRICS[column][1] and column != "occupancy_class":
        return column, None
    for key in sorted((k for k, (_l, needs) in METRICS.items() if needs), key=len, reverse=True):
        if column.startswith(key + "_") and len(column) > len(key) + 1:
            return key, column[len(key) + 1:]
    return None


def is_known_field(name) -> bool:
    return isinstance(name, str) and (name in (START, TILE, SEAM, IDENTITY, GATE, WINDOW) or name in POSITION_FIELDS
                                      or name in ROW_FLAGS or metric_of(name) is not None)


def per_timepoint(name: str) -> bool:
    """Whether *name* has a value per timepoint, and so is tested *at* one."""
    if name in (TILE, SEAM, IDENTITY, GATE) or name in ROW_FLAGS:
        return True
    metric = metric_of(name)
    return metric is not None and metric[0] not in PER_NANOWELL


def is_category(name: str) -> bool:
    """Whether *name* is tested with *is one of* / *is not one of* a set of values: the imaging field, the
    identity verdict."""
    return name in (TILE, IDENTITY)


def value_label(name: str, value: str) -> str:
    """How a category condition writes *value*: an identity verdict in words, a tile as it is named."""
    return IDENTITY_VERDICTS.get(value, value) if name == IDENTITY else value


def is_flag(name: str) -> bool:
    """Whether *name* is a yes/no field, tested with *yes* or *no* and no bound."""
    return name == SEAM or name in ROW_FLAGS


def is_gate(name: str) -> bool:
    """Whether *name* tests a scatter gate: *inside* or *outside* one named gate."""
    return name == GATE


def is_window(name: str) -> bool:
    """Whether *name* tests a frozen Distribution window: *inside* or *outside* its list of nanowells."""
    return name == WINDOW


def field_label(name: str) -> str:
    if name == START:
        return "Starting cells"
    if name == TILE:
        return "Imaging field"
    if name == SEAM:
        return "On a tile seam"
    if name == IDENTITY:
        return "Identity verdict"
    if name == GATE:
        return "Scatter gate"
    if name == WINDOW:
        return "Distribution window"
    if name in ROW_FLAGS:
        return ROW_FLAGS[name]
    if name in POSITION_FIELDS:
        return POSITION_FIELDS[name]
    metric = metric_of(name)
    if metric is None:
        return name
    key, channel = metric
    return METRICS[key][0] + (f" · {channel}" if channel else "")


def field_choices(channels=(), *, gates: bool = False, sizes=None) -> list[tuple[str, str, str]]:
    """Every field a condition can test for a dataset with *channels*: ``(field, label, group)`` in the editor's
    order — the cells at the start, the metrics (each channel metric once per channel), the position, the tile and
    seam, the identity verdict and the per-timepoint properties, and the scatter gates when *gates* (some gate is
    drawn on two nanowell metrics). *sizes* (Cell Gating's ``SizeClassRule``) names the size classes in the menu."""
    out = [(START, field_label(START), "Cells")]
    for key, (_label, needs) in METRICS.items():
        if key == "occupancy_class":
            continue
        for column in ([f"{key}_{ch}" for ch in channels] if needs else [key]):
            label = field_label(column)
            out.append((column, sizes.name(label) if sizes is not None else label, "Metric"))
    out += [(name, label, "Position") for name, label in POSITION_FIELDS.items()]
    out.append((TILE, field_label(TILE), "Imaging"))
    out.append((SEAM, field_label(SEAM), "Imaging"))
    out.append((IDENTITY, field_label(IDENTITY), "Tracking"))
    for group in ("Tracking", "Quality"):
        out += [(name, label, group) for name, label in ROW_FLAGS.items()
                if _ROW_FLAG_GROUPS.get(name, "Quality") == group]
    if gates:
        out.append((GATE, field_label(GATE), "Gates"))
    return out


def ops_for(name: str) -> dict:
    if is_gate(name) or is_window(name):
        return GATE_OPS
    return CATEGORY_OPS if is_category(name) else FLAG_OPS if is_flag(name) else NUMERIC_OPS


def _uses_baseline(name: str, at: str) -> bool:
    if name == START:
        return True
    metric = metric_of(name)
    return (metric is not None and metric[0] in _FOLD_CHANGES) or (per_timepoint(name) and at == "baseline")


# ── conditions ───────────────────────────────────────────────────────────────
def _window_sites(sites) -> tuple:
    """*sites* as a window condition keeps them — ``(well, q, r)`` each, sorted, without repeats — or ``ValueError``:
    a site that is not a well and two whole numbers, none at all, or more than :data:`MAX_WINDOW_SITES`."""
    out = set()
    for site in sites or ():
        if not (isinstance(site, (tuple, list)) and len(site) == 3 and isinstance(site[0], str) and site[0]
                and all(isinstance(v, int) and not isinstance(v, bool) for v in site[1:])):
            raise ValueError(f"a window's nanowell is (well, q, r), not {site!r}")
        out.add((site[0], int(site[1]), int(site[2])))
    if not out:
        raise ValueError("a window condition holds at least one nanowell")
    if len(out) > MAX_WINDOW_SITES:
        raise ValueError(f"a window condition holds at most {MAX_WINDOW_SITES:,} nanowells")
    return tuple(sorted(out))


def _number(value, whole: bool):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"a bound must be a number, not {value!r}")
    if whole:
        if float(value) != int(value):
            raise ValueError(f"a bound on a whole-number field must be whole, not {value!r}")
        return int(value)
    return float(value)


def _fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


@dataclass(frozen=True)
class Condition:
    """One test of a nanowell: *field* compared by *op* with *a* (and *b* for a range), or for the imaging field
    with *values*; *at* is the timepoint a per-timepoint field is read at (``""`` for any other field)."""
    field: str
    op: str = "ge"
    a: float | None = None
    b: float | None = None
    at: str = ""
    values: tuple = ()
    #: A gate condition's box as the gate stands now, ``(x key, y key, x0, y0, x1, y1)``; ``()`` when no nanowell
    #: gate has the name; ``None`` until :func:`bind_gates` has looked. Never saved and never compared: the
    #: condition is the gate's *name*, and its box is read from the scatter each time the subset is evaluated.
    box: tuple | None = field(default=None, compare=False, repr=False)
    #: A window condition's nanowells, ``(well, q, r)`` each, sorted and without repeats; ``()`` for every other field.
    sites: tuple = ()

    def validated(self) -> Condition:
        """This condition in its one canonical form, or ``ValueError`` saying why it is not a condition."""
        if not is_known_field(self.field):
            raise ValueError(f"no field {self.field!r}")
        if self.op not in ops_for(self.field):
            raise ValueError(f"{field_label(self.field)} cannot be compared by {self.op!r}")
        at = ""
        if per_timepoint(self.field):
            at = self.at or "baseline"
            if at not in ATS and not (at.startswith(TP_PREFIX) and len(at) > len(TP_PREFIX)):
                raise ValueError(f"no timepoint {at!r}")
        if is_window(self.field):
            names = [v for v in self.values if isinstance(v, str) and v]
            if len(names) != 1:
                raise ValueError("a window condition has one name")
            return Condition(self.field, self.op, None, None, "", (names[0],), sites=_window_sites(self.sites))
        if is_gate(self.field):
            names = [v for v in self.values if isinstance(v, str) and v]
            if len(names) != 1:
                raise ValueError("choose one scatter gate")
            return Condition(self.field, self.op, None, None, at, (names[0],), self.box)
        if is_category(self.field):
            values = tuple(dict.fromkeys(str(v) for v in self.values if isinstance(v, str) and v))
            if not values:
                raise ValueError(f"choose at least one {field_label(self.field).lower()}")
            if self.field == IDENTITY:
                unknown = [v for v in values if v not in IDENTITY_VERDICTS]
                if unknown:
                    raise ValueError(f"no identity verdict {unknown[0]!r}")
                # The editor's order, so the condition reads confirmed before contradicted rather than by spelling.
                return Condition(self.field, self.op, None, None, at,
                                 tuple(v for v in IDENTITY_VERDICTS if v in values))
            return Condition(self.field, self.op, None, None, at, tuple(sorted(values)))
        if is_flag(self.field):
            return Condition(self.field, self.op, None, None, at, ())
        whole = self.field in INTEGER_FIELDS
        a = _number(self.a, whole)
        b = _number(self.b, whole) if self.op in ("between", "outside") else None
        if b is not None and a > b:
            raise ValueError(f"{field_label(self.field)}: the lower bound ({_fmt(a)}) is above the upper ({_fmt(b)})")
        if self.field == START and a < 0:
            raise ValueError("a starting-cells bound must be a whole number of cells ≥ 0")
        if self.op == "between" and a == b:                # one value, written as a range: the same condition
            return Condition(self.field, "eq", a, None, at, ())
        return Condition(self.field, self.op, a, b, at, ())

    def bounds(self) -> tuple:
        """``(lo, hi)`` the values must lie within (``None``: open on that side), for every op but *outside*."""
        return {"ge": (self.a, None), "le": (None, self.a), "eq": (self.a, self.a),
                "between": (self.a, self.b)}.get(self.op, (None, None))

    def text(self) -> str:
        """The condition as a figure writes it: ``Starting cells 1–2``, ``Ring (steps from the centre) ≤3``,
        ``Count fold change at the latest timepoint ≥2``, ``Imaging field is one of T01, T02``."""
        where = ""
        if self.at:
            where = (" " + ATS[self.at]) if self.at in ATS else f" at {self.at[len(TP_PREFIX):]}"
        label = field_label(self.field) + where
        if is_window(self.field):
            return f"{label}: {GATE_OPS[self.op]} “{self.values[0] if self.values else ''}” ({len(self.sites):,} nanowells)"
        if is_gate(self.field):
            missing = " (no nanowell gate by that name)" if self.box == () else ""
            return f"{label}: {GATE_OPS[self.op]} “{self.values[0] if self.values else ''}”{missing}"
        if is_category(self.field):
            return f"{label} {CATEGORY_OPS[self.op]} {', '.join(value_label(self.field, v) for v in self.values)}"
        if is_flag(self.field):
            return f"{label}: {FLAG_OPS[self.op]}"
        if self.field == START and self.op != "outside":
            return f"{label} {sc.bounds_text(*self.bounds())}"
        a, b = _fmt(self.a), _fmt(self.b) if self.b is not None else ""
        return f"{label} " + {"ge": f"≥{a}", "le": f"≤{a}", "eq": f"= {a}", "between": f"{a}–{b}",
                              "outside": f"outside {a}–{b}"}[self.op]

    def to_config(self) -> dict:
        out = {"field": self.field, "op": self.op}
        if is_window(self.field):
            out["values"] = list(self.values)
            out["sites"] = [list(site) for site in self.sites]
        elif is_category(self.field) or is_gate(self.field):
            out["values"] = list(self.values)
        elif not is_flag(self.field):
            out["a"] = self.a
            if self.b is not None:
                out["b"] = self.b
        if self.at:
            out["at"] = self.at
        return out

    @classmethod
    def from_config(cls, block) -> Condition:
        if not isinstance(block, dict):
            raise ValueError("a condition is a mapping")
        values, sites = block.get("values"), block.get("sites")
        return cls(block.get("field"), block.get("op"), block.get("a"), block.get("b"), block.get("at") or "",
                   tuple(values) if isinstance(values, list) else (),
                   sites=tuple(tuple(s) if isinstance(s, list) else s for s in sites)
                   if isinstance(sites, list) else ()).validated()


@dataclass(frozen=True)
class Subset:
    """The conditions, and whether a nanowell must meet *all* of them or *any*. No conditions: every nanowell."""
    conditions: tuple = ()
    match: str = "all"

    @classmethod
    def starting(cls, lo, hi) -> Subset:
        """The Starting cells filter of *lo* to *hi* cells at the baseline (``ValueError`` for bounds that are not
        one), as a subset: one condition, or none when neither bound is set."""
        lo, hi = sc.normalise(lo, hi)
        if lo is None and hi is None:
            return cls()
        if hi is None:
            return cls((Condition(START, "ge", lo),))
        if lo is None:
            return cls((Condition(START, "le", hi),))
        return cls((Condition(START, "eq", lo),) if lo == hi else (Condition(START, "between", lo, hi),))

    def validated(self) -> Subset:
        if self.match not in MATCHES:
            raise ValueError(f"match must be one of {MATCHES}, not {self.match!r}")
        if len(self.conditions) > MAX_CONDITIONS:
            raise ValueError(f"a subset holds at most {MAX_CONDITIONS} conditions")
        return Subset(tuple(c.validated() for c in self.conditions), self.match)

    def is_active(self) -> bool:
        return bool(self.conditions)

    def is_starting_only(self) -> bool:
        """One Starting cells condition and nothing else: the figure then says what the Starting cells filter said."""
        return len(self.conditions) == 1 and self.conditions[0].field == START and self.conditions[0].op != "outside"

    def start_bounds(self) -> tuple:
        """The ``(min, max)`` count at the baseline every kept nanowell is known to lie within — for the occupancy
        key, which fades the classes the subset removes whole. ``(None, None)`` when the subset does not bound it:
        no Starting cells condition, or one of several under *any*."""
        if self.match == "any" and len(self.conditions) > 1:
            return None, None
        lo = hi = None
        for c in self.conditions:
            if c.field != START or c.op == "outside":
                continue
            a, b = c.bounds()
            lo = a if lo is None or (a is not None and a > lo) else lo
            hi = b if hi is None or (b is not None and b < hi) else hi
        return (None if lo == 0 else lo), hi

    def uses_baseline(self) -> bool:
        return any(_uses_baseline(c.field, c.at) for c in self.conditions)

    def needs_geometry(self) -> bool:
        return any(c.field in ("ring", "distance_um") for c in self.conditions)

    def label(self) -> str:
        if not self.conditions:
            return "All nanowells"
        if self.is_starting_only():
            return self.conditions[0].text()
        return "Subset: " + (" and " if self.match == "all" else " or ").join(c.text() for c in self.conditions)

    def to_config(self) -> dict:
        return {"match": self.match, "conditions": [c.to_config() for c in self.conditions]}

    @classmethod
    def from_config(cls, block) -> Subset:
        """The saved subset, or ``ValueError``: a subset restored with a condition missing would be another subset
        than the one saved, so one bad condition refuses the whole block."""
        if not isinstance(block, dict) or not isinstance(block.get("conditions"), list):
            raise ValueError("a subset is a mapping with a list of conditions")
        return cls(tuple(Condition.from_config(c) for c in block["conditions"]),
                   block.get("match", "all")).validated()


def restore(block) -> Subset:
    """:meth:`Subset.from_config`, or no subset when the block is not one."""
    try:
        return Subset.from_config(block)
    except (ValueError, TypeError):
        return Subset()


# ── saved subsets ────────────────────────────────────────────────────────────
#: The longest name a saved subset takes: room for "Singles, ring ≤ 3, GFP+ at 48 h" with plenty to spare.
MAX_NAME = 60
#: The most subsets one dataset keeps: far beyond the questions asked of one plate, and a bound on the saved block.
MAX_SAVED = 50


def clean_name(text) -> str:
    """*text* as a saved subset's name — trimmed, inner runs of spaces made one — or ``ValueError`` saying why it
    cannot be one (empty, or longer than :data:`MAX_NAME`)."""
    name = " ".join(str(text or "").split())
    if not name:
        raise ValueError("a saved subset needs a name")
    if len(name) > MAX_NAME:
        raise ValueError(f"a name holds at most {MAX_NAME} characters")
    return name


def saved_name_of(saved: dict, subset: Subset) -> str:
    """The name *subset* is saved under in *saved* (``name -> Subset``), or ``""`` when it is saved under none —
    so the page names a saved subset only while it is exactly the one saved: one condition changed and it is
    the page's own again."""
    if not subset.is_active():
        return ""
    return next((name for name, kept in saved.items() if kept == subset), "")


def figure_label(subset: Subset, saved: dict | None = None) -> str:
    """The line a figure drawn under *subset* carries on itself, so a figure read away from the page — a saved plot,
    an exported file, a slide — says which nanowells it is of: ``Subset: “name”`` while *subset* is saved under a
    name in *saved* (``name -> Subset``), else its conditions (:meth:`Subset.label`), and ``""`` with no subset.

    The name is looked up by the conditions (:func:`saved_name_of`), so it is the name only while the subset drawn is
    exactly the one saved: a figure whose conditions were edited after choosing a saved subset does not claim it."""
    if not subset.is_active():
        return ""
    name = saved_name_of(saved or {}, subset)
    if name:
        return f"Subset: \u201c{name}\u201d"
    text = subset.label()
    return text if text.startswith("Subset: ") else f"Subset: {text}"


def saved_to_config(saved: dict) -> dict:
    """``name -> subset block``, in the order the subsets were saved."""
    return {name: subset.to_config() for name, subset in saved.items()}


def saved_from_config(block) -> dict:
    """The saved subsets of a dataset's page block, ``name -> Subset``. An entry whose name or subset is not one is
    dropped rather than restored as a different subset under its name; past :data:`MAX_SAVED`, the rest are."""
    out: dict = {}
    for name, item in (block.items() if isinstance(block, dict) else ()):
        try:
            name = clean_name(name) if isinstance(name, str) else None
            subset = Subset.from_config(item)
        except (ValueError, TypeError):
            continue
        if (name and subset.is_active() and name not in out and subset not in out.values()
                and len(out) < MAX_SAVED):
            out[name] = subset
    return out


def with_saved(saved: dict, name: str, subset: Subset) -> dict:
    """*saved* with *subset* kept under *name* (replacing a subset of that name in its place), or ``ValueError``:
    no conditions to keep, a name that is not one, the same conditions already kept under another name, or
    :data:`MAX_SAVED` already kept. One subset has one name: with two, the Subset box could show only one of them
    (:func:`saved_name_of`), and Rename or Delete would act on the name the user did not choose."""
    name = clean_name(name)
    if not subset.is_active():
        raise ValueError("the subset has no conditions to save")
    other = next((k for k, kept in saved.items() if kept == subset.validated() and k != name), None)
    if other is not None:
        raise ValueError(f"these conditions are already saved as \u201c{other}\u201d: rename that one instead")
    if name not in saved and len(saved) >= MAX_SAVED:
        raise ValueError(f"a dataset keeps at most {MAX_SAVED} subsets: delete one first")
    out = dict(saved)
    out[name] = subset.validated()
    return out


def renamed(saved: dict, old: str, new: str) -> dict:
    """*saved* with the subset called *old* called *new*, in the same place, or ``ValueError``: no subset called
    *old*, a new name that is not one, or one another subset already has."""
    new = clean_name(new)
    if old not in saved:
        raise ValueError(f"no saved subset is called {old!r}")
    if new != old and new in saved:
        raise ValueError(f"a subset called {new!r} is already saved")
    return {(new if name == old else name): subset for name, subset in saved.items()}


# ── scatter gates ────────────────────────────────────────────────────────────
def _nanowell_key(column) -> str | None:
    return column[len(_NW_PREFIX):] if isinstance(column, str) and column.startswith(_NW_PREFIX) else None


def nanowell_gates(gates) -> list[tuple[str, tuple]]:
    """``(name, box)`` of every named gate in *gates* (the scatter's gate dicts) drawn on two nanowell metrics, in
    the scatter's order; *box* is ``(x key, y key, x0, y0, x1, y1)`` with the corners in order."""
    out = []
    for gate in gates or ():
        name = str(gate.get("name") or "")
        x_key, y_key = _nanowell_key(gate.get("x_col")), _nanowell_key(gate.get("y_col"))
        if not name or x_key is None or y_key is None:
            continue
        try:
            x0, x1 = sorted((float(gate["x0"]), float(gate["x1"])))
            y0, y1 = sorted((float(gate["y0"]), float(gate["y1"])))
        except (KeyError, TypeError, ValueError):
            continue
        out.append((name, (x_key, y_key, x0, y0, x1, y1)))
    return out


def bind_gates(subset: Subset, gates) -> Subset:
    """*subset* with each gate condition's box read from *gates* as they are now (``()`` for a name no nanowell gate
    has). Equal to *subset* (the box is not compared), so the page's subset is unchanged by binding it."""
    if not any(is_gate(c.field) for c in subset.conditions):
        return subset
    boxes = dict(nanowell_gates(gates))
    return Subset(tuple(replace(c, box=boxes.get(c.values[0] if c.values else "", ())) if is_gate(c.field) else c
                        for c in subset.conditions), subset.match)


#: The prefix of a metrics column that holds a nanowell metric as Cell Gating judges a scatter gate on it: counted
#: without the scatter-gate layer (``controller.gate_tally(scatter_gates=False)``, as
#: ``review/data/observations.nanowell_gate_values`` counts it). The page's own columns are counted after that layer,
#: so a gate that leaves out every cell of a nanowell reads that nanowell's count as 0 there — inside a box over low
#: counts, although the gate rejected it. :func:`_metric_values` reads these columns first (:func:`with_gate_values`).
GATE_VALUE_PREFIX = "gate_value::"


def with_gate_values(metrics: pd.DataFrame, ungated: pd.DataFrame | None, columns) -> pd.DataFrame:
    """*metrics* with each of *columns* of *ungated* (the same site metrics, counted without the scatter-gate layer,
    row for row) beside it under :data:`GATE_VALUE_PREFIX`; *metrics* unchanged when *ungated* is ``None``."""
    if ungated is None or not len(metrics):
        return metrics
    present = [c for c in columns if c in ungated.columns]
    extra = pd.DataFrame({GATE_VALUE_PREFIX + c: ungated[c].to_numpy() for c in present}, index=metrics.index)
    return pd.concat([metrics, extra], axis=1)


def _metric_values(metrics: pd.DataFrame, key: str) -> np.ndarray:
    """A nanowell metric as a scatter gate is judged on it: the :data:`GATE_VALUE_PREFIX` column when the metrics
    carry one, else the metrics column, or *occupied* (holds ≥ 1 cell) from the count, as ``observation_compute``
    derives it."""
    def column(name):
        own = GATE_VALUE_PREFIX + name
        return own if own in metrics else name if name in metrics else None

    if key == "occupied" and column("n") is not None:
        n = pd.to_numeric(metrics[column("n")], errors="coerce").to_numpy(dtype=float)
        return np.where(np.isnan(n), np.nan, (n >= 1).astype(float))
    name = column(key)
    if name is None:
        return np.full(len(metrics), np.nan)
    return pd.to_numeric(metrics[name], errors="coerce").to_numpy(dtype=float)


def _gate_values(metrics: pd.DataFrame, c: Condition) -> np.ndarray:
    """Per row: 1 where the row's point passes *c* (inside the box for *inside*), 0 where it fails, NaN where the
    point has no value on an axis or the gate is not there."""
    if not c.box:
        return np.full(len(metrics), np.nan)
    x_key, y_key, x0, y0, x1, y1 = c.box
    x, y = _metric_values(metrics, x_key), _metric_values(metrics, y_key)
    with np.errstate(invalid="ignore"):
        inside = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    passed = inside if c.op == "in" else ~inside
    return np.where(np.isnan(x) | np.isnan(y), np.nan, passed.astype(float))


def _window_values(sites: pd.DataFrame, c: Condition) -> np.ndarray:
    """Per nanowell: 1 where it passes the window condition *c* (on the list for *inside*, off it for *outside*), 0
    where it fails. Never undecided: a nanowell is on the list or it is not, and one with no address is not."""
    listed = frozenset(c.sites)
    q = pd.to_numeric(sites["nw_q"], errors="coerce")
    r = pd.to_numeric(sites["nw_r"], errors="coerce")
    on = np.array([bool(ok) and (str(w), int(a), int(b)) in listed
                   for w, a, b, ok in zip(sites["well"], q.fillna(0), r.fillna(0), q.notna() & r.notna())])
    return (on if c.op == "in" else ~on).astype(float)


# ── evaluation ───────────────────────────────────────────────────────────────
def geometry_of(data, wells) -> dict:
    """``well -> (lattice kind, pitch µm or None)`` for *wells*, read from their grid records."""
    out = {}
    for well in wells:
        tps = data.timepoints(well) if data is not None else []
        kind = lattice_kind(data, well, tps[0]) if tps else "hex"
        out[well] = (kind, well_pitch(data, well) if data is not None else None)
    return out


def _compare(values: np.ndarray, c: Condition) -> np.ndarray:
    """1 where *values* pass *c*, 0 where they fail, NaN where there is no value."""
    if is_category(c.field):
        known = np.array([isinstance(v, str) and v != "" for v in values], dtype=bool)
        hit = np.isin(values.astype(object), np.array(c.values, dtype=object))
        passed = hit if c.op == "in" else ~hit
        return np.where(known, passed.astype(float), np.nan)
    v = np.asarray(values, dtype=float)
    if is_flag(c.field):
        return np.where(np.isnan(v), np.nan, (v == (1.0 if c.op == "yes" else 0.0)).astype(float))
    if c.op == "outside":
        passed = (v < c.a) | (v > c.b)
    else:
        lo, hi = c.bounds()
        passed = np.ones(len(v), dtype=bool)
        if lo is not None:
            passed &= v >= lo
        if hi is not None:
            passed &= v <= hi
    return np.where(np.isnan(v), np.nan, passed.astype(float))


def _position_values(sites: pd.DataFrame, name: str, geometry: dict) -> np.ndarray:
    q = pd.to_numeric(sites["nw_q"], errors="coerce").to_numpy(dtype=float)
    r = pd.to_numeric(sites["nw_r"], errors="coerce").to_numpy(dtype=float)
    if name in ("nw_q", "nw_r"):
        return q if name == "nw_q" else r
    out = np.full(len(sites), np.nan)
    wells = sites["well"].astype(str).to_numpy()
    for well in pd.unique(wells):
        at = wells == well
        kind, pitch = geometry.get(well, ("hex", None))
        if name == "ring":
            out[at] = np.asarray(lattice_ring(q[at], r[at], kind), dtype=float)
        elif pitch:
            x, y = lattice_xy(q[at], r[at], pitch, kind)
            out[at] = np.hypot(x, y)
    return out


def _timepoint_rows(metrics: pd.DataFrame, at: str) -> np.ndarray:
    """The rows a per-timepoint field is read at, for one-timepoint choices."""
    if at == "baseline":
        return (metrics["timepoint"] == metrics["baseline_timepoint"]).to_numpy() if "baseline_timepoint" in metrics \
            else np.zeros(len(metrics), dtype=bool)
    if at in ("earliest", "latest"):
        order = pd.to_numeric(metrics["tp_order"], errors="coerce")
        edge = order.groupby(metrics["well"]).transform("min" if at == "earliest" else "max")
        return (order == edge).to_numpy()
    return (metrics["timepoint"].astype(str) == at[len(TP_PREFIX):]).to_numpy()


def _row_flag(metrics: pd.DataFrame, name: str) -> np.ndarray:
    """1 / 0 per row for the per-timepoint property *name* (:data:`ROW_FLAGS`); NaN on every row when the table
    lacks the column it is read from, so a table without it decides nothing rather than everything."""
    def column(key):
        return metrics[key] if key in metrics else None

    if name in ("found", "tp_unaddressed", "occluded", "no_count"):
        # The run's own state, before Cell Gating or the rim-focus rule overwrite it (``site_metrics``).
        state = column("run_state")
        if state is None:
            return np.full(len(metrics), np.nan)
        if name == "found":
            hit = state.isin(_RECORDED)
        elif name == "tp_unaddressed":
            hit = state.isin((UNADDRESSED, NOT_LOCATED))
        elif name == "occluded":
            hit = state.isin((OCCLUDED, CLIPPED))
        else:
            count = column("n_cells")
            no_count = (pd.to_numeric(count, errors="coerce").isna() if count is not None
                        else pd.Series(True, index=metrics.index))
            hit = state.isin(MEASURABLE) & no_count
        return hit.to_numpy(dtype=float)
    if name == "field_left_out":
        state = column("state")
        return np.full(len(metrics), np.nan) if state is None else (state == FIELD_EXCLUDED).to_numpy(dtype=float)
    if name == "rim_fitted":
        fitted = column("rim_fitted")
        return np.full(len(metrics), np.nan) if fitted is None else fitted.map(_flag_value).to_numpy(dtype=float)
    ok = column({"identity_out": "identity_ok", "rim_out_of_focus": "rim_focus_ok"}[name])
    if ok is None:
        return np.full(len(metrics), np.nan)
    return 1.0 - ok.map(_flag_value).to_numpy(dtype=float)


def _row_values(metrics: pd.DataFrame, name: str) -> np.ndarray:
    if name in ROW_FLAGS:
        return _row_flag(metrics, name)
    if name not in metrics:
        return np.full(len(metrics), np.nan, dtype=object if is_category(name) else float)
    if is_category(name):
        return metrics[name].astype(object).where(metrics[name].notna(), None).to_numpy()
    if is_flag(name):
        return metrics[name].map(_flag_value).to_numpy(dtype=float)
    return pd.to_numeric(metrics[name], errors="coerce").to_numpy(dtype=float)


def _per_timepoint(metrics: pd.DataFrame, codes: np.ndarray, n_sites: int, c: Condition) -> np.ndarray:
    result = _gate_values(metrics, c) if is_gate(c.field) else _compare(_row_values(metrics, c.field), c)
    frame = pd.DataFrame({"site": codes, "v": result})
    if c.at in ("any", "every"):
        grouped = frame.groupby("site")["v"]
        passed = grouped.max().reindex(range(n_sites))            # NaN only when the nanowell has no value at all
        failed = grouped.min().reindex(range(n_sites)) == 0
        if c.at == "any":
            return passed.to_numpy(dtype=float)
        complete = frame["v"].notna().groupby(frame["site"]).all().reindex(range(n_sites), fill_value=False)
        out = np.where(failed, 0.0, np.where(complete, 1.0, np.nan))
        return out.astype(float)
    rows = frame[_timepoint_rows(metrics, c.at)]
    return rows.groupby("site")["v"].max().reindex(range(n_sites)).to_numpy(dtype=float)


def _flag_value(value) -> float:
    """1 / 0 for a yes/no cell as the site table reads it (a bool, or its text), NaN when there is none."""
    if isinstance(value, (bool, np.bool_)):
        return float(value)
    text = str(value).strip().lower() if isinstance(value, str) else None
    return 1.0 if text == "true" else 0.0 if text == "false" else float("nan")


def _site_answers(metrics: pd.DataFrame, subset: Subset, data=None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """``(sites, codes, answers)``: one row per nanowell (first appearance order), each row's site number, and an
    ``n_sites × n_conditions`` array of 1 / 0 / NaN."""
    codes = metrics.groupby(SITE_KEY, sort=False, dropna=False).ngroup().to_numpy()
    sites = metrics.drop_duplicates(SITE_KEY)
    n = len(sites)
    geometry = geometry_of(data, sites["well"].astype(str).unique()) if subset.needs_geometry() else {}
    answers = np.full((n, len(subset.conditions)), np.nan)
    for j, c in enumerate(subset.conditions):
        if per_timepoint(c.field):
            answers[:, j] = _per_timepoint(metrics, codes, n, c)
        elif is_window(c.field):
            answers[:, j] = _window_values(sites, c)
        elif c.field in POSITION_FIELDS:
            answers[:, j] = _compare(_position_values(sites, c.field, geometry), c)
        else:
            answers[:, j] = _compare(_row_values(sites, c.field), c)
    return sites, codes, answers


def _combine(answers: np.ndarray, match: str) -> np.ndarray:
    """Per nanowell: 1 kept, 0 decided against, NaN undecided for want of a value."""
    known = ~np.isnan(answers)
    if match == "all":
        no = (known & (answers == 0)).any(axis=1)
        yes = (known & (answers == 1)).all(axis=1)
    else:
        yes = (known & (answers == 1)).any(axis=1)
        no = (known & (answers == 0)).all(axis=1)
    return np.where(yes, 1.0, np.where(no, 0.0, np.nan))


def inside(metrics: pd.DataFrame, subset: Subset, data=None) -> pd.Series:
    """For each row, whether its nanowell is in *subset* (every row when it has no conditions)."""
    if not subset.is_active() or not len(metrics):
        return pd.Series(True, index=metrics.index)
    _sites, codes, answers = _site_answers(metrics, subset, data)
    return pd.Series(_combine(answers, subset.match)[codes] == 1, index=metrics.index)


@dataclass
class SubsetResult:
    """What the subset did to one figure's nanowells."""
    subset: Subset
    baseline: str
    total: int = 0                  # nanowells of the drawn wells
    kept: int = 0
    outside: int = 0                # decided against by a condition
    unknown: int = 0                # undecided for want of a value
    late: int = 0                   # kept, counted after the well's earliest timepoint (any baseline but earliest)
    per_well: dict = field(default_factory=dict)    # well -> nanowells kept
    outside_sites: frozenset = frozenset()          # (well, q, r) of every nanowell not kept

    def label(self) -> str:
        return self.subset.label()


def apply(metrics: pd.DataFrame, subset: Subset, baseline: str, wells=None,
          data=None) -> tuple[pd.DataFrame, SubsetResult | None]:
    """*metrics* with only the rows of nanowells in *subset*, and what was done; ``(metrics, None)`` with no
    condition. *wells* are the drawn wells (every well in *metrics* when ``None``): each is counted, and named when
    it keeps fewer than :data:`FEW_BELOW`, even when it keeps none. *data* gives the lattice for a position
    condition."""
    if not subset.is_active():
        return metrics, None
    frame = metrics if wells is None else metrics[metrics["well"].isin(list(wells))]
    done = SubsetResult(subset, baseline)
    names = [str(w) for w in (wells if wells is not None else frame["well"].unique())]
    if not len(frame):
        done.per_well = {w: 0 for w in names}
        return frame, done
    sites, codes, answers = _site_answers(frame, subset, data)
    verdict = _combine(answers, subset.match)
    keep_site = verdict == 1
    done.total, done.kept = len(sites), int(keep_site.sum())
    done.unknown = int(np.isnan(verdict).sum())
    done.outside = done.total - done.kept - done.unknown
    counts = sites[keep_site].groupby("well").size()
    done.per_well = {w: int(counts.get(w, 0)) for w in names}
    out = sites[~keep_site]
    done.outside_sites = frozenset(zip(out["well"], out["nw_q"].astype(int), out["nw_r"].astype(int)))
    if baseline != "earliest" and subset.uses_baseline():
        first = frame[frame["tp_order"] == frame.groupby("well")["tp_order"].transform("min")]
        earliest = dict(zip(first["well"], first["timepoint"]))
        kept = sites[keep_site]
        done.late = int((kept["baseline_timepoint"].notna()
                         & (kept["baseline_timepoint"] != kept["well"].map(earliest))).sum())
    return frame[keep_site[codes]], done


def _where(baseline: str) -> str:
    tp = baseline_timepoint(baseline)
    if tp is not None:
        return f"timepoint {tp}"
    return "each nanowell's first measurable timepoint" if baseline == "first_measurable" else "the earliest timepoint"


def note(done: SubsetResult) -> str:
    """The figure's sentence about the subset: what it is, the baseline when it matters, and every nanowell left
    out by why."""
    if done.subset.is_starting_only():
        said = (f"{done.label()} (count at {_where(done.baseline)}): {done.kept:,} of {done.total:,} nanowells kept "
                f"— {done.outside:,} outside the range, {done.unknown:,} with no count at baseline.")
        group = "filter"
    else:
        where = f" (baseline: {_where(done.baseline)})" if done.subset.uses_baseline() else ""
        said = (f"{done.label()}{where}: {done.kept:,} of {done.total:,} nanowells kept — {done.outside:,} outside "
                f"the subset, {done.unknown:,} with no value for a condition.")
        group = "subset"
    if done.late:
        said += (f" {done.late:,} kept were counted after the earliest timepoint, when their cells may already "
                 "have divided.")
    few = [f"{w} ({k})" for w, k in done.per_well.items() if k < FEW_BELOW]
    if few:
        said += f" Few nanowells in the {group}: " + ", ".join(few) + "."
    return said


def empty_text(done: SubsetResult) -> str:
    """What a figure says when the subset keeps no nanowell of the drawn wells."""
    subset = done.subset
    if subset.is_starting_only():
        bounds = sc.bounds_text(*subset.conditions[0].bounds())
        return (f"No nanowell of the drawn wells started with {bounds} {'cell' if bounds == '1' else 'cells'} "
                f"(count at {_where(done.baseline)}). Widen Starting cells or choose Any.")
    joined = (" and " if subset.match == "all" else " or ").join(c.text() for c in subset.conditions)
    return f"No nanowell of the drawn wells is in the subset ({joined}). Widen the subset or choose All nanowells."


def site_badge(rows: pd.DataFrame, subset: Subset, data=None) -> str | None:
    """Why one nanowell (*rows*: its rows at every timepoint) is outside *subset*, or ``None`` when it is in."""
    if not subset.is_active() or not len(rows):
        return None
    _sites, _codes, answers = _site_answers(rows, subset, data)
    verdict = _combine(answers, subset.match)[0]
    if verdict == 1:
        return None
    if subset.is_starting_only():
        n0 = pd.to_numeric(rows["n0"], errors="coerce").iloc[0] if "n0" in rows else float("nan")
        count = "no count at baseline" if not math.isfinite(n0) else f"{int(n0)} cells at baseline"
        return f"outside {subset.label()} ({count})"
    failed = [c.text() for c, a in zip(subset.conditions, answers[0]) if a == 0]
    missing = [c.text() for c, a in zip(subset.conditions, answers[0]) if np.isnan(a)]
    if verdict == 0:
        return "outside the subset (fails " + "; ".join(failed) + ")"
    return "outside the subset (no value for " + "; ".join(missing) + ")"
