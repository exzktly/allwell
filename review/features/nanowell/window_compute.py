"""The Distribution window: a box drawn on the Distribution figure, and the nanowells whose values fall inside it.

**A window is a value range over whole columns.** Its y extent is a range of the metric, inclusive at both ends. Its
x extent is snapped to the columns it overlaps (:func:`snap`) and stored as their keys: on *Along x: Time* a
column is a timepoint (``Swarm.label``), on *Along x: Wells* a group (``Swarm.group``). A dot's horizontal offset
inside its column is packing, not data, so it never decides anything.

**Membership is read from the model, not from the drawing** (:func:`members`). Every nanowell with a value is in its
swarm's ``values`` and ``sites``, whether it was drawn as a dot, packed into a "+N" overflow, or drawn inside a
density — so the window reaches the nanowells a click cannot.

**Every or any.** Over several timepoints, *every* keeps a nanowell only when it has a value inside the range at
each covered timepoint its panel has; a nanowell with no value at one of them is not a member. *Any* keeps it when
one such value is inside. Along wells there is one value per nanowell and the two agree. Both counts are always
computed, so the figure can state both.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review.features.nanowell.distribution_compute import TIME, WELLS

RULES = ("every", "any")


@dataclass(frozen=True)
class WindowRow:
    """One member, as ``renderer.render_site_rows`` draws a row: the fields of ``extremes_compute.ExtremeRow``. Its
    own class, because ``extremes_compute`` imports ``state``, which imports this module."""
    site: tuple               # (well, q, r)
    rank: int                 # 1 is the first
    value: float              # the value it is ranked by (NaN when it has none at the ranking column)
    values: dict              # timepoint -> its value there, over the panel's timepoints


@dataclass(frozen=True)
class DistributionWindow:
    column: str               # the metrics column the window was drawn on (``DistributionModel.column``)
    axis: str                 # ``"time"`` or ``"wells"`` (``DistributionModel.axis``)
    keys: tuple               # the covered columns' keys, in the figure's order
    lo: float
    hi: float                 # lo <= hi, both inclusive, in the metric's units
    panel: str = ""           # the title of the panel it was drawn in
    panel_only: bool = False  # *This panel only*: cover that panel alone, not every panel
    rule: str = "every"
    drawn_under: tuple = ()   # ``settings_key`` when the window was drawn
    n_at_drawing: int = 0     # the members it had then, for the "changed since" sentence

    def to_config(self) -> dict:
        return {"column": self.column, "axis": self.axis, "keys": list(self.keys), "lo": self.lo, "hi": self.hi,
                "panel": self.panel, "panel_only": self.panel_only, "rule": self.rule,
                "drawn_under": [list(p) for p in self.drawn_under], "n_at_drawing": self.n_at_drawing}

    @classmethod
    def from_config(cls, block) -> DistributionWindow | None:
        """The saved window, or ``None`` for a block that is not one (it is then dropped, never half restored)."""
        try:
            lo, hi = float(block["lo"]), float(block["hi"])
            keys = tuple(str(k) for k in block["keys"])
            if (block["axis"] not in (TIME, WELLS) or block.get("rule", "every") not in RULES or not keys
                    or not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi):
                return None
            under = tuple((str(name), str(value)) for name, value in block.get("drawn_under", ()))
            return cls(str(block["column"]), block["axis"], keys, lo, hi, str(block.get("panel", "")),
                       block.get("panel_only") is True, block.get("rule", "every"), under,
                       int(block.get("n_at_drawing", 0)))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class WindowResult:
    rows: list                                     # WindowRow per member, ranked
    rank_key: str                                  # the column the rows are ranked at ("" for a per-nanowell metric)
    n_every: int = 0
    n_any: int = 0
    covered: dict = field(default_factory=dict)    # panel index -> set of covered column indices (round(Swarm.x))
    missing_panel: bool = False                    # *This panel only*, and no panel has that title now

    def sites(self) -> list:
        return [row.site for row in self.rows]


def column_key(swarm, axis: str) -> str:
    """What a swarm's column is called in a window: its timepoint along time, its group along wells."""
    return str(swarm.label) if axis == TIME else str(swarm.group)


