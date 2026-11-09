"""The Distribution window: a box drawn on the Distribution figure, and the nanowells whose values fall inside it.

**A window is a value range over the swarms it was drawn around.** Its y extent is a range of the metric, inclusive
at both ends. Its x extent is snapped to *slots* (:func:`snap_slots`): one swarm, which is one well at one column --
on *Along x: Time* a column is a timepoint (``Swarm.label``), on *Along x: Wells* a group (``Swarm.group``), and a
replicate group draws its wells side by side inside each column. A slot is covered when the drag overlaps the middle
half of it, so a box drawn around one well's dots takes neither the well beside it in the same column nor a
neighbouring column it merely grazes; snapping to whole columns took both, which is what a user reported. The window
keeps the covered columns' keys, which -- unless *This panel only* -- it covers in every panel, as each panel is a group
drawn on the same columns. A column the box covers only *part* of, in the panel it was drawn in, is narrowed to the
wells it was drawn around: ``slots`` holds those ``(column key, well)`` pairs (:func:`partial_slots`). A dot's
horizontal offset inside its slot is packing, not data, so it never decides anything.

**Membership is read from the model, not from the drawing** (:func:`members`). Every nanowell with a value is in its
swarm's ``values`` and ``sites``, whether it was drawn as a dot, packed into a "+N" overflow, or drawn inside a
density — so the window reaches the nanowells a click cannot.

**Every or any.** Over several timepoints, *every* keeps a nanowell only when it has a value inside the range at
each covered timepoint its panel has; a nanowell with no value at one of them is not a member. *Any* keeps it when
one such value is inside. Along wells there is one value per nanowell and the two agree. Both counts are always
computed, so the figure can state both.

**A window can be several boxes.** A drag made with **Shift** held adds its box to the window already drawn rather
than replacing it, so one window can hold separate, non-adjacent regions of the figure: two value ranges in one
column, or the high tail of one well and the low tail of another. The newest box is the window's own fields
(``keys``, ``lo``, ``hi``, ``panel``, ``slots``), which the strip's Low and High fields edit; the earlier ones are
in ``more``, newest first. Membership is the **union** of the boxes' members, each box judged on its own columns and
range under the window's one *every | any* rule and *This panel only* choice. A Shift-drag made while a window is
already in the Subset (after *Use as Subset*) carries that window's nanowells over in ``carried``: they are members
whatever the boxes hold, so the Subset can be built up one region at a time (``_window_controller.on_release``).

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

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
    slots: tuple = ()         # ((column key, well), ...) for each column covered only in part: just those wells
    more: tuple = ()          # the earlier boxes of a Shift-drawn window, newest first (each a DistributionWindow)
    carried: tuple = ()       # ((well, q, r), ...) kept from the Subset's window when this one was Shift-drawn on it

    def boxes(self) -> tuple:
        """Every box of the window, newest first, each as a window of its own under this window's rule and *This
        panel only* choice — the controls edit those once, for the whole window."""
        return (replace(self, more=(), carried=()),) + tuple(
            replace(box, rule=self.rule, panel_only=self.panel_only, more=(), carried=()) for box in self.more)

    def all_keys(self) -> tuple:
        """The columns any box covers, the newest box's first, without repeats."""
        return tuple(dict.fromkeys(key for box in self.boxes() for key in box.keys))

    def added(self, box: DistributionWindow) -> DistributionWindow:
        """This window with *box* drawn on it with Shift: *box* becomes the newest box, this window's boxes the
        earlier ones, and its rule, panel choice and carried nanowells are kept."""
        return replace(box, rule=self.rule, panel_only=self.panel_only, more=self.boxes(), carried=self.carried)

    def covers(self, swarm, axis: str) -> bool:
        """Whether *swarm* is one this window was drawn around: its column is covered, and when that column was
        covered only in part, its well is one of the ones the box was drawn around."""
        key = column_key(swarm, axis)
        if key not in self.keys:
            return False
        partial = {k for k, _well in self.slots}
        return key not in partial or (key, str(swarm.well)) in self.slots

    def to_config(self) -> dict:
        out = {"column": self.column, "axis": self.axis, "keys": list(self.keys), "lo": self.lo, "hi": self.hi,
               "panel": self.panel, "panel_only": self.panel_only, "rule": self.rule,
               "drawn_under": [list(p) for p in self.drawn_under], "n_at_drawing": self.n_at_drawing,
               "slots": [list(p) for p in self.slots]}
        if self.more:
            out["more"] = [box.to_config() for box in self.more]
        if self.carried:
            out["carried"] = [list(site) for site in self.carried]
        return out

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
            slots = tuple((str(key), str(well)) for key, well in block.get("slots", ()))
            if any(key not in keys for key, _well in slots):
                return None
            more = tuple(cls.from_config(box) for box in block.get("more", ()))
            if any(box is None or box.column != str(block["column"]) or box.axis != block["axis"] for box in more):
                return None                              # one box that does not read drops the window, not the box
            carried = tuple(_carried_site(site) for site in block.get("carried", ()))
            return cls(str(block["column"]), block["axis"], keys, lo, hi, str(block.get("panel", "")),
                       block.get("panel_only") is True, block.get("rule", "every"), under,
                       int(block.get("n_at_drawing", 0)), slots, more, carried)
        except (KeyError, TypeError, ValueError):
            return None


