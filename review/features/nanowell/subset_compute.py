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
* ``owner`` — the imaging field (tile) the run cut the nanowell from, at a timepoint as a metric is.

A numeric field is compared with ``≥``, ``≤``, ``=``, *between* two values or *not between* them (both bounds
inclusive); the imaging field *is one of* or *is not one of* a set of tiles.

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
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.features.nanowell import starting_cells_compute as sc
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
#: The fields whose values are whole numbers: their bounds are typed without decimals.
INTEGER_FIELDS = frozenset({START, "n", "ring", "nw_q", "nw_r"})
#: Fold changes are measured from the baseline, so a condition on one depends on the Baseline control.
_FOLD_CHANGES = ("count_fc", "log2_count_fc", "confluence_fc", "brightness_fc", "signal_fc")

#: How a numeric field is compared, and how the imaging field is: op -> what the editor and the figure write.
NUMERIC_OPS = {"ge": "≥", "le": "≤", "eq": "=", "between": "between", "outside": "not between"}
CATEGORY_OPS = {"in": "is one of", "not_in": "is not one of"}
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
    return isinstance(name, str) and (name in (START, TILE) or name in POSITION_FIELDS or metric_of(name) is not None)


def per_timepoint(name: str) -> bool:
    """Whether *name* has a value per timepoint, and so is tested *at* one."""
    if name == TILE:
        return True
    metric = metric_of(name)
    return metric is not None and metric[0] not in PER_NANOWELL


def is_category(name: str) -> bool:
    return name == TILE


def field_label(name: str) -> str:
    if name == START:
        return "Starting cells"
    if name == TILE:
        return "Imaging field"
    if name in POSITION_FIELDS:
        return POSITION_FIELDS[name]
    metric = metric_of(name)
    if metric is None:
        return name
    key, channel = metric
    return METRICS[key][0] + (f" · {channel}" if channel else "")


def field_choices(channels=()) -> list[tuple[str, str, str]]:
    """Every field a condition can test for a dataset with *channels*: ``(field, label, group)`` in the editor's
    order — the cells at the start, the metrics (each channel metric once per channel), the position, the tile."""
    out = [(START, field_label(START), "Cells")]
    for key, (_label, needs) in METRICS.items():
        if key == "occupancy_class":
            continue
        for column in ([f"{key}_{ch}" for ch in channels] if needs else [key]):
            out.append((column, field_label(column), "Metric"))
    out += [(name, label, "Position") for name, label in POSITION_FIELDS.items()]
    out.append((TILE, field_label(TILE), "Imaging"))
    return out


def ops_for(name: str) -> dict:
    return CATEGORY_OPS if is_category(name) else NUMERIC_OPS


def _uses_baseline(name: str, at: str) -> bool:
    if name == START:
        return True
    metric = metric_of(name)
    return (metric is not None and metric[0] in _FOLD_CHANGES) or (per_timepoint(name) and at == "baseline")


# ── conditions ───────────────────────────────────────────────────────────────
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
        if is_category(self.field):
            values = tuple(dict.fromkeys(str(v) for v in self.values if isinstance(v, str) and v))
            if not values:
                raise ValueError(f"choose at least one {field_label(self.field).lower()}")
            return Condition(self.field, self.op, None, None, at, tuple(sorted(values)))
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
        if is_category(self.field):
            return f"{label} {CATEGORY_OPS[self.op]} {', '.join(self.values)}"
        if self.field == START and self.op != "outside":
            return f"{label} {sc.bounds_text(*self.bounds())}"
        a, b = _fmt(self.a), _fmt(self.b) if self.b is not None else ""
        return f"{label} " + {"ge": f"≥{a}", "le": f"≤{a}", "eq": f"= {a}", "between": f"{a}–{b}",
                              "outside": f"outside {a}–{b}"}[self.op]

    def to_config(self) -> dict:
        out = {"field": self.field, "op": self.op}
        if is_category(self.field):
            out["values"] = list(self.values)
        else:
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
        values = block.get("values")
        return cls(block.get("field"), block.get("op"), block.get("a"), block.get("b"), block.get("at") or "",
                   tuple(values) if isinstance(values, list) else ()).validated()


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


def _row_values(metrics: pd.DataFrame, name: str) -> np.ndarray:
    if name not in metrics:
        return np.full(len(metrics), np.nan, dtype=object if is_category(name) else float)
    if is_category(name):
        return metrics[name].astype(object).where(metrics[name].notna(), None).to_numpy()
    return pd.to_numeric(metrics[name], errors="coerce").to_numpy(dtype=float)


def _per_timepoint(metrics: pd.DataFrame, codes: np.ndarray, n_sites: int, c: Condition) -> np.ndarray:
    result = _compare(_row_values(metrics, c.field), c)
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