def window_key(column: str, axis: str) -> str:
    """The key a window is kept under on the state: one per metric and axis."""
    return f"{column}|{axis}"


def settings_key(st) -> tuple:
    """What the values a window reads were computed under: when it differs from ``drawn_under`` the figure says so."""
    return (("baseline", str(st.baseline)), ("moving average", str(st.moving)),
            ("baseline window", str(st.baseline_moving)), ("fixed cohort", "on" if st.cohort else "off"),
            ("subset", repr(st.subset.to_config())))


def snap(model, panel_index: int, x0: float, x1: float) -> tuple:
    """The keys of the columns of panel *panel_index* that the x span ``[x0, x1]`` overlaps, in the panel's order. A
    column ``i`` spans ``(i - 0.5, i + 0.5)``; an empty tuple when the span overlaps no column that has a swarm."""
    lo, hi = min(x0, x1), max(x0, x1)
    panel = model.panels[panel_index]
    keys = []
    for swarm in sorted(panel.swarms, key=lambda s: s.x):
        i = round(swarm.x)
        if lo < i + 0.5 and hi > i - 0.5:
            key = column_key(swarm, model.axis)
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def _key_order(model) -> list:
    out = []
    for panel in model.panels:
        for swarm in sorted(panel.swarms, key=lambda s: s.x):
            key = column_key(swarm, model.axis)
            if key not in out:
                out.append(key)
    return out


def members(model, window: DistributionWindow) -> WindowResult:
    """The nanowells inside *window* on *model*, ranked: by their value at the latest covered column, largest
    first, a nanowell without a value there last, then by well, ``q`` and ``r``."""
    keys = set(window.keys)
    order = [k for k in _key_order(model) if k in keys]
    per_nanowell = model.axis == WELLS and not model.timepoint
    rank_key = "" if per_nanowell else (order[-1] if model.axis == TIME and order else model.timepoint)
    result = WindowResult([], rank_key)
    panels = [(i, p) for i, p in enumerate(model.panels) if not window.panel_only or p.title == window.panel]
    result.missing_panel = window.panel_only and not panels
    every, anyof, series = [], [], {}
    for i, panel in panels:
        mine = [s for s in panel.swarms if column_key(s, model.axis) in keys]
        result.covered[i] = {round(s.x) for s in mine}
        required = {column_key(s, model.axis) for s in mine}
        inside: dict = {}
        for s in panel.swarms:
            key = column_key(s, model.axis)
            for site, value in zip(s.sites, s.values):
                site = tuple(site)
                if not per_nanowell:
                    series.setdefault(site, {})[str(s.label) if model.axis == TIME else model.timepoint] = float(value)
                if key in keys and window.lo <= value <= window.hi:
                    inside.setdefault(site, set()).add(key)
        for site, hit in inside.items():
            anyof.append(site)
            if hit >= required:
                every.append(site)
    result.n_every, result.n_any = len(every), len(anyof)
    chosen = every if window.rule == "every" or model.axis == WELLS else anyof
    rank_values: dict = {}
    for _i, panel in panels:
        for s in panel.swarms:
            # Along time, the value at the ranking column; along wells, the one value each nanowell has.
            if model.axis == WELLS or column_key(s, model.axis) == rank_key:
                for site, value in zip(s.sites, s.values):
                    rank_values.setdefault(tuple(site), float(value))

    def sort_key(site):
        v = rank_values.get(site, math.nan)
        return (math.isnan(v), 0.0 if math.isnan(v) else -v, site[0], site[1], site[2])

    ranked = sorted(set(chosen), key=sort_key)
    result.rows = [WindowRow(site, k, rank_values.get(site, math.nan), dict(series.get(site, {})))
                   for k, site in enumerate(ranked, 1)]
    return result