def _carried_site(site) -> tuple:
    """A saved carried nanowell as ``(well, q, r)``; ``ValueError`` for anything else."""
    well, q, r = site
    if not isinstance(well, str) or isinstance(q, bool) or isinstance(r, bool) or q != int(q) or r != int(r):
        raise ValueError(f"not a nanowell: {site!r}")
    return (well, int(q), int(r))


@dataclass
class WindowResult:
    rows: list                                     # WindowRow per member, ranked
    rank_key: str                                  # the column the rows are ranked at ("" for a per-nanowell metric)
    n_every: int = 0
    n_any: int = 0
    covered: dict = field(default_factory=dict)    # panel index -> set of covered column indices (round(Swarm.x))
    spans: dict = field(default_factory=dict)      # panel index -> [(lo, hi)] x extent of each covered slot
    bands: dict = field(default_factory=dict)      # panel index -> [(x0, x1, lo, hi)]: each box's band, per run of slots
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


def slot_half_widths(panel) -> dict:
    """Column index (``round(Swarm.x)``) -> half the width of one slot in it: half the spacing between the swarms the
    column holds, or ``0.5`` (the whole column) when it holds one."""
    by_col: dict = {}
    for swarm in panel.swarms:
        by_col.setdefault(round(swarm.x), set()).add(float(swarm.x))
    out = {}
    for col, xs in by_col.items():
        xs = sorted(xs)
        out[col] = min((b - a for a, b in zip(xs, xs[1:])), default=1.0) / 2
    return out


def slot_extent(swarm, half_widths: dict) -> tuple:
    half = half_widths.get(round(swarm.x), 0.5)
    return float(swarm.x) - half, float(swarm.x) + half


def snap_slots(model, panel_index: int, x0: float, x1: float) -> tuple:
    """``(column key, well)`` of each swarm of panel *panel_index* whose slot's middle half the x span ``[x0, x1]``
    overlaps, in the panel's order; empty when it overlaps none."""
    lo, hi = min(x0, x1), max(x0, x1)
    panel = model.panels[panel_index]
    half = slot_half_widths(panel)
    out: list = []
    for swarm in sorted(panel.swarms, key=lambda s: s.x):
        inner = half.get(round(swarm.x), 0.5) / 2
        if lo < swarm.x + inner and hi > swarm.x - inner:
            slot = (column_key(swarm, model.axis), str(swarm.well))
            if slot not in out:
                out.append(slot)
    return tuple(out)


def partial_slots(model, panel_index: int, slots: tuple) -> tuple:
    """The members of *slots* (:func:`snap_slots`) whose column they do not cover whole in panel *panel_index*: what
    a window keeps in ``slots``. A column covered whole is left to its key, which reaches every panel."""
    wells_of: dict = {}
    for swarm in model.panels[panel_index].swarms:
        wells_of.setdefault(column_key(swarm, model.axis), set()).add(str(swarm.well))
    covered: dict = {}
    for key, well in slots:
        covered.setdefault(key, set()).add(well)
    return tuple((key, well) for key, well in slots if covered[key] != wells_of.get(key, set()))


def snap(model, panel_index: int, x0: float, x1: float) -> tuple:
    """The keys of the columns :func:`snap_slots` covers, in the panel's order."""
    return tuple(dict.fromkeys(key for key, _well in snap_slots(model, panel_index, x0, x1)))


def _key_order(model) -> list:
    out = []
    for panel in model.panels:
        for swarm in sorted(panel.swarms, key=lambda s: s.x):
            key = column_key(swarm, model.axis)
            if key not in out:
                out.append(key)
    return out


def _box_members(model, box: DistributionWindow, result: WindowResult, series: dict) -> tuple:
    """``(every, any, panels)`` for one box: the nanowells inside it under each rule, and the ``(index, panel)``
    pairs it covers. Records the box's covered columns, slot extents and bands on *result*, and every nanowell's
    value per timepoint in *series*."""
    per_nanowell = model.axis == WELLS and not model.timepoint
    panels = [(i, p) for i, p in enumerate(model.panels) if not box.panel_only or p.title == box.panel]
    every, anyof = set(), set()
    for i, panel in panels:
        mine = [s for s in panel.swarms if box.covers(s, model.axis)]
        result.covered.setdefault(i, set()).update(round(s.x) for s in mine)
        half = slot_half_widths(panel)
        extents = [slot_extent(s, half) for s in mine]
        result.spans.setdefault(i, []).extend(extents)
        result.bands.setdefault(i, []).extend((x0, x1, box.lo, box.hi) for x0, x1 in merged_spans(extents))
        # A nanowell has values only in its own well's swarms, so "inside at every covered column" asks about the
        # columns covered *for its well*: a box over well A at one timepoint and well B at the next is not a column
        # A's nanowells could ever be inside at.
        required: dict = {}
        for s in mine:
            required.setdefault(str(s.well), set()).add(column_key(s, model.axis))
        inside: dict = {}
        for s in panel.swarms:
            key = column_key(s, model.axis)
            covered = box.covers(s, model.axis)
            for site, value in zip(s.sites, s.values):
                site = tuple(site)
                if not per_nanowell:
                    series.setdefault(site, {})[str(s.label) if model.axis == TIME else model.timepoint] = float(value)
                if covered and box.lo <= value <= box.hi:
                    inside.setdefault(site, set()).add(key)
        for site, hit in inside.items():
            anyof.add(site)
            if hit >= required.get(str(site[0]), set()):
                every.add(site)
    if model.axis == WELLS:
        # Along wells a nanowell has one value, in its own group's column, so "inside at every covered column" can
        # only hold when the window covers one column: across two neighbouring groups it held for no nanowell at all.
        # Inside the range in its own column is the whole test, and every and any are the same set.
        every = anyof
    return every, anyof, panels


def members(model, window: DistributionWindow) -> WindowResult:
    """The nanowells inside *window* on *model* — the union over its boxes, with the nanowells it carries — ranked:
    by their value at the latest covered column, largest first, a nanowell without a value there last, then by
    well, ``q`` and ``r``."""
    keys = set(window.all_keys())
    order = [k for k in _key_order(model) if k in keys]
    per_nanowell = model.axis == WELLS and not model.timepoint
    rank_key = "" if per_nanowell else (order[-1] if model.axis == TIME and order else model.timepoint)
    result = WindowResult([], rank_key)
    series: dict = {}
    every, anyof = set(window.carried), set(window.carried)
    panels: dict = {}
    for box in window.boxes():
        box_every, box_any, box_panels = _box_members(model, box, result, series)
        every |= box_every
        anyof |= box_any
        panels.update(box_panels)
    result.missing_panel = window.panel_only and not panels
    result.n_every, result.n_any = len(every), len(anyof)
    chosen = every if window.rule == "every" else anyof
    rank_values: dict = {}
    # Along time, the value at the ranking column; along wells, the one value each nanowell has. A carried
    # nanowell is ranked by its value in whichever panel holds it, covered or not.
    for _i, panel in sorted(panels.items()) + [(i, p) for i, p in enumerate(model.panels) if i not in panels]:
        for s in panel.swarms:
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
    """The strip's sentence: the range (the newest box's, and how many more boxes and carried nanowells the window
    holds), the counts, and the members with no dot on the figure, by why."""
    rng = f"{window.lo:.4g}–{window.hi:.4g}"
    if result.missing_panel:
        return f"Window {rng}: the panel it was drawn in ({window.panel}) is not on the figure."
    extra = len(window.more)
    if extra:
        rng += f" + {extra:,} more box{'es' if extra > 1 else ''}"
    if window.carried:
        rng += f" + {len(window.carried):,} kept from the earlier window"
    if axis == TIME and len(window.all_keys()) > 1:
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
        spans = result.spans.get(index_of.get(id(ax), -1), [])
        drawn.update(tuple(s) for x, s in zip(xs, sites) if in_spans(float(x), spans))
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


def merged_spans(spans) -> list:
    """*spans* with touching or overlapping extents joined: the bands a panel draws, one per run of covered slots."""
    out: list = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def in_spans(x: float, spans) -> bool:
    """Whether *x* lies in one of the covered slots' extents (``WindowResult.spans``)."""
    return any(lo <= x <= hi for lo, hi in spans)


def _members_per_column(model, panel) -> int:
    """How many wells share a column of *panel*: the renderer draws violins above ``distribution_style_compute.density_above`` of the page's Show."""
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    by_col: dict = {}
    for s in panel.swarms:
        by_col.setdefault(round(s.x), set()).add(s.well)
    return max((len(v) for v in by_col.values()), default=1)