@dataclass(frozen=True)
class WindowRows:
    """What ``renderer.render_site_rows`` needs of a model, for the window's rows."""
    timepoint: str            # the column ranked at (marked); "" for a per-nanowell metric
    column: str
    timepoints: list          # every timepoint of the data, in order: the montage's columns

    def format(self, value: float) -> str:
        """*value* as a row writes it: a fold change as ``2.31×`` (``+1.20`` on the log₂ scale), any other metric as
        the bare number — a cell count of 3 is not ``3×``. ``—`` when undefined."""
        if not isinstance(value, float) or not math.isfinite(value):
            return "—"
        if self.column.startswith("log2_"):
            return f"{value:+.3g}"
        return f"{value:.3g}×" if "_fc" in self.column else f"{value:.3g}"


def changed_sentence(window: DistributionWindow, now: tuple, n_now: int) -> str:
    """"" when the settings are those the window was drawn under; else which changed and how the count moved."""
    then = dict(window.drawn_under)
    changed = [name for name, value in now if then.get(name) != value]
    if not changed:
        return ""
    return (f"{', '.join(changed).capitalize()} changed since the window was drawn: {window.n_at_drawing:,} → "
            f"{n_now:,} nanowells.")


def label_text(window: DistributionWindow, result: WindowResult, not_drawn: dict, axis: str) -> str:
    """The strip's sentence: the range, the counts, and the members with no dot on the figure, by why."""
    rng = f"{window.lo:.4g}–{window.hi:.4g}"
    if result.missing_panel:
        return f"Window {rng}: the panel it was drawn in ({window.panel}) is not on the figure."
    if axis == TIME and len(window.keys) > 1:
        counts = (f"{result.n_every:,} inside at every covered timepoint · {result.n_any:,} at any"
                  if window.rule == "every" else
                  f"{result.n_any:,} inside at any covered timepoint · {result.n_every:,} at every")
    else:
        counts = f"{len(result.rows):,} nanowells inside"
    hidden = sum(not_drawn.values())
    why = ", ".join(f"{n:,} {k}" for k, n in not_drawn.items() if n)
    return f"Window {rng} · {counts}" + (f" · {hidden:,} with no dot on the figure ({why})" if hidden else "")


def not_drawn(result: WindowResult, model, hits, *, max_panels: int, density_above: int) -> dict:
    """Members with no drawn dot inside the window's columns, by why: ``"in panels not drawn"`` (past
    *max_panels*), ``"in densities"`` (a panel drawn as densities), ``"in overflow"`` (a "+N" a slot could not
    hold). *hits* is the figure's ``renderer.HitMap``, whose ``panels`` say which axes drew which panel."""
    drawn = set()
    index_of = {id(ax): i for ax, i in getattr(hits, "panels", [])}
    for ax, xs, _ys, sites in getattr(hits, "entries", []):
        cols = result.covered.get(index_of.get(id(ax), -1), set())
        drawn.update(tuple(s) for x, s in zip(xs, sites) if round(float(x)) in cols)
    panel_of = {}
    for i, panel in enumerate(model.panels):
        for s in panel.swarms:
            for site in s.sites:
                panel_of.setdefault(tuple(site), i)
    out = {"in panels not drawn": 0, "in densities": 0, "in overflow": 0}
    for row in result.rows:
        if row.site in drawn:
            continue
        i = panel_of.get(row.site, 0)
        if i >= max_panels:
            out["in panels not drawn"] += 1
        elif _members_per_column(model, model.panels[i]) > density_above:
            out["in densities"] += 1
        else:
            out["in overflow"] += 1
    return out


def _members_per_column(model, panel) -> int:
    """How many wells share a column of *panel*: the renderer draws densities above ``DENSITY_ABOVE``."""
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    by_col: dict = {}
    for s in panel.swarms:
        by_col.setdefault(round(s.x), set()).add(s.well)
    return max((len(v) for v in by_col.values()), default=1)
